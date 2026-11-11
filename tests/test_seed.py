from app.seed import seed, seed_payments, seed_user_id
from app.store import MemoryStore


def test_seed_is_deterministic_and_consistent():
    a, b = seed_payments(), seed_payments()
    assert a == b
    assert len({r.id for r in a}) == len(a)
    assert all(r.refunded <= r.amount for r in a)
    assert all((r.status in ("refunded",)) == (r.refunded == r.amount) for r in a)
    live = [r.order_id for r in a if r.status != "failed"]
    assert len(live) == len(set(live)), "at most one live payment per order"
    assert {"captured", "pending", "failed", "partially_refunded", "refunded"} <= {r.status for r in a}


def test_seed_user_ids_match_the_orders_service():
    assert seed_user_id(0) == "78db596d-05c1-4f55-a5ff-eb4939d742a0"
    assert seed_user_id(7) == "a2f57e0a-5abb-42de-a3dc-73fe9597de72"


def test_seed_is_idempotent():
    store = MemoryStore()
    n = seed(store)
    assert n == len(seed_payments()) and seed(store) == 0
