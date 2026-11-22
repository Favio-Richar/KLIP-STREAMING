import pathlib
import re

src = pathlib.Path("docs/10-roadmap-ejecucion.md").read_text(encoding="utf-8")
out = [
    "# Progreso",
    "",
    "Generado desde `docs/10-roadmap-ejecucion.md`. Claude Code marca `[x]` y anota fecha y resultado al terminar cada tarea.",
    "Formato: `- [ ] ID título` → `- [x] ID título — AAAA-MM-DD — resultado`",
    "",
]
phase = None
for line in src.splitlines():
    m = re.match(r"^## (F\d) — (.+)", line)
    if m:
        out += ["", f"## {m.group(1)} — {m.group(2)}", ""]
        continue
    t = re.match(r"^\*\*(F\d-\d+)\*\* (.+?) — ", line)
    if t:
        out.append(f"- [ ] {t.group(1)} {t.group(2)}")
    a = re.match(r"^\*\*Aceptación (F\d):\*\*", line)
    if a:
        out.append(f"- [ ] {a.group(1)}-ACEPTACION prueba de aceptación de la fase")
pathlib.Path("docs/PROGRESS.md").write_text("\n".join(out) + "\n", encoding="utf-8")
print(len([l for l in out if l.startswith("- [ ]")]), "tareas")
