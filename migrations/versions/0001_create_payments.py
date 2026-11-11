"""create payments

Revision ID: 0001
Revises:
"""
from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE payments (
          id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          order_id       uuid NOT NULL,
          user_id        uuid NOT NULL,
          status         text NOT NULL CHECK (status IN ('pending', 'captured', 'failed', 'refunded', 'partially_refunded')),
          amount         integer NOT NULL CHECK (amount > 0),
          currency       text NOT NULL,
          method         text NOT NULL CHECK (method IN ('card', 'wallet')),
          attempt        integer NOT NULL CHECK (attempt >= 1),
          provider_ref   text,
          failure_reason text,
          refunded       integer NOT NULL DEFAULT 0 CHECK (refunded >= 0 AND refunded <= amount),
          created_at     timestamptz NOT NULL DEFAULT now(),
          updated_at     timestamptz NOT NULL DEFAULT now(),
          UNIQUE (order_id, attempt)
        );
        CREATE INDEX payments_order_idx ON payments (order_id);
        -- At most one live payment per order; only a failed attempt lets the customer try again.
        CREATE UNIQUE INDEX payments_one_open_per_order ON payments (order_id) WHERE status <> 'failed';
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE payments")
