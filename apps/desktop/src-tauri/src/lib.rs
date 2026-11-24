//! Cascarón de escritorio de Klick (Tauri 2).
//!
//! No tiene lógica de negocio: abre la aplicación web (local o alojada) en una ventana y suma lo
//! que un navegador no puede: bandeja del sistema, selector de archivos nativo, enlaces `klick://`
//! y atajos globales. Superficie mínima por diseño:
//! - la ventana solo navega al origen de la app y al acceso con Google (`nav`);
//! - la página remota solo puede invocar el selector de archivos (`capabilities/default.json`);
//! - los enlaces `klick://` se validan contra una lista antes de tocar la ventana.

mod nav;
mod shortcuts;
mod tray;

use tauri::{Manager, Url, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_deep_link::DeepLinkExt;

/// Lleva la ventana principal a una ruta de la app. `path` ya fue validado por `nav`.
fn open_route(app: &tauri::AppHandle, app_url: &Url, path: &str) {
    let Ok(target) = app_url.join(path) else {
        return;
    };
    if let Some(window) = app.get_webview_window("main") {
        // `target` sale de `nav::deep_link_path` (solo [a-z0-9_-] y `/`) y se serializa como
        // literal JSON: no hay forma de inyectar código.
        let script = format!(
            "window.location.assign({})",
            serde_json::to_string(target.as_str()).unwrap_or_else(|_| "\"/\"".into())
        );
        let _ = window.eval(&script);
    }
    tray::show_main(app);
}

pub fn run() {
    let app_url = match nav::resolve_app_url(
        std::env::var("KLICK_APP_URL").ok().as_deref(),
        option_env!("KLICK_APP_URL"),
    ) {
        Ok(url) => url,
        Err(message) => {
            eprintln!("Klick: {message}");
            std::process::exit(2);
        }
    };

    tauri::Builder::default()
        // Una sola instancia: abrir un enlace klick:// con la app abierta reutiliza la ventana.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            tray::show_main(app);
        }))
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .setup(move |app| {
            let guard_url = app_url.clone();
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(app_url.clone()))
                .title("Klick")
                .inner_size(1280.0, 800.0)
                .min_inner_size(960.0, 600.0)
                .on_navigation(move |target| nav::is_allowed_navigation(&guard_url, target))
                .build()?;

            tray::build(app.handle())?;
            shortcuts::register_all(app.handle(), shortcuts::DEFAULT_SHORTCUTS)?;

            // En desarrollo el esquema klick:// se registra en tiempo de ejecución.
            #[cfg(any(windows, target_os = "linux"))]
            app.deep_link().register_all()?;

            let handle = app.handle().clone();
            let link_base = app_url.clone();
            app.deep_link().on_open_url(move |event| {
                for url in event.urls() {
                    if let Some(path) = nav::deep_link_path(&url) {
                        open_route(&handle, &link_base, &path);
                    }
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            // Con la bandeja activa, cerrar la ventana la oculta en vez de terminar la app.
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error al iniciar Klick");
}
