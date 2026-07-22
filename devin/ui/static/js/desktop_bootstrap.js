// Desktop bootstrap (app nativa 2026-07-22).
//
// L'app desktop usa SEMPRE il backend LOCALE sul PC: e' quello che legge i TUOI
// file (un programma vede solo il disco della macchina su cui gira). L'inferenza
// va poi al modello del rig (Ornith su 8080), gestita DENTRO il backend con
// fallback al modello locale. Il backend sul rig (:5000) e' un'altra cosa: la
// web app raggiungibile da fuori per i progetti che stanno sul rig.
//
// Quindi qui: prova il backend locale; se non e' su, avvialo (leggero, niente
// VRAM). La scelta rig-vs-locale riguarda il MODELLO, non il backend, e vive
// nel backend stesso.
//
// Usato SOLO nel bundle desktop (lo inietta scripts/build_frontend_bundle.py).
// La versione web resta servita dal backend, same-origin, senza bootstrap.

const LOCAL_BASE = "http://127.0.0.1:5000";

async function probe(base, ms = 1500) {
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), ms);
    const res = await fetch(base + "/api/health", { signal: ctrl.signal });
    clearTimeout(timer);
    return res.ok;
  } catch (_e) {
    return false;
  }
}

function tauriInvoke(cmd, args) {
  const t = window.__TAURI__;
  if (t && t.core && typeof t.core.invoke === "function") return t.core.invoke(cmd, args);
  if (t && typeof t.invoke === "function") return t.invoke(cmd, args);
  return Promise.reject(new Error("API Tauri non disponibile"));
}

function removeOverlay() {
  const el = document.getElementById("devin-boot-overlay");
  if (el) el.remove();
}

function overlay(innerHtml) {
  removeOverlay();
  const div = document.createElement("div");
  div.id = "devin-boot-overlay";
  div.style.cssText =
    "position:fixed;inset:0;display:flex;align-items:center;justify-content:center;" +
    "background:#0b1220;color:#e5e7eb;font-family:system-ui,sans-serif;z-index:99999;";
  div.innerHTML =
    '<div style="max-width:440px;text-align:center;padding:28px;border:1px solid #1f2a44;border-radius:16px;background:#0f172a;">' +
    innerHtml + "</div>";
  document.body.appendChild(div);
  return div;
}

async function loadApp(base) {
  window.__DEVIN_API_BASE__ = base;
  removeOverlay();
  await import("/static/js/codex_app.js");
}

async function boot() {
  overlay('<div style="font-size:15px;">Connessione al backend DEVIN…</div>');
  if (await probe(LOCAL_BASE)) { await loadApp(LOCAL_BASE); return; }

  // Backend locale non attivo: avvialo (e' leggero, legge i file; il modello
  // locale parte solo come fallback se il rig e' giu').
  overlay('<div style="font-size:15px;">Avvio del backend locale… (qualche secondo)</div>');
  try {
    const base = await tauriInvoke("start_local_backend");
    await loadApp(base || LOCAL_BASE);
  } catch (err) {
    overlay(
      '<div style="font-size:15px;margin-bottom:8px;">Impossibile avviare il backend locale.</div>' +
      '<div style="font-size:13px;color:#94a3b8;">' + String((err && err.message) || err) + "</div>"
    );
  }
}

boot();
