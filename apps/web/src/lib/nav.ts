/** Destino posterior al acceso: solo rutas internas (evita redirecciones abiertas). */
export function safeNext(value: string | null | undefined, fallback = "/"): string {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) {
    return fallback;
  }
  try {
    const u = new URL(value, "http://klick.local");
    if (u.origin !== "http://klick.local") return fallback;
    return u.pathname + u.search + u.hash;
  } catch {
    return fallback;
  }
}

/** Genera un slug válido para la API (`^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$`) desde un nombre. */
export function slugify(name: string): string {
  const base = name
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40)
    .replace(/-+$/g, "");
  return base;
}
