// Il pannello del ragionamento, esercitato davvero.
//
// Non ricopia le funzioni: le ESTRAE dal codex_app.js vero e le fa girare
// contro un DOM finto. Ricopiarle vorrebbe dire provare una copia e spedire
// l'originale — il difetto di famiglia di questo progetto.
//
// Il caso che questo banco esiste per sorvegliare: renderAssistantStream()
// veniva chiamata a OGNI token della risposta e, non trovando marcatori
// <think> inline, faceva panel?.remove(). Col pensiero che arriva sull'evento
// SSE invece che inline, avrebbe cancellato il pannello appena riempito.

import fs from "node:fs";
import path from "node:path";
import assert from "node:assert/strict";

const SORGENTE = path.join(process.cwd(), "devin/ui/static/js/codex_app.js");

// --- DOM finto, il minimo che serve ---------------------------------------
class Nodo {
  constructor(tag) {
    this.tagName = (tag || "").toUpperCase();
    this.children = [];
    this.parentElement = null;
    this.className = "";
    this.textContent = "";
    this.isConnected = true;
    this.open = false;
    this.dataset = {};
  }
  set innerHTML(html) {
    // basta riconoscere <summary></summary><pre></pre>
    this.children = [];
    for (const tag of html.matchAll(/<(\w+)>/g)) {
      const n = new Nodo(tag[1]);
      n.parentElement = this;
      this.children.push(n);
    }
  }
  get innerHTML() { return ""; }
  appendChild(n) { n.parentElement = this; this.children.push(n); return n; }
  insertBefore(n, rif) {
    n.parentElement = this;
    const i = this.children.indexOf(rif);
    this.children.splice(i < 0 ? this.children.length : i, 0, n);
    return n;
  }
  remove() {
    this.isConnected = false;
    const p = this.parentElement;
    if (p) p.children.splice(p.children.indexOf(this), 1);
    this.parentElement = null;
  }
  querySelector(sel) {
    const cerca = (nodo) => {
      for (const f of nodo.children) {
        const perClasse = sel.startsWith(".") && f.className.split(/\s+/).includes(sel.slice(1));
        const perTag = !sel.startsWith(".") && f.tagName === sel.toUpperCase();
        if (perClasse || perTag) return f;
        const dentro = cerca(f);
        if (dentro) return dentro;
      }
      return null;
    };
    return cerca(this);
  }
}

const documentFinto = { createElement: (tag) => new Nodo(tag) };

// --- estrazione delle funzioni dal file vero -------------------------------
function estrai(sorgente, nome) {
  const inizio = sorgente.indexOf(`function ${nome}(`);
  assert.notEqual(inizio, -1, `funzione ${nome} non trovata in codex_app.js`);
  const fine = sorgente.indexOf("\n}\n", inizio);
  assert.notEqual(fine, -1, `fine di ${nome} non trovata`);
  return sorgente.slice(inizio, fine + 3);
}

const sorgente = fs.readFileSync(SORGENTE, "utf8");
const NOMI = ["splitReasoning", "reasoningCompleto", "formatDurata", "testoRiepilogo",
  "fermaCronometroRagionamento", "avviaCronometroRagionamento", "renderAssistantStream"];
const corpo = NOMI.map((n) => estrai(sorgente, n)).join("\n");

const fabbrica = new Function("document", "THINK_OPEN", "THINK_CLOSE", "setInterval", "clearInterval",
  `${corpo}\nreturn { splitReasoning, reasoningCompleto, formatDurata, testoRiepilogo,
     fermaCronometroRagionamento, avviaCronometroRagionamento, renderAssistantStream };`);

let intervalliVivi = 0;
const F = fabbrica(documentFinto, "<think>", "</think>",
  () => { intervalliVivi += 1; return intervalliVivi; },
  () => { intervalliVivi -= 1; });

// --- utilità del banco ------------------------------------------------------
function nuovoMessaggio() {
  const article = new Nodo("article");
  const p = article.appendChild(new Nodo("p"));
  return p;
}
const pannelloDi = (nodo) => nodo.parentElement.querySelector(".reasoning-panel");
const testoPannello = (nodo) => pannelloDi(nodo)?.querySelector("pre").textContent ?? null;

let passati = 0;
const prove = [];
function prova(nome, fn) { prove.push([nome, fn]); }

// --- le prove ---------------------------------------------------------------

prova("il pensiero dall'evento SSE finisce nel pannello", () => {
  const n = nuovoMessaggio();
  n._reasoning = "sto pensando";
  F.renderAssistantStream(n, "");
  assert.equal(testoPannello(n), "sto pensando");
  assert.equal(n.textContent, "");
});

