// Agente locale a passaggi (21/10), lato app: le funzioni vere estratte da
// codex_app.js, con un desktop finto (desktopInvoke) e un rig finto (apiFetch
// che risponde SSE). Sorveglia: gli strumenti girano sul PC con i parametri
// giusti, i risultati tornano al rig con la stessa sessione, e il ciclo finisce.
import fs from "node:fs";
import path from "node:path";
import assert from "node:assert/strict";

const sorgente = fs.readFileSync(path.join(process.cwd(), "devin/ui/static/js/codex_app.js"), "utf8");
function estrai(nome) {
  const inizio = sorgente.search(new RegExp(`^(async )?function ${nome}\\(`, "m"));
  assert.notEqual(inizio, -1, `funzione ${nome} non trovata`);
  const fine = sorgente.indexOf("\n}\n", inizio);
  return sorgente.slice(inizio, fine + 3);
}
function costante(nome) {
  const riga = sorgente.match(new RegExp(`^const ${nome} = .*;$`, "m"));
  assert.ok(riga, `costante ${nome} non trovata`);
  return riga[0];
}

function ambiente({ desktop = {}, passi = [], conferma = true, modo = "revisione" } = {}) {
  const log = { invoke: [], richieste: [], eventi: [], training: [], conferme: 0, mostrate: [], banner: [] };
  const nodoFinto = () => ({ children: [], textContent: "", append(...figli) { this.children.push(...figli); },
    addEventListener() {}, remove() { log.banner.push("tolto"); documento.corrente = null; },
    querySelector() { return this.children[0]; } });
  const documento = { corrente: null,
    getElementById: () => documento.corrente,
    createElement: () => nodoFinto(),
    body: { append(nodo) { documento.corrente = nodo; log.banner.push("messo"); } } };
  const state = { selectedProjectPath: "/w/spyengine", selectedChatId: "c1", reasoningEffort: "", localCommandId: null };
  const sse = (eventi) => eventi.map(([tipo, dati]) => (tipo ? `event: ${tipo}\n` : "") + `data: ${JSON.stringify(dati)}\n\n`).join("");
  const stub = {
    state,
    localUuid: () => "11111111-1111-4111-8111-111111111111",
    desktopInvoke: async (comando, args) => {
      log.invoke.push([comando, args]);
      const r = desktop[comando];
      if (r instanceof Error) throw r;
      return typeof r === "function" ? r(args) : r;
    },
    confirmModal: async () => { log.conferme += 1; return conferma; },
    modoApplica: () => modo,
    document: documento,
    mostraModificheApplicate: (nodo, ws, applicati) => { log.mostrate.push([...(applicati || [])]); },
    errorMessage: (e) => String(e?.message || e),
    apiFetch: async (url, opzioni) => {
      log.richieste.push({ url, corpo: JSON.parse(opzioni.body) });
      const testo = sse(passi.shift());
      const bytes = new TextEncoder().encode(testo);
      let letto = false;
      return {
        ok: true, status: 200,
        headers: { get: () => "text/event-stream" },
        body: { getReader: () => ({ read: async () => (letto ? { done: true } : (letto = true, { value: bytes, done: false })) }) },
      };
    },
    applyChatEvent: (evento, nodo) => {
      log.eventi.push(evento.type);
      if (evento.type === "message") nodo._raw = (nodo._raw || "") + JSON.parse(evento.data).token;
    },
    splitReasoning: (t) => ({ answer: t }),
    dividiLavoro: (t) => ({ finale: t }),
    renderMarkdownBody: (nodo, t) => { nodo.reso = t; },
    recordLocalAgentTrainingAttempt: async (dati) => { log.training.push(dati); },
    loadProjectTree: async () => {}, renderActivityRail: async () => {},
  };
  const NOMI = ["parseSseBlock", "localAgentBoundedText", "localExecutionTrainingEvidence", "formatoRigheLocali",
    "elencoLocale", "proponiModificheLocali", "applicaModificheLocali", "bannerProgramma", "chiudiProgrammi",
    "esitoProgramma", "eseguiStrumentoLocale", "chiudiRisposta", "runLocalWorkspaceAgent"];
  const COSTANTI = ["AGENTE_LOCALE_RIGHE", "AGENTE_LOCALE_MAX_RIGHE", "AGENTE_LOCALE_MAX_CARATTERI", "AGENTE_LOCALE_MAX_ELENCO"];
  const corpo = COSTANTI.map(costante).join("\n") + "\n" + NOMI.map(estrai).join("\n") + `\nreturn { ${NOMI.join(", ")} };`;
  const f = new Function(...Object.keys(stub), corpo)(...Object.values(stub));
  return { f, log, state };
}

