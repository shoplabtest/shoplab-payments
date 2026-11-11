import json
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, Tuple

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from .events import event_for

REFUNDABLE = ("captured", "partially_refunded")


@dataclass(frozen=True)
class PaymentRow:
    id: str
    order_id: str
    user_id: str
    status: str
    amount: int
    currency: str
    method: str
    attempt: int
    provider_ref: Optional[str]
    failure_reason: Optional[str]
    refunded: int
    created_at: datetime
    updated_at: datetime


class Store(Protocol):
    def open_payment(self, order_id: str) -> Optional[PaymentRow]: ...
    def attempts(self, order_id: str) -> int: ...
    def create_attempt(self, order_id: str, user_id: str, amount: int, currency: str, method: str) -> Tuple[PaymentRow, bool]: ...
    def set_provider_ref(self, payment_id: str, ref: str) -> None: ...
    def get(self, payment_id: str) -> Optional[PaymentRow]: ...
    def settle(self, payment_id: str, outcome: str, reason: Optional[str], at: datetime) -> Tuple[Optional[PaymentRow], bool]: ...
    def refund(self, payment_id: str, amount: int) -> Tuple[str, Optional[PaymentRow]]: ...
    def outbox_pending(self, limit: int) -> List[Tuple[int, str, Dict[str, Any]]]: ...
    def mark_published(self, outbox_id: int) -> None: ...
    def import_row(self, row: PaymentRow) -> bool: ...


COLS = (
    "id::text AS id, order_id::text AS order_id, user_id::text AS user_id, status, amount, currency, method, attempt, "
    "provider_ref, failure_reason, refunded, created_at, updated_at"
)


def _row(m: Any) -> PaymentRow:
    return PaymentRow(**dict(m))


def make_engine(url: str) -> Engine:
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return create_engine(url, pool_pre_ping=True)


class PgStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def open_payment(self, order_id: str) -> Optional[PaymentRow]:
        with self.engine.connect() as c:
            m = c.execute(
                text(f"SELECT {COLS} FROM payments WHERE order_id = CAST(:o AS uuid) AND status <> 'failed'"), {"o": order_id}
            ).mappings().first()
        return _row(m) if m else None

    def attempts(self, order_id: str) -> int:
        with self.engine.connect() as c:
            return c.execute(text("SELECT count(*) FROM payments WHERE order_id = CAST(:o AS uuid)"), {"o": order_id}).scalar_one()

    def create_attempt(self, order_id: str, user_id: str, amount: int, currency: str, method: str) -> Tuple[PaymentRow, bool]:
        params = {"o": order_id, "u": user_id, "a": amount, "c": currency, "m": method}
        try:
            with self.engine.begin() as c:
                m = c.execute(
                    text(
                        f"""INSERT INTO payments (order_id, user_id, status, amount, currency, method, attempt)
                        VALUES (CAST(:o AS uuid), CAST(:u AS uuid), 'pending', :a, :c, :m,
                                (SELECT COALESCE(MAX(attempt), 0) + 1 FROM payments WHERE order_id = CAST(:o AS uuid)))
                        RETURNING {COLS}"""
                    ),
                    params,
                ).mappings().one()
            return _row(m), True
        except IntegrityError:
            # A concurrent request created the live payment first; hand that one back.
            existing = self.open_payment(order_id)
            if existing is None:
                raise
            return existing, False

    def set_provider_ref(self, payment_id: str, ref: str) -> None:
        with self.engine.begin() as c:
            c.execute(text("UPDATE payments SET provider_ref = :r WHERE id = CAST(:i AS uuid)"), {"r": ref, "i": payment_id})

    def get(self, payment_id: str) -> Optional[PaymentRow]:
        with self.engine.connect() as c:
            m = c.execute(text(f"SELECT {COLS} FROM payments WHERE id = CAST(:i AS uuid)"), {"i": payment_id}).mappings().first()
        return _row(m) if m else None

    def settle(self, payment_id: str, outcome: str, reason: Optional[str], at: datetime) -> Tuple[Optional[PaymentRow], bool]:
        with self.engine.begin() as c:
            cur = c.execute(
                text(f"SELECT {COLS} FROM payments WHERE id = CAST(:i AS uuid) FOR UPDATE"), {"i": payment_id}
            ).mappings().first()
            if cur is None:
                return None, False
            if cur["status"] != "pending":
                return _row(cur), False
            new = c.execute(
                text(
                    f"""UPDATE payments SET status = :s, failure_reason = :r, updated_at = :at
                    WHERE id = CAST(:i AS uuid) RETURNING {COLS}"""
                ),
                {"s": "captured" if outcome == "captured" else "failed", "r": reason if outcome == "failed" else None, "at": at, "i": payment_id},
            ).mappings().one()
            row = _row(new)
            key, body = event_for(outcome, row, reason, at)
            c.execute(text("INSERT INTO outbox (routing_key, body) VALUES (:k, CAST(:b AS jsonb))"), {"k": key, "b": json.dumps(body)})
            return row, True

    def refund(self, payment_id: str, amount: int) -> Tuple[str, Optional[PaymentRow]]:
        with self.engine.begin() as c:
            cur = c.execute(
                text(f"SELECT {COLS} FROM payments WHERE id = CAST(:i AS uuid) FOR UPDATE"), {"i": payment_id}
            ).mappings().first()
            if cur is None:
                return "not_found", None
            row = _row(cur)
            if row.status not in REFUNDABLE:
                return "not_refundable", row
            if amount > row.amount - row.refunded:
                return "exceeds", row
            c.execute(text("INSERT INTO refunds (payment_id, amount) VALUES (CAST(:i AS uuid), :a)"), {"i": payment_id, "a": amount})
            total = row.refunded + amount
            new = c.execute(
                text(
                    f"""UPDATE payments SET refunded = :t, status = :s, updated_at = now()
                    WHERE id = CAST(:i AS uuid) RETURNING {COLS}"""
                ),
                {"t": total, "s": "refunded" if total == row.amount else "partially_refunded", "i": payment_id},
            ).mappings().one()
            return "ok", _row(new)

    def outbox_pending(self, limit: int) -> List[Tuple[int, str, Dict[str, Any]]]:
        with self.engine.connect() as c:
            rs = c.execute(text("SELECT id, routing_key, body FROM outbox WHERE published_at IS NULL ORDER BY id LIMIT :n"), {"n": limit})
            return [(r.id, r.routing_key, r.body) for r in rs]

    def mark_published(self, outbox_id: int) -> None:
        with self.engine.begin() as c:
            c.execute(text("UPDATE outbox SET published_at = now() WHERE id = :i"), {"i": outbox_id})

    def import_row(self, row: PaymentRow) -> bool:
        with self.engine.begin() as c:
            r = c.execute(
                text(
                    """INSERT INTO payments (id, order_id, user_id, status, amount, currency, method, attempt, provider_ref,
                                             failure_reason, refunded, created_at, updated_at)
                    VALUES (CAST(:id AS uuid), CAST(:order_id AS uuid), CAST(:user_id AS uuid), :status, :amount, :currency, :method,
                            :attempt, :provider_ref, :failure_reason, :refunded, :created_at, :updated_at)
                    ON CONFLICT DO NOTHING"""
                ),
                row.__dict__,
            )
            created = (r.rowcount or 0) > 0
            if created and row.refunded:
                c.execute(text("INSERT INTO refunds (payment_id, amount) VALUES (CAST(:i AS uuid), :a)"), {"i": row.id, "a": row.refunded})
            return created


