// Desktop bootstrap (app nativa 2026-07-22).
//
// La UI e' bundlata nell'app: prima di avviarla, questo bootstrap SCOPRE il
// backend rig-first e solo dopo carica il resto dell'app. Se nessun backend
// risponde, mostra il prompt "Rig esterno non presente, vuoi procedere in
// locale?": Si' avvia il backup locale (comando Tauri), No non avvia nulla e
// invita a controllare il rig. Nessun avvio automatico.
//
// Usato SOLO nel bundle desktop (lo inietta scripts/build_frontend_bundle.py).
// La versione web resta servita dal backend, same-origin, senza bootstrap.

const RIG_BASE = "http://192.168.1.100:5000";   // backend sul rig (quando attivo)
const LOCAL_BASE = "http://127.0.0.1:5000";     // backup locale sul PC

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

async function loadApp(base) {
  window.__DEVIN_API_BASE__ = base;
  removeOverlay();
  await import("/static/js/codex_app.js");
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

function showConnecting() {
  overlay('<div style="font-size:15px;">Connessione al backend DEVIN…</div>');
}

function showRigPrompt() {
  const div = overlay(
    '<div style="font-size:16px;margin-bottom:6px;">Rig esterno non presente</div>' +
    '<div style="font-size:13px;color:#94a3b8;margin-bottom:20px;">Vuoi procedere in locale?' +
    " Verranno avviati il backend e il modello di backup sul PC.</div>" +
    '<button id="devin-boot-yes" style="margin:0 6px;padding:8px 18px;border-radius:999px;border:1px solid #22d3ee;background:#0891b2;color:#fff;cursor:pointer;">Sì, in locale</button>' +
    '<button id="devin-boot-no" style="margin:0 6px;padding:8px 18px;border-radius:999px;border:1px solid #334155;background:transparent;color:#e5e7eb;cursor:pointer;">No</button>'
  );
  div.querySelector("#devin-boot-yes").addEventListener("click", startLocal);
  div.querySelector("#devin-boot-no").addEventListener("click", () => {
    overlay(
      '<div style="font-size:15px;margin-bottom:8px;">Backend non avviato.</div>' +
      '<div style="font-size:13px;color:#94a3b8;">Accendi il rig esterno e riapri DEVIN,' +
      " oppure riavvia scegliendo la modalità locale.</div>"
    );
  });
}

async function startLocal() {
  overlay('<div style="font-size:15px;">Avvio backend locale… (può richiedere qualche secondo)</div>');
  try {
    const base = await tauriInvoke("start_local_backend");
    await loadApp(base || LOCAL_BASE);
  } catch (err) {
    overlay(
      '<div style="font-size:15px;margin-bottom:8px;">Avvio locale fallito.</div>' +
      '<div style="font-size:13px;color:#94a3b8;">' + String(err && err.message || err) + "</div>"
    );
  }
}

async function boot() {
  showConnecting();
  if (await probe(RIG_BASE)) { await loadApp(RIG_BASE); return; }
  if (await probe(LOCAL_BASE)) { await loadApp(LOCAL_BASE); return; }
  showRigPrompt();
}

boot();