const prove = [];
const prova = (nome, fn) => prove.push([nome, fn]);
const WS = { bridge_id: "b1" };

prova("leggi: righe numerate, a pezzi, con lo sha e l'indicazione per continuare", async () => {
  const contenuto = Array.from({ length: 1000 }, (_, i) => `riga ${i + 1}`).join("\n");
  const { f, log } = ambiente({ desktop: { local_workspace_read: { path: "a.py", content: contenuto, sha256: "abc", truncated: false } } });
  const r = await f.eseguiStrumentoLocale({ name: "leggi", arguments: { percorso: "a.py", da_riga: 401, righe: 5 } }, WS, { esecuzioni: [], piani: [] });
  assert.deepEqual(log.invoke[0], ["local_workspace_read", { bridgeId: "b1", path: "a.py" }]);
  assert.match(r, /^a\.py · righe 401-405 di 1000 · sha256 abc \(continua: leggi da_riga 406\)/);
  assert.match(r, /\n401: riga 401\n/);
  assert.ok(!r.includes("400: riga 400") && r.includes("405: riga 405"));
  const tutto = await f.eseguiStrumentoLocale({ name: "leggi", arguments: { percorso: "a.py", righe: 99999 } }, WS, { esecuzioni: [], piani: [] });
  assert.match(tutto, /righe 1-800 di 1000/, "al massimo 800 righe per volta");
});

prova("elenca: filtra la cartella e dice se manca qualcosa", async () => {
  const albero = { truncated: true, files: [
    { path: "src/a.py", size: 10, is_text: true }, { path: "src/b.png", size: 99, is_text: false },
    { path: "tests/t.py", size: 5, is_text: true }] };
  const { f, log } = ambiente({ desktop: { local_workspace_tree: albero } });
  const ctx = { esecuzioni: [], piani: [] };
  const r = await f.eseguiStrumentoLocale({ name: "elenca", arguments: { cartella: "src/" } }, WS, ctx);
  assert.match(r, /^2 file in 'src'\nsrc\/a\.py \(10 byte\)\nsrc\/b\.png \(99 byte\) \[binario\]/);
  assert.match(r, /usa cerca/);
  await f.eseguiStrumentoLocale({ name: "elenca", arguments: {} }, WS, ctx);
  assert.equal(log.invoke.length, 1, "l'albero si chiede al PC una volta per sessione");
  assert.match(await f.eseguiStrumentoLocale({ name: "elenca", arguments: { cartella: "nulla" } }, WS, ctx), /^ERRORE: nessun file/);
});

prova("cerca ed esegui passano al desktop i parametri giusti", async () => {
  const { f, log, state } = ambiente({ desktop: {
    local_workspace_search: { query: "normalizza", scanned_files: 12, truncated: false,
      matches: [{ path: "src/a.py", line: 4, text: "def normalizza():" }] },
    run_local_workspace_command: (a) => {
      assert.equal(state.localCommandId, a.commandId, "Stop puo' fermare il processo in corso");
      return { exit_code: 1, duration_ms: 812, timed_out: false, cancelled: false, stdout: "1 failed", stderr: "", program: "pytest" };
    } } });
  const ctx = { esecuzioni: [], piani: [] };
  const c = await f.eseguiStrumentoLocale({ name: "cerca", arguments: { testo: "normalizza", cartella: "src" } }, WS, ctx);
  assert.deepEqual(log.invoke[0][1], { bridgeId: "b1", query: "normalizza", folder: "src", maxResults: 80 });
  assert.equal(c, "1 risultati in 12 file\nsrc/a.py:4: def normalizza():");
  const e = await f.eseguiStrumentoLocale({ name: "esegui", arguments: { programma: "pytest", argomenti: ["-q"], secondi: 5000 } }, WS, ctx);
  assert.deepEqual(log.invoke[1][1].args, ["-q"]);
  assert.equal(log.invoke[1][1].timeoutSeconds, 900);
  assert.match(e, /^exit 1 · 812 ms\n--- stdout ---\n1 failed/);
  assert.equal(state.localCommandId, null);
  assert.equal(ctx.esecuzioni.length, 1);
});

