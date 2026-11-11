import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from .auth import Principal
from .config import Config
from .events import Publisher
from .orders_client import OrdersClient, OrdersUnavailable
from .provider import Provider
from .store import PaymentRow, Store

log = logging.getLogger("payments.service")


class ServiceError(Exception):
    def __init__(self, status: int, error: str) -> None:
        super().__init__(error)
        self.status = status
        self.error = error


def present(p: PaymentRow) -> dict:
    """Payment schema from shoplab-contracts openapi/payments.yaml."""
    return {"id": p.id, "orderId": p.order_id, "status": p.status, "amount": p.amount, "currency": p.currency, "refunded": p.refunded}


class Payments:
    def __init__(self, cfg: Config, store: Store, orders: OrdersClient, provider: Provider, publisher: Publisher) -> None:
        self.cfg = cfg
        self.store = store
        self.orders = orders
        self.provider = provider
        self.publisher = publisher

    def create_charge(self, who: Principal, order_id: str, method: str) -> Tuple[PaymentRow, bool]:
        """Charges the order total. Repeating the call while a payment is live returns that payment; a failed
        payment allows a new attempt, up to `max_attempts` per order."""
        try:
            order = self.orders.get_order(order_id, f"Bearer {who.token}")
        except OrdersUnavailable:
            raise ServiceError(502, "orders_unavailable")
        if order is None or (order.user_id != who.sub and not who.is_staff):
            raise ServiceError(404, "order_not_found")
        if order.status != "placed" or order.total < 1:
            raise ServiceError(409, "order_not_payable")
        live = self.store.open_payment(order_id)
        if live:
            return live, False
        if self.store.attempts(order_id) >= self.cfg.max_attempts:
            raise ServiceError(409, "retries_exhausted")
        row, created = self.store.create_attempt(order_id, order.user_id, order.total, order.currency, method)
        if created:
            ref = self.provider.submit(row.id, row.amount, row.currency, row.method)
            self.store.set_provider_ref(row.id, ref)
            row = self.store.get(row.id) or row
        return row, created

    def get_payment(self, who: Principal, payment_id: str) -> PaymentRow:
        row = self.store.get(payment_id)
        if row is None or (row.user_id != who.sub and not who.is_staff):
            raise ServiceError(404, "not_found")
        return row

    def refund(self, payment_id: str, amount: int) -> PaymentRow:
        result, row = self.store.refund(payment_id, amount)
        if result == "not_found" or row is None:
            raise ServiceError(404, "not_found")
        if result == "not_refundable":
            raise ServiceError(409, "not_refundable")
        if result == "exceeds":
            raise ServiceError(409, "refund_exceeds_remaining")
        return row

    def handle_webhook(self, payment_id: str, outcome: str, reason: Optional[str]) -> bool:
        """Applies the provider's verdict once. A replay, or a verdict for an already settled payment, changes nothing."""
        row, changed = self.store.settle(payment_id, outcome, reason, datetime.now(timezone.utc))
        if row is None:
            raise ServiceError(404, "unknown_payment")
        self.drain()
        return changed

    def drain(self, limit: int = 50) -> int:
        """Publishes outbox rows. A broker outage leaves them queued for the next call."""
        sent = 0
        for outbox_id, key, body in self.store.outbox_pending(limit):
            try:
                self.publisher.publish(key, body)
            except Exception as e:  # broker down: keep the row and try again on the next call
                log.warning("publish %s failed: %s", key, e)
                break
            self.store.mark_published(outbox_id)
            sent += 1
        return sent
