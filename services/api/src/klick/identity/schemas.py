"""Esquemas de entrada y salida de identidad (extra=forbid, docs/09)."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

PASSWORD_MIN = 10
PASSWORD_MAX = 128

Password = Annotated[str, Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)]
Token = Annotated[str, StringConstraints(min_length=20, max_length=200)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterRequest(StrictModel):
    email: EmailStr
    password: Password
    display_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
    ]

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower()


class LoginRequest(StrictModel):
    email: EmailStr
    password: Annotated[str, Field(min_length=1, max_length=PASSWORD_MAX)]

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower()


class ForgotPasswordRequest(StrictModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower()


class ResetPasswordRequest(StrictModel):
    token: Token
    new_password: Password


class VerifyEmailRequest(StrictModel):
    token: Token


class AcceptedResponse(BaseModel):
    message: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    locale: str
    timezone: str
    email_verified: bool
    mfa_enabled: bool = False
    created_at: datetime


class SessionOut(BaseModel):
    id: uuid.UUID
    user_agent: str | None
    ip: str | None
    created_at: datetime
    expires_at: datetime
    current: bool


class LoginResponse(BaseModel):
    user: UserOut
    csrf_token: str