class MemoryStore:
    """In-memory store for unit tests. Mirrors the SQL rules, including the one-live-payment-per-order index."""

    def __init__(self) -> None:
        self.rows: Dict[str, PaymentRow] = {}
        self.refunds: List[Tuple[str, int]] = []
        self.outbox: List[Dict[str, Any]] = []
        self._lock = threading.RLock()

    def open_payment(self, order_id: str) -> Optional[PaymentRow]:
        with self._lock:
            return next((r for r in self.rows.values() if r.order_id == order_id and r.status != "failed"), None)

    def attempts(self, order_id: str) -> int:
        with self._lock:
            return sum(1 for r in self.rows.values() if r.order_id == order_id)

    def create_attempt(self, order_id: str, user_id: str, amount: int, currency: str, method: str) -> Tuple[PaymentRow, bool]:
        with self._lock:
            existing = self.open_payment(order_id)
            if existing:
                return existing, False
            now = datetime.now().astimezone()
            row = PaymentRow(str(uuid.uuid4()), order_id, user_id, "pending", amount, currency, method, self.attempts(order_id) + 1, None, None, 0, now, now)
            self.rows[row.id] = row
            return row, True

    def set_provider_ref(self, payment_id: str, ref: str) -> None:
        with self._lock:
            self.rows[payment_id] = replace(self.rows[payment_id], provider_ref=ref)

    def get(self, payment_id: str) -> Optional[PaymentRow]:
        with self._lock:
            return self.rows.get(payment_id)

    def settle(self, payment_id: str, outcome: str, reason: Optional[str], at: datetime) -> Tuple[Optional[PaymentRow], bool]:
        with self._lock:
            cur = self.rows.get(payment_id)
            if cur is None:
                return None, False
            if cur.status != "pending":
                return cur, False
            row = replace(cur, status="captured" if outcome == "captured" else "failed", failure_reason=reason if outcome == "failed" else None, updated_at=at)
            self.rows[payment_id] = row
            key, body = event_for(outcome, row, reason, at)
            self.outbox.append({"id": len(self.outbox) + 1, "key": key, "body": body, "published": False})
            return row, True

    def refund(self, payment_id: str, amount: int) -> Tuple[str, Optional[PaymentRow]]:
        with self._lock:
            row = self.rows.get(payment_id)
            if row is None:
                return "not_found", None
            if row.status not in REFUNDABLE:
                return "not_refundable", row
            if amount > row.amount - row.refunded:
                return "exceeds", row
            total = row.refunded + amount
            new = replace(row, refunded=total, status="refunded" if total == row.amount else "partially_refunded", updated_at=datetime.now().astimezone())
            self.rows[payment_id] = new
            self.refunds.append((payment_id, amount))
            return "ok", new

    def outbox_pending(self, limit: int) -> List[Tuple[int, str, Dict[str, Any]]]:
        with self._lock:
            return [(o["id"], o["key"], o["body"]) for o in self.outbox if not o["published"]][:limit]

    def mark_published(self, outbox_id: int) -> None:
        with self._lock:
            self.outbox[outbox_id - 1]["published"] = True

    def import_row(self, row: PaymentRow) -> bool:
        with self._lock:
            if row.id in self.rows:
                return False
            self.rows[row.id] = row
            return True
