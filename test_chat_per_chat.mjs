// Una risposta in corso per chat, chat generali multiple, allegati visibili (19/10).
//
// Il difetto che questo banco sorveglia, visto dal vivo: DEVIN rispondeva nella
// General chat, Alessandro apriva un'altra chat, allegava un'immagine e scriveva.
// Lo stato della risposta in corso era UNO per tutta l'app: il composer restava
// "Stop / Nota", il messaggio diventava una nota per un'altra chat e veniva
// rifiutato ("DEVIN non sta lavorando qui adesso"), l'immagine non partiva.
// E "New" senza progetto rispondeva "Seleziona un progetto".
//
// Le funzioni si ESTRAGGONO dal codex_app.js vero, come negli altri banchi.

import fs from "node:fs";
import path from "node:path";
import assert from "node:assert/strict";

const SORGENTE = path.join(process.cwd(), "devin/ui/static/js/codex_app.js");
const sorgente = fs.readFileSync(SORGENTE, "utf8");

function estrai(nome) {
  let inizio = sorgente.search(new RegExp(`^(async )?function ${nome}\\(`, "m"));
  assert.notEqual(inizio, -1, `funzione ${nome} non trovata in codex_app.js`);
  const fine = sorgente.indexOf("\n}\n", inizio);
  assert.notEqual(fine, -1, `fine di ${nome} non trovata`);
  return sorgente.slice(inizio, fine + 3);
}

// --- DOM finto, il minimo ---------------------------------------------------
class Nodo {
  constructor(tag) {
    this.tagName = (tag || "").toUpperCase();
    this.children = [];
    this.parentElement = null;
    this.hidden = false;
    this.textContent = "";
    this.className = "";
    this.title = "";
    this.scrollTop = 0;
    this.scrollHeight = 0;
  }
  appendChild(n) {
    if (n.parentElement) n.parentElement.children = n.parentElement.children.filter((c) => c !== n);
    n.parentElement = this;
    this.children.push(n);
    return n;
  }
  replaceChildren(...nodi) { this.children = []; nodi.forEach((n) => this.appendChild(n)); }
  addEventListener() {}
  focus() {}
}

function ambiente(opzioni = {}) {
  const thread = new Nodo("div");
  const fileInput = new Nodo("input");
  fileInput.files = [];
  Object.defineProperty(fileInput, "value", {
    get() { return ""; },
    set(v) { if (v === "") this.files = []; },
  });
  const nodi = {
    "chat-thread": thread, "chat-file": fileInput, "chat-file-label": new Nodo("span"),
    "chat-attachments": new Nodo("div"), "chat-input": new Nodo("textarea"), "chat-stop": new Nodo("button"),
  };
  const state = { selectedProjectPath: "", selectedChatId: null, chatStreams: new Map(), webForced: false };
  const log = { occupato: [], post: [], richieste: [], messaggi: [] };
  const rispostePendenti = [];

  const stub = {
    $: (id) => nodi[id] || null,
    state,
    document: { createElement: (tag) => new Nodo(tag) },
    URL: { createObjectURL: () => "blob:x", revokeObjectURL() {} },
    DataTransfer: undefined,
    setChatBusy: (v) => log.occupato.push(v),
    appendChatMessage(role, content) {
      const articolo = new Nodo("article");
      const corpo = new Nodo("div");
      articolo.appendChild(corpo);
      corpo.textContent = content;
      corpo._ruolo = role;
      thread.appendChild(articolo);
      log.messaggi.push([role, content]);
      return corpo;
    },
    // con un workspace locale diretto c'e' un await PRIMA della richiesta:
    // e' li' che un cambio di chat poteva infilarsi
    selectedDirectWorkspace: () => (opzioni.diretto ? { bridge_id: "b1" } : null),
    desktopInvoke: async () => { await attesa(); return { content: "" }; },
    // la risposta del backend resta "in volo" finche' il banco non la chiude
    apiFetch: (url, opzioni) => new Promise((risolvi, rifiuta) => {
      log.richieste.push({ url, opzioni });
      rispostePendenti.push({ risolvi, rifiuta });
      opzioni.signal?.addEventListener?.("abort", () => rifiuta(Object.assign(new Error("abort"), { name: "AbortError" })));
    }),
    postJson: async (url, body) => { log.post.push({ url, body }); return url.endsWith("/chats/new") ? { chat_id: "chat_nuova" } : {}; },
    loadProjectOverview: async () => {},
    runAttivoDellaChat: () => null,
    renderNote() {},
    applyChatEvent() {},
    parseSseBlock: (b) => b,
    splitReasoning: (t) => ({ answer: t }),
    dividiLavoro: (t) => ({ finale: t }),
    renderMarkdownBody() {},
    errorMessage: (e) => String(e?.message || e),
    showRunStatus() {}, selectRun: async () => {}, setPipelineStage() {}, runLocalWorkspaceAgent: async () => {},
    renderActivityRail: async () => {},
  };

  const NOMI = ["chiaveChatCorrente", "streamDellaChat", "aggiornaComposerPerChat", "riattaccaRispostaInCorso",
    "selectedChatFiles", "eImmagine", "formatoDimensione", "schedaAllegato", "renderAllegati", "togliAllegato",
    "svuotaAllegati", "mostraAllegatiInviati", "formatFileLabel", "chiudiRisposta", "sendChatMessage", "inviaNota",
    "createProjectChat"];
  const corpo = `const IMMAGINE_ALLEGATO_RE = /\\.(png|jpe?g|gif|bmp)$/i;\n`
    + NOMI.map(estrai).join("\n")
    + `\nreturn { ${NOMI.join(", ")} };`;
  const nomiStub = Object.keys(stub);
  const fabbrica = new Function(...nomiStub, corpo);
  const f = fabbrica(...nomiStub.map((k) => stub[k]));
  // Una risposta SSE vuota, che chiude subito lo stream.
  const chiudi = (i) => rispostePendenti[i].risolvi({
    headers: { get: () => "text/event-stream" }, ok: true,
    body: { getReader: () => ({ read: async () => ({ done: true }) }) },
  });
  return { f, state, nodi, log, thread, chiudi };
}

