from dataclasses import dataclass
from typing import Optional, Protocol

import httpx


@dataclass(frozen=True)
class OrderInfo:
    id: str
    user_id: str
    status: str
    total: int
    currency: str


class OrdersUnavailable(Exception):
    pass


class OrdersClient(Protocol):
    def get_order(self, order_id: str, authorization: str) -> Optional[OrderInfo]:
        """None when the order does not exist; raises OrdersUnavailable on any other failure."""


class HttpOrdersClient:
    def __init__(self, base_url: str, timeout: float = 3.0) -> None:
        self.base_url = base_url
        self.timeout = timeout

    def get_order(self, order_id: str, authorization: str) -> Optional[OrderInfo]:
        try:
            res = httpx.get(f"{self.base_url}/orders/{order_id}", headers={"authorization": authorization}, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise OrdersUnavailable(str(e)) from e
        if res.status_code in (403, 404):
            return None
        if res.status_code != 200:
            raise OrdersUnavailable(f"orders responded {res.status_code}")
        o = res.json()
        return OrderInfo(id=o["id"], user_id=o["userId"], status=o["status"], total=o["total"], currency=o["currency"])
