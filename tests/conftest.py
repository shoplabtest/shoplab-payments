import time
from typing import Dict, Optional

import jwt
import pytest
from fastapi.testclient import TestClient

from app.config import Config, load_config
from app.events import NullPublisher
from app.main import create_app, sign
from app.orders_client import OrderInfo, OrdersUnavailable
from app.provider import SandboxProvider
from app.store import MemoryStore

ALICE = "11111111-1111-4111-a111-111111111111"
BOB = "22222222-2222-4222-a222-222222222222"
ORDER = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"


class FakeOrders:
    def __init__(self) -> None:
        self.orders: Dict[str, OrderInfo] = {ORDER: OrderInfo(ORDER, ALICE, "placed", 5000, "USD")}
        self.down = False
        self.seen_auth: Optional[str] = None

    def get_order(self, order_id: str, authorization: str) -> Optional[OrderInfo]:
        self.seen_auth = authorization
        if self.down:
            raise OrdersUnavailable("down")
        return self.orders.get(order_id)


class Provider(SandboxProvider):
    def __init__(self) -> None:
        self.submitted = []

    def submit(self, payment_id, amount, currency, method):
        self.submitted.append((payment_id, amount, currency, method))
        return super().submit(payment_id, amount, currency, method)


@pytest.fixture
def cfg() -> Config:
    return load_config({"JWT_SECRET": "test-secret-0123456789-0123456789-xx", "PAYMENT_WEBHOOK_SECRET": "whsec", "MAX_CHARGE_ATTEMPTS": "3"})


@pytest.fixture
def world(cfg):
    class W:
        pass

    w = W()
    w.cfg, w.store, w.orders, w.provider, w.events = cfg, MemoryStore(), FakeOrders(), Provider(), NullPublisher()
    w.app = create_app(cfg, w.store, w.orders, w.provider, w.events)
    w.client = TestClient(w.app)
    w.token = lambda sub, role="customer": {"authorization": "Bearer " + jwt.encode({"sub": sub, "role": role, "tenant": "default", "exp": int(time.time()) + 600}, cfg.jwt_secret, algorithm="HS256")}
    w.hook = lambda body, secret=None: w.client.post(
        "/payments/webhook", content=body, headers={"x-provider-signature": sign(secret or cfg.webhook_secret, body), "content-type": "application/json"}
    )
    w.charge = lambda sub=ALICE, order=ORDER, method="card", role="customer": w.client.post(
        "/payments/charges", json={"orderId": order, "method": method}, headers=w.token(sub, role)
    )
    return w
