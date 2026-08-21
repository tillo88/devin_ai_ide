// Bootstrap used only by the bundled Tauri shell. Rust reads the protected
// desktop configuration and navigates the webview to the authenticated rig
// front door; the URL and token are never returned to JavaScript.

let booting = false;

function tauriInvoke(cmd, args) {
  const tauri = window.__TAURI__;
  if (tauri && tauri.core && typeof tauri.core.invoke === "function") {
    return tauri.core.invoke(cmd, args);
  }
  if (tauri && typeof tauri.invoke === "function") return tauri.invoke(cmd, args);
  return Promise.reject(new Error("API Tauri non disponibile"));
}

function showOverlay(title, detail = "", retry = false) {
  const previous = document.getElementById("devin-boot-overlay");
  if (previous) previous.remove();

  const overlay = document.createElement("div");
  overlay.id = "devin-boot-overlay";
  overlay.style.cssText =
    "position:fixed;inset:0;display:flex;align-items:center;justify-content:center;" +
    "background:#0b1220;color:#e5e7eb;font-family:system-ui,sans-serif;z-index:99999;";

  const card = document.createElement("div");
  card.style.cssText =
    "width:min(520px,calc(100vw - 48px));text-align:center;padding:30px;" +
    "border:1px solid #1f2a44;border-radius:16px;background:#0f172a;";
  const heading = document.createElement("div");
  heading.style.cssText = "font-size:17px;font-weight:650;";
  heading.textContent = title;
  card.appendChild(heading);

  if (detail) {
    const description = document.createElement("div");
    description.style.cssText =
      "font-size:13px;line-height:1.55;color:#94a3b8;margin-top:10px;white-space:pre-wrap;";
    description.textContent = detail;
    card.appendChild(description);
  }

  if (retry) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Riprova";
    button.style.cssText =
      "margin-top:18px;padding:9px 18px;border:1px solid #3b82f6;border-radius:9px;" +
      "background:#2563eb;color:white;font:inherit;cursor:pointer;";
    button.addEventListener("click", boot);
    card.appendChild(button);
  }

  overlay.appendChild(card);
  document.body.appendChild(overlay);
}

async function boot() {
  if (booting) return;
  booting = true;
  showOverlay(
    "Connessione a DEVIN sul rig…",
    "Se il modello non e' ancora residente, il frontdoor mostrera' avanzamento ed ETA."
  );
  try {
    await tauriInvoke("connect_frontdoor");
  } catch (error) {
    const detail = String((error && error.message) || error || "Errore sconosciuto");
    showOverlay("DEVIN non e' raggiungibile", detail, true);
  } finally {
    booting = false;
  }
}

boot();
