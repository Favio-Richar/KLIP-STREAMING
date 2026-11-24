/* Service worker de Klick.
 *
 * Alcance deliberadamente pequeño: hace la app instalable y deja una pantalla sin conexión.
 * NUNCA guarda respuestas de la API ni documentos HTML autenticados: el estado vive en el
 * servidor y no debe quedar servido desde una caché local. Solo activos estáticos inmutables
 * (/_next/static) y la pantalla de respaldo.
 */
const VERSION = "klick-v1";
const STATIC_CACHE = `${VERSION}-static`;
const OFFLINE_URL = "/offline.html";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(STATIC_CACHE)
      .then((cache) => cache.add(new Request(OFFLINE_URL, { cache: "reload" })))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k))),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  // La API y el SSE pasan siempre directo a la red.
  if (url.pathname.startsWith("/api/")) return;

  // Activos con hash en el nombre: inmutables, "caché primero".
  if (url.pathname.startsWith("/_next/static/")) {
    event.respondWith(
      caches.open(STATIC_CACHE).then(async (cache) => {
        const hit = await cache.match(req);
        if (hit) return hit;
        const res = await fetch(req);
        if (res.ok) cache.put(req, res.clone());
        return res;
      }),
    );
    return;
  }

  // Navegaciones: siempre red; si no hay conexión, la pantalla de respaldo.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(() => caches.match(OFFLINE_URL).then((r) => r || Response.error())),
    );
  }
});
