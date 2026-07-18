#!/usr/bin/env python3
"""
KillModels — Termina i server AI locali (Ollama, llama.cpp, vLLM)
Cross-platform: Linux, WSL, Windows
"""

import subprocess
import sys
import os
import signal

# ═══════════════════════════════════════════════════════════════
# CONFIGURAZIONE — nomi dei processi da cercare
# ═══════════════════════════════════════════════════════════════
TARGETS = {
    # nome processo → descrizione
    "ollama": "Ollama server",
    "ollama.exe": "Ollama server (Win)",
    "llama": "llama.cpp server",
    "llama-server": "llama.cpp server",
    "main": "llama.cpp main",
    "vllm": "vLLM",
    "python": "Python (vLLM spesso gira su python)",
    "python3": "Python3",
    "uvicorn": "Uvicorn (vLLM API)",
    "fastapi": "FastAPI (vLLM)",
}

# Parole chiave nei command-line che identificano vLLM / llama.cpp
KEYWORDS_VLLM = ["vllm", "serve", "api_server", "--model"]
KEYWORDS_LLAMA = ["llama.cpp", "llama-server", "--model", "-m "]

# ═══════════════════════════════════════════════════════════════
# UTILS
# ═══════════════════════════════════════════════════════════════

def run_cmd(cmd, shell=False):
    try:
        result = subprocess.run(cmd, shell=shell, capture_output=True, text=True, timeout=10)
        return result.stdout, result.stderr, result.returncode
    except Exception as e:
        return "", str(e), 1

def is_windows():
    return sys.platform.startswith("win")

def is_wsl():
    return "microsoft" in platform.release().lower() if 'platform' in globals() else False

# ═══════════════════════════════════════════════════════════════
# PSUTIL (metodo preferito)
# ═══════════════════════════════════════════════════════════════

def kill_with_psutil():
    try:
        import psutil
    except ImportError:
        return False, "psutil non installato"

    killed = []
    skipped = []

    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            pinfo = proc.info
            name = (pinfo.get('name') or "").lower()
            cmdline = " ".join(pinfo.get('cmdline') or []).lower()
            pid = pinfo.get('pid')

            if not pid:
                continue

            matched = False
            reason = ""

            # Match per nome esatto
            for target_name, desc in TARGETS.items():
                if target_name.lower() in name:
                    matched = True
                    reason = desc
                    break

            # Match per keyword nel cmdline (vLLM, llama.cpp)
            if not matched:
                for kw in KEYWORDS_VLLM:
                    if kw in cmdline and ("vllm" in cmdline or "serve" in cmdline):
                        matched = True
                        reason = "vLLM (detected via cmdline)"
                        break
                for kw in KEYWORDS_LLAMA:
                    if kw in cmdline:
                        matched = True
                        reason = "llama.cpp (detected via cmdline)"
                        break

            if matched:
                try:
                    p = psutil.Process(pid)
                    # Se è il nostro stesso script, non suicidarsi
                    if pid == os.getpid():
                        skipped.append(f"PID {pid} — è questo script, salto")
                        continue

                    p.terminate()
                    try:
                        p.wait(timeout=3)
                    except psutil.TimeoutExpired:
                        p.kill()
                        p.wait(timeout=2)
                    killed.append(f"PID {pid} — {reason}")
                except psutil.NoSuchProcess:
                    skipped.append(f"PID {pid} — già morto")
                except Exception as e:
                    skipped.append(f"PID {pid} — errore: {e}")

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    return True, {"killed": killed, "skipped": skipped}

# ═══════════════════════════════════════════════════════════════
# FALLBACK senza psutil
# ═══════════════════════════════════════════════════════════════

def kill_with_shell():
    killed = []
    skipped = []

    if is_windows():
        # Windows: tasklist + taskkill
        for target_name in TARGETS:
            stdout, _, rc = run_cmd(f"taskkill /F /IM {target_name}", shell=True)
            if rc == 0:
                killed.append(f"{target_name} — terminato via taskkill")
            else:
                skipped.append(f"{target_name} — non trovato o errore")
    else:
        # Linux/WSL: ps + kill
        stdout, _, _ = run_cmd("ps aux", shell=True)
        lines = stdout.splitlines()

        for line in lines:
            parts = line.split()
            if len(parts) < 11:
                continue
            try:
                pid = int(parts[1])
                cmdline = " ".join(parts[10:]).lower()
            except:
                continue

            if pid == os.getpid():
                continue

            matched = False
            reason = ""

            for target_name, desc in TARGETS.items():
                if target_name.lower() in cmdline:
                    matched = True
                    reason = desc
                    break

            if not matched:
                for kw in KEYWORDS_VLLM:
                    if kw in cmdline and ("vllm" in cmdline or "serve" in cmdline):
                        matched = True
                        reason = "vLLM"
                        break
                for kw in KEYWORDS_LLAMA:
                    if kw in cmdline:
                        matched = True
                        reason = "llama.cpp"
                        break

            if matched:
                try:
                    os.kill(pid, signal.SIGTERM)
                    import time
                    time.sleep(0.5)
                    # Verifica se è ancora vivo
                    try:
                        os.kill(pid, 0)
                        os.kill(pid, signal.SIGKILL)
                        killed.append(f"PID {pid} — {reason} (SIGKILL)")
                    except ProcessLookupError:
                        killed.append(f"PID {pid} — {reason} (SIGTERM)")
                except ProcessLookupError:
                    skipped.append(f"PID {pid} — già morto")
                except PermissionError:
                    skipped.append(f"PID {pid} — {reason} (permesso negato, prova sudo)")
                except Exception as e:
                    skipped.append(f"PID {pid} — errore: {e}")

    return True, {"killed": killed, "skipped": skipped}

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  🔪 KillModels — Terminator per server AI locali")
    print("  Target: Ollama, llama.cpp, vLLM")
    print("=" * 60)
    print()

    # Prova psutil prima
    success, result = kill_with_psutil()

    if not success:
        print(f"⚠️  psutil non disponibile: {result}")
        print("🔄 Passo al fallback shell...")
        print()
        success, result = kill_with_shell()

    killed = result.get("killed", [])
    skipped = result.get("skipped", [])

    if killed:
        print(f"✅ TERMINATI ({len(killed)}):")
        for k in killed:
            print(f"   • {k}")
    else:
        print("ℹ️  Nessun processo AI trovato da terminare.")

    print()

    if skipped:
        print(f"⚠️  SKIPPED / FALLITI ({len(skipped)}):")
        for s in skipped:
            print(f"   • {s}")

    print()
    print("=" * 60)
    print("  Pulizia completata. La VRAM è libera. 🧹")
    print("=" * 60)

if __name__ == "__main__":
    main()
