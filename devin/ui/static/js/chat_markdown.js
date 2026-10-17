// Markdown delle risposte di DEVIN, reso SENZA innerHTML.
//
// Il testo arriva dal modello: non e' fidato. Qui niente stringhe HTML, niente
// link cliccabili, niente immagini: solo nodi creati con createElement e testo
// messo con textContent. Il peggio che un modello puo' fare e' formattare male.
//
// Copre quello che DEVIN scrive davvero: blocchi ``` (anche non chiusi, mentre
// scrive), titoli #, elenchi puntati e numerati, tabelle |a|b|, righe ---,
// **grassetto**, *corsivo*, `codice`, e le formule LaTeX che il modello scrive
// per abitudine (\( ... \), \[ ... \], $$ ... $$): diventano testo leggibile
// con i simboli veri (20 turni ÷ 4 squadre, x², a/b, ≤, √) senza librerie e
// sempre con textContent. Il resto resta testo semplice.

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

// --- formule: da LaTeX a testo con i simboli giusti ---------------------------
const SIMBOLI = {
  div: "÷", times: "×", cdot: "·", pm: "±", mp: "∓", le: "≤", leq: "≤", ge: "≥", geq: "≥",
  ne: "≠", neq: "≠", approx: "≈", sim: "∼", simeq: "≃", equiv: "≡", propto: "∝", to: "→",
  rightarrow: "→", Rightarrow: "⇒", leftarrow: "←", Leftarrow: "⇐", leftrightarrow: "↔",
  iff: "⇔", implies: "⇒", infty: "∞", sum: "Σ", prod: "Π", int: "∫", partial: "∂",
  nabla: "∇", in: "∈", notin: "∉", cup: "∪", cap: "∩", subset: "⊂", subseteq: "⊆",
  forall: "∀", exists: "∃", emptyset: "∅", degree: "°", circ: "°", ldots: "…", dots: "…",
  cdots: "⋯", lfloor: "⌊", rfloor: "⌋", lceil: "⌈", rceil: "⌉", langle: "⟨", rangle: "⟩",
  alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", varepsilon: "ε", zeta: "ζ",
  eta: "η", theta: "θ", lambda: "λ", mu: "μ", nu: "ν", xi: "ξ", pi: "π", rho: "ρ",
  sigma: "σ", tau: "τ", phi: "φ", varphi: "φ", chi: "χ", psi: "ψ", omega: "ω",
  Gamma: "Γ", Delta: "Δ", Theta: "Θ", Lambda: "Λ", Pi: "Π", Sigma: "Σ", Phi: "Φ",
  Psi: "Ψ", Omega: "Ω", quad: "  ", qquad: "    ", ",": " ", ";": " ", ":": " ", "!": "",
  "%": "%", "$": "$", "{": "{", "}": "}", "&": "&", "#": "#", "_": "_", " ": " ",
};
const APICI = { 0: "⁰", 1: "¹", 2: "²", 3: "³", 4: "⁴", 5: "⁵", 6: "⁶", 7: "⁷", 8: "⁸", 9: "⁹",
  "+": "⁺", "-": "⁻", "=": "⁼", "(": "⁽", ")": "⁾", n: "ⁿ", i: "ⁱ" };
const PEDICI = { 0: "₀", 1: "₁", 2: "₂", 3: "₃", 4: "₄", 5: "₅", 6: "₆", 7: "₇", 8: "₈", 9: "₉",
  "+": "₊", "-": "₋", "=": "₌", "(": "₍", ")": "₎" };

// L'argomento {…} che parte in `pos` (graffe annidate), o un solo carattere.
function argomento(src, pos) {
  while (src[pos] === " ") pos += 1;
  if (src[pos] === "\\") {
    const comando = src.slice(pos).match(/^\\([A-Za-z]+|.)/)?.[0] ?? "\\";
    return [comando, pos + comando.length];
  }
  if (src[pos] !== "{") return [src[pos] ?? "", pos + 1];
  let depth = 0;
  for (let i = pos; i < src.length; i += 1) {
    if (src[i] === "{") depth += 1;
    else if (src[i] === "}") { depth -= 1; if (!depth) return [src.slice(pos + 1, i), i + 1]; }
  }
  return [src.slice(pos + 1), src.length];
}

const semplice = (t) => /^[\w.²³]+$/.test(t);
const conMappa = (t, mappa, segno) => ([...t].every((c) => mappa[c]) ? [...t].map((c) => mappa[c]).join("")
  : `${segno}${t.length > 1 ? `(${t})` : t}`);

