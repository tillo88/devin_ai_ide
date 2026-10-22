// La finestrella di DEVIN (22/10): il float.js vero con un documento finto e
// un bus finto degli eventi di Tauri. Sorveglia: disegna lo stato che riceve,
// mostra solo foto PNG, e rimanda alla finestra principale solo le scelte
// (una risposta per domanda, Ferma, "pronta" all'avvio).
import fs from "node:fs";
import path from "node:path";
import assert from "node:assert/strict";
import vm from "node:vm";

const sorgente = fs.readFileSync(path.join(process.cwd(), "devin/ui/static/js/float.js"), "utf8");
const html = fs.readFileSync(path.join(process.cwd(), "devin/ui/static/float.html"), "utf8");

function documento() {
  const nodi = {};
  for (const id of ["programmi", "azione", "foto", "domanda", "domanda-testo", "consenti", "no", "ferma"]) {
    assert.ok(html.includes(`id="${id}"`), `float.html senza #${id}`);
    nodi[id] = { id, textContent: "", hidden: true, src: "", dataset: {}, clic: [],
      addEventListener(tipo, fn) { if (tipo === "click") this.clic.push(fn); },
      click() { for (const fn of this.clic) fn(); } };
  }
  return { nodi, getElementById: (id) => nodi[id] };
}

async function carica() {
  const doc = documento();
  const bus = { mandati: [], ascoltatori: [] };
  const finestra = {
    document: doc,
    __TAURI__: { event: {
      // JSON: gli oggetti nascono in un altro contesto vm (prototipi diversi)
      emitTo: async (dove, nome, dati) => { bus.mandati.push([dove, nome, JSON.parse(JSON.stringify(dati))]); },
      listen: async (nome, fn) => { bus.ascoltatori.push([nome, fn]); return () => {}; },
    } },
  };
  const contesto = { window: finestra, globalThis: {} };
  contesto.globalThis = contesto;
  vm.runInNewContext(sorgente, contesto);
  await new Promise((fatto) => setTimeout(fatto, 0));
  const arriva = (stato) => { for (const [nome, fn] of bus.ascoltatori) if (nome === "devin-float") fn({ payload: stato }); };
  return { doc, bus, arriva, f: contesto.__devinFloat };
}

const prove = [];
const prova = (nome, fn) => prove.push([nome, fn]);

prova("all'avvio ascolta lo stato e dice alla finestra principale che e' pronta", async () => {
  const { bus } = await carica();
  assert.deepEqual(bus.ascoltatori.map(([nome]) => nome), ["devin-float"]);
  assert.deepEqual(bus.mandati, [["main", "devin-float-risposta", { risposta: "pronta" }]]);
});

prova("disegna programmi, azione, foto e domanda", async () => {
  const { doc, arriva } = await carica();
  arriva({ programmi: [{ titolo: "SpyEngine" }], azione: "clic in (40, 60)", foto: "data:image/png;base64,AAA",
    domanda: { id: "q1", testo: "DEVIN vuole usare «SpyEngine»… Consenti?" } });
  const n = doc.nodi;
  assert.equal(n.programmi.textContent, "usa «SpyEngine»");
  assert.equal(n.azione.textContent, "clic in (40, 60)");
  assert.equal(n.foto.hidden, false);
  assert.equal(n.foto.src, "data:image/png;base64,AAA");
  assert.equal(n.domanda.hidden, false);
  assert.equal(n.domanda.dataset.id, "q1");
  assert.match(n["domanda-testo"].textContent, /Consenti\?/);
  arriva({ programmi: [{ titolo: "SpyEngine" }], azione: "fatto", foto: "javascript:alert(1)", domanda: null });
  assert.equal(n.foto.hidden, true, "solo foto PNG in data URL");
  assert.equal(n.domanda.hidden, true);
  assert.equal(n.domanda.dataset.id, undefined);
});

prova("Consenti manda una risposta sola, No dice no, Ferma ferma", async () => {
  const { doc, bus, arriva } = await carica();
  arriva({ programmi: [{ titolo: "A" }], domanda: { id: "q1", testo: "?" } });
  doc.nodi.consenti.click();
  doc.nodi.consenti.click();   // doppio clic: la seconda volta la domanda non c'e' piu'
  assert.deepEqual(bus.mandati.slice(1), [["main", "devin-float-risposta", { id: "q1", risposta: "consenti" }]]);
  assert.equal(doc.nodi.domanda.hidden, true);
  arriva({ programmi: [{ titolo: "A" }], domanda: { id: "q2", testo: "?" } });
  doc.nodi.no.click();
  assert.deepEqual(bus.mandati.at(-1)[2], { id: "q2", risposta: "no" });
  doc.nodi.ferma.click();
  assert.deepEqual(bus.mandati.at(-1)[2], { risposta: "ferma" });
  assert.match(doc.nodi.azione.textContent, /fermo DEVIN/);
});

prova("fuori dall'app desktop non fa niente", async () => {
  const contesto = { window: { document: documento() } };
  contesto.globalThis = contesto;
  vm.runInNewContext(sorgente, contesto);
  assert.equal(typeof contesto.__devinFloat.disegna, "function");
});

prova("la finestrella non ha comandi dell'app: solo eventi e trascinamento", async () => {
  const capability = JSON.parse(fs.readFileSync(path.join(process.cwd(), "src-tauri/capabilities/float.json"), "utf8"));
  assert.deepEqual(capability.windows, ["devin-float"]);
  assert.deepEqual(capability.permissions, ["core:event:default", "core:window:allow-start-dragging"]);
  const principale = JSON.parse(fs.readFileSync(path.join(process.cwd(), "src-tauri/capabilities/default.json"), "utf8"));
  assert.deepEqual(principale.windows, ["main"], "i comandi restano alla sola finestra principale");
  const rust = fs.readFileSync(path.join(process.cwd(), "src-tauri/src/local_float.rs"), "utf8");
  assert.match(rust, /FLOAT_LABEL: &str = "devin-float"/);
  assert.match(rust, /FLOAT_PAGE: &str = "static\/float\.html"/);
});

let fallite = 0;
for (const [nome, fn] of prove) {
  try {
    await fn();
    console.log(`ok   ${nome}`);
  } catch (errore) {
    fallite += 1;
    console.log(`FAIL ${nome}\n${errore.stack}`);
  }
}
console.log(`\n${prove.length - fallite}/${prove.length} prove passate`);
process.exit(fallite ? 1 : 0);
