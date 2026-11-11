from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig

ROOT = Path(__file__).resolve().parent.parent


def upgrade(database_url: str) -> None:
    """Applies every Alembic migration in migrations/versions."""
    cfg = AlembicConfig(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")
