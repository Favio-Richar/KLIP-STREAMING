"""Vuelca el contrato OpenAPI de la API a `openapi.json` (fuente del cliente tipado)."""

import json
from pathlib import Path

from klick.main import create_app

OUT = Path(__file__).resolve().parent.parent / "openapi.json"


def main() -> None:
    schema = create_app().openapi()
    OUT.write_text(
        json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"{len(schema['paths'])} rutas -> {OUT}")


if __name__ == "__main__":
    main()
