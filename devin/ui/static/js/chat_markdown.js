// Markdown delle risposte di DEVIN, reso SENZA innerHTML.
//
// Il testo arriva dal modello: non e' fidato. Qui niente stringhe HTML, niente
// link cliccabili, niente immagini: solo nodi creati con createElement e testo
// messo con textContent. Il peggio che un modello puo' fare e' formattare male.
//
// Copre quello che DEVIN scrive davvero: blocchi ``` (anche non chiusi, mentre
// scrive), titoli #, elenchi puntati e numerati, tabelle |a|b|, righe ---,
// **grassetto**, *corsivo*, `codice`. Il resto resta testo semplice.

const FENCE = /^\s*(```|~~~)\s*([\w+#.-]*)\s*$/;
const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const HR = /^\s*([-*_])(\s*\1){2,}\s*$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const ORDERED = /^\s*(\d{1,9})[.)]\s+(.*)$/;
const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function tableCells(line) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  return s.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
}

const isTableRow = (line) => /^\s*\|.*\|\s*$/.test(line);

export function parseMarkdown(text) {
  const lines = String(text ?? "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let para = [];
  const flushPara = () => {
    if (para.length) blocks.push({ type: "para", lines: para });
    para = [];
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const fence = line.match(FENCE);
    if (fence) {
      flushPara();
      const body = [];
      let closed = false;
      for (i += 1; i < lines.length; i += 1) {
        if (lines[i].trim() === fence[1]) { closed = true; break; }
        body.push(lines[i]);
      }
      blocks.push({ type: "code", lang: fence[2] || "", text: body.join("\n"), closed });
      continue;
    }
    if (!line.trim()) { flushPara(); continue; }
    const heading = line.match(HEADING);
    if (heading) {
      flushPara();
      blocks.push({ type: "heading", level: heading[1].length, text: heading[2] });
      continue;
    }
    if (HR.test(line)) {
      flushPara();
      blocks.push({ type: "hr" });
      continue;
    }
    if (isTableRow(line) && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
      flushPara();
      const header = tableCells(line);
      const rows = [];
      for (i += 2; i < lines.length && isTableRow(lines[i]); i += 1) rows.push(tableCells(lines[i]));
      i -= 1;
      blocks.push({ type: "table", header, rows });
      continue;
    }
    const bullet = line.match(BULLET);
    const ordered = line.match(ORDERED);
    if (bullet || ordered) {
      flushPara();
      const isOrdered = Boolean(ordered && !bullet);
      const start = isOrdered ? Number(ordered[1]) : 1;
      const items = [];
      for (; i < lines.length; i += 1) {
        const b = lines[i].match(BULLET);
        const o = lines[i].match(ORDERED);
        const m = isOrdered ? (!b && o) : b;
        if (m) { items.push(isOrdered ? m[2] : m[1]); continue; }
        // riga di continuazione indentata: appartiene all'ultimo punto
        if (lines[i].trim() && /^\s{2,}/.test(lines[i]) && !lines[i].match(FENCE) && items.length) {
          items[items.length - 1] += ` ${lines[i].trim()}`;
          continue;
        }
        break;
      }
      i -= 1;
      blocks.push({ type: "list", ordered: isOrdered, start, items });
      continue;
    }
    para.push(line);
  }
  flushPara();
  return blocks;
}

// Inline: `codice` prima di tutto (dentro non si formatta), poi **forte**, poi *corsivo*/_corsivo_.
export function parseInline(text) {
  const out = [];
  const src = String(text ?? "");
  const re = /(`+)([\s\S]*?[^`])\1(?!`)|\*\*(?=\S)([\s\S]*?\S)\*\*|__(?=\S)([\s\S]*?\S)__|\*(?=[^\s*])([^*\n]*?[^\s*])\*|(?<![\w])_(?=[^\s_])([^_\n]*?[^\s_])_(?![\w])/g;
  let last = 0;
  for (const m of src.matchAll(re)) {
    if (m.index > last) out.push({ type: "text", text: src.slice(last, m.index) });
    if (m[1]) out.push({ type: "code", text: m[2].replace(/^ (.*) $/, "$1") });
    else if (m[3] !== undefined || m[4] !== undefined) out.push({ type: "strong", children: parseInline(m[3] ?? m[4]) });
    else out.push({ type: "em", children: parseInline(m[5] ?? m[6]) });
    last = m.index + m[0].length;
  }
  if (last < src.length) out.push({ type: "text", text: src.slice(last) });
  return out;
}

function appendTokens(doc, parent, tokens) {
  for (const tok of tokens) {
    if (tok.type === "text") {
      parent.appendChild(doc.createTextNode(tok.text));
      continue;
    }
    const el = doc.createElement(tok.type);
    if (tok.type === "code") el.textContent = tok.text;
    else appendTokens(doc, el, tok.children);
    parent.appendChild(el);
  }
}

const appendInline = (doc, parent, text) => appendTokens(doc, parent, parseInline(text));

// Svuota `container` e lo riempie col markdown reso. `doc` serve ai test.
export function renderMarkdownInto(container, text, doc = globalThis.document) {
  while (container.firstChild) container.removeChild(container.firstChild);
  for (const block of parseMarkdown(text)) {
    let el;
    if (block.type === "code") {
      el = doc.createElement("pre");
      el.className = "md-code";
      const code = doc.createElement("code");
      if (block.lang) code.dataset.lang = block.lang;
      code.textContent = block.text;
      el.appendChild(code);
    } else if (block.type === "heading") {
      el = doc.createElement(`h${Math.min(6, block.level + 2)}`);
      el.className = "md-heading";
      appendInline(doc, el, block.text);
    } else if (block.type === "hr") {
      el = doc.createElement("hr");
    } else if (block.type === "list") {
      el = doc.createElement(block.ordered ? "ol" : "ul");
      if (block.ordered && block.start !== 1) el.start = block.start;
      for (const item of block.items) {
        const li = doc.createElement("li");
        appendInline(doc, li, item);
        el.appendChild(li);
      }
    } else if (block.type === "table") {
      el = doc.createElement("div");
      el.className = "md-table-wrap";
      const table = doc.createElement("table");
      const thead = doc.createElement("thead");
      const hr = doc.createElement("tr");
      for (const cell of block.header) {
        const th = doc.createElement("th");
        appendInline(doc, th, cell);
        hr.appendChild(th);
      }
      thead.appendChild(hr);
      table.appendChild(thead);
      const tbody = doc.createElement("tbody");
      for (const row of block.rows) {
        const tr = doc.createElement("tr");
        for (let c = 0; c < block.header.length; c += 1) {
          const td = doc.createElement("td");
          appendInline(doc, td, row[c] ?? "");
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      el.appendChild(table);
    } else {
      el = doc.createElement("p");
      block.lines.forEach((line, idx) => {
        if (idx) el.appendChild(doc.createElement("br"));
        appendInline(doc, el, line);
      });
    }
    container.appendChild(el);
  }
}