prova("un errore del desktop torna al modello come ERRORE, non ferma il ciclo", async () => {
  const { f } = ambiente({ desktop: { local_workspace_read: new Error("File binario non visualizzabile") } });
  const r = await f.eseguiStrumentoLocale({ name: "leggi", arguments: { percorso: "x.bin" } }, WS, { esecuzioni: [], piani: [] });
  assert.equal(r, "ERRORE: File binario non visualizzabile");
});

prova("proponi_modifiche: senza approvazione non si applica niente", async () => {
  const { f, log } = ambiente({ conferma: false });
  const ctx = { esecuzioni: [], piani: [] };
  const r = await f.eseguiStrumentoLocale({ name: "proponi_modifiche", arguments: {
    summary: "fix", operations: [{ path: "a.py", operation: "write", content: "x" }] } }, WS, ctx);
  assert.match(r, /NON ha approvato/);
  assert.equal(log.conferme, 1);
  assert.ok(!log.invoke.some(([c]) => c === "apply_local_workspace_plan"));
  assert.deepEqual(ctx.piani, ["rifiutato"]);
});

prova("applica da solo: niente conferma, backup e Annulla", async () => {
  const { f, log } = ambiente({ modo: "auto", desktop: {
    apply_local_workspace_plan: { run_id: "r1", files: 1, recovery_path: "C:/x" } } });
  const ctx = { esecuzioni: [], piani: [], applicati: [] };
  const r = await f.eseguiStrumentoLocale({ name: "proponi_modifiche", arguments: {
    summary: "prezzo prudente", operations: [{ path: "a.py", operation: "write", content: "x" }] } }, WS, ctx);
  assert.equal(log.conferme, 0, "con applica da solo non si chiede");
  assert.deepEqual(log.invoke[0], ["apply_local_workspace_plan",
    { bridgeId: "b1", operations: [{ path: "a.py", operation: "write", content: "x" }] }]);
  assert.match(r, /backup/);
  assert.match(r, /correggi e riprova/);
  assert.deepEqual(ctx.applicati, [{ run_id: "r1", descrizione: "prezzo prudente" }]);
  assert.deepEqual(ctx.piani, ["applicato"]);
});

prova("le modifiche applicate restano annullabili anche se il ciclo si interrompe", async () => {
  const { f, log } = ambiente({ modo: "auto",
    desktop: { apply_local_workspace_plan: { run_id: "r9", files: 1, recovery_path: "C:/x" } },
    passi: [
      [["sessione", { session_id: "s1" }], ["strumenti", { session_id: "s1", chiamate: [{ id: "c0", name: "proponi_modifiche",
        arguments: { summary: "fix", operations: [{ path: "a.py", operation: "write", content: "x" }] } }] }]],
      [["sessione", { session_id: "s1" }]],
    ] });
  await assert.rejects(f.runLocalWorkspaceAgent("x", WS, { textContent: "" }, null), /senza concludere/);
  assert.deepEqual(log.mostrate, [[{ run_id: "r9", descrizione: "fix" }]]);
});

prova("guarda_programma: avvia, aspetta, fotografa e passa le foto come immagini", async () => {
  const { f, log } = ambiente({ desktop: {
    run_local_workspace_command: { exit_code: 1, duration_ms: 5200, looked: true, stdout: "avvio", stderr: "",
      screenshots: [{ title: "SpyEngine", width: 800, height: 600, png_base64: "iVBORw0KGgo=" }] } } });
  const ctx = { esecuzioni: [], piani: [] };
  const r = await f.eseguiStrumentoLocale({ name: "guarda_programma",
    arguments: { programma: "python", argomenti: ["app.py"], aspetta_secondi: 999 } }, WS, ctx);
  assert.equal(log.invoke[0][1].lookAfterSeconds, 60, "al massimo 60 secondi");
  assert.deepEqual(log.invoke[0][1].args, ["app.py"]);
  assert.match(r.testo, /^1 finestra\/e fotografate, poi chiuso/);
  assert.match(r.testo, /finestra «SpyEngine» 800x600/);
  assert.deepEqual(r.immagini, [{ url: "data:image/png;base64,iVBORw0KGgo=", titolo: "SpyEngine" }]);
});

