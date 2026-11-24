//! Atajos globales (funcionan aunque Klick no tenga el foco).
//!
//! Estructura lista para F4 (p. ej. `Ctrl+Alt+K` = "clip ahora" durante un directo). Hoy la lista
//! está vacía a propósito: no se registra ningún atajo hasta que exista la acción que dispara.
//! Al pulsarse un atajo solo se emite un evento con el identificador de la acción a la ventana
//! principal; la lógica de negocio vive en la aplicación web y en la API, no aquí.

use tauri::{AppHandle, Emitter};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

/// (combinación, identificador de acción). Se llena cuando exista la acción correspondiente.
pub const DEFAULT_SHORTCUTS: &[(&str, &str)] = &[];

pub const SHORTCUT_EVENT: &str = "klick:shortcut";

/// Interpreta una combinación como `Ctrl+Alt+K`.
pub fn parse(accelerator: &str) -> Result<Shortcut, String> {
    accelerator
        .parse::<Shortcut>()
        .map_err(|e| format!("atajo inválido «{accelerator}»: {e}"))
}

/// Registra las combinaciones indicadas. Falla si alguna es inválida, sin registrar a medias.
pub fn register_all(app: &AppHandle, list: &[(&str, &str)]) -> Result<(), String> {
    let parsed: Vec<(Shortcut, String)> = list
        .iter()
        .map(|(accel, action)| parse(accel).map(|s| (s, (*action).to_string())))
        .collect::<Result<_, _>>()?;
    for (shortcut, action) in parsed {
        let handle = app.clone();
        app.global_shortcut()
            .on_shortcut(shortcut, move |_app, _shortcut, event| {
                if event.state() == ShortcutState::Pressed {
                    let _ = handle.emit_to("main", SHORTCUT_EVENT, action.clone());
                }
            })
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_valid_accelerators_and_rejects_garbage() {
        assert!(parse("Ctrl+Alt+K").is_ok());
        assert!(parse("CommandOrControl+Shift+F9").is_ok());
        assert!(parse("").is_err());
        assert!(parse("Ctrl+Alt+").is_err());
        assert!(parse("no es un atajo").is_err());
    }

    #[test]
    fn default_shortcuts_are_all_valid_and_unique() {
        let mut seen = std::collections::HashSet::new();
        for (accel, action) in DEFAULT_SHORTCUTS {
            assert!(parse(accel).is_ok(), "{accel}");
            assert!(!action.is_empty());
            assert!(
                seen.insert(parse(accel).unwrap().id()),
                "duplicado: {accel}"
            );
        }
    }
}
