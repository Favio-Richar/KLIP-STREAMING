"""Esquemas de organizaciones, miembros, invitaciones y tokens (extra=forbid)."""

import uuid
import zoneinfo
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
)

RoleKey = Literal["owner", "admin", "editor", "reviewer", "viewer"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrgSettingsIn(StrictModel):
    locale: Literal["es-CL", "en"] | None = None
    timezone: Annotated[str, Field(max_length=64)] | None = None

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str | None) -> str | None:
        if v is not None and v not in zoneinfo.available_timezones():
            raise ValueError("zona horaria desconocida")
        return v


class OrgCreate(StrictModel):
    name: Name
    slug: (
        Annotated[str, StringConstraints(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")] | None
    ) = None


class OrgUpdate(StrictModel):
    name: Name | None = None
    settings: OrgSettingsIn | None = None


class OrgOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    role: str
    permissions: list[str]
    settings: dict[str, object]
    created_at: datetime


class MemberOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    display_name: str
    role: str
    status: str
    joined_at: datetime


class MemberUpdate(StrictModel):
    role: RoleKey


class InvitationCreate(StrictModel):
    email: EmailStr
    role: RoleKey

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower()


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    expires_at: datetime
    created_at: datetime


class TokenCreate(StrictModel):
    name: Name
    scopes: Annotated[list[str], Field(min_length=1, max_length=50)]
    expires_in_days: Annotated[int, Field(ge=1, le=365)] | None = 90


class TokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None


class TokenCreated(TokenOut):
    token: str  # se muestra una sola vez
