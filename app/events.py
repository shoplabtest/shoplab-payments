import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, Tuple

import pika

log = logging.getLogger("payments.events")

EXCHANGE = "shop.events"


def iso(at: datetime) -> str:
    return at.isoformat().replace("+00:00", "Z")


def event_for(outcome: str, payment: Any, reason: Optional[str], at: datetime) -> Tuple[str, Dict[str, Any]]:
    """Bodies follow shoplab-contracts events/payment.captured and payment.failed."""
    if outcome == "captured":
        return "payment.captured", {
            "paymentId": payment.id,
            "orderId": payment.order_id,
            "amount": payment.amount,
            "currency": payment.currency,
            "capturedAt": iso(at),
        }
    body: Dict[str, Any] = {"paymentId": payment.id, "orderId": payment.order_id, "failedAt": iso(at)}
    if reason:
        body["reason"] = reason
    return "payment.failed", body


class Publisher(Protocol):
    def publish(self, routing_key: str, body: Dict[str, Any]) -> None: ...


class NullPublisher:
    def __init__(self) -> None:
        self.published: List[Tuple[str, Dict[str, Any]]] = []
        self.fail = False

    def publish(self, routing_key: str, body: Dict[str, Any]) -> None:
        if self.fail:
            raise ConnectionError("broker unavailable")
        self.published.append((routing_key, body))


class RabbitPublisher:
    """Opens a short-lived connection per message; the outbox retries when the broker is down."""

    def __init__(self, url: str) -> None:
        self.params = pika.URLParameters(url)

    def publish(self, routing_key: str, body: Dict[str, Any]) -> None:
        conn = pika.BlockingConnection(self.params)
        try:
            ch = conn.channel()
            ch.exchange_declare(exchange=EXCHANGE, exchange_type="topic", durable=True)
            ch.basic_publish(
                exchange=EXCHANGE,
                routing_key=routing_key,
                body=json.dumps(body).encode(),
                properties=pika.BasicProperties(content_type="application/json", delivery_mode=2),
            )
        finally:
            conn.close()
