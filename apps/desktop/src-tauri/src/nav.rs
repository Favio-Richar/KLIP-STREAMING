//! Reglas de navegación y enlaces profundos. Lógica pura (sin Tauri) para poder probarla.
//!
//! Todo lo que llega de fuera es entrada no confiable: la configuración del entorno, las
//! redirecciones de la página y los enlaces `klick://` que cualquier sitio o documento puede abrir.

use tauri::Url;

pub const DEFAULT_APP_URL: &str = "http://localhost:3000";

/// Orígenes externos a los que la ventana puede ir además de la app: solo el acceso con Google.
pub const AUTH_HOSTS: &[&str] = &["accounts.google.com"];

/// Primer segmento permitido en `klick://<destino>/...` y la ruta de la app a la que corresponde.
const DESTINATIONS: &[(&str, &str)] = &[
    ("panel", "/"),
    ("library", "/library"),
    ("clips", "/clips"),
    ("settings", "/settings"),
];
const SETTINGS_PAGES: &[&str] = &["profile", "members"];
const MAX_SEGMENTS: usize = 4;
const MAX_SEGMENT_LEN: usize = 64;

fn is_loopback(url: &Url) -> bool {
    matches!(url.host_str(), Some("localhost" | "127.0.0.1" | "[::1]"))
}

/// URL de la aplicación web que carga la ventana. Solo `https`, o `http` en la propia máquina
/// (desarrollo); sin credenciales incrustadas. Prioridad: variable en ejecución, valor de
/// compilación, valor por defecto.
pub fn resolve_app_url(runtime: Option<&str>, compiled: Option<&str>) -> Result<Url, String> {
    let raw = runtime
        .filter(|s| !s.trim().is_empty())
        .or(compiled.filter(|s| !s.trim().is_empty()))
        .unwrap_or(DEFAULT_APP_URL);
    let url =
        Url::parse(raw.trim()).map_err(|e| format!("KLICK_APP_URL no es una URL válida: {e}"))?;
    match url.scheme() {
        "https" => {}
        "http" if is_loopback(&url) => {}
        other => return Err(format!("esquema no permitido para la aplicación: {other}")),
    }
    if url.host_str().is_none() {
        return Err("la URL de la aplicación no tiene host".into());
    }
    if !url.username().is_empty() || url.password().is_some() {
        return Err("la URL de la aplicación no puede incluir credenciales".into());
    }
    Ok(url)
}

fn same_origin(a: &Url, b: &Url) -> bool {
    a.scheme() == b.scheme()
        && a.host_str() == b.host_str()
        && a.port_or_known_default() == b.port_or_known_default()
}

/// ¿Puede la ventana navegar a `target`? Solo el origen de la app y el acceso con Google.
pub fn is_allowed_navigation(app: &Url, target: &Url) -> bool {
    if target.as_str() == "about:blank" {
        return true;
    }
    same_origin(app, target)
        || (target.scheme() == "https"
            && target.host_str().is_some_and(|h| AUTH_HOSTS.contains(&h)))
}

