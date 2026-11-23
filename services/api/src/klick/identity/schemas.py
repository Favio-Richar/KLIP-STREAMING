"""Esquemas de entrada y salida de identidad (extra=forbid, docs/09)."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

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
    mfa_required: Literal[False] = False
    user: UserOut
    csrf_token: str


class MfaRequiredResponse(BaseModel):
    mfa_required: Literal[True] = True
    mfa_token: str


Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=6, max_length=32)]


class MfaVerifyRequest(StrictModel):
    mfa_token: Token | None = None  # también puede venir en la cookie (flujo Google)
    code: Code | None = None
    recovery_code: Code | None = None

    @model_validator(mode="after")
    def _one_factor(self) -> "MfaVerifyRequest":
        if (self.code is None) == (self.recovery_code is None):
            raise ValueError("envía `code` o `recovery_code`, no ambos")
        return self


class MfaEnrollResponse(BaseModel):
    secret: str
    otpauth_uri: str


class MfaConfirmRequest(StrictModel):
    code: Code


class MfaConfirmResponse(BaseModel):
    recovery_codes: list[str]


class MfaDisableRequest(StrictModel):
    password: Annotated[str, Field(max_length=PASSWORD_MAX)] | None = None
    code: Code
