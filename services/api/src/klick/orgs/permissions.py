"""Catálogo de permisos y roles de sistema (docs/05 §3). Fuente única para semillas y servicios."""

PERMISSIONS: dict[str, str] = {
    "org:manage": "Administrar la organización",
    "members:manage": "Invitar y administrar miembros y roles",
    "media:read": "Ver biblioteca de medios",
    "media:write": "Subir y editar medios",
    "clips:read": "Ver clips",
    "clips:write": "Crear y editar clips",
    "templates:read": "Ver plantillas y marcas",
    "templates:write": "Crear y editar plantillas y marcas",
    "ai:run": "Ejecutar tareas de IA",
    "ai:manage": "Administrar proveedores y modelos de IA",
    "agent:run": "Usar el agente",
    "approvals:decide": "Aprobar o rechazar acciones del agente",
    "live:read": "Ver directos",
    "live:write": "Controlar directos",
    "connections:manage": "Administrar conexiones sociales",
    "publish:draft": "Crear borradores de publicación",
    "publish:schedule": "Programar y aprobar publicaciones",
    "automations:read": "Ver automatizaciones",
    "automations:run": "Ejecutar automatizaciones",
    "automations:write": "Crear y editar automatizaciones",
    "analytics:read": "Ver analítica",
    "billing:manage": "Administrar facturación",
    "audit:read": "Ver el registro de auditoría",
}

ALL_PERMISSIONS: frozenset[str] = frozenset(PERMISSIONS)

ROLE_NAMES: dict[str, str] = {
    "owner": "Propietario",
    "admin": "Administrador",
    "editor": "Editor",
    "reviewer": "Revisor",
    "viewer": "Lector",
}

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": ALL_PERMISSIONS,
    "admin": ALL_PERMISSIONS - {"billing:manage"},
    "editor": frozenset(
        {
            "media:read",
            "media:write",
            "clips:read",
            "clips:write",
            "templates:read",
            "templates:write",
            "ai:run",
            "agent:run",
            "live:read",
            "live:write",
            "publish:draft",
            "automations:read",
            "automations:run",
            "analytics:read",
        }
    ),
    "reviewer": frozenset(
        {"media:read", "clips:read", "approvals:decide", "publish:schedule", "analytics:read"}
    ),
    "viewer": frozenset({"media:read", "clips:read", "analytics:read"}),
}

ROLE_KEYS: tuple[str, ...] = tuple(ROLE_NAMES)
