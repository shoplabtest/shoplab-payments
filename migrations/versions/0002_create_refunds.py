"""create refunds

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE refunds (
          id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          payment_id uuid NOT NULL REFERENCES payments(id),
          amount     integer NOT NULL CHECK (amount > 0),
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX refunds_payment_idx ON refunds (payment_id);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE refunds")
