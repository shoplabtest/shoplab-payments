import logging
from typing import Protocol

log = logging.getLogger("payments.provider")


class Provider(Protocol):
    def submit(self, payment_id: str, amount: int, currency: str, method: str) -> str:
        """Hands the charge to the payment provider and returns its reference.
        The outcome arrives later on POST /payments/webhook."""


class SandboxProvider:
    """Stand-in for the real provider: accepts everything and answers through the webhook."""

    def submit(self, payment_id: str, amount: int, currency: str, method: str) -> str:
        log.info("provider charge %s %s %s via %s", payment_id, amount, currency, method)
        return f"prov_{payment_id.replace('-', '')[:12]}"