fn valid_segment(seg: &str) -> bool {
    let mut chars = seg.chars();
    seg.len() <= MAX_SEGMENT_LEN
        && chars
            .next()
            .is_some_and(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
        && chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-' || c == '_')
}

/// Traduce un enlace `klick://...` a una ruta interna de la app, o `None` si no es válido.
///
/// `klick://library` → `/library`, `klick://settings/profile` → `/settings/profile`.
/// Se rechaza todo lo que no esté en la lista: otros esquemas, destinos desconocidos, consultas,
/// fragmentos, credenciales, puertos, mayúsculas, `..`, segmentos vacíos o demasiado largos.
pub fn deep_link_path(url: &Url) -> Option<String> {
    if url.scheme() != "klick"
        || url.query().is_some()
        || url.fragment().is_some()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.port().is_some()
    {
        return None;
    }
    let mut segments: Vec<&str> = vec![url.host_str()?];
    if let Some(rest) = url.path_segments() {
        let rest: Vec<&str> = rest.collect();
        for (i, seg) in rest.iter().enumerate() {
            if seg.is_empty() {
                // una barra final (`klick://library/`) es válida; una vacía en medio no
                if i + 1 == rest.len() {
                    continue;
                }
                return None;
            }
            segments.push(seg);
        }
    }
    if segments.len() > MAX_SEGMENTS || !segments.iter().all(|s| valid_segment(s)) {
        return None;
    }
    let (_, base) = DESTINATIONS.iter().find(|(name, _)| *name == segments[0])?;
    let rest = &segments[1..];
    match segments[0] {
        "panel" if !rest.is_empty() => None,
        "settings" if rest.len() > 1 || rest.iter().any(|s| !SETTINGS_PAGES.contains(s)) => None,
        _ => {
            let mut path = (*base).to_string();
            for seg in rest {
                if !path.ends_with('/') {
                    path.push('/');
                }
                path.push_str(seg);
            }
            Some(path)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn u(s: &str) -> Url {
        Url::parse(s).unwrap()
    }

    #[test]
    fn app_url_defaults_and_priority() {
        assert_eq!(
            resolve_app_url(None, None).unwrap().as_str(),
            "http://localhost:3000/"
        );
        assert_eq!(
            resolve_app_url(Some("https://app.klick.test"), Some("http://localhost:1"))
                .unwrap()
                .as_str(),
            "https://app.klick.test/"
        );
        assert_eq!(
            resolve_app_url(Some("  "), Some("https://compilada.klick.test"))
                .unwrap()
                .host_str(),
            Some("compilada.klick.test")
        );
    }

    #[test]
    fn app_url_rejects_unsafe_values() {
        for bad in [
            "http://app.klick.test",    // http fuera de la máquina local
            "http://192.168.1.10:3000", // red local no es "esta máquina"
            "ftp://localhost",
            "file:///C:/secreto",
            "javascript:alert(1)",
            "https://user:pass@app.klick.test", // credenciales incrustadas
            "no es una url",
        ] {
            assert!(resolve_app_url(Some(bad), None).is_err(), "{bad}");
        }
        assert!(resolve_app_url(Some("http://127.0.0.1:3100"), None).is_ok());
        assert!(resolve_app_url(Some("http://[::1]:3000"), None).is_ok());
    }

    #[test]
    fn navigation_is_limited_to_the_app_and_google_sign_in() {
        let app = u("https://app.klick.test");
        assert!(is_allowed_navigation(
            &app,
            &u("https://app.klick.test/library")
        ));
        assert!(is_allowed_navigation(
            &app,
            &u("https://app.klick.test:443/x")
        ));
        assert!(is_allowed_navigation(
            &app,
            &u("https://accounts.google.com/o/oauth2/v2/auth")
        ));
        assert!(is_allowed_navigation(&app, &u("about:blank")));
        for bad in [
            "https://evil.test/",
            "https://app.klick.test.evil.test/", // subdominio engañoso
            "http://app.klick.test/",            // esquema distinto
            "https://app.klick.test:8443/",      // puerto distinto
            "http://accounts.google.com/",       // Google solo por https
            "https://accounts.google.com.evil.test/",
            "file:///C:/Windows/win.ini",
            "javascript:alert(1)",
            "data:text/html,<script>1</script>",
        ] {
            assert!(!is_allowed_navigation(&app, &u(bad)), "{bad}");
        }
    }

    #[test]
    fn deep_links_map_to_known_routes() {
        let cases = [
            ("klick://panel", "/"),
            ("klick://library", "/library"),
            ("klick://library/", "/library"),
            // el parser resuelve `..` (también codificado): nunca sale de /library
            ("klick://library/..", "/library"),
            ("klick://library/%2e%2e", "/library"),
            ("klick://library/0190a1b2-c3d4", "/library/0190a1b2-c3d4"),
            ("klick://clips/abc123", "/clips/abc123"),
            ("klick://settings", "/settings"),
            ("klick://settings/profile", "/settings/profile"),
            ("klick://settings/members", "/settings/members"),
        ];
        for (link, path) in cases {
            assert_eq!(deep_link_path(&u(link)).as_deref(), Some(path), "{link}");
        }
    }

    #[test]
    fn deep_links_reject_everything_else() {
        for bad in [
            "https://library",
            "klick://",
            "klick://admin",
            "klick://panel/algo",
            "klick://settings/billing",
            "klick://settings/profile/extra",
            "klick://library?next=https://evil.test",
            "klick://library#frag",
            "klick://user@library",
            "klick://library:8080",
            "klick://Library",
            "klick://library//x",
            "klick://library/a/b/c/d",
            "klick://library/con espacio",
        ] {
            let parsed = Url::parse(bad);
            assert!(
                parsed.is_err() || deep_link_path(&parsed.unwrap()).is_none(),
                "{bad}"
            );
        }
        let long = format!("klick://library/{}", "a".repeat(65));
        assert!(deep_link_path(&u(&long)).is_none());
    }
}
