// Il controllo degli aggiornamenti dell'app (22/10): non solo all'avvio, ogni
// 30 minuti e al ritorno sulla finestra; «Più tardi» nasconde quella versione.
// La funzione vera estratta da desktop_bootstrap.js, con un DOM finto.
import fs from "node:fs";
import path from "node:path";
import assert from "node:assert/strict";

const sorgente = fs.readFileSync(path.join(process.cwd(), "devin/ui/static/js/desktop_bootstrap.js"), "utf8");
const inizio = sorgente.indexOf("const UPDATE_EVERY_MS");
const fine = sorgente.indexOf("\n}\n", sorgente.indexOf("async function checkForUpdate")) + 3;
assert.ok(inizio > 0 && fine > inizio, "checkForUpdate non trovata");
assert.match(sorgente, /setInterval\(\(\) => void checkForUpdate\(\), UPDATE_EVERY_MS\)/, "controllo periodico");
assert.match(sorgente, /addEventListener\("focus", \(\) => void checkForUpdate\(\)\)/, "controllo al ritorno sulla finestra");

function ambiente(versioni) {
  const chiamate = [];
  let banner = null;
  const nodo = (testo = "") => ({ testo, figli: [], style: {}, setAttribute() {}, appendChild(f) { this.figli.push(f); }, remove() { banner = null; } });
  const stub = {
    tauriInvoke: async (cmd) => {
      chiamate.push(cmd);
      const v = versioni.shift();
      return v ? { available: { version: v }, current: "0.2.59" } : { available: null, current: "0.2.59" };
    },
    element: (tag, cls, testo) => nodo(testo),
    action: (label, variant, handler) => ({ ...nodo(label), label, click: handler }),
    errorText: String,
    document: {
      getElementById: (id) => (id === "devin-update-banner" ? banner : null),
      body: { appendChild: (b) => { banner = b; } },
    },
  };
  const corpo = sorgente.slice(inizio, fine) + "\nreturn { checkForUpdate };";
  const f = new Function(...Object.keys(stub), corpo)(...Object.values(stub));
  const bottone = (label) => {
    const cerca = (n) => n.label === label ? n : n.figli?.map(cerca).find(Boolean);
    return cerca(banner);
  };
  return { f, chiamate, banner: () => banner, bottone };
}

const MIN = 60 * 1000;
const { f, chiamate, banner, bottone } = ambiente(["0.2.61", "0.2.61", "0.2.61", "0.2.62"]);
await f.checkForUpdate(1_000_000);
assert.ok(banner(), "all'avvio avvisa");
await f.checkForUpdate(1_000_000 + 5 * MIN);
assert.equal(chiamate.length, 1, "non piu' di un controllo ogni 30 minuti");
bottone("Più tardi").click();
assert.equal(banner(), null);
await f.checkForUpdate(1_000_000 + 31 * MIN);
assert.equal(chiamate.length, 2, "dopo 30 minuti ricontrolla");
assert.equal(banner(), null, "la versione rimandata non riavvisa");
await f.checkForUpdate(1_000_000 + 62 * MIN);
assert.equal(banner(), null);
await f.checkForUpdate(1_000_000 + 93 * MIN);
assert.ok(banner(), "una versione piu' nuova riavvisa");
console.log("ok   aggiornamento: all'avvio, ogni 30 minuti, «Più tardi» per versione");
