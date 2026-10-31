"""Аутентификация, RBAC и rate limiting (ТЗ 8)."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import get_session
from .models import AuditLog, User

bearer = HTTPBearer(auto_error=False)

ROLE_LEVEL = {"guest": 0, "analyst": 1, "admin": 2}


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_token(user: User) -> tuple[str, int]:
    expires_in = settings.jwt_ttl_hours * 3600
    payload = {
        "sub": user.id,
        "username": user.username,
        "role": user.role,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=settings.jwt_ttl_hours),
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token, expires_in


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется авторизация")

    try:
        payload = jwt.decode(
            credentials.credentials, settings.secret_key, algorithms=[settings.jwt_algorithm]
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Срок действия токена истёк")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Некорректный токен")

    user = await session.get(User, payload.get("sub"))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Пользователь не найден или заблокирован")
    return user


def require_role(minimum: str):
    """Зависимость RBAC: admin > analyst > guest (ТЗ 8)."""

    async def dependency(user: User = Depends(current_user)) -> User:
        if ROLE_LEVEL.get(user.role, 0) < ROLE_LEVEL[minimum]:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Недостаточно прав: требуется роль не ниже «{minimum}»",
            )
        return user

    return dependency


class RateLimiter:
    """Скользящее окно в памяти. Для нескольких реплик заменяется на Redis,
    интерфейс при этом не меняется."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> bool:
        now = time.monotonic()
        window = self._hits[key]
        while window and now - window[0] > 60.0:
            window.popleft()
        if len(window) >= self.per_minute:
            return False
        window.append(now)
        return True


limiter = RateLimiter(settings.rate_limit_per_minute)


async def audit(
    session: AsyncSession,
    request: Request,
    action: str,
    user: User | None = None,
    **details,
) -> None:
    """Журнал действий (ТЗ 8: хранение не менее 90 дней)."""
    session.add(
        AuditLog(
            user_id=user.id if user else None,
            action=action[:64],
            details=details,
            ip_address=request.client.host if request.client else None,
        )
    )
