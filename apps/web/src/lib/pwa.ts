/** Registra el service worker (solo en producción: en desarrollo estorba al recargar en caliente). */
export function registerServiceWorker(): void {
  if (typeof window === "undefined" || !("serviceWorker" in navigator)) return;
  if (process.env.NODE_ENV !== "production") {
    // Si quedó uno de una compilación anterior, se retira para no servir pantallas viejas.
    void navigator.serviceWorker
      .getRegistrations()
      .then((rs) => rs.forEach((r) => void r.unregister()));
    return;
  }
  window.addEventListener(
    "load",
    () => void navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => undefined),
    { once: true },
  );
}
