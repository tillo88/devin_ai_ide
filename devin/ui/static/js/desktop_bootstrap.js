// Native-only bootstrap for the DEVIN Tauri shell. Rust owns credentials,
// filesystem writes and network probes. JavaScript may submit a token entered
// by the user, but Rust never returns a stored token to this page.

let booting = false;
let latestStatus = null;

function tauriInvoke(cmd, args) {
  const tauri = window.__TAURI__;
  if (tauri && tauri.core && typeof tauri.core.invoke === "function") {
    return tauri.core.invoke(cmd, args);
  }
  if (tauri && typeof tauri.invoke === "function") return tauri.invoke(cmd, args);
  return Promise.reject(new Error("API Tauri non disponibile"));
}

function errorText(error) {
  return String((error && error.message) || error || "Errore sconosciuto");
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function action(label, variant, handler) {
  const button = element("button", `devin-boot-action ${variant || "secondary"}`, label);
  button.type = "button";
  button.addEventListener("click", handler);
  return button;
}

function createOverlay(eyebrow, title, detail = "") {
  const previous = document.getElementById("devin-boot-overlay");
  if (previous) previous.remove();

  const overlay = element("div", "devin-boot-overlay");
  overlay.id = "devin-boot-overlay";
  const glow = element("div", "devin-boot-glow");
  glow.setAttribute("aria-hidden", "true");
  overlay.appendChild(glow);

  const card = element("main", "devin-boot-card");
  card.setAttribute("role", "dialog");
  card.setAttribute("aria-modal", "true");
  card.setAttribute("aria-labelledby", "devin-boot-title");

  const brand = element("div", "devin-boot-brand");
  brand.appendChild(element("span", "devin-boot-mark", "D"));
  const brandCopy = element("div", "devin-boot-brand-copy");
  brandCopy.appendChild(element("strong", "", "DEVIN AI IDE"));
  brandCopy.appendChild(element("span", "", "Windows thin client · rig workspace"));
  brand.appendChild(brandCopy);
  card.appendChild(brand);

  const copy = element("section", "devin-boot-copy");
  copy.appendChild(element("div", "devin-boot-eyebrow", eyebrow));
  const heading = element("h1", "", title);
  heading.id = "devin-boot-title";
  copy.appendChild(heading);
  if (detail) {
    const description = element("p", "devin-boot-detail");
    description.textContent = detail;
    copy.appendChild(description);
  }
  card.appendChild(copy);
  overlay.appendChild(card);
  document.body.appendChild(overlay);
  return card;
}

function endpointRow(status) {
  if (!status || !status.frontdoor_url) return null;
  const row = element("div", "devin-boot-endpoint");
  row.appendChild(element("span", "devin-boot-status-dot"));
  const copy = element("div", "");
  copy.appendChild(element("small", "", "Frontdoor configurato"));
  copy.appendChild(element("strong", "", status.frontdoor_url));
  row.appendChild(copy);
  return row;
}

function showConnecting(status) {
  const card = createOverlay(
    "Connessione protetta",
    "Preparo il workspace DEVIN",
    "Il frontdoor gestisce il passaggio da Clippy a DEVIN e mostrera' avanzamento ed ETA soltanto se serve."
  );
  const endpoint = endpointRow(status);
  if (endpoint) card.appendChild(endpoint);
  const progress = element("div", "devin-boot-progress");
  progress.appendChild(element("span", ""));
  card.appendChild(progress);
  card.appendChild(element("p", "devin-boot-footnote", "Backend, modelli e workspace restano sul rig."));
}

function showFailure(detail, status) {
  const card = createOverlay(
    "Connessione non riuscita",
    "DEVIN non e' raggiungibile",
    detail
  );
  const endpoint = endpointRow(status);
  if (endpoint) card.appendChild(endpoint);
  const actions = element("div", "devin-boot-actions");
  actions.appendChild(action("Riprova", "primary", boot));
  actions.appendChild(action("Impostazioni", "secondary", () => showSettings(status)));
  card.appendChild(actions);
  card.appendChild(element("p", "devin-boot-footnote", "Il test impostazioni non attiva il modello DEVIN."));
}

function field(label, input, hint) {
  const wrapper = element("label", "devin-boot-field");
  wrapper.appendChild(element("span", "", label));
  wrapper.appendChild(input);
  if (hint) wrapper.appendChild(element("small", "", hint));
  return wrapper;
}

function setFormBusy(form, busy) {
  for (const control of form.querySelectorAll("input,button")) control.disabled = busy;
  form.setAttribute("aria-busy", String(busy));
}

function showSettings(status = latestStatus, notice = "") {
  const configured = Boolean(status && status.configured);
  const card = createOverlay(
    configured ? "Impostazioni connessione" : "Prima configurazione",
    configured ? "Collega un altro frontdoor" : "Collega DEVIN al rig",
    "La configurazione resta sul PC con ACL limitata al tuo utente e SYSTEM. Il token salvato non viene mai restituito alla UI."
  );

  const form = element("form", "devin-boot-form");
  form.autocomplete = "off";
  const url = element("input", "");
  url.type = "url";
  url.name = "frontdoor-url";
  url.required = true;
  url.autocomplete = "url";
  url.spellcheck = false;
  url.placeholder = "http://192.168.1.101:5000";
  url.value = (status && status.frontdoor_url) || "";
  form.appendChild(field("URL frontdoor", url, "Solo radice http/https, senza token o percorsi aggiuntivi."));

  const token = element("input", "");
  token.type = "password";
  token.name = "frontdoor-token";
  token.autocomplete = "off";
  token.spellcheck = false;
  token.required = !configured;
  token.minLength = 32;
  token.maxLength = 256;
  token.placeholder = configured ? "Token gia' protetto" : "Incolla il token frontdoor";
  form.appendChild(field(
    "Token di accesso",
    token,
    configured ? "Lascialo vuoto per conservare il token esistente." : "Da 32 a 256 caratteri, senza spazi."
  ));

  const feedback = element("div", "devin-boot-feedback", notice);
  feedback.setAttribute("aria-live", "polite");
  form.appendChild(feedback);

  const actions = element("div", "devin-boot-actions");
  const probeButton = action("Test senza attivare", "secondary", async () => {
    if (!url.reportValidity()) return;
    setFormBusy(form, true);
    feedback.className = "devin-boot-feedback pending";
    feedback.textContent = "Verifico solo la porta del frontdoor…";
    try {
      const result = await tauriInvoke("test_frontdoor_connection", { frontdoorUrl: url.value });
      feedback.className = `devin-boot-feedback ${result.reachable ? "success" : "error"}`;
      feedback.textContent = result.reachable
        ? `Frontdoor raggiungibile su ${result.origin}. Nessuna attivazione eseguita.`
        : `Frontdoor non raggiungibile su ${result.origin}.`;
    } catch (error) {
      feedback.className = "devin-boot-feedback error";
      feedback.textContent = errorText(error);
    } finally {
      setFormBusy(form, false);
    }
  });
  actions.appendChild(probeButton);

  if (configured) {
    actions.appendChild(action("Annulla", "secondary", boot));
  }
  const submit = action("Salva e connetti", "primary", () => form.requestSubmit());
  actions.appendChild(submit);
  form.appendChild(actions);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!url.reportValidity()) return;
    setFormBusy(form, true);
    feedback.className = "devin-boot-feedback pending";
    feedback.textContent = "Proteggo e salvo la configurazione…";
    try {
      latestStatus = await tauriInvoke("save_frontdoor_config", {
        frontdoorUrl: url.value,
        accessToken: token.value || null,
      });
      token.value = "";
      await connectConfigured();
    } catch (error) {
      token.value = "";
      feedback.className = "devin-boot-feedback error";
      feedback.textContent = errorText(error);
      setFormBusy(form, false);
    }
  });

  card.appendChild(form);
  if (status && status.managed_by_environment) {
    const managed = element("p", "devin-boot-warning", "Gli override DEVIN_FRONTDOOR_* sono attivi: rimuovili per salvare dall'app.");
    card.appendChild(managed);
  }
  window.setTimeout(() => url.focus(), 0);
}

async function connectConfigured() {
  if (booting) return;
  booting = true;
  showConnecting(latestStatus);
  try {
    await tauriInvoke("connect_frontdoor");
  } catch (error) {
    showFailure(errorText(error), latestStatus);
  } finally {
    booting = false;
  }
}

async function boot() {
  if (booting) return;
  booting = true;
  try {
    latestStatus = await tauriInvoke("desktop_config_status");
    if (!latestStatus.configured) {
      booting = false;
      showSettings(latestStatus, latestStatus.issue || "Inserisci i dati del frontdoor.");
      return;
    }
  } catch (error) {
    booting = false;
    showSettings(null, errorText(error));
    return;
  }
  booting = false;
  await connectConfigured();
}

boot();
