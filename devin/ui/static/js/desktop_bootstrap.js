// Native-only bootstrap for the DEVIN Tauri shell. Rust owns credentials,
// filesystem writes, network probes and HTTP to the trusted rig LAN. The local
// cockpit receives Response-compatible streams over Tauri IPC and never loads
// remote frontend code.

let booting = false;
let latestStatus = null;
let cockpitLoaded = false;

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

function installDesktopTransport(connection) {
  if (!connection || connection.schema !== "devin_desktop_connection_v1") {
    throw new Error("Contratto connessione desktop non supportato");
  }
  const endpoint = new URL(String(connection.api_base || ""));
  if (!/^https?:$/.test(endpoint.protocol) || endpoint.pathname !== "/") {
    throw new Error("Endpoint frontdoor restituito da Tauri non valido");
  }
  const Channel = window.__TAURI__?.core?.Channel;
  if (typeof Channel !== "function") {
    throw new Error("Canale streaming Tauri non disponibile");
  }

  const requestId = () => {
    if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map(value => value.toString(16).padStart(2, "0"));
    return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
  };

  const bytesToBase64 = (bytes) => {
    let binary = "";
    for (let offset = 0; offset < bytes.length; offset += 0x8000) {
      binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
    }
    return btoa(binary);
  };

  const base64ToBytes = (encoded) => {
    const binary = atob(encoded);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }
    return bytes;
  };

  const nativeFetch = async (path, options = {}) => {
    if (typeof path !== "string" || !path.startsWith("/") || path.startsWith("//")) {
      throw new Error("Percorso API desktop non valido");
    }
    const headers = new Headers(options.headers || {});
    if (options.cache === "no-store" && !headers.has("Cache-Control")) {
      headers.set("Cache-Control", "no-store");
    }
    let bodyText = null;
    let bodyBase64 = null;
    if (typeof options.body === "string") {
      bodyText = options.body;
    } else if (options.body != null) {
      const encoded = new Response(options.body);
      if (!headers.has("Content-Type") && encoded.headers.has("Content-Type")) {
        headers.set("Content-Type", encoded.headers.get("Content-Type"));
      }
      bodyBase64 = bytesToBase64(new Uint8Array(await encoded.arrayBuffer()));
    }

    if (options.signal?.aborted) throw new DOMException("Richiesta annullata", "AbortError");
    const id = requestId();
    const channel = new Channel();
    let responseSettled = false;
    let streamClosed = false;
    let resolveResponse;
    let rejectResponse;
    let streamController;
    const responsePromise = new Promise((resolve, reject) => {
      resolveResponse = resolve;
      rejectResponse = reject;
    });
    const bodyStream = new ReadableStream({
      start(controller) {
        streamController = controller;
      },
      cancel() {
        if (!streamClosed) void tauriInvoke("desktop_http_cancel", { requestId: id });
      },
    });

    const fail = (error) => {
      const failure = error instanceof Error ? error : new Error(errorText(error));
      if (!responseSettled) {
        responseSettled = true;
        rejectResponse(failure);
      } else if (!streamClosed) {
        streamClosed = true;
        streamController.error(failure);
      }
    };
    channel.onmessage = (event) => {
      if (!event || streamClosed) return;
      if (event.kind === "head") {
        if (responseSettled) return;
        responseSettled = true;
        const nullBody = [101, 103, 204, 205, 304].includes(Number(event.status));
        resolveResponse(new Response(nullBody ? null : bodyStream, {
          status: Number(event.status),
          statusText: String(event.status_text || ""),
          headers: new Headers(event.headers || []),
        }));
      } else if (event.kind === "chunk") {
        streamController.enqueue(base64ToBytes(String(event.data || "")));
      } else if (event.kind === "done") {
        streamClosed = true;
        streamController.close();
      } else if (event.kind === "error") {
        fail(new Error(String(event.message || "Trasporto desktop fallito")));
      }
    };

    const abort = () => {
      void tauriInvoke("desktop_http_cancel", { requestId: id });
      fail(new DOMException("Richiesta annullata", "AbortError"));
    };
    options.signal?.addEventListener("abort", abort, { once: true });
    void tauriInvoke("desktop_http_stream", {
      request: {
        requestId: id,
        method: String(options.method || "GET").toUpperCase(),
        path,
        headers: [...headers.entries()],
        bodyText,
        bodyBase64,
      },
      onEvent: channel,
    }).catch(fail).finally(() => options.signal?.removeEventListener("abort", abort));
    return responsePromise;
  };

  const createEventSource = (path) => {
    const controller = new AbortController();
    const listeners = new Map();
    let closed = false;
    const source = {
      CONNECTING: 0,
      OPEN: 1,
      CLOSED: 2,
      readyState: 0,
      onopen: null,
      onmessage: null,
      onerror: null,
      addEventListener(type, listener) {
        if (typeof listener !== "function") return;
        const bucket = listeners.get(type) || new Set();
        bucket.add(listener);
        listeners.set(type, bucket);
      },
      removeEventListener(type, listener) {
        listeners.get(type)?.delete(listener);
      },
      close() {
        if (closed) return;
        closed = true;
        source.readyState = source.CLOSED;
        controller.abort();
      },
    };

    const dispatch = (type, event = { type }) => {
      const handler = source[`on${type}`];
      if (typeof handler === "function") handler.call(source, event);
      for (const listener of listeners.get(type) || []) listener.call(source, event);
    };

    const dispatchFrame = (frame) => {
      let eventType = "message";
      const data = [];
      for (const line of frame.replace(/\r\n/g, "\n").split("\n")) {
        if (!line || line.startsWith(":")) continue;
        const separator = line.indexOf(":");
        const field = separator < 0 ? line : line.slice(0, separator);
        let value = separator < 0 ? "" : line.slice(separator + 1);
        if (value.startsWith(" ")) value = value.slice(1);
        if (field === "event" && value) eventType = value;
        if (field === "data") data.push(value);
      }
      if (!data.length && eventType === "message") return;
      dispatch(eventType, { type: eventType, data: data.join("\n") });
    };

    void (async () => {
      try {
        const response = await nativeFetch(path, {
          headers: { Accept: "text/event-stream" },
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok || !response.body) {
          throw new Error(`stream ${response.status}`);
        }
        source.readyState = source.OPEN;
        dispatch("open");
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (!closed) {
          const { value, done } = await reader.read();
          buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
          buffer = buffer.replace(/\r\n/g, "\n");
          let boundary = buffer.indexOf("\n\n");
          while (boundary >= 0) {
            dispatchFrame(buffer.slice(0, boundary));
            buffer = buffer.slice(boundary + 2);
            boundary = buffer.indexOf("\n\n");
          }
          if (done) break;
        }
        if (!closed) {
          source.readyState = source.CLOSED;
          dispatch("error", { type: "error", error: new Error("stream chiuso") });
        }
      } catch (error) {
        if (closed || error?.name === "AbortError") return;
        source.readyState = source.CLOSED;
        dispatch("error", { type: "error", error });
      }
    })();
    return source;
  };

  const existing = window.__DEVIN_TRANSPORT__;
  if (existing) {
    if (existing.origin !== endpoint.origin) {
      throw new Error("Il frontdoor e' cambiato: riavvia DEVIN per applicarlo");
    }
    return existing;
  }
  const transport = Object.freeze({
    schema: "devin_desktop_transport_v1",
    origin: endpoint.origin,
    fetch: nativeFetch,
    createEventSource,
  });
  Object.defineProperty(window, "__DEVIN_TRANSPORT__", {
    value: transport,
    configurable: false,
    enumerable: false,
    writable: false,
  });
  return transport;
}