const prove = [];
function prova(nome, fn) { prove.push([nome, fn]); }
const attesa = () => new Promise((r) => setTimeout(r, 0));

prova("una risposta in corso nella General chat non blocca un'altra chat", async () => {
  const { f, state, log } = ambiente();
  const inCorso = f.sendChatMessage("fammi un supporto");
  await attesa();
  assert.ok(f.streamDellaChat(), "la General chat ha la sua risposta in corso");
  assert.equal(log.occupato.at(-1), true);

  state.selectedProjectPath = "/w/prova immagine";
  f.aggiornaComposerPerChat();
  assert.equal(log.occupato.at(-1), false, "nell'altra chat il composer torna Invia");
  assert.equal(f.streamDellaChat(), null);
  void inCorso;
});

prova("il secondo messaggio parte davvero, con la sua chat e i suoi allegati", async () => {
  const { f, state, nodi, log, chiudi } = ambiente();
  const primo = f.sendChatMessage("supporto");
  await attesa();
  state.selectedProjectPath = "/w/prova";
  state.selectedChatId = "chat_b";
  nodi["chat-file"].files = [{ name: "1234.png", size: 2048, type: "image/png" }];
  const secondo = f.sendChatMessage("cosa vedi?");
  await attesa();

  assert.equal(log.richieste.length, 2, "due richieste al backend, non una nota");
  assert.equal(log.richieste[0].url, "/api/chat");
  assert.deepEqual(JSON.parse(log.richieste[0].opzioni.body).project_path, null);
  assert.equal(log.richieste[1].url, "/api/chat/document", "con l'immagine va su /api/chat/document");
  const form = log.richieste[1].opzioni.body;
  assert.equal(form.get("project_path"), "/w/prova");
  assert.equal(form.get("chat_id"), "chat_b");
  assert.equal(form.getAll("files").length, 1);
  assert.deepEqual(nodi["chat-file"].files, [], "gli allegati si svuotano all'invio");
  assert.equal(state.chatStreams.size, 2);

  // la prima finisce mentre guardi la seconda: la seconda resta "in corso"
  log.occupato.length = 0;
  chiudi(0);
  await primo;
  assert.equal(state.chatStreams.size, 1);
  assert.ok(f.streamDellaChat(), "la risposta della chat che guardi e' ancora in corso");
  assert.deepEqual(log.occupato, [], "il composer di questa chat non torna Invia per la fine dell'altra");
  chiudi(1);
  await secondo;
  assert.equal(state.chatStreams.size, 0);
  assert.deepEqual(log.occupato, [false]);
});

