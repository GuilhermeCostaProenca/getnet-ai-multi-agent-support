"""Caller authentication (JWT) and per-identity rate limiting (SEC-01..04).

The acting user is always the token subject. HS256 keeps the demo self-contained; production would verify
RS256 tokens issued by the company IdP (JWKS) with the same `verify_token` contract (ADR-02).
"""

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

import jwt

from app.config import Settings

ROLES = ("merchant", "operator")


class AuthError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class Principal:
    sub: str
    role: str


def issue_token(settings: Settings, sub: str, role: str, ttl_minutes: int | None = None) -> str:
    if role not in ROLES:
        raise ValueError(f"unknown role {role}")
    now = int(time.time())
    ttl = settings.jwt_ttl_minutes if ttl_minutes is None else ttl_minutes
    claims = {"sub": sub, "role": role, "iss": settings.jwt_issuer, "iat": now, "exp": now + 60 * ttl}
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def verify_token(settings: Settings, token: str) -> Principal:
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], issuer=settings.jwt_issuer,
                            options={"require": ["exp", "iss", "sub"]})
    except jwt.ExpiredSignatureError as exc:
        raise AuthError(401, "token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError(401, "invalid token") from exc
    if claims.get("role") not in ROLES:
        raise AuthError(401, "invalid role claim")
    return Principal(sub=str(claims["sub"]), role=claims["role"])


class RateLimiter:
    """Sliding-window limiter per identity (in-process; production would use Redis)."""

    def __init__(self, limit: int, window_seconds: float = 60.0):
        self._limit = limit
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self._window:
                hits.popleft()
            if len(hits) >= self._limit:
                return False
            hits.append(now)
            return True
