// Il markdown delle risposte di DEVIN: parser e resa contro un DOM finto.
// Il DOM finto NON ha innerHTML: se la resa provasse a usarlo, il test cade.
import test from "node:test";
import assert from "node:assert/strict";

import { parseInline, parseMarkdown, renderMarkdownInto } from "./devin/ui/static/js/chat_markdown.js";

class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.childNodes = [];
    this.className = "";
    this.dataset = {};
    this._text = "";
  }
  get firstChild() { return this.childNodes[0] ?? null; }
  appendChild(n) { this.childNodes.push(n); return n; }
  removeChild(n) { this.childNodes.splice(this.childNodes.indexOf(n), 1); return n; }
  set textContent(v) { this.childNodes = [new Txt(String(v))]; }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(""); }
  set innerHTML(_v) { throw new Error("innerHTML usato: vietato per testo del modello"); }
}
class Txt {
  constructor(t) { this.nodeName = "#text"; this.textContent = t; }
}
const doc = { createElement: (t) => new El(t), createTextNode: (t) => new Txt(t) };

function render(md) {
  const root = new El("div");
  renderMarkdownInto(root, md, doc);
  return root;
}
const tags = (el) => el.childNodes.map((c) => c.tagName ?? "#text");

test("il caso reale: titoli, grassetto, tabella, elenco, codice", () => {
  const md = [
    "## Piano",
    "",
    "**Comandi (argparse subparsers):**",
    "",
    "| Comando | Esempio |",
    "|---|---|",
    "| `add` | `python scadenze.py add \"Bolletta\" --mensile` |",
    "| `paga` | `python scadenze.py paga <id>` |",
    "",
    "- `add` con `--mensile` setta `ricorrente=True`.",
    "- Persistenza: read-modify-write su `scadenze.json`.",
    "",
    "---",
    "",
    "```python",
    "def f():",
    "    return \"**non grassetto**\"",
    "```",
  ].join("\n");
  const root = render(md);
  assert.deepEqual(tags(root), ["H4", "P", "DIV", "UL", "HR", "PRE"]);
  assert.equal(root.childNodes[1].childNodes[0].tagName, "STRONG");
  const table = root.childNodes[2].childNodes[0];
  assert.equal(table.tagName, "TABLE");
  const firstRow = table.childNodes[1].childNodes[0];
  assert.equal(firstRow.childNodes[1].textContent, "python scadenze.py add \"Bolletta\" --mensile");
  assert.equal(firstRow.childNodes[1].childNodes[0].tagName, "CODE");
  assert.equal(table.childNodes[1].childNodes[1].childNodes[1].textContent, "python scadenze.py paga <id>");
  const code = root.childNodes[5].childNodes[0];
  assert.equal(code.dataset.lang, "python");
  assert.equal(code.textContent, "def f():\n    return \"**non grassetto**\"");
});

test("l'HTML del modello resta testo, mai markup", () => {
  const root = render("<img src=x onerror=alert(1)> **<b>ciao</b>**");
  const p = root.childNodes[0];
  assert.equal(p.tagName, "P");
  assert.equal(p.textContent, "<img src=x onerror=alert(1)> <b>ciao</b>");
  assert.deepEqual(tags(p), ["#text", "STRONG"]);
  assert.deepEqual(tags(p.childNodes[1]), ["#text"]);
});

test("i link markdown non diventano cliccabili", () => {
  const root = render("[clicca](javascript:alert(1))");
  assert.deepEqual(tags(root.childNodes[0]), ["#text"]);
});

test("un blocco di codice non chiuso (stream a meta') resta codice", () => {
  const blocks = parseMarkdown("ecco:\n```js\nconst a = 1;\n# non titolo");
  assert.equal(blocks.length, 2);
  assert.equal(blocks[1].type, "code");
  assert.equal(blocks[1].closed, false);
  assert.equal(blocks[1].text, "const a = 1;\n# non titolo");
});

test("elenco numerato conserva l'inizio e le righe di continuazione", () => {
  const [list] = parseMarkdown("3. tre\n   continua\n4. quattro");
  assert.equal(list.type, "list");
  assert.equal(list.ordered, true);
  assert.equal(list.start, 3);
  assert.deepEqual(list.items, ["tre continua", "quattro"]);
});

test("paragrafo su piu' righe tiene gli a capo", () => {
  const root = render("riga uno\nriga due");
  assert.deepEqual(tags(root.childNodes[0]), ["#text", "BR", "#text"]);
});

test("inline: codice protegge gli asterischi, grassetto e corsivo annidati", () => {
  assert.deepEqual(parseInline("`a*b*c`"), [{ type: "code", text: "a*b*c" }]);
  const [strong] = parseInline("**forte *e corsivo* ancora**");
  assert.equal(strong.type, "strong");
  assert.equal(strong.children[1].type, "em");
  // snake_case e moltiplicazioni non diventano corsivo
  assert.deepEqual(parseInline("pagata_il e 2 * 3 * 4"), [{ type: "text", text: "pagata_il e 2 * 3 * 4" }]);
});

test("la resa svuota il contenitore (ri-resa idempotente)", () => {
  const root = new El("div");
  renderMarkdownInto(root, "# uno", doc);
  renderMarkdownInto(root, "# uno", doc);
  assert.equal(root.childNodes.length, 1);
});

test("tabella con riga corta non perde colonne", () => {
  const root = render("| a | b |\n|---|---|\n| solo |");
  const tr = root.childNodes[0].childNodes[0].childNodes[1].childNodes[0];
  assert.equal(tr.childNodes.length, 2);
  assert.equal(tr.childNodes[1].textContent, "");
});

test("un trattino non e' una riga orizzontale, tre si'", () => {
  assert.equal(parseMarkdown("- voce")[0].type, "list");
  assert.equal(parseMarkdown("---")[0].type, "hr");
  assert.equal(parseMarkdown("* * *")[0].type, "hr");
});

test("le formule LaTeX diventano testo con i simboli veri (copia-incolla del 17/10)", async () => {
  const { latexToText } = await import("./devin/ui/static/js/chat_markdown.js");
  assert.equal(latexToText("20 \\text{ turni totali} \\div 4 \\text{ squadre} = 5 \\text{ turni pro capite}"),
    "20 turni totali ÷ 4 squadre = 5 turni pro capite");
  assert.equal(latexToText("\\frac{a+b}{2} \\le \\sqrt{x^2 + 1}"), "(a+b)/2 ≤ √(x² + 1)");
  assert.equal(latexToText("30^\\circ, x_{10}, 2^{n+1}"), "30°, x₁₀, 2ⁿ⁺¹");
  assert.equal(latexToText("\\left( \\frac{7}{21} \\right) \\times 100 \\approx 33\\%"), "( 7/21 ) × 100 ≈ 33%");
  assert.equal(latexToText("\\comandoignoto{x}"), "comandoignotox");
});

test("formula in linea e a blocco, sempre senza innerHTML", () => {
  const root = render("ciascuna fa 5 turni (\\(20 \\div 4 = 5\\)).\n\n\\[\n\\frac{1}{2}\n\\]\n\n$$a \\times b$$");
  const [p, blocco, blocco2] = root.childNodes;
  const span = p.childNodes.find((c) => c.className === "md-math");
  assert.equal(span.textContent, "20 ÷ 4 = 5");
  assert.equal(blocco.className, "md-math-block");
  assert.equal(blocco.textContent, "1/2");
  assert.equal(blocco2.textContent, "a × b");
  // un prezzo con il dollaro non e' una formula
  assert.equal(render("costa $5 o $6").textContent, "costa $5 o $6");
});
