# Progetto Codex locale Windows e superfici SSHFS

**Aggiornato:** 2026-09-29

**Scopo:** lavorare sul frontend da Windows senza perdere accesso osservabile a
rig, shared e Raspberry e senza confondere i rispettivi confini.

## Perché serve una chat locale

Una chat Codex usa filesystem e shell dell'host scelto all'avvio. La precedente
chat SSH poteva modificare `/home/tillo/devin_ai_ide`, ma non poteva aggiungere
`F:`. Il progetto nuovo deve quindi essere creato sul PC Windows con root
`F:\devin_ai_ide`.

I collegamenti SSHFS restano raggiungibili da Explorer e PowerShell per
consultazioni mirate. Non vanno aggiunti interamente come radici del progetto:
farebbe indicizzare modelli, log, recovery e dati live che non appartengono al
repository. Non fondono Windows e Linux: una build PowerShell gira sul PC,
mentre `systemctl`, journal e test nel venv Linux devono girare in una sessione
sul rig.

Riferimento OpenAI sul modello host/workspace:
<https://learn.chatgpt.com/docs/remote-connections>.

## Mappa verificata

```text
Windows PC
├─ F:\devin_ai_ide                         sorgente Git primaria
├─ %LOCALAPPDATA%\DEVIN\desktop-host       output/runtime Tauri generato
├─ %LOCALAPPDATA%\DEVIN AI IDE\            installazione stabile 0.2.0
├─ Z:\                                      SSHFS: /home/tillo sul rig .100
├─ ai-rig-shared                            SSHFS: /mnt/ai-rig-shared sul rig
└─ R:\                                      SSHFS: home Raspberry .86
```

Verificare le mappature all'inizio della sessione: SSHFS può essere scollegato
o rimontato con una lettera diversa. Non dedurre la destinazione dal solo nome
mostrato in Explorer.

## Regole di modifica

- modificare il repository solo in `F:\devin_ai_ide`;
- aprire il progetto Codex locale con la sola radice `F:\devin_ai_ide`;
- usare GitHub per trasferire modifiche verso il rig;
- trattare `Z:\devin_ai_ide` come mirror leggibile/diagnostico, non come secondo
  checkout su cui sviluppare in parallelo;
- non modificare `/opt` attraverso mount o copie manuali: il deploy è un
  `git pull --ff-only` controllato nella copia di produzione;
- non rinominare, spostare o eliminare file sotto shared durante un run;
- `R:\` non autorizza cambi al watcher: Raspberry resta un host operativo
  distinto;
- non aprire modelli, log live, memory JSONL, `.env` o token nel progetto Codex.

`ai-rig-ops` conserva repository e cronologia separati. Se diventa necessario
modificarlo dal PC, creare un clone Windows dedicato (per esempio
`F:\ai-rig-ops`) e aprirlo come progetto distinto: mai annidarlo dentro DEVIN e
mai usare la copia di produzione `/opt/ai-rig-ops` come workspace.

## Creazione del progetto locale

1. Tornare alla schermata progetti di Codex sul PC e scegliere l'host locale.
2. Creare/aprire un progetto usando `F:\devin_ai_ide` come cartella.
3. Nella prima chat chiedere di leggere, nell'ordine, `CURRENT.md`, `AGENTS.md`
   e `docs/LOCAL_WINDOWS_WORKSPACE_20260929.md`.
4. Verificare da PowerShell `git status --short --branch` prima di modificare.
5. Usare `Z:`, `R:` e la voce `ai-rig-shared` soltanto quando il task richiede
   un file preciso; per operazioni Linux aprire la connessione SSH dedicata.

La conversazione SSH corrente non si converte in una conversazione locale: il
passaggio di consegne persistente è `CURRENT.md`, versionato nello stesso repo.

## Sorgente, bundle e release

Il template e gli asset veri sono sotto `devin/ui`. `src-tauri/frontend` viene
rigenerato ed è ignorato da Git. `%LOCALAPPDATA%\DEVIN\desktop-host` è un mirror
per la compilazione nativa. Nessuno dei due sostituisce la sorgente.

Per il ciclo visuale rapido:

```powershell
Set-Location 'F:\devin_ai_ide'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File '.\scripts\build-frontend-bundle.ps1' -SourceRepo 'F:\devin_ai_ide'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File '.\scripts\prepare-windows-desktop-host.ps1' -SourceRepo 'F:\devin_ai_ide' -SkipNpmInstall
& "$env:LOCALAPPDATA\DEVIN\DEVIN Desktop.cmd"
```

La release installata sotto `%LOCALAPPDATA%\DEVIN AI IDE` incorpora il bundle
del momento in cui è stata compilata. Copiare il nuovo bundle nel desktop host
non aggiorna quell'eseguibile. Per una release usare
`scripts/build-windows-installer.ps1`, verificare il manifest e reinstallare il
pacchetto prodotto.

## Stato Git ereditato

Prima della migrazione locale, `main` Windows era `ahead 10, behind 54`. La
cronologia è stata preservata in `backup/windows-main-diverged-20260929`, poi
`main` è stato riallineato a `origin/main`. Un `index.lock` stale, lungo zero
byte e senza processo Git proprietario, è stato rinominato e conservato.

Le directory locali `Claude outputs/`, `_backup/` e `_bridge/` restano
untracked. Non usare `git clean`. Possono essere aggiunte a `.git/info/exclude`
sul PC, senza introdurle nel `.gitignore` condiviso, dopo averne confermato il
contenuto.

## Handoff frontend

PR `#33` ha introdotto la barra del flusso progetto e la shell PWA `v16`.
Il bundle è stato visto sul PC: la nuova barra compare, quindi sync e bootstrap
sono corretti. La texture generale resta quella storica; il prossimo lavoro è
il redesign visuale mantenendo:

- layout a tre colonne;
- stati derivati da API reali;
- Goal, Runs, Diff, Log e Governance come superfici collegate;
- un solo model slot fisico, con ruoli logici non presentati come modelli
  simultaneamente residenti;
- nessuna VRAM o percentuale di avanzamento inventata;
- fallback `/`, `/chat` e `/history` intatti.
