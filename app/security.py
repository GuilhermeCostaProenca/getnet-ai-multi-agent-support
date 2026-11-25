"""JWT identity, roles and per-identity rate limiting (SEC-01..03, ADR-05)."""

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

import jwt

from app.config import Settings


class AuthError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class Principal:
    sub: str
    role: str


def issue_token(settings: Settings, sub: str, role: str = "merchant", ttl_minutes: int | None = None) -> str:
    now = int(time.time())
    claims = {"sub": sub, "role": role, "iss": settings.jwt_issuer, "iat": now,
              "exp": now + 60 * (ttl_minutes or settings.jwt_ttl_minutes)}
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def verify_token(settings: Settings, token: str) -> Principal:
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], issuer=settings.jwt_issuer,
                            options={"require": ["exp", "iss", "sub"]})
    except jwt.ExpiredSignatureError as exc:
        raise AuthError(401, "token expired") from exc
    except jwt.PyJWTError as exc:
        raise AuthError(401, "invalid token") from exc
    role = claims.get("role", "merchant")
    if role not in ("merchant", "operator"):
        raise AuthError(401, "invalid role")
    return Principal(sub=str(claims["sub"]), role=role)


class RateLimiter:
    """Sliding one-minute window per identity."""

    def __init__(self, per_minute: int):
        self._limit = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self._limit:
                return False
            hits.append(now)
            return True