export function latexToText(latex) {
  const src = String(latex ?? "");
  let out = "";
  for (let i = 0; i < src.length;) {
    const c = src[i];
    if (c === "\\") {
      const nome = src.slice(i + 1).match(/^([A-Za-z]+|.)/)?.[1] ?? "";
      i += 1 + nome.length;
      if (["text", "mathrm", "textrm", "textbf", "mathbf", "textit", "mathit", "operatorname",
        "mbox", "boldsymbol", "mathsf", "texttt"].includes(nome)) {
        const [arg, fine] = argomento(src, i);
        out += latexToText(arg);
        i = fine;
      } else if (nome === "frac" || nome === "dfrac" || nome === "tfrac") {
        const [num, f1] = argomento(src, i);
        const [den, f2] = argomento(src, f1);
        const a = latexToText(num);
        const b = latexToText(den);
        out += `${semplice(a) ? a : `(${a})`}/${semplice(b) ? b : `(${b})`}`;
        i = f2;
      } else if (nome === "sqrt") {
        let indice = "";
        if (src[i] === "[") { const fine = src.indexOf("]", i); indice = src.slice(i + 1, fine); i = fine + 1; }
        const [arg, fine] = argomento(src, i);
        const t = latexToText(arg);
        out += `${indice ? conMappa(indice, APICI, "^") : ""}√${semplice(t) ? t : `(${t})`}`;
        i = fine;
      } else if (nome === "left" || nome === "right" || nome === "big" || nome === "Big"
        || nome === "bigl" || nome === "bigr" || nome === "displaystyle") {
        // solo dimensione: il delimitatore che segue resta
      } else if (Object.hasOwn(SIMBOLI, nome)) {
        out += SIMBOLI[nome];
      } else {
        out += nome;            // comando sconosciuto: almeno il nome, senza barra
      }
      continue;
    }
    if (c === "^" || c === "_") {
      const [arg, fine] = argomento(src, i + 1);
      const t = latexToText(arg);
      out += t === "°" ? "°" : conMappa(t, c === "^" ? APICI : PEDICI, c);
      i = fine;
      continue;
    }
    if (c === "{" || c === "}") { i += 1; continue; }
    out += c;
    i += 1;
  }
  return out.replace(/ {2,}/g, (m) => (m.length >= 4 ? m : " "));
}

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
    const apre = line.trim().match(/^(\\\[|\$\$)(.*)$/);
    if (apre) {
      // formula a blocco: \[ ... \] o $$ ... $$, anche su piu' righe
      const chiusura = apre[1] === "$$" ? "$$" : "\\]";
      let corpo = apre[2];
      let fine = corpo.indexOf(chiusura);
      while (fine === -1 && i + 1 < lines.length) {
        i += 1;
        corpo += `\n${lines[i]}`;
        fine = corpo.indexOf(chiusura);
      }
      flushPara();
      blocks.push({ type: "math", text: latexToText((fine === -1 ? corpo : corpo.slice(0, fine)).trim()) });
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
  const re = /\\\(([\s\S]+?)\\\)|\$\$([\s\S]+?)\$\$|(`+)([\s\S]*?[^`])\3(?!`)|\*\*(?=\S)([\s\S]*?\S)\*\*|__(?=\S)([\s\S]*?\S)__|\*(?=[^\s*])([^*\n]*?[^\s*])\*|(?<![\w])_(?=[^\s_])([^_\n]*?[^\s_])_(?![\w])/g;
  let last = 0;
  for (const m of src.matchAll(re)) {
    if (m.index > last) out.push({ type: "text", text: src.slice(last, m.index) });
    if (m[1] !== undefined || m[2] !== undefined) out.push({ type: "math", text: latexToText((m[1] ?? m[2]).trim()) });
    else if (m[3]) out.push({ type: "code", text: m[4].replace(/^ (.*) $/, "$1") });
    else if (m[5] !== undefined || m[6] !== undefined) out.push({ type: "strong", children: parseInline(m[5] ?? m[6]) });
    else out.push({ type: "em", children: parseInline(m[7] ?? m[8]) });
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
    if (tok.type === "math") {
      const span = doc.createElement("span");
      span.className = "md-math";
      span.textContent = tok.text;
      parent.appendChild(span);
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
    } else if (block.type === "math") {
      el = doc.createElement("div");
      el.className = "md-math-block";
      el.textContent = block.text;
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
