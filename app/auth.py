from dataclasses import dataclass

import jwt
from fastapi import Header, HTTPException

from .config import Config

STAFF_ROLES = ("staff", "admin")


@dataclass(frozen=True)
class Principal:
    sub: str
    role: str
    tenant: str
    token: str

    @property
    def is_staff(self) -> bool:
        return self.role in STAFF_ROLES


def authenticate(cfg: Config):
    """Dependency: reads the iam JWT (claims `sub`, `role`, `tenant`, shared `JWT_SECRET`)."""

    def dep(authorization: str = Header(default="")) -> Principal:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(401, {"error": "unauthorized"})
        try:
            claims = jwt.decode(token, cfg.jwt_secret, algorithms=["HS256"])
        except jwt.PyJWTError:
            raise HTTPException(401, {"error": "unauthorized"})
        if not isinstance(claims.get("sub"), str):
            raise HTTPException(401, {"error": "unauthorized"})
        return Principal(claims["sub"], str(claims.get("role", "customer")), str(claims.get("tenant", "default")), token)

    return dep

