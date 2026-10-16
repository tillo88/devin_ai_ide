import ast
import copy
import re
from devin.ai.client import AIClient
from devin.agents.prompts import (
    CODER_SYSTEM_PROMPT, SCAFFOLD_CODER_SYSTEM_PROMPT, WHOLE_FILE_CODER_SYSTEM_PROMPT,
)

SCAFFOLD_MAX_TOKENS = 12288
# Quanto contesto dei file gia' scritti vede il coder. Il 16/10 i test di
# scadenze.py sono stati chiesti con in mano solo i primi 1500 caratteri del
# modulo (su 9924): 19 test su funzioni che non vedeva, il modello ha
# ragionato fino al tetto e il file e' uscito vuoto.
SCAFFOLD_CONTEXT_CHARS = 12000
CONTESTO_FILE_INTERO = 4000


def _firma(nodo: ast.AST) -> ast.AST:
    """Copia di una def/class con il corpo ridotto a docstring + `...`."""
    corpo = []
    doc = ast.get_docstring(nodo, clean=False) if isinstance(
        nodo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else None
    if doc:
        corpo.append(ast.Expr(ast.Constant(doc)))
    if isinstance(nodo, ast.ClassDef):
        for figlio in nodo.body:
            if isinstance(figlio, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                corpo.append(_firma(figlio))
            elif isinstance(figlio, (ast.AnnAssign, ast.Assign)):
                corpo.append(figlio)
    if not corpo or (doc and len(corpo) == 1 and not isinstance(nodo, ast.ClassDef)):
        corpo.append(ast.Expr(ast.Constant(...)))
    copia = copy.copy(nodo)
    copia.body = corpo
    return copia


def interfaccia_python(sorgente: str) -> str | None:
    """L'interfaccia pubblica di un modulo: import, costanti, firme e docstring.

    None se il sorgente non si analizza: chi chiama lo dice, non inventa.
    """
    try:
        albero = ast.parse(sorgente)
    except SyntaxError:
        return None
    righe = []
    for nodo in albero.body:
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            righe.append(ast.unparse(_firma(nodo)))
        elif isinstance(nodo, (ast.Import, ast.ImportFrom, ast.AnnAssign, ast.Assign)):
            testo = ast.unparse(nodo)
            righe.append(testo if len(testo) <= 300 else testo[:300] + "  # ...")
        elif isinstance(nodo, ast.Expr) and isinstance(getattr(nodo, "value", None), ast.Constant) \
                and isinstance(nodo.value.value, str):
            righe.append(ast.unparse(nodo))
    return "\n".join(righe)


def contesto_file(nome: str, contenuto: str, limite: int = CONTESTO_FILE_INTERO) -> str:
    """Il pezzo di contesto con cui il coder vede un file gia' scritto.

    Intero se e' corto; per un modulo Python lungo, la sua interfaccia
    (firme e docstring di tutto il modulo, non solo della testa); altrimenti
    la testa, dicendo che e' tagliata.
    """
    if len(contenuto) <= limite:
        return contenuto
    if nome.endswith(".py"):
        interfaccia = interfaccia_python(contenuto)
        if interfaccia is not None:
            return ("# (interfaccia del modulo: firme e docstring, corpi omessi)\n"
                    + interfaccia[:limite * 2])
    return contenuto[:limite] + f"\n# ... (tagliato: {len(contenuto)} caratteri in tutto)"


class Coder:
    def __init__(self, ai_client: AIClient):
        self.ai = ai_client

    def _extract_diff(self, raw: str) -> str:
        """Estrae una unified diff valida dalla risposta LLM."""
        raw = raw.strip()
        if not raw:
            return ""

        # 1. Cerca blocco markdown ```diff ... ```
        m = re.search(r'```(?:diff)?\s*\n?(.*?)```', raw, re.DOTALL)
        if m:
            return m.group(1).strip()

        # 2. Cerca inizio diff --git
        if "diff --git" in raw:
            idx = raw.index("diff --git")
            return raw[idx:].strip()

        # 3. Cerca pattern --- / +++
        m = re.search(r'--- .*?\n\+\+\+ .*', raw, re.DOTALL)
        if m:
            start = m.start()
            return raw[start:].strip()

        # Nessuna diff trovata
        return ""

    def generate(self, plan, context, feedback=None) -> str:
        """Modalità 1 (Mantenimento): genera una unified diff su codice esistente."""
        feedback_block = ""
        if feedback:
            feedback_block = f"""

FEEDBACK ON PREVIOUS ATTEMPT (the previous patch failed — fix these issues):
{feedback}
"""

        plan_str = getattr(plan, "raw_response", str(plan))

        messages = [
            {"role": "system", "content": CODER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"""
PLAN:
{plan_str}

CURRENT CODE:
{context[:12000]}
{feedback_block}
"""
            }
        ]

        raw = self.ai.local(messages, mode="coder", timeout=90)
        if not raw:
            return ""

        diff = self._extract_diff(raw)

        # Log per debug
        if diff:
            print(f"[Coder] Extracted diff: {len(diff.splitlines())} lines")
        else:
            print(f"[Coder] WARNING: no valid diff found in response ({len(raw.splitlines())} lines)")

        return diff

    def _parse_full_files(self, raw: str) -> dict:
        """Estrae i blocchi '### FILE: <path>' + fenced code block dalla risposta
        whole-file. Ritorna {rel_path: full_content} nell'ordine di apparizione."""
        files = {}
        if not raw:
            return files
        pattern = re.compile(
            r'#+\s*FILE:\s*(?P<path>[^\n`]+?)\s*\n+```[a-zA-Z0-9_+.\-]*\n(?P<body>.*?)\n```',
            re.DOTALL,
        )
        for m in pattern.finditer(raw):
            path = m.group("path").strip().strip('`').strip().lstrip("/")
            body = m.group("body")
            if path:
                files[path] = body
        return files

    def generate_full_files(self, plan, context, feedback=None) -> dict:
        """Modalità WHOLE-FILE (file piccoli): il Coder restituisce il contenuto
        COMPLETO di ogni file da creare/modificare, niente diff e niente patcher.
        Elimina la classe di fallimenti da unified-diff su modelli piccoli (righe
        di contesto allucinate → patch non applicabile). Ritorna {rel_path: content}."""
        feedback_block = ""
        if feedback:
            feedback_block = f"""

FEEDBACK ON PREVIOUS ATTEMPT (the previous result failed — fix these issues):
{feedback}
"""
        plan_str = getattr(plan, "raw_response", str(plan))

        messages = [
            {"role": "system", "content": WHOLE_FILE_CODER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"""
PLAN:
{plan_str}

CURRENT CODE (the exact current content of the project files):
{context[:12000]}
{feedback_block}
"""
            }
        ]

        raw = self.ai.local(messages, mode="coder", timeout=120) or ""
        files = self._parse_full_files(raw)

        if files:
            print(f"[Coder] Whole-file: {len(files)} file -> {list(files.keys())}")
        else:
            print(f"[Coder] WARNING: whole-file, nessun file estratto ({len(raw.splitlines())} righe grezze)")

        return files

    def generate_file(self, filename: str, spec: str, project_context: str = "") -> str:
        """
        Modalità 2 (Zero-Shot Scaffolding): genera il contenuto COMPLETO di un file
        da zero, scrittura diretta (no diff pipeline). Usato da Orchestrator.run_scaffold().
        """
        messages = [
            {"role": "system", "content": SCAFFOLD_CODER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"""
FILE DA CREARE: {filename}

SPEC:
{spec}

CONTESTO PROGETTO (file già creati in questo scaffold, per coerenza import/nomi):
{project_context[:SCAFFOLD_CONTEXT_CHARS] if project_context.strip() else "(primo file del progetto, nessun contesto precedente)"}

Restituisci SOLO il contenuto del file {filename}.
"""
            }
        ]

        # Un tetto all'uscita: con lo stream il timeout non taglia piu' una
        # generazione lunga, e un modello in loop scriverebbe fino a riempire
        # il contesto (131k token, ore). Un file di scaffold ci sta largo.
        risposta = self.ai.local(messages, mode="coder", timeout=60, max_tokens=SCAFFOLD_MAX_TOKENS)
        if risposta is None:
            raise ValueError(f"nessuna risposta dal modello per {filename} (rig non raggiungibile)")
        raw = risposta
        # Un file arrivato al tetto e' tagliato: dirlo, non scriverlo a meta'.
        # E se il tetto arriva mentre il modello pensa, il file e' vuoto per
        # questo, non per un guasto del protocollo.
        if getattr(self.ai, "ultima_fine", "") == "length":
            pensiero = len(getattr(self.ai, "ultimo_ragionamento", "") or "")
            if not raw.strip():
                raise ValueError(
                    f"tetto di {SCAFFOLD_MAX_TOKENS} token esaurito mentre il modello ragionava "
                    f"({pensiero} caratteri di pensiero): {filename} non e' stato scritto")
            raise ValueError(
                f"{filename} tagliato al tetto di {SCAFFOLD_MAX_TOKENS} token "
                f"({len(raw)} caratteri scritti, {pensiero} di pensiero)")
        # Strip di un eventuale fence markdown residuo, anche se il prompt lo vieta esplicitamente
        content = re.sub(r'^```[a-zA-Z]*\n', '', raw.strip())
        content = re.sub(r'\n```$', '', content)
        return content
