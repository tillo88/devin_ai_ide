import os
import subprocess
from pathlib import Path

# --- CONFIGURAZIONE ---
CARTELLA_LOCALE = r"/home/tillo/devin_ai_ide"  # Metti il percorso della cartella da caricare
URL_REPO_GITHUB = "https://github.com/tillo88/devin_ai_ide"  # Il link del tuo repository
RAMO_PRINCIPALE = "main"  # O "master", a seconda di come si chiama il tuo ramo
LIMITE_MB = 30
# ----------------------

MAX_BYTES = LIMITE_MB * 1024 * 1024

def esegui_comando(comando, directory=None):
    """Esegue un comando nel terminale e restituisce l'output."""
    risultato = subprocess.run(comando, cwd=directory, shell=True, text=True, capture_output=True)
    if risultato.returncode != 0:
        print(f"Errore nell'esecuzione di: {comando}")
        print(risultato.stderr)
        return False
    return True

def prepara_e_invia():
    path_cartella = Path(CARTELLA_LOCALE)
    
    if not path_cartella.exists():
        print("La cartella locale specificata non esiste!")
        return

    print("1. Inizializzo Git nella cartella locale (se non presente)...")
    if not (path_cartella / ".git").exists():
        esegui_comando("git init", CARTELLA_LOCALE)
        esegui_comando(f"git remote add origin {URL_REPO_GITHUB}", CARTELLA_LOCALE)
        # Rinomina il branch in main se non lo è già
        esegui_comando(f"git branch -M {RAMO_PRINCIPALE}", CARTELLA_LOCALE)
    else:
        print("Git già inizializzato.")

    print(f"2. Configuro l'esclusione per i file maggiori di {LIMITE_MB}MB...")
    # Scansioniamo la cartella per trovare i file grandi e li aggiungiamo al file .gitignore
    file_grandi = []
    for file_path in path_cartella.rglob("*"):
        if file_path.is_file() and ".git" not in file_path.parts:
            if file_path.stat().st_size > MAX_BYTES:
                # Calcola il percorso relativo rispetto alla cartella principale
                relativo = file_path.relative_to(path_cartella)
                file_grandi.append(str(relativo).replace("\\", "/"))

    # Scriviamo i file grandi nel .gitignore locale
    gitignore_path = path_cartella / ".gitignore"
    with open(gitignore_path, "a", encoding="utf-8") as g:
        if file_grandi:
            g.write("\n# File esclusi automaticamente perché superiori a 30MB\n")
            for f in file_grandi:
                g.write(f"{f}\n")
                print(f"Escluso: {f}")
        else:
            print("Nessun file superiore a 30MB trovato.")

    print("3. Preparo i file per il caricamento (git add)...")
    esegui_comando("git add .", CARTELLA_LOCALE)

    print("4. Creo il commit...")
    # Usiamo un messaggio generico con la data
    esegui_comando('git commit -m "Upload automatico cartella ed esclusioni >30MB"', CARTELLA_LOCALE)

    print("5. Eseguo il push su GitHub (Upload)...")
    print("Nota: se il repository è molto grande, questo processo potrebbe richiedere qualche minuto.")
    
    # Il flag -u serve per collegare la cartella locale al server remoto per i push futuri
    successo = esegui_comando(f"git push -u origin {RAMO_PRINCIPALE}", CARTELLA_LOCALE)
    
    if successo:
        print("\n Tutto completato con successo! Le cartelle e i file (sotto i 30MB) sono su GitHub.")
    else:
        print("\n Qualcosa è andato storto durante il push. Controlla le credenziali o la connessione.")

if __name__ == "__main__":
    prepara_e_invia()