prova("le foto delle finestre tornano al rig insieme al risultato", async () => {
  const { f, log } = ambiente({
    desktop: { run_local_workspace_command: { exit_code: 0, duration_ms: 10, looked: false, stdout: "", stderr: "", screenshots: [] } },
    passi: [
      [["sessione", { session_id: "s1" }], ["strumenti", { session_id: "s1", chiamate: [{ id: "g0", name: "guarda_programma", arguments: { programma: "node" } }] }]],
      [["sessione", { session_id: "s1" }], ["", { token: "ok" }], ["done", {}]],
    ] });
  await f.runLocalWorkspaceAgent("prova l'app", WS, { textContent: "" }, null);
  const [risultato] = log.richieste[1].corpo.results;
  assert.equal(risultato.id, "g0");
  assert.match(risultato.content, /finito prima della foto/);
  assert.deepEqual(risultato.immagini, []);
});

prova("apri e usa un programma: chiede il permesso per quell'app, poi clic e foto", async () => {
  const foto = { title: "SpyEngine", width: 968, height: 548, png_base64: "iVBORw0KGgo=" };
  const { f, log } = ambiente({ conferma: false, desktop: {
    start_local_program: (a) => ({ program_id: a.programId, running: true, windows: ["SpyEngine"], screenshot: foto, done: [] }),
    act_local_program: { running: true, windows: ["SpyEngine"], screenshot: foto, done: ["clic in (40, 60)"] },
    stop_local_program: { running: false, exit_code: 0, stdout: "chiusa", stderr: "", duration_ms: 9000 },
  } });
  const ctx = { esecuzioni: [], piani: [] };
  const aperto = await f.eseguiStrumentoLocale({ name: "apri_programma", arguments: { programma: "python", argomenti: ["app.py"] } }, WS, ctx);
  const id = aperto.testo.match(/id (\S+)/)[1];
  assert.equal(log.invoke[0][0], "start_local_program");
  assert.match(aperto.testo, /foto della finestra «SpyEngine» 968x548/);
  assert.equal(aperto.immagini.length, 1);
  assert.equal(log.banner.length, 0, "aperto ma non ancora usato: nessuna striscia");
  // senza permesso non si tocca niente
  const stub = f;
  const negato = await stub.eseguiStrumentoLocale({ name: "usa_programma", arguments: { id, azioni: [{ tipo: "clic", x: 40, y: 60 }] } }, WS, ctx);
  assert.match(negato, /NON ha dato il permesso/);
  assert.ok(!log.invoke.some(([c]) => c === "act_local_program"));
  assert.equal(await stub.eseguiStrumentoLocale({ name: "usa_programma", arguments: { id: "inventato", azioni: [] } }, WS, ctx)
    .then((r) => /nessun programma aperto/.test(r)), true);
  await stub.chiudiProgrammi(ctx);
  assert.ok(log.invoke.some(([c, a]) => c === "stop_local_program" && a.programId === id), "Ferma chiude i programmi");
});

prova("con il permesso usa il programma, la striscia avvisa e alla fine si chiude tutto", async () => {
  const foto = { title: "SpyEngine", width: 968, height: 548, png_base64: "iVBORw0KGgo=" };
  const { f, log } = ambiente({ conferma: true, desktop: {
    start_local_program: (a) => ({ program_id: a.programId, running: true, windows: ["SpyEngine"], screenshot: foto, done: [] }),
    act_local_program: { running: true, windows: ["SpyEngine"], screenshot: foto, done: ["clic in (40, 60)", "scritto 4 caratteri"] },
    stop_local_program: { running: false, exit_code: 0, stdout: "", stderr: "", duration_ms: 1 },
  } });
  const ctx = { esecuzioni: [], piani: [] };
  const aperto = await f.eseguiStrumentoLocale({ name: "apri_programma", arguments: { programma: "python" } }, WS, ctx);
  const id = aperto.testo.match(/id (\S+)/)[1];
  const usato = await f.eseguiStrumentoLocale({ name: "usa_programma", arguments: {
    id, azioni: [{ tipo: "clic", x: 40, y: 60 }, { tipo: "scrivi", testo: "ram" }], aspetta_ms: 99999 } }, WS, ctx);
  assert.equal(log.conferme, 1);
  const azione = log.invoke.find(([c]) => c === "act_local_program")[1];
  assert.deepEqual(azione.actions, [{ tipo: "clic", x: 40, y: 60 }, { tipo: "scrivi", testo: "ram" }]);
  assert.equal(azione.waitMs, 10000, "attesa al massimo 10 s");
  assert.match(usato.testo, /fatto: clic in \(40, 60\); scritto 4 caratteri/);
  assert.deepEqual(log.banner, ["messo"], "la striscia avvisa che mouse e tastiera sono di DEVIN");
  await f.eseguiStrumentoLocale({ name: "usa_programma", arguments: { id, azioni: [{ tipo: "tasti", tasti: "invio" }] } }, WS, ctx);
  assert.equal(log.conferme, 1, "il permesso vale per quell'app finche' resta aperta");
  const chiuso = await f.eseguiStrumentoLocale({ name: "chiudi_programma", arguments: { id } }, WS, ctx);
  assert.match(chiuso, /programma chiuso/);
  assert.deepEqual(log.banner, ["messo", "tolto"]);
});

