"""Política declarada de cada endpoint: tipo de autenticación y permiso requerido.

Fuente de verdad de las pruebas de seguridad. ``permission=None`` significa "cualquier miembro".
Un endpoint nuevo debe declararse aquí: ``test_every_route_is_classified`` falla si falta o si el
tipo declarado no coincide con las dependencias reales de la ruta.
"""

from dataclasses import dataclass

from sec_routes import Kind

API = "/api/v1"


@dataclass(frozen=True)
class Policy:
    kind: Kind
    permission: str | None = None
    # Endpoints donde el actor puede afectar su propio recurso sin permiso (p. ej. salir de la org).
    self_service: bool = False


def _p(path: str) -> str:
    return f"{API}{path}"


POLICY: dict[tuple[str, str], Policy] = {
    # --- públicos ---------------------------------------------------------------------------
    ("GET", "/healthz"): Policy("public"),
    ("GET", "/readyz"): Policy("public"),
    ("POST", _p("/auth/register")): Policy("public"),
    ("POST", _p("/auth/email/verify")): Policy("public"),
    ("POST", _p("/auth/login")): Policy("public"),
    ("POST", _p("/auth/mfa/verify")): Policy("public"),
    ("POST", _p("/auth/refresh")): Policy("public"),
    ("POST", _p("/auth/logout")): Policy("public"),
    ("POST", _p("/auth/password/forgot")): Policy("public"),
    ("POST", _p("/auth/password/reset")): Policy("public"),
    ("GET", _p("/auth/google/start")): Policy("public"),
    ("GET", _p("/auth/google/callback")): Policy("public"),
    # --- usuario autenticado (sin organización) ---------------------------------------------
    ("POST", _p("/auth/mfa/totp/enroll")): Policy("user"),
    ("POST", _p("/auth/mfa/totp/confirm")): Policy("user"),
    ("POST", _p("/auth/mfa/totp/disable")): Policy("user"),
    ("GET", _p("/me")): Policy("user"),
    ("GET", _p("/me/sessions")): Policy("user"),
    ("DELETE", _p("/me/sessions/{session_id}")): Policy("user"),
    ("GET", _p("/orgs")): Policy("user"),
    ("POST", _p("/orgs")): Policy("user"),
    ("POST", _p("/invitations/{token}/accept")): Policy("user"),
    # --- organización por cabecera X-Org-ID -------------------------------------------------
    ("GET", _p("/members")): Policy("org_header"),
    ("PATCH", _p("/members/{member_id}")): Policy("org_header", "members:manage"),
    ("DELETE", _p("/members/{member_id}")): Policy(
        "org_header", "members:manage", self_service=True
    ),
    ("GET", _p("/invitations")): Policy("org_header", "members:manage"),
    ("POST", _p("/invitations")): Policy("org_header", "members:manage"),
    ("DELETE", _p("/invitations/{invitation_id}")): Policy("org_header", "members:manage"),
    ("GET", _p("/me/api-tokens")): Policy("org_header"),
    ("POST", _p("/me/api-tokens")): Policy("org_header"),
    ("DELETE", _p("/me/api-tokens/{token_id}")): Policy("org_header"),
    # --- organización por ruta --------------------------------------------------------------
    ("GET", _p("/orgs/{org_id}")): Policy("org_path"),
    ("PATCH", _p("/orgs/{org_id}")): Policy("org_path", "org:manage"),
}
