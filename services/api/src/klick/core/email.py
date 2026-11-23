"""Envío de correo: consola en desarrollo, SMTP por configuración."""

import asyncio
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

import structlog

from klick.core.config import Settings


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    body: str


class EmailSender(Protocol):
    async def send(self, message: Email) -> None: ...


class ConsoleEmailSender:
    """Solo desarrollo: imprime el correo (incluye enlaces con token) en la consola del servidor."""

    def __init__(self) -> None:
        self._log = structlog.get_logger("klick.email")

    async def send(self, message: Email) -> None:
        # La clave `email_body` no se redacta a propósito: es el canal de desarrollo del enlace.
        self._log.info(
            "email_console", to=message.to, subject=message.subject, email_body=message.body
        )


class MemoryEmailSender:
    """Para pruebas: guarda los correos enviados."""

    def __init__(self) -> None:
        self.sent: list[Email] = []

    async def send(self, message: Email) -> None:
        self.sent.append(message)


class SmtpEmailSender:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def _send_sync(self, message: Email) -> None:
        msg = EmailMessage()
        msg["From"] = self._s.email_from
        msg["To"] = message.to
        msg["Subject"] = message.subject
        msg.set_content(message.body)
        with smtplib.SMTP(self._s.smtp_host, self._s.smtp_port, timeout=15) as smtp:
            if self._s.smtp_starttls:
                smtp.starttls()
            if self._s.smtp_user:
                smtp.login(self._s.smtp_user, self._s.smtp_password.get_secret_value())
            smtp.send_message(msg)

    async def send(self, message: Email) -> None:
        await asyncio.to_thread(self._send_sync, message)


def build_email_sender(settings: Settings) -> EmailSender:
    if settings.email_backend == "smtp":
        if not settings.smtp_host:
            raise RuntimeError("SMTP_HOST es obligatorio con EMAIL_BACKEND=smtp")
        return SmtpEmailSender(settings)
    if settings.is_production:
        raise RuntimeError("EMAIL_BACKEND=console no está permitido en producción")
    return ConsoleEmailSender()