prova("la seconda chat dice che e' in coda finche' il rig e' occupato", async () => {
  const { f, state, thread, chiudi } = ambiente();
  const primo = f.sendChatMessage("supporto");
  await attesa();
  const bollaPrima = thread.children[1].children[0];
  assert.equal(bollaPrima.textContent, "", "la prima parte subito: niente 'in coda'");
  state.selectedChatId = "chat_b";
  f.sendChatMessage("cosa vedi?");
  await attesa();
  const bollaSeconda = f.streamDellaChat().nodo;
  assert.match(bollaSeconda.textContent, /^In coda: DEVIN sta rispondendo in un'altra chat/);
  assert.equal(bollaSeconda._letturaMostrata, true, "il primo segnale del modello sostituisce la riga");
  chiudi(0);
  await primo;
  assert.equal(bollaPrima._chiusa, true, "finito lo stream la risposta e' chiusa");
});

prova("la richiesta usa chat e progetto di quando l'hai inviata", async () => {
  const { f, state, nodi, log } = ambiente({ diretto: true });
  state.selectedChatId = "chat_a";
  const inCorso = f.sendChatMessage("ciao");
  state.selectedChatId = "chat_b";                  // cambi chat prima che la richiesta parta
  state.selectedProjectPath = "/w/altro";
  await attesa(); await attesa();
  assert.equal(log.richieste.length, 1, "la richiesta e' partita dopo il cambio");
  const corpo = JSON.parse(log.richieste[0].opzioni.body);
  assert.equal(corpo.chat_id, "chat_a");
  assert.equal(corpo.project_path, null);
  state.selectedChatId = "chat_c";
  state.selectedProjectPath = "";
  nodi["chat-file"].files = [{ name: "a.png", size: 1, type: "image/png" }];
  f.sendChatMessage("con allegato");
  state.selectedChatId = "chat_d";
  await attesa(); await attesa();
  assert.equal(log.richieste[1].opzioni.body.get("chat_id"), "chat_c");
  state.selectedChatId = "chat_b";
  state.selectedProjectPath = "/w/altro";
  assert.equal(f.streamDellaChat(), null);
  assert.ok(f.streamDellaChat("|chat_a"));
  void inCorso;
});

prova("in un'altra chat il testo non diventa una nota", async () => {
  const { f, state, log } = ambiente();
  f.sendChatMessage("supporto");
  await attesa();
  state.selectedChatId = "chat_b";
  assert.equal(f.streamDellaChat(), null, "il submit vede che qui DEVIN non lavora");
  state.selectedChatId = null;
  assert.equal(await f.inviaNota("aggiungi le viti"), "ok");
  assert.equal(log.post.at(-1).url, "/api/chat/nota");
  assert.equal(f.streamDellaChat().nodo._note[0].testo, "aggiungi le viti");
});

prova("tornando nella chat la risposta in corso si rimette in fondo, solo li'", async () => {
  const { f, state, thread } = ambiente();
  f.sendChatMessage("supporto");
  await attesa();
  const articolo = f.streamDellaChat().nodo.parentElement;
  thread.children = [];                              // cambio chat: lo storico si ridisegna
  articolo.parentElement = null;
  state.selectedChatId = "chat_b";
  f.riattaccaRispostaInCorso(thread);
  assert.equal(thread.children.length, 0, "nella chat B non compare la risposta di A");
  state.selectedChatId = null;
  f.riattaccaRispostaInCorso(thread);
  assert.deepEqual(thread.children, [articolo]);
});

prova("Stop ferma solo la risposta della chat che guardi", async () => {
  const { f, state } = ambiente();
  const a = f.sendChatMessage("uno");
  await attesa();
  state.selectedChatId = "chat_b";
  const b = f.sendChatMessage("due");
  await attesa();
  f.streamDellaChat()?.abort.abort();
  await b;
  assert.equal(state.chatStreams.size, 1);
  assert.ok(f.streamDellaChat("|"), "la risposta della General chat continua");
  void a;
});

prova("New senza progetto crea un'altra General chat", async () => {
  const { f, state, log } = ambiente();
  await f.createProjectChat();
  assert.deepEqual(log.post.at(-1), { url: "/api/project/chats/new",
    body: { project_path: "", title: "Nuova chat", continue_from_chat_id: "" } });
  assert.equal(state.selectedChatId, "chat_nuova");
  assert.ok(!log.messaggi.some(([, t]) => /Seleziona un progetto/.test(t)));
});

prova("l'allegato si vede: scheda con miniatura, nome, dimensione e x", async () => {
  const { f, nodi } = ambiente();
  nodi["chat-file"].files = [{ name: "1234.png", size: 2048, type: "image/png" },
    { name: "note.txt", size: 10, type: "text/plain" }];
  f.renderAllegati();
  const vassoio = nodi["chat-attachments"];
  assert.equal(vassoio.hidden, false);
  assert.equal(vassoio.children.length, 2);
  const [img, nome, togli] = vassoio.children[0].children;
  assert.equal(img.tagName, "IMG");
  assert.equal(nome.textContent, "1234.png · 2 KB");
  assert.equal(togli.textContent, "×");
  assert.equal(vassoio.children[1].children[0].textContent, "📄");
  f.svuotaAllegati();
  assert.equal(vassoio.hidden, true);
  assert.equal(nodi["chat-file-label"].textContent, "no files");
});

prova("il messaggio inviato mostra cosa e' partito", async () => {
  const { f, nodi, thread } = ambiente();
  nodi["chat-file"].files = [{ name: "1234.png", size: 2048, type: "image/png" }];
  f.sendChatMessage("descrivi");
  await attesa();
  const utente = thread.children[0];
  const riga = utente.children.find((c) => c.className === "chat-attachments sent");
  assert.ok(riga, "sotto il messaggio c'e' la riga degli allegati");
  assert.equal(riga.children[0].children[0].tagName, "IMG");
  assert.equal(riga.children[0].children.length, 2, "nel messaggio inviato niente x");
});

let fallite = 0;
for (const [nome, fn] of prove) {
  try {
    await fn();
    console.log(`ok   ${nome}`);
  } catch (err) {
    fallite += 1;
    console.log(`FAIL ${nome}\n     ${err.stack || err}`);
  }
}
console.log(`\n${prove.length - fallite}/${prove.length} prove passate`);
process.exit(fallite ? 1 : 0);
