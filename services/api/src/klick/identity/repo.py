"""Acceso a datos de identidad. Solo lo usa identity.service."""

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from klick.identity.models import AuthToken, Session, User


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    return (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await db.get(User, user_id)


async def add_user(db: AsyncSession, user: User) -> User:
    db.add(user)
    await db.flush()
    return user


async def add_session(db: AsyncSession, session: Session) -> Session:
    db.add(session)
    await db.flush()
    return session


async def get_session_by_refresh_hash(
    db: AsyncSession, refresh_hash: str, *, for_update: bool = False
) -> Session | None:
    stmt = select(Session).where(Session.refresh_token_hash == refresh_hash)
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_session(db: AsyncSession, session_id: uuid.UUID) -> Session | None:
    return await db.get(Session, session_id)


async def list_active_sessions(
    db: AsyncSession, user_id: uuid.UUID, now: datetime
) -> list[Session]:
    stmt = (
        select(Session)
        .where(Session.user_id == user_id, Session.revoked_at.is_(None), Session.expires_at > now)
        .order_by(Session.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars())


async def revoke_family(db: AsyncSession, family_id: uuid.UUID, now: datetime) -> None:
    await db.execute(
        update(Session)
        .where(Session.family_id == family_id, Session.revoked_at.is_(None))
        .values(revoked_at=now)
    )


async def revoke_all_for_user(db: AsyncSession, user_id: uuid.UUID, now: datetime) -> None:
    await db.execute(
        update(Session)
        .where(Session.user_id == user_id, Session.revoked_at.is_(None))
        .values(revoked_at=now)
    )


async def add_auth_token(db: AsyncSession, token: AuthToken) -> AuthToken:
    db.add(token)
    await db.flush()
    return token


async def get_auth_token(
    db: AsyncSession, token_hash: str, purpose: str, *, for_update: bool = False
) -> AuthToken | None:
    stmt = select(AuthToken).where(AuthToken.token_hash == token_hash, AuthToken.purpose == purpose)
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


async def invalidate_auth_tokens(
    db: AsyncSession, user_id: uuid.UUID, purpose: str, now: datetime
) -> None:
    await db.execute(
        update(AuthToken)
        .where(
            AuthToken.user_id == user_id,
            AuthToken.purpose == purpose,
            AuthToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