prova("LA TRAPPOLA: i token della risposta non cancellano il pannello", () => {
  const n = nuovoMessaggio();
  n._reasoning = "ragionamento arrivato per evento";
  F.renderAssistantStream(n, "");
  assert.ok(pannelloDi(n), "il pannello doveva esserci");
  // ora arriva la risposta, senza nessun marcatore <think> dentro
  n._raw = "def f():";
  F.renderAssistantStream(n, n._raw);
  n._raw += " pass";
  F.renderAssistantStream(n, n._raw);
  assert.ok(pannelloDi(n), "il pannello e' stato cancellato dai token della risposta");
  assert.equal(testoPannello(n), "ragionamento arrivato per evento");
  assert.equal(n.textContent, "def f(): pass");
});

prova("il pensiero inline continua a funzionare come prima", () => {
  const n = nuovoMessaggio();
  F.renderAssistantStream(n, "<think>rifletto</think>ecco");
  assert.equal(testoPannello(n), "rifletto");
  assert.equal(n.textContent, "ecco");
});

prova("le due sorgenti si sommano invece di escludersi", () => {
  const n = nuovoMessaggio();
  n._reasoning = "dall'evento";
  F.renderAssistantStream(n, "<think>inline</think>risposta");
  assert.equal(testoPannello(n), "dall'evento\ninline");
  assert.equal(n.textContent, "risposta");
});

prova("senza nessun pensiero il pannello non compare", () => {
  const n = nuovoMessaggio();
  F.renderAssistantStream(n, "solo risposta");
  assert.equal(pannelloDi(n), null);
  assert.equal(n.textContent, "solo risposta");
});

prova("il riepilogo dice che sta pensando, poi quanto ci ha messo", () => {
  const n = nuovoMessaggio();
  n._reasoning = "x";
  n._reasoningStart = Date.now() - 42000;
  F.renderAssistantStream(n, "");
  const sommario = pannelloDi(n).querySelector("summary").textContent;
  assert.match(sommario, /sta pensando/, `riepilogo inatteso: ${sommario}`);
  assert.match(sommario, /4[12]s/, `durata non mostrata: ${sommario}`);

  n._reasoningDone = { seconds: 102, tokens: 3210 };
  F.renderAssistantStream(n, "");
  const finito = pannelloDi(n).querySelector("summary").textContent;
  assert.match(finito, /1m 42s/, `durata finale sbagliata: ${finito}`);
  assert.match(finito, /3\D?210 token/, `token non mostrati: ${finito}`);
});

prova("il pannello e' aperto mentre pensa e chiuso quando ha finito", () => {
  const a = nuovoMessaggio();
  a._reasoning = "x";
  F.renderAssistantStream(a, "");
  assert.equal(pannelloDi(a).open, true, "doveva essere aperto mentre pensa");

  const b = nuovoMessaggio();
  b._reasoning = "x";
  b._reasoningDone = { seconds: 5, tokens: 1 };
  F.renderAssistantStream(b, "");
  assert.equal(pannelloDi(b).open, false, "doveva essere chiuso a pensiero finito");
});

prova("formatDurata", () => {
  assert.equal(F.formatDurata(0), "0s");
  assert.equal(F.formatDurata(42), "42s");
  assert.equal(F.formatDurata(60), "1m 00s");
  assert.equal(F.formatDurata(1728), "28m 48s");
});

prova("il cronometro si spegne quando il pannello sparisce", () => {
  const n = nuovoMessaggio();
  n._reasoning = "x";
  F.renderAssistantStream(n, "");
  F.avviaCronometroRagionamento(n);
  assert.ok(n._reasoningTimer, "il cronometro doveva partire");
  F.fermaCronometroRagionamento(n);
  assert.equal(n._reasoningTimer, null, "il cronometro doveva fermarsi");
});

prova("un pensiero mai chiuso non diventa risposta", () => {
  const n = nuovoMessaggio();
  F.renderAssistantStream(n, "<think>mi tronco qui");
  assert.equal(n.textContent, "");
  assert.match(testoPannello(n), /mi tronco qui/);
});

// --- esecuzione -------------------------------------------------------------
const rotte = [];
for (const [nome, fn] of prove) {
  try {
    fn();
    passati += 1;
    console.log(`  OK    ${nome}`);
  } catch (err) {
    rotte.push(nome);
    console.log(`  ROTTO ${nome}\n        ${err.message}`);
  }
}
console.log(`\n=== ${prove.length} prove, ${rotte.length} rotte ===`);
if (rotte.length) process.exit(1);
console.log("tutte verdi");
