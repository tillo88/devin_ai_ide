"""Router workspace: picker cartelle, lista progetti, creazione progetto.

Quarto router estratto da fast_app.py (split plan 2026-07-18,
docs/FAST_APP_SPLIT_PLAN.md). Move puro: path e comportamento identici.

Dipendenze che RESTANO in fast_app (import lazy dentro gli handler, mai
top-level — circolo fatale se il router e' importato per primo):
- `_register_allowed_root` / `_LINKED_PROJECT_ROOTS`: la allowlist di
  sicurezza (#8 audit) e' un singleton di fast_app, mutato dal picker e
  letto da explorer/projects/chat/runs; l'identita' degli oggetti e'
  preservata perche' importata, non copiata.
- `WORKSPACE_DIR`: costante condivisa.
"""

import asyncio
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, Request

from devin.core.project_space import ProjectSpace

router = APIRouter()


def _pick_folder_windows() -> dict:
    """Apre il dialog nativo di Windows "Sfoglia cartelle" (FolderBrowserDialog)
    via powershell.exe. Funziona in due contesti (migrazione nativa 2026-07-21):
    - backend nativo Windows: powershell.exe sul PATH, il path scelto e' gia'
      utilizzabile cosi' com'e' (nessuna conversione);
    - backend in WSL sulla stessa macchina: interop powershell.exe + conversione
      del path Windows in path WSL con wslpath.
    Bloccante finche' l'utente non chiude il dialog (chiamare via
    asyncio.to_thread). Se non siamo ne' su Windows ne' su WSL (es. deploy sul
    rig), errore pulito."""
    import os as _os
    import shutil as _shutil
    import subprocess as _sp

    if _os.name == "nt":
        ps = _shutil.which("powershell.exe") or r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    else:
        ps = _shutil.which("powershell.exe") or "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    if not Path(ps).exists():
        return {"error": "Dialog disponibile solo quando il server gira in WSL sulla stessa macchina "
                          "(powershell.exe non trovato). Inserisci il path a mano."}
    # Il dialog va forzato in PRIMO PIANO: lanciato da WSL/headless si apriva
    # DIETRO la finestra Tauri. Windows nega SetForegroundWindow ai processi
    # senza focus (anti-popup), quindi non basta TopMost: serve P/Invoke
    # user32.SetForegroundWindow sull'handle del form owner, che tira davanti
    # anche il dialog figlio.
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -Namespace Native -Name W -MemberDefinition "
        "'[DllImport(\"user32.dll\")] public static extern bool SetForegroundWindow(IntPtr h);"
        "[DllImport(\"user32.dll\")] public static extern bool ShowWindow(IntPtr h, int c);'; "
        "$owner = New-Object System.Windows.Forms.Form; "
        "$owner.TopMost = $true; $owner.ShowInTaskbar = $false; "
        "$owner.StartPosition = 'CenterScreen'; $owner.Size = New-Object System.Drawing.Size(1,1); "
        "$owner.Show(); [Native.W]::ShowWindow($owner.Handle, 5) | Out-Null; "
        "[Native.W]::SetForegroundWindow($owner.Handle) | Out-Null; "
        "[System.Windows.Forms.Application]::DoEvents(); "
        "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$f.Description = 'Seleziona la cartella del progetto per DEVIN AI IDE'; "
        "$f.ShowNewFolderButton = $true; "
        "$res = $f.ShowDialog($owner); $owner.Close(); "
        "if ($res -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $f.SelectedPath }"
    )
    try:
        # -STA: i dialog WinForms richiedono un thread STA
        out = _sp.run([ps, "-NoProfile", "-STA", "-Command", script],
                      capture_output=True, text=True, timeout=180)
        win_path = (out.stdout or "").strip().splitlines()[-1].strip() if (out.stdout or "").strip() else ""
        if not win_path:
            return {"cancelled": True}
        if _os.name == "nt":
            # Backend nativo Windows: nessuna conversione necessaria.
            return {"path": win_path, "windows_path": win_path}
        wsl = _sp.run(["wslpath", "-u", win_path], capture_output=True, text=True, timeout=10)
        linux_path = (wsl.stdout or "").strip()
        if not linux_path:
            return {"error": f"Conversione path fallita per: {win_path}"}
        return {"path": linux_path, "windows_path": win_path}
    except _sp.TimeoutExpired:
        return {"error": "Dialog scaduto (3 minuti senza scelta)."}
    except Exception as e:
        return {"error": f"Dialog fallito: {e}"}


