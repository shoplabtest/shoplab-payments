import hashlib
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from .config import load_config
from .migrate import upgrade
from .store import PaymentRow, PgStore, Store, make_engine


def seed_user_id(i: int) -> str:
    """Same ids as shoplab-orders `seedUserId`; neither matches the ids iam generates."""
    h = hashlib.sha1(f"shoplab-seed-user-{i}".encode()).hexdigest()
    return f"{h[0:8]}-{h[8:12]}-4{h[13:16]}-a{h[17:20]}-{h[20:32]}"


def _id(kind: str, i: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"shoplab-seed-{kind}-{i}"))


OUTCOMES = ["captured"] * 12 + ["retried"] * 3 + ["pending"] * 2 + ["partial"] * 3 + ["refunded"] + ["failed"] * 2


def seed_payments(count: int = 100, seed: int = 13) -> List[PaymentRow]:
    rnd = random.Random(seed)
    base = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rows: List[PaymentRow] = []
    for i in range(count):
        outcome = rnd.choice(OUTCOMES)
        amount = 500 + rnd.randrange(40000)
        order_id, user_id = _id("order", i), seed_user_id(rnd.randrange(50))
        at = base + timedelta(hours=i)

        def row(attempt: int, status: str, refunded: int = 0, reason: Optional[str] = None) -> PaymentRow:
            return PaymentRow(_id(f"payment-{attempt}", i), order_id, user_id, status, amount, "USD", rnd.choice(["card", "wallet"]), attempt,
                              f"prov_seed{i:04d}{attempt}", reason, refunded, at, at)

        if outcome == "retried":
            rows += [row(1, "failed", reason="card_declined"), row(2, "captured")]
        elif outcome == "failed":
            rows.append(row(1, "failed", reason="insufficient_funds"))
        elif outcome == "partial":
            rows.append(row(1, "partially_refunded", refunded=1 + rnd.randrange(amount - 1)))
        elif outcome == "refunded":
            rows.append(row(1, "refunded", refunded=amount))
        else:
            rows.append(row(1, outcome))
    return rows


def seed(store: Store) -> int:
    return sum(1 for r in seed_payments() if store.import_row(r))


if __name__ == "__main__":
    url = load_config().database_url
    upgrade(url)
    print(f"seeded {seed(PgStore(make_engine(url)))} payments")