prova("i programmi rimasti aperti si chiudono a fine ciclo", async () => {
  const { f, log } = ambiente({
    desktop: {
      start_local_program: (a) => ({ program_id: a.programId, running: true, windows: [], screenshot: null, done: [] }),
      stop_local_program: { running: false, exit_code: 0, stdout: "", stderr: "", duration_ms: 1 },
    },
    passi: [
      [["sessione", { session_id: "s1" }], ["strumenti", { session_id: "s1", chiamate: [{ id: "a0", name: "apri_programma", arguments: { programma: "node" } }] }]],
      [["sessione", { session_id: "s1" }], ["", { token: "fatto" }], ["done", {}]],
    ] });
  await f.runLocalWorkspaceAgent("prova", WS, { textContent: "" }, null);
  assert.ok(log.invoke.some(([c]) => c === "stop_local_program"));
});

prova("il ciclo: compito, strumenti al PC, risultati con la sessione, risposta", async () => {
  const { f, log } = ambiente({
    desktop: { local_workspace_search: { query: "run", scanned_files: 3, truncated: false, matches: [] } },
    passi: [
      [["sessione", { session_id: "s1" }], ["info", { message: "🔎 Cerco «run»" }],
       ["strumenti", { session_id: "s1", chiamate: [{ id: "c0", name: "cerca", arguments: { testo: "run" } }] }]],
      [["sessione", { session_id: "s1" }], ["", { token: "Non c'e' run()." }], ["salvato", { assistant_index: 3 }], ["done", {}]],
    ] });
  const nodo = { textContent: "", parentElement: null };
  await f.runLocalWorkspaceAgent("analizza", WS, nodo, null);
  assert.equal(log.richieste.length, 2);
  assert.deepEqual(log.richieste[0].corpo, { project_path: "/w/spyengine", chat_id: "c1", task: "analizza", reasoning_effort: null });
  assert.deepEqual(log.richieste[1].corpo.results, [{ id: "c0", content: "nessun risultato per 'run' (3 file letti)" }]);
  assert.equal(log.richieste[1].corpo.session_id, "s1");
  assert.ok(!("task" in log.richieste[1].corpo));
  assert.ok(log.eventi.includes("info") && !log.eventi.includes("strumenti"), "strumenti e sessione non si disegnano");
  assert.equal(nodo.reso, "Non c'e' run().");
  assert.equal(nodo._chiusa, true, "a fine ciclo la risposta e' chiusa: niente piu' testo attenuato");
  assert.equal(log.training[0].outcome, "analysis_completed");
});

prova("uno stream che finisce senza done ne' strumenti e' un errore, non un silenzio", async () => {
  const { f } = ambiente({ passi: [[["sessione", { session_id: "s1" }]]] });
  await assert.rejects(f.runLocalWorkspaceAgent("x", WS, { textContent: "" }, null), /senza concludere/);
});

let fallite = 0;
for (const [nome, fn] of prove) {
  try { await fn(); console.log(`ok   ${nome}`); } catch (err) { fallite += 1; console.log(`FAIL ${nome}\n     ${err.stack || err}`); }
}
console.log(`\n${prove.length - fallite}/${prove.length} prove passate`);
process.exit(fallite ? 1 : 0);
