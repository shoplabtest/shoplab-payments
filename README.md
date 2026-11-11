# shoplab-payments

Charges, partial refunds, retries and the provider webhook. Python 3.12, FastAPI, SQLAlchemy, Alembic, Postgres.

| Endpoint | Notes |
| --- | --- |
| `POST /payments/charges` | `{ orderId, method: card \| wallet }`. Looks the order up at `ORDERS_CALLBACK_URL` with the caller's own token, charges the order total, answers `202`. Repeating the call while a payment is live returns that payment. After a failed payment the customer can try again, up to `MAX_CHARGE_ATTEMPTS` (3) per order, then `409 retries_exhausted` |
| `GET /payments/:id` | the owner or staff |
| `POST /payments/:id/refunds` | staff or admin. `{ amount }` in minor units; partial refunds add up and may not exceed what is left (`409`) |
| `POST /payments/webhook` | called by the provider. The raw body is signed with hex HMAC-SHA256 in `x-provider-signature` (an optional `sha256=` prefix is accepted); the key is `PAYMENT_WEBHOOK_SECRET`. `204` when processed or replayed, `401` for a bad signature, `404` for an unknown payment |

Payment status: `pending` → `captured` or `failed`; a captured payment moves to `partially_refunded` and then `refunded`.

**Events.** `payment.captured` and `payment.failed` are published to the `shop.events` topic exchange. They are written to an `outbox` table in the same transaction as the status change and sent afterwards, so a broker outage delays an event but never loses it, and a replayed webhook never sends one twice. The shipped schemas are in `shoplab-contracts`. `shoplab-orders` consumes both: a captured payment moves the order to `paid`.

**Auth.** The iam JWT (`sub`, `role`, `tenant`, shared `JWT_SECRET`). Customers see only their own payments.

Env: `DATABASE_URL`, `JWT_SECRET`, `PAYMENT_WEBHOOK_SECRET`, `ORDERS_CALLBACK_URL` (default `http://localhost:3004`), `RABBITMQ_URL` (optional; without it events are discarded after the outbox marks them sent), `PORT` (3005), `MAX_CHARGE_ATTEMPTS`.

## Develop

```
pip install -r requirements-dev.txt
python -m pytest                          # in-memory store
TEST_DATABASE_URL=postgres://postgres:pw@localhost:5432/test python -m pytest   # also migrations, seed and concurrency tests
alembic upgrade head                      # needs DATABASE_URL
python -m app.seed                        # same 111 payments every time
python scripts/simulate_provider.py <paymentId> captured    # plays the provider (signed webhook)
```

`migrations/` holds the Alembic versions and `schema.sql` is a `pg_dump` snapshot. The seed users use the same stable ids as `shoplab-orders`' seed; the seeded order ids are not orders' (the services share no database), and neither matches the ids iam generates.
