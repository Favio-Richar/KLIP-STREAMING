# Entorno local (Windows 11)

Ruta del proyecto: `C:\Users\favio\KLICK-STREAMING`. Todo lo siguiente lo ejecuta Claude Code en F0 cuando sea posible; lo que requiere permisos de administrador o reinicio queda indicado para Favio.

## Requisitos
| Herramienta | Versión mínima | Instalación sugerida |
|---|---|---|
| Git | 2.45 | `winget install Git.Git` |
| Node.js | 22 LTS | `winget install OpenJS.NodeJS.LTS` |
| pnpm | 9 | `corepack enable` |
| Python | 3.12 | `winget install Python.Python.3.12` |
| uv | última | `winget install astral-sh.uv` |
| Docker Desktop | última, con WSL2 | `winget install Docker.DockerDesktop` |
| Controlador NVIDIA | con soporte CUDA para WSL2 | desde el sitio del fabricante |
| Rust (para Tauri) | estable | `winget install Rustlang.Rustup` |
| Herramientas de compilación de Visual Studio (Tauri) | 2022 | requerido por Tauri en Windows |

> **Nota para Claude Code:** instalar software es una acción del usuario. Si falta algo, listar exactamente qué falta, qué comando ejecutar y continuar con las tareas que no lo necesiten.

## Finales de línea
Crear `.gitattributes` con `* text=auto eol=lf` y configurar `git config core.autocrlf false` **antes del primer commit**, para que los scripts `.sh` y los Dockerfiles no se rompan con CRLF dentro de los contenedores.

## Primer arranque
```
copy .env.example .env        # completar valores locales
pnpm install
uv sync
pnpm infra:up                 # postgres, redis, minio, mediamtx
pnpm db:migrate
pnpm db:seed
pnpm api:dev                  # http://localhost:8000
pnpm worker:dev               # workers en Docker
pnpm web:dev                  # http://localhost:3000
```
GPU: `pnpm infra:up -- --profile gpu` levanta `worker-ai`. Verificar con `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi`; si no responde, revisar Docker Desktop → Settings → Resources → WSL integration y el controlador.

## Puertos
| Servicio | Puerto |
|---|---|
| web | 3000 |
| api | 8000 |
| postgres | 5434 |
| redis | 6381 |
| minio (S3 / consola) | 9000 / 9001 |
| mediamtx RTMP / SRT / API | 1935 / 8890 / 9997 |
| grafana (perfil observability) | 3001 |

## Probar un directo sin OBS
```
ffmpeg -re -f lavfi -i testsrc2=size=1280x720:rate=30 -f lavfi -i sine=frequency=440 \
  -c:v libx264 -preset veryfast -g 60 -c:a aac -f flv rtmp://localhost:1935/live/<clave_de_ingesta>
```

## Rendimiento en Windows
- El código de Python que se recarga en caliente corre en contenedor con montaje de la carpeta; si la recarga es lenta, usar `watchfiles` con sondeo.
- `node_modules` y `.venv` se instalan nativos en Windows (no dentro de contenedores) y se excluyen de los montajes.
- Los datos pesados (PostgreSQL, MinIO) usan volúmenes con nombre de Docker, no carpetas de Windows.

## Verificación de entorno limpio (tarea F0-17)
Clonar en una carpeta nueva, seguir esta guía al pie de la letra y confirmar que `pnpm check` pasa. Cualquier paso que falle se corrige aquí.
