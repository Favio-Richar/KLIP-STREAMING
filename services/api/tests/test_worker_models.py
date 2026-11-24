import subprocess
import sys

import pytest

SCRIPT = """
import os
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
import klick.worker_main  # el punto de entrada real de los workers
from klick.core.models import Base
from sqlalchemy.orm import configure_mappers

configure_mappers()
tables = {t.name for t in Base.metadata.sorted_tables}  # lanza si una clave foránea no resuelve
print(sorted(tables))
"""


@pytest.mark.parametrize("module", ["klick.worker_main"])
def test_a_fresh_worker_process_can_resolve_every_foreign_key(module: str) -> None:
    """Regresión: el worker solo importaba parte de los modelos y fallaba con
    NoReferencedTableError al procesar media_assets (users no estaba cargada)."""
    out = subprocess.run(  # noqa: S603 - código fijo, sin entrada externa
        [sys.executable, "-I", "-c", SCRIPT], capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, out.stderr[-1500:]
    for table in ("users", "organizations", "media_assets", "storage_objects", "jobs"):
        assert f"'{table}'" in out.stdout
