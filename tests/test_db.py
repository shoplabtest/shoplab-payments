import os
import threading
import time

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import load_config
from app.events import NullPublisher
from app.main import create_app, sign
from app.migrate import upgrade
from app.provider import SandboxProvider
from app.seed import seed, seed_payments
from app.store import PgStore, make_engine

from conftest import ALICE, FakeOrders, ORDER

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture(scope="module")
def engine():
    eng = make_engine(URL)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    upgrade(URL)
    yield eng
    eng.dispose()


def concurrently(n, fn):
    out, barrier = [None] * n, threading.Barrier(n)

    def run(i):
        barrier.wait()
        out[i] = fn(i)

    ts = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return out


def test_migrations_apply_to_an_empty_database_and_are_repeatable(engine):
    with engine.connect() as c:
        tables = {r[0] for r in c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))}
    assert {"payments", "refunds", "outbox", "alembic_version"} <= tables
    upgrade(URL)  # second run is a no-op


def test_seed_is_idempotent_on_postgres(engine):
    store = PgStore(engine)
    assert seed(store) == len(seed_payments())
    assert seed(store) == 0
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM refunds")).scalar_one() == sum(1 for r in seed_payments() if r.refunded)


def test_only_one_live_payment_per_order_under_concurrency(engine):
    store = PgStore(engine)
    order = "dddddddd-dddd-4ddd-dddd-dddddddddddd"
    results = concurrently(8, lambda i: store.create_attempt(order, ALICE, 1000, "USD", "card"))
    assert sum(1 for _, created in results if created) == 1
    assert len({row.id for row, _ in results}) == 1
    pid = results[0][0].id
    store.settle(pid, "failed", "declined", time_now())
    again, created = store.create_attempt(order, ALICE, 1000, "USD", "card")
    assert created and again.attempt == 2 and store.attempts(order) == 2


def time_now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def test_settling_twice_writes_one_outbox_row(engine):
    store = PgStore(engine)
    row, _ = store.create_attempt("eeeeeeee-eeee-4eee-eeee-eeeeeeeeeeee", ALICE, 900, "USD", "wallet")
    results = concurrently(6, lambda i: store.settle(row.id, "captured", None, time_now()))
    assert sum(1 for _, changed in results if changed) == 1
    with engine.connect() as c:
        n = c.execute(text("SELECT count(*) FROM outbox WHERE body->>'paymentId' = :p"), {"p": row.id}).scalar_one()
    assert n == 1


def test_concurrent_refunds_never_exceed_the_amount(engine):
    store = PgStore(engine)
    row, _ = store.create_attempt("ffffffff-ffff-4fff-ffff-ffffffffffff", ALICE, 1000, "USD", "card")
    store.settle(row.id, "captured", None, time_now())
    results = concurrently(5, lambda i: store.refund(row.id, 400))
    assert sorted(r for r, _ in results) == ["exceeds", "exceeds", "exceeds", "ok", "ok"]
    final = store.get(row.id)
    assert (final.refunded, final.status) == (800, "partially_refunded")
    assert store.refund(row.id, 200)[1].status == "refunded"


def test_charge_webhook_and_event_end_to_end_on_postgres(engine):
    cfg = load_config({"JWT_SECRET": "s" * 40, "PAYMENT_WEBHOOK_SECRET": "w"})
    events = NullPublisher()
    auth = {"authorization": "Bearer " + jwt.encode({"sub": ALICE, "role": "customer", "tenant": "t", "exp": int(time.time()) + 60}, "s" * 40, algorithm="HS256")}
    # the order id is shared with other tests, so use a fresh order for this flow
    order = "99999999-9999-4999-9999-999999999999"
    orders = FakeOrders()
    orders.orders[order] = orders.orders[ORDER]
    client = TestClient(create_app(cfg, PgStore(engine), orders, SandboxProvider(), events))
    pid = client.post("/payments/charges", json={"orderId": order, "method": "card"}, headers=auth).json()["id"]
    body = f'{{"paymentId": "{pid}", "outcome": "captured"}}'.encode()
    assert client.post("/payments/webhook", content=body, headers={"x-provider-signature": sign("w", body)}).status_code == 204
    assert client.get(f"/payments/{pid}", headers=auth).json()["status"] == "captured"
    # drain also flushes rows left by earlier tests, so look at this payment's events only
    mine = [k for k, b in events.published if b["paymentId"] == pid]
    assert mine == ["payment.captured"]
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM outbox WHERE published_at IS NULL")).scalar_one() == 0
