/* DEVIN AI IDE service worker (2026-07-18, PWA slice).
 *
 * Policy:
 * - Shell-only precache: la route HTML /app + asset statici della SPA +
 *   manifest + icone, in una cache versionata (CACHE_VERSION).
 * - NETWORK-ONLY per TUTTE le chiamate /api/*: contenuti di memoria/chat
 *   non devono MAI finire in cache (requisito di privacy, non preferenza).
 * - Network-first per pagina e asset di shell, con fallback offline.
 * - Activate: pulizia delle cache di versioni precedenti.
 *
 * Convenzione deploy: incrementare CACHE_VERSION ad ogni modifica della
 * shell (HTML/CSS/JS/icone), altrimenti i client continuano a servire la
 * versione vecchia dalla cache. Vedi docs/CONTINUITY_2026-07-18.md.
 */
const CACHE_VERSION = "devin-shell-v4";
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
      // Una shell desktop puo' restare aperta per giorni. Dopo l'attivazione
      // forza una sola navigazione dei client /app: il nuovo worker prende il
      // controllo subito e non lascia il vecchio JavaScript visibile fino al
      // secondo riavvio manuale.
      .then(() => self.clients.matchAll({ type: "window" }))
      .then((clients) =>
        Promise.all(
          clients.map((client) => {
            const url = new URL(client.url);
            return url.pathname === "/app" ? client.navigate(client.url) : null;
          })
        )
      )
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

  const isShellRequest =
    request.mode === "navigate" || SHELL_URLS.includes(url.pathname);
  if (!isShellRequest) return;

  // Shell network-first: un update appena installato non puo' restare
  // intrappolato nella vecchia cache. La cache serve soltanto da fallback
  // offline e viene aggiornata dopo ogni risposta valida del backend.
  event.respondWith(
    fetch(request, { cache: "no-store" })
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE_VERSION).then((cache) => cache.put(request, copy));
        }
        return response;
      })
      .catch(() =>
        caches.match(request).then((cached) => {
          if (cached) return cached;
          return caches.match(url.pathname, { ignoreSearch: true });
        })
      )
  );
});
