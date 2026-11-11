import json

from app.orders_client import OrderInfo

from conftest import ALICE, BOB, ORDER


def outcome(pid, kind, reason=None):
    body = {"paymentId": pid, "outcome": kind}
    if reason:
        body["reason"] = reason
    return json.dumps(body).encode()


def test_charge_creates_a_pending_payment_for_the_order_total(world):
    res = world.charge()
    assert res.status_code == 202
    body = res.json()
    assert set(body) == {"id", "orderId", "status", "amount", "currency", "refunded"}
    assert (body["status"], body["amount"], body["currency"], body["refunded"], body["orderId"]) == ("pending", 5000, "USD", 0, ORDER)
    assert world.provider.submitted == [(body["id"], 5000, "USD", "card")]
    assert world.store.rows[body["id"]].provider_ref.startswith("prov_")
    assert world.orders.seen_auth.startswith("Bearer ")


def test_charge_is_idempotent_while_a_payment_is_live(world):
    first = world.charge().json()
    second = world.charge(method="wallet").json()
    assert first["id"] == second["id"]
    assert len(world.store.rows) == 1
    assert len(world.provider.submitted) == 1


def test_charge_checks_auth_ownership_and_order_state(world):
    assert world.client.post("/payments/charges", json={"orderId": ORDER, "method": "card"}).status_code == 401
    assert world.charge(sub=BOB).status_code == 404
    assert world.charge(order="bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb").status_code == 404
    world.orders.orders[ORDER] = OrderInfo(ORDER, ALICE, "cancelled", 5000, "USD")
    assert world.charge().json() == {"error": "order_not_payable"}
    world.orders.orders[ORDER] = OrderInfo(ORDER, ALICE, "placed", 5000, "USD")
    world.orders.down = True
    assert world.charge().status_code == 502
    world.orders.down = False
    assert world.charge(sub=BOB, role="staff").status_code == 202  # staff may charge on the customer's behalf
    assert world.store.rows[next(iter(world.store.rows))].user_id == ALICE


def test_charge_validates_input(world):
    assert world.charge(method="cash").status_code == 400
    assert world.client.post("/payments/charges", json={"orderId": "nope", "method": "card"}, headers=world.token(ALICE)).json()["error"] == "invalid_request"


def test_webhook_needs_a_valid_signature(world):
    pid = world.charge().json()["id"]
    body = outcome(pid, "captured")
    assert world.client.post("/payments/webhook", content=body).status_code == 401
    assert world.hook(body, secret="wrong").status_code == 401
    assert world.client.post("/payments/webhook", content=body, headers={"x-provider-signature": "sha256=" + "0" * 64}).status_code == 401
    assert world.store.rows[pid].status == "pending"
    assert world.hook(body).status_code == 204


def test_captured_publishes_payment_captured_once(world):
    pid = world.charge().json()["id"]
    assert world.hook(outcome(pid, "captured")).status_code == 204
    assert world.hook(outcome(pid, "captured")).status_code == 204  # provider retry
    assert world.hook(outcome(pid, "failed", "late")).status_code == 204  # conflicting verdict is ignored
    assert world.store.rows[pid].status == "captured"
    assert len(world.events.published) == 1
    key, ev = world.events.published[0]
    assert key == "payment.captured"
    assert set(ev) == {"paymentId", "orderId", "amount", "currency", "capturedAt"}
    assert (ev["paymentId"], ev["orderId"], ev["amount"], ev["currency"]) == (pid, ORDER, 5000, "USD")
    assert ev["capturedAt"].endswith("Z")


def test_failed_publishes_payment_failed_and_allows_three_attempts(world):
    ids = []
    for n in range(3):
        pid = world.charge().json()["id"]
        ids.append(pid)
        assert world.hook(outcome(pid, "failed", f"declined {n}")).status_code == 204
    assert len(set(ids)) == 3
    assert [r.attempt for r in world.store.rows.values()] == [1, 2, 3]
    keys = [k for k, _ in world.events.published]
    assert keys == ["payment.failed"] * 3
    assert world.events.published[0][1]["reason"] == "declined 0"
    assert set(world.events.published[0][1]) == {"paymentId", "orderId", "reason", "failedAt"}
    assert world.charge().json() == {"error": "retries_exhausted"}


def test_unknown_payment_in_a_webhook_is_404(world):
    assert world.hook(outcome("cccccccc-cccc-4ccc-cccc-cccccccccccc", "captured")).status_code == 404
    assert world.client.post("/payments/webhook", content=b"{}", headers={"x-provider-signature": "x"}).status_code == 401


def test_a_broker_outage_delays_the_event_but_not_the_webhook(world):
    pid = world.charge().json()["id"]
    world.events.fail = True
    assert world.hook(outcome(pid, "captured")).status_code == 204
    assert world.events.published == []
    assert len(world.store.outbox_pending(10)) == 1
    world.events.fail = False
    assert world.app.state.payments.drain() == 1
    assert world.app.state.payments.drain() == 0
    assert [k for k, _ in world.events.published] == ["payment.captured"]


def test_get_payment_is_visible_to_the_owner_and_staff(world):
    pid = world.charge().json()["id"]
    assert world.client.get(f"/payments/{pid}", headers=world.token(ALICE)).json()["id"] == pid
    assert world.client.get(f"/payments/{pid}", headers=world.token(BOB)).status_code == 404
    assert world.client.get(f"/payments/{pid}", headers=world.token(BOB, "admin")).status_code == 200
    assert world.client.get(f"/payments/{pid}").status_code == 401
    assert world.client.get("/payments/cccccccc-cccc-4ccc-cccc-cccccccccccc", headers=world.token(ALICE, "staff")).status_code == 404


def test_refunds_are_staff_only_partial_and_bounded(world):
    pid = world.charge().json()["id"]
    refund = lambda amount, role="staff": world.client.post(f"/payments/{pid}/refunds", json={"amount": amount}, headers=world.token(BOB, role))
    assert refund(100).json() == {"error": "not_refundable"}  # still pending
    world.hook(outcome(pid, "captured"))
    assert refund(100, "customer").status_code == 403
    assert world.client.post(f"/payments/{pid}/refunds", json={"amount": 100}).status_code == 401
    assert refund(0).status_code == 400
    first = refund(1500)
    assert first.status_code == 201
    assert (first.json()["status"], first.json()["refunded"]) == ("partially_refunded", 1500)
    assert refund(3501).json() == {"error": "refund_exceeds_remaining"}
    last = refund(3500)
    assert (last.json()["status"], last.json()["refunded"]) == ("refunded", 5000)
    assert refund(1).status_code == 409
    assert world.charge().json()["id"] == pid  # a refunded payment still counts as the order's payment
    assert world.client.post("/payments/cccccccc-cccc-4ccc-cccc-cccccccccccc/refunds", json={"amount": 1}, headers=world.token(BOB, "staff")).status_code == 404
