"""create event outbox

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE outbox (
          id           bigserial PRIMARY KEY,
          routing_key  text NOT NULL,
          body         jsonb NOT NULL,
          created_at   timestamptz NOT NULL DEFAULT now(),
          published_at timestamptz
        );
        CREATE INDEX outbox_unpublished_idx ON outbox (id) WHERE published_at IS NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE outbox")
