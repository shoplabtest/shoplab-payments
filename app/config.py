import os
from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass(frozen=True)
class Config:
    port: int
    database_url: str
    rabbitmq_url: Optional[str]
    jwt_secret: str
    orders_callback_url: str
    webhook_secret: str
    max_attempts: int


def load_config(env: Optional[Mapping[str, str]] = None) -> Config:
    env = os.environ if env is None else env
    return Config(
        port=int(env.get("PORT", "3005")),
        database_url=env.get("DATABASE_URL", "postgresql+psycopg://payments:payments@localhost:5432/payments"),
        rabbitmq_url=env.get("RABBITMQ_URL"),
        jwt_secret=env.get("JWT_SECRET", "dev-only-secret"),
        orders_callback_url=env.get("ORDERS_CALLBACK_URL", "http://localhost:3004").rstrip("/"),
        webhook_secret=env.get("PAYMENT_WEBHOOK_SECRET", "dev-only-webhook-secret"),
        max_attempts=int(env.get("MAX_CHARGE_ATTEMPTS", "3")),
    )