async function loadCockpit() {
  if (cockpitLoaded) return;
  const scriptUrl = new URL("./codex_app.js", import.meta.url);
  scriptUrl.search = new URL(import.meta.url).search;
  await import(scriptUrl.href);
  cockpitLoaded = true;
  document.getElementById("devin-boot-overlay")?.remove();
}

function updateBackendProgress(snapshot) {
  if (!snapshot || typeof snapshot !== "object") return;
  const phase = document.getElementById("devin-boot-phase");
  const eta = document.getElementById("devin-boot-eta");
  if (phase) {
    phase.textContent = `Fase: ${snapshot.phase || snapshot.state || "preparazione"}`
      + (snapshot.waiting_unit ? ` · attendo ${snapshot.waiting_unit}` : "");
  }
  if (eta) {
    eta.textContent = snapshot.eta_seconds == null
      ? "Stima in aggiornamento"
      : `ETA circa ${snapshot.eta_seconds} secondi`;
  }
}

async function readStatusResponse(response) {
  let payload = null;
  try {
    payload = await response.json();
  } catch (_) {
    throw new Error(`Risposta lifecycle frontdoor non valida (${response.status})`);
  }
  if (response.status === 401 || response.status === 403) {
    throw new Error("Il frontdoor non ha autorizzato questo client della LAN");
  }
  return payload?.status && typeof payload.status === "object" ? payload.status : payload;
}

async function waitForBackendReady(transport) {
  const deadline = Date.now() + (20 * 60 * 1000);
  let snapshot = null;
  let pollCount = 0;
  const activate = async () => {
    const response = await transport.fetch("/control/activate", {
      method: "POST",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    snapshot = await readStatusResponse(response);
    updateBackendProgress(snapshot);
  };

  await activate();
  while (!snapshot?.ready) {
    if (snapshot?.state === "attention" || snapshot?.phase === "failed") {
      throw new Error(`Lifecycle DEVIN in errore: ${snapshot.phase || snapshot.state}`);
    }
    if (Date.now() >= deadline) {
      throw new Error("DEVIN non e' diventato pronto entro 20 minuti");
    }
    await new Promise(resolve => window.setTimeout(resolve, 2000));
    const response = await transport.fetch("/control/status", {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    snapshot = await readStatusResponse(response);
    updateBackendProgress(snapshot);
    pollCount += 1;
    const session = snapshot?.units?.session;
    if (!snapshot?.ready && pollCount % 5 === 0 && !["active", "activating"].includes(session)) {
      await activate();
    }
  }
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
  const phase = element("p", "devin-boot-phase", "Fase: richiesta attivazione controllata");
  phase.id = "devin-boot-phase";
  card.appendChild(phase);
  const progress = element("div", "devin-boot-progress");
  progress.appendChild(element("span", ""));
  card.appendChild(progress);
  const eta = element("p", "devin-boot-eta", "Stima in aggiornamento");
  eta.id = "devin-boot-eta";
  card.appendChild(eta);
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
    "DEVIN accetta i client della rete locale configurata; sul PC viene salvato soltanto l'indirizzo del rig."
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
      });
      await connectConfigured();
    } catch (error) {
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
    const connection = await tauriInvoke("connect_frontdoor");
    const transport = installDesktopTransport(connection);
    await waitForBackendReady(transport);
    await loadCockpit();
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
