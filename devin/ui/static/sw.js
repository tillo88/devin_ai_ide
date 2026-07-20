/* DEVIN AI IDE service worker (2026-07-18, PWA slice).
 *
 * Policy:
 * - Shell-only precache: la route HTML /app + asset statici della SPA +
 *   manifest + icone, in una cache versionata (CACHE_VERSION).
 * - NETWORK-ONLY per TUTTE le chiamate /api/*: contenuti di memoria/chat
 *   non devono MAI finire in cache (requisito di privacy, non preferenza).
 * - Cache-first per gli asset di shell, con fallback di rete.
 * - Activate: pulizia delle cache di versioni precedenti.
 *
 * Convenzione deploy: incrementare CACHE_VERSION ad ogni modifica della
 * shell (HTML/CSS/JS/icone), altrimenti i client continuano a servire la
 * versione vecchia dalla cache. Vedi docs/CONTINUITY_2026-07-18.md.
 */
const CACHE_VERSION = "devin-shell-v2";
const SHELL_URLS = [
  "/app",
  "/manifest.webmanifest",
  "/static/css/codex_app.css",
  "/static/js/codex_app.js",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_VERSION)
      .then((cache) => cache.addAll(SHELL_URLS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(
          names
            .filter((name) => name !== CACHE_VERSION)
            .map((name) => caches.delete(name))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;

  // Solo GET: tutto il resto va in rete senza toccare la cache.
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Privacy: TUTTE le API (memoria/chat/contenuti) sono network-only.
  // Mai caches.match, mai cache.put su /api/*.
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(fetch(request));
    return;
  }

  // Asset di shell: cache-first, poi rete (e ripopola la cache).
  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) return cached;
      return fetch(request).then((response) => {
        if (response.ok && SHELL_URLS.includes(url.pathname)) {
          const copy = response.clone();
          caches.open(CACHE_VERSION).then((cache) => cache.put(request, copy));
        }
        return response;
      });
    })
  );
});
