// Le immagini restano nella chat (21/10): dalla riga nello storico l'app
// ridisegna la miniatura chiedendo l'immagine al rig. Funzioni vere estratte.
import fs from "node:fs";
import path from "node:path";
import assert from "node:assert/strict";

const sorgente = fs.readFileSync(path.join(process.cwd(), "devin/ui/static/js/codex_app.js"), "utf8");
const estrai = (nome) => {
  const i = sorgente.search(new RegExp(`^(async )?function ${nome}\\(`, "m"));
  assert.notEqual(i, -1, nome);
  return sorgente.slice(i, sorgente.indexOf("\n}\n", i) + 3);
};
const regex = sorgente.match(/^const NOTA_IMMAGINE_RE = .*;$/m)[0];

class Nodo {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.parentElement = null; this.textContent = ""; this.ascolti = {}; }
  appendChild(n) { n.parentElement = this; this.children.push(n); return n; }
  remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter((c) => c !== this); }
  addEventListener(tipo, fn) { this.ascolti[tipo] = fn; }
}
const richieste = [];
const risposte = {};
const stub = {
  document: { createElement: (t) => new Nodo(t), body: new Nodo("body") },
  URL: { createObjectURL: (b) => `blob:${b.nome}` },
  formatoDimensione: (n) => `${n} B`,
  apiFetch: async (url) => {
    richieste.push(url);
    const id = new URL(url, "http://x").searchParams.get("id");
    return risposte[id] ? { ok: true, blob: async () => ({ nome: id }) } : { ok: false, blob: async () => null };
  },
};
const corpo = `${regex}\n${["separaImmaginiAllegate", "apriImmagine", "mostraImmaginiStorico"].map(estrai).join("\n")}
return { separaImmaginiAllegate, mostraImmaginiStorico };`;
const f = new Function(...Object.keys(stub), corpo)(...Object.values(stub));
const attesa = () => new Promise((r) => setTimeout(r, 0));

// la stessa riga che scrive il backend (_image_note): test_chat_immagini.py fissa il formato
const nota = (nome, id) => `[Immagine allegata: ${nome} | image/png | size=2048 bytes | sha256=${id}... — il modello la vede in questo messaggio]`;
const A = "0123456789abcdef";
const B = "fedcba9876543210";

const separato = f.separaImmaginiAllegate(`${nota("1234.png", A)}\n${nota("schizzo.png", B)}\n\ncosa vedi?`);
assert.equal(separato.testo, "cosa vedi?", "nella bolla resta solo cio' che hai scritto");
assert.deepEqual(separato.immagini.map((i) => [i.nome, i.id, i.size]), [["1234.png", A, 2048], ["schizzo.png", B, 2048]]);
assert.deepEqual(f.separaImmaginiAllegate("[Immagine allegata: senza formato]").immagini, [], "righe diverse restano testo");

risposte[A] = true;
const article = new Nodo("article");
const body = article.appendChild(new Nodo("div"));
f.mostraImmaginiStorico(body, separato.immagini, "/w/p", "chat_1");
await attesa(); await attesa();
const riga = article.children[1];
assert.equal(riga.className, "chat-attachments sent");
const [prima, seconda] = riga.children;
assert.equal(prima.children[0].src, `blob:${A}`, "l'immagine torna dal rig");
assert.ok(prima.children[0].ascolti.click, "clic per ingrandire");
assert.match(richieste[0], /^\/api\/chat\/allegato\?id=0123456789abcdef&project_path=%2Fw%2Fp&chat_id=chat_1$/);
assert.equal(seconda.children.length, 1, "se il rig non l'ha piu', niente immagine rotta");
assert.match(seconda.children[0].textContent, /schizzo\.png · 2048 B · non piu' disponibile/);
console.log("ok: immagini nello storico della chat (4 controlli)");
