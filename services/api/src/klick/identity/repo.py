"""Acceso a datos de identidad. Solo lo usa identity.service."""

import uuid
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from klick.identity.models import (
    AuthToken,
    MfaFactor,
    MfaRecoveryCode,
    Session,
    User,
    UserIdentity,
)


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


# ----------------------------------------------------------------------------- MFA


async def get_totp_factor(
    db: AsyncSession, user_id: uuid.UUID, *, confirmed: bool | None = None, for_update: bool = False
) -> MfaFactor | None:
    stmt = select(MfaFactor).where(MfaFactor.user_id == user_id, MfaFactor.type == "totp")
    if confirmed is True:
        stmt = stmt.where(MfaFactor.confirmed_at.is_not(None))
    elif confirmed is False:
        stmt = stmt.where(MfaFactor.confirmed_at.is_(None))
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt.limit(1))).scalar_one_or_none()


async def add_factor(db: AsyncSession, factor: MfaFactor) -> MfaFactor:
    db.add(factor)
    await db.flush()
    return factor


async def delete_unconfirmed_factors(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(
        delete(MfaFactor).where(MfaFactor.user_id == user_id, MfaFactor.confirmed_at.is_(None))
    )


async def delete_mfa(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(delete(MfaFactor).where(MfaFactor.user_id == user_id))
    await db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id))


async def replace_recovery_codes(
    db: AsyncSession, user_id: uuid.UUID, rows: list[MfaRecoveryCode]
) -> None:
    await db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id))
    db.add_all(rows)
    await db.flush()


async def consume_recovery_code(
    db: AsyncSession, user_id: uuid.UUID, code_hash: str, now: datetime
) -> bool:
    """Marca el código como usado de forma atómica; False si no existe o ya se usó."""
    result = await db.execute(
        update(MfaRecoveryCode)
        .where(
            MfaRecoveryCode.user_id == user_id,
            MfaRecoveryCode.code_hash == code_hash,
            MfaRecoveryCode.used_at.is_(None),
        )
        .values(used_at=now)
        .returning(MfaRecoveryCode.id)
    )
    return result.first() is not None


async def count_unused_recovery_codes(db: AsyncSession, user_id: uuid.UUID) -> int:
    rows = await db.execute(
        select(MfaRecoveryCode.id).where(
            MfaRecoveryCode.user_id == user_id, MfaRecoveryCode.used_at.is_(None)
        )
    )
    return len(rows.all())


# ------------------------------------------------------------------------ identidades


async def get_identity(db: AsyncSession, provider: str, subject: str) -> UserIdentity | None:
    return (
        await db.execute(
            select(UserIdentity).where(
                UserIdentity.provider == provider, UserIdentity.subject == subject
            )
        )
    ).scalar_one_or_none()


async def add_identity(db: AsyncSession, identity: UserIdentity) -> UserIdentity:
    db.add(identity)
    await db.flush()
    return identity
