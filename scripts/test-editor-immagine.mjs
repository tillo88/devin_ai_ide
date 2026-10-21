// Disegno sulle immagini (21/10), provato in Chromium vero: le funzioni vere
// estratte da codex_app.js, un PNG bianco allegato, segni col mouse, salvataggio.
// Uso: PLAYWRIGHT_MODULE=<percorso>/playwright/index.mjs node scripts/test-editor-immagine.mjs
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const modulo = process.env.PLAYWRIGHT_MODULE || "playwright";
const { chromium } = await import(/^[A-Za-z]:[\\/]/.test(modulo) || modulo.startsWith("/") ? pathToFileURL(modulo).href : modulo);
const radice = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sorgente = fs.readFileSync(path.join(radice, "devin/ui/static/js/codex_app.js"), "utf8");
const estrai = (nome) => {
  const i = sorgente.search(new RegExp(`^(async )?function ${nome}\\(`, "m"));
  assert.notEqual(i, -1, nome);
  return sorgente.slice(i, sorgente.indexOf("\n}\n", i) + 3);
};
const costante = (nome) => sorgente.match(new RegExp(`^const ${nome} = .*;$`, "m"))[0];
const codice = [
  "const $ = (id) => document.getElementById(id);",
  "window.testi = ['ATTENZIONE'];",
  "const promptModal = async () => window.testi.shift() ?? null;",
  costante("IMMAGINE_ALLEGATO_RE"), costante("COLORI_DISEGNO"), costante("LATO_MASSIMO_DISEGNO"),
  ...["selectedChatFiles", "eImmagine", "formatoDimensione", "formatFileLabel", "apriImmagine", "schedaAllegato",
    "renderAllegati", "togliAllegato", "sostituisciAllegato", "nomeSegnato", "apriEditorImmagine",
    "costruisciEditorImmagine"].map(estrai),
  "window.renderAllegati = renderAllegati;",
].join("\n");

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1000, height: 800 } });
const errori = [];
page.on("pageerror", (e) => errori.push(e.message));
await page.setContent(`<!doctype html><body style="margin:0;background:#222">
  <input type="file" id="chat-file" multiple><span id="chat-file-label"></span><div id="chat-attachments"></div>
  <style>.editor-immagine{position:fixed;inset:0;display:flex;flex-direction:column;align-items:center}
  .editor-immagine-tela canvas{width:400px;height:300px}</style></body>`);
await page.addScriptTag({ content: codice });
// un PNG bianco 400x300 allegato come lo allegherebbe l'utente
await page.evaluate(async () => {
  const c = document.createElement("canvas");
  c.width = 400; c.height = 300;
  const x = c.getContext("2d"); x.fillStyle = "#fff"; x.fillRect(0, 0, 400, 300);
  const blob = await new Promise((r) => c.toBlob(r, "image/png"));
  const dt = new DataTransfer();
  dt.items.add(new File([blob], "schermata.png", { type: "image/png" }));
  dt.items.add(new File(["testo"], "note.txt", { type: "text/plain" }));
  document.getElementById("chat-file").files = dt.files;
  window.renderAllegati();
});
assert.equal(await page.locator(".chat-attachment-draw").count(), 1, "la matita solo sulle immagini");
await page.locator(".chat-attachment-draw").click();
const tela = page.locator(".editor-immagine canvas");
await tela.waitFor();
const box = await tela.boundingBox();
const a = (x, y) => [box.x + x * box.width / 400, box.y + y * box.height / 300];

// penna: una riga orizzontale a y=50
await page.mouse.move(...a(40, 50)); await page.mouse.down(); await page.mouse.move(...a(200, 50), { steps: 8 }); await page.mouse.up();
// annulla toglie solo l'ultimo segno: un secondo tratto a y=250, poi annulla
await page.mouse.move(...a(40, 250)); await page.mouse.down(); await page.mouse.move(...a(200, 250), { steps: 8 }); await page.mouse.up();
await page.locator('[data-annulla]').click();
// riquadro con il secondo colore (giallo)
await page.locator('[data-strumento="riquadro"]').click();
await page.locator('[data-colore]').click();
await page.mouse.move(...a(250, 100)); await page.mouse.down(); await page.mouse.move(...a(350, 200), { steps: 5 }); await page.mouse.up();
// testo
await page.locator('[data-strumento="testo"]').click();
await page.mouse.click(...a(60, 150));
await page.waitForTimeout(100);
await page.locator('[data-salva]').click();
await page.waitForFunction(() => !document.querySelector(".editor-immagine"));

const esito = await page.evaluate(async () => {
  const files = [...document.getElementById("chat-file").files];
  const img = await createImageBitmap(files[0]);
  const c = document.createElement("canvas"); c.width = img.width; c.height = img.height;
  const x = c.getContext("2d"); x.drawImage(img, 0, 0);
  const px = (u, v) => [...x.getImageData(u, v, 1, 1).data].slice(0, 3);
  let scritto = 0;
  const d = x.getImageData(60, 120, 200, 40).data;
  // pixel del colore del testo (giallo, il colore scelto), non solo il contorno scuro
  for (let i = 0; i < d.length; i += 4) if (d[i] > 200 && d[i + 1] > 170 && d[i + 2] < 100) scritto += 1;
  return { nomi: files.map((f) => f.name), tipo: files[0].type, lato: [img.width, img.height],
           riga: px(120, 50), annullata: px(120, 250), riquadro: px(250, 150), dentro: px(300, 150), scritto,
           schede: document.querySelectorAll("#chat-attachments .chat-attachment").length };
});
assert.deepEqual(esito.nomi, ["schermata-segnato.png", "note.txt"], "sostituisce solo l'immagine, nello stesso posto");
assert.equal(esito.tipo, "image/png");
assert.deepEqual(esito.lato, [400, 300]);
assert.ok(esito.riga[0] > 200 && esito.riga[1] < 120 && esito.riga[2] < 120, `penna rossa: ${esito.riga}`);
assert.deepEqual(esito.annullata, [255, 255, 255], "il tratto annullato non c'e'");
assert.ok(esito.riquadro[0] > 200 && esito.riquadro[1] > 170 && esito.riquadro[2] < 100, `riquadro giallo: ${esito.riquadro}`);
assert.deepEqual(esito.dentro, [255, 255, 255], "il riquadro e' un contorno, non copre la zona");
assert.ok(esito.scritto > 100, `il testo e' stato scritto nel colore scelto (${esito.scritto} pixel)`);
assert.equal(esito.schede, 2);
assert.deepEqual(errori, []);
await browser.close();
console.log("PASS: disegno sulle immagini (penna, annulla, riquadro, colore, testo, sostituzione allegato)");
