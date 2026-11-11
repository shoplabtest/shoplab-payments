import hashlib
import hmac
import json
from typing import Literal, Optional
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .auth import Principal, authenticate
from .config import Config
from .events import Publisher
from .orders_client import OrdersClient
from .provider import Provider
from .service import Payments, ServiceError, present
from .store import Store


class ChargeBody(BaseModel):
    orderId: UUID
    method: Literal["card", "wallet"]


class RefundBody(BaseModel):
    amount: int = Field(ge=1)


class WebhookBody(BaseModel):
    paymentId: UUID
    outcome: Literal["captured", "failed"]
    reason: Optional[str] = None


def sign(secret: str, body: bytes) -> str:
    """The provider's signature: hex HMAC-SHA256 of the raw request body."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def create_app(cfg: Config, store: Store, orders: OrdersClient, provider: Provider, publisher: Publisher) -> FastAPI:
    app = FastAPI(title="ShopLab Payments", docs_url=None, redoc_url=None, openapi_url=None)
    payments = Payments(cfg, store, orders, provider, publisher)
    app.state.payments = payments
    user = authenticate(cfg)

    def staff(who: Principal = Depends(user)) -> Principal:
        if not who.is_staff:
            raise HTTPException(403, {"error": "forbidden"})
        return who

    @app.exception_handler(HTTPException)
    async def http_error(_req: Request, e: HTTPException):
        return JSONResponse(e.detail if isinstance(e.detail, dict) else {"error": str(e.detail)}, status_code=e.status_code)

    @app.exception_handler(ServiceError)
    async def service_error(_req: Request, e: ServiceError):
        return JSONResponse({"error": e.error}, status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def invalid(_req: Request, e: RequestValidationError):
        fields = [str(x) for err in e.errors() for x in err["loc"][-1:]]
        return JSONResponse({"error": "invalid_request", "field": fields[0] if fields else None}, status_code=400)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.post("/payments/charges", status_code=202)
    def create_charge(body: ChargeBody, who: Principal = Depends(user)):
        row, _created = payments.create_charge(who, str(body.orderId), body.method)
        return present(row)

    # Registered before /payments/{id} so "webhook" is never read as an id.
    @app.post("/payments/webhook", status_code=204)
    async def webhook(request: Request, x_provider_signature: str = Header(default="")):
        raw = await request.body()
        given = x_provider_signature.removeprefix("sha256=")
        if not given or not hmac.compare_digest(sign(cfg.webhook_secret, raw), given):
            raise HTTPException(401, {"error": "bad_signature"})
        try:
            body = WebhookBody(**json.loads(raw))
        except (ValueError, TypeError):
            raise HTTPException(400, {"error": "invalid_request"})
        payments.handle_webhook(str(body.paymentId), body.outcome, body.reason)
        return Response(status_code=204)

    @app.get("/payments/{payment_id}")
    def get_payment(payment_id: UUID, who: Principal = Depends(user)):
        return present(payments.get_payment(who, str(payment_id)))

    @app.post("/payments/{payment_id}/refunds", status_code=201)
    def create_refund(payment_id: UUID, body: RefundBody, _who: Principal = Depends(staff)):
        return present(payments.refund(str(payment_id), body.amount))

    return app
