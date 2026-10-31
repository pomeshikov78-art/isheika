"""Аутентификация: регистрация и вход (ТЗ 8, 10.1)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import User
from ..schemas import LoginRequest, RegisterRequest, TokenResponse
from ..security import audit, create_token, current_user, hash_password, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    existing = (
        await session.execute(select(User).where(User.username == payload.username))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Пользователь с таким именем уже существует")

    # Первый зарегистрированный пользователь становится администратором:
    # иначе в свежей установке некому выдавать права.
    is_first = (await session.execute(select(User.id).limit(1))).first() is None

    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role="admin" if is_first else payload.role,
    )
    session.add(user)
    await audit(session, request, "user.register", user, username=user.username, role=user.role)
    await session.commit()

    token, expires_in = create_token(user)
    return TokenResponse(
        access_token=token, expires_in=expires_in, role=user.role, username=user.username
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    user = (
        await session.execute(select(User).where(User.username == payload.username))
    ).scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.password_hash):
        # Одинаковый ответ на «нет пользователя» и «неверный пароль»:
        # разные тексты позволяют перебирать имена.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверное имя пользователя или пароль")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Учётная запись заблокирована")

    await audit(session, request, "user.login", user)
    await session.commit()

    token, expires_in = create_token(user)
    return TokenResponse(
        access_token=token, expires_in=expires_in, role=user.role, username=user.username
    )


@router.get("/me")
async def me(user: User = Depends(current_user)) -> dict:
    return {"id": user.id, "username": user.username, "role": user.role}
