# Escritorio (Tauri 2)

La app de escritorio es un **cascarón**: abre la aplicación web de Klick en una ventana propia y suma
lo que un navegador no puede. No contiene lógica de negocio; toda la autoridad sigue en la API.

## Requisitos (solo para compilar el escritorio)
| Herramienta | Para qué | Cómo |
|---|---|---|
| Rust (estable) | Tauri está escrito en Rust | `rustup` oficial: <https://rustup.rs> (perfil mínimo) y `rustup component add rustfmt clippy` |
| Build Tools de C++ (MSVC + SDK de Windows) | Rust enlaza el `.exe` en Windows | Instalador de Microsoft `vs_BuildTools.exe` con `VC.Tools.x86.x64` y `Windows11SDK` |
| WebView2 | Motor web de la ventana | Ya viene con Windows 11 |

Quien no compila el escritorio no necesita nada de esto: la web, la API y los workers no lo usan.

## Uso
```
pnpm web:dev                      # el escritorio carga la web: debe estar corriendo
pnpm desktop:dev                  # ventana de desarrollo (http://localhost:3000)
pnpm --filter desktop test:rust   # pruebas de la lógica de navegación y enlaces
pnpm --filter desktop lint:rust   # clippy + rustfmt
pnpm --filter desktop build       # instalador de Windows (NSIS)
```
`KLICK_APP_URL` cambia la URL que carga la ventana (en ejecución, o fijada al compilar). Solo se acepta
`https`, o `http` en la propia máquina (`localhost`, `127.0.0.1`, `[::1]`), sin credenciales incrustadas.
Si es inválida, la app termina con un mensaje en lugar de abrir algo distinto.

## Qué hace hoy
- **Ventana** con la aplicación web. Cerrarla la oculta; **bandeja del sistema** con «Abrir Klick» y «Salir».
- **Una sola instancia:** abrir otro acceso o un enlace `klick://` reutiliza la ventana existente.
- **Selector de archivos nativo:** la web puede usar `@tauri-apps/plugin-dialog` (`open`).
- **Enlaces `klick://`:** `klick://library`, `klick://clips/<id>`, `klick://settings/profile`,
  `klick://settings/members`, `klick://panel`.
- **Atajos globales:** la estructura existe (`src/shortcuts.rs`) y la lista está vacía a propósito.
  F4 registrará, por ejemplo, `Ctrl+Alt+K` = «clip ahora»; al pulsarse solo emite el evento
  `klick:shortcut` con el id de la acción.

## Seguridad (por qué es así)
- **La ventana solo navega** al origen de la app y a `accounts.google.com` (acceso con Google). Cualquier otro
  destino se bloquea: una redirección o un enlace malicioso no saca la ventana de Klick.
- **La web remota solo puede invocar el selector de archivos** (`capabilities/default.json`, `dialog:allow-open`),
  y solo desde los orígenes listados en `remote.urls`. No se concede acceso al sistema de archivos, a la shell
  ni a la red del sistema.
- **Los enlaces `klick://` son entrada no confiable** (cualquier sitio o documento puede abrirlos). Se
  traducen a rutas internas contra una lista cerrada; se rechazan consultas, fragmentos, credenciales, puertos,
  mayúsculas, segmentos vacíos o largos y destinos desconocidos. Las pruebas de `src/nav.rs` lo cubren.
- Al abrir una ruta se usa `window.location.assign` con la URL serializada como literal JSON, nunca
  concatenada.

## Al tener dominio de producción (F9)
Añadir el dominio a `capabilities/default.json` → `remote.urls` y fijar `KLICK_APP_URL` al compilar.
Sin esto la versión de producción no podría usar el selector de archivos nativo.

## Actualización automática (estructura, aún inactiva)
La actualización automática exige firmar las versiones. **La clave privada nunca se sube al repositorio.**
Pasos cuando se publique la primera versión:
1. Generar el par de claves: `pnpm --filter desktop tauri signer generate -w <ruta fuera del repo>`.
2. Guardar la clave privada y su contraseña en el gestor de secretos (variables `TAURI_SIGNING_PRIVATE_KEY`
   y `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` solo en CI).
3. Añadir `tauri-plugin-updater` y, en `tauri.conf.json`: `bundle.createUpdaterArtifacts: true` y
   `plugins.updater` con `pubkey` (la pública) y `endpoints` (URL del manifiesto de versiones).
4. Publicar el instalador y su firma `.sig` junto al manifiesto.
Hasta entonces `createUpdaterArtifacts` está en `false` y no hay endpoint configurado.
