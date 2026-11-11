import logging
import threading
import time

from .config import load_config
from .events import NullPublisher, RabbitPublisher
from .main import create_app
from .orders_client import HttpOrdersClient
from .provider import SandboxProvider
from .store import PgStore, make_engine

log = logging.getLogger("payments.server")


def build():
    cfg = load_config()
    publisher = RabbitPublisher(cfg.rabbitmq_url) if cfg.rabbitmq_url else NullPublisher()
    app = create_app(cfg, PgStore(make_engine(cfg.database_url)), HttpOrdersClient(cfg.orders_callback_url), SandboxProvider(), publisher)

    def drain_forever() -> None:
        while True:
            try:
                app.state.payments.drain()
            except Exception:
                log.exception("outbox drain failed")
            time.sleep(5)

    threading.Thread(target=drain_forever, daemon=True, name="outbox-drain").start()
    return app


app = build()