@router.post("/api/workspace/pick_folder")
async def api_workspace_pick_folder():
    """Apre il dialog cartelle di Windows e ritorna il path WSL scelto.
    In thread separato: il dialog resta aperto anche minuti, non deve
    bloccare l'event loop (l'intera UI si congelerebbe)."""
    from devin.ui.fast_app import _register_allowed_root  # lazy: no circolo
    result = await asyncio.to_thread(_pick_folder_windows)
    # #8: una cartella scelta dall'utente diventa root leggibile via file explorer
    if isinstance(result, dict) and result.get("path"):
        if _register_allowed_root(result["path"]):
            result["project"] = {"name": Path(result["path"]).name, "path": result["path"], "linked": True}
        else:
            # Registrazione fallita: senza questo avviso l'utente vedrebbe solo
            # 403 inspiegabili nell'esploratore file (fix 2026-07-18).
            result["warning"] = (
                "Cartella non registrabile come root leggibile (inesistente o non "
                "accessibile): l'esploratore file restera' bloccato su questo path."
            )
    return result


@router.get("/api/workspace/projects")
async def api_workspace_projects():
    """Lista dei progetti = sottocartelle di workspace/ (escluse quelle interne).
    Per la sidebar 'Progetti come cartelle' + per il rilevamento dei progetti
    collegati nei messaggi (vedi _detect_linked_projects)."""
    from devin.ui.fast_app import WORKSPACE_DIR, _LINKED_PROJECT_ROOTS  # lazy
    projects = []
    if WORKSPACE_DIR.exists():
        for d in sorted(WORKSPACE_DIR.iterdir()):
            if not d.is_dir() or d.name.startswith(("_", ".")) or d.name == "sandbox":
                continue
            ps = ProjectSpace(str(d))
            projects.append({
                "name": d.name,
                "path": str(d),
                "chats": len(ps.list_chats()),
                "knowledge": len(ps.list_knowledge()),
                "has_instructions": bool(ps.get_instructions()),
                "work_dir": ps.get_work_dir(),
                "linked": False,
            })
    for d in sorted(_LINKED_PROJECT_ROOTS, key=lambda item: item.name.lower()):
        if not d.exists() or not d.is_dir():
            continue
        ps = ProjectSpace(str(d))
        projects.append({
            "name": d.name,
            "path": str(d),
            "chats": len(ps.list_chats()),
            "knowledge": len(ps.list_knowledge()),
            "has_instructions": bool(ps.get_instructions()),
            "linked": True,
        })
    return {"projects": projects, "workspace": str(WORKSPACE_DIR)}


@router.post("/api/workspace/projects/new")
async def api_workspace_projects_new(request: Request):
    """Crea una nuova cartella-progetto in workspace/. Nome sanitizzato, niente
    path traversal; se esiste gia' torna quella esistente (idempotente)."""
    import re as _re
    from devin.ui.fast_app import WORKSPACE_DIR  # lazy: no import circolare
    data = await request.json()
    name = _re.sub(r"[^\w\-. ]", "_", Path(data.get("name", "")).name).strip()
    if not name:
        return {"error": "nome progetto vuoto o non valido"}
    target = WORKSPACE_DIR / name
    target.mkdir(parents=True, exist_ok=True)
    return {"name": name, "path": str(target), "created": True}


@router.post("/api/workspace/projects/remove")
async def api_workspace_projects_remove(request: Request):
    """Rimuove un progetto dalla sidebar (2026-07-21).

    - progetto INTERNO (sottocartella diretta di workspace/): spostato in
      workspace/_trash/<nome>-<timestamp> — recuperabile a mano, mai delete
      permanente (filosofia del progetto: nessuna distruzione irreversibile);
    - progetto COLLEGATO (cartella esterna): solo scollegato dal registro,
      i file dell'utente non vengono toccati.
    """
    from devin.ui import fast_app as _fa  # lazy: no import circolare

    data = await request.json()
    raw = str(data.get("path", "")).strip()
    if not raw:
        return {"error": "path mancante"}
    target = Path(raw).expanduser().resolve()
    workspace_root = _fa.WORKSPACE_DIR.resolve()

    # Collegato: unlink dal registro, file intatti.
    if target in _fa._LINKED_PROJECT_ROOTS:
        _fa._LINKED_PROJECT_ROOTS[:] = [
            p for p in _fa._LINKED_PROJECT_ROOTS if p != target
        ]
        _fa._ALLOWED_ROOTS.discard(target)
        return {"status": "unlinked", "path": str(target)}

    # Interno: solo sottocartelle DIRETTE di workspace/, mai riservate/trash
    # (il confronto sul parent risolto blocca anche i path traversal).
    if target.parent != workspace_root or target.name.startswith(("_", ".")):
        return {"error": "path non rimovibile: non e' un progetto del workspace"}
    if not target.is_dir():
        return {"error": "progetto inesistente"}

    trash_dir = workspace_root / "_trash"
    trash_dir.mkdir(parents=True, exist_ok=True)
    destination = trash_dir / "{}-{}".format(target.name, time.strftime("%Y%m%d-%H%M%S"))
    await asyncio.to_thread(shutil.move, str(target), str(destination))
    return {"status": "trashed", "path": str(target), "trash_path": str(destination)}
