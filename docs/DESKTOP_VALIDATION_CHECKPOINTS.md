# DEVIN Desktop — checkpoint di validazione operativa

Aggiornato: 2026-08-22

Questo e' il percorso pratico corrente per collaudare la thin client Windows
contro il rig. Sostituisce il vecchio flusso WSL/backend locale: l'app Windows
ospita solo Tauri/WebView2, mentre frontdoor, backend, workspace e modello DEVIN
vivono sul rig.

## 0. Perimetro del collaudo ordinario

Un normale collaudo Desktop verifica una sola attivazione e un solo rilascio:

```text
Clippy residente -> apertura app -> DEVIN pronto -> chiusura app
                  -> gate idle/busy -> Clippy residente
```

Non ripetere SHA completi dei GGUF, probe 32K, NVML/`nvidia-smi` o test del
supporto USB. Quei gate si riaprono soltanto quando cambia modello, artefatto o
hardware interessato. Non avviare una nuova istanza server per ogni controllo.

## 1. Precondizioni

- release installata:
  `%LOCALAPPDATA%\DEVIN AI IDE\devin-ai-ide-desktop.exe`;
- configurazione protetta:
  `%APPDATA%\DEVIN\desktop.json`;
- frontdoor configurato sull'indirizzo del rig, senza stampare o copiare il
  token nei log;
- stato iniziale atteso sul rig:
  `READY | resident=clippy | devin=idle`.

Il probe **Test senza attivare** della schermata nativa e' soltanto TCP: non
invia credenziali e non deve cambiare il ruolo residente. La connessione normale
e' invece un'azione intenzionale che puo' richiedere il model-slot DEVIN.

## 2. Apertura dell'app e fase di preparazione

Aprire **DEVIN AI IDE** dal collegamento Desktop o dal menu Start. Il bootstrap
Rust usa il token senza restituirlo a JavaScript; il frontdoor lo converte in un
cookie `HttpOnly` e rimuove il token dall'URL visibile.

Durante l'attivazione la finestra deve mostrare **DEVIN si sta preparando** con:

- fase reale del lifecycle, per esempio `loading_devin_model`;
- unita' systemd attesa, per esempio `ai-rig-model-slot@devin.service`;
- ETA aggiornato oppure “stima in aggiornamento”;
- nessun falso stato ready basato sul solo `systemctl active`.

Il model-slot broker e' l'unico owner della transizione. Clippy puo' risultare
ancora attivo nella prima parte della preparazione, ma deve essere rilasciato in
modo controllato prima che DEVIN occupi lo slot.

Controllo read-only dal rig:

```bash
devin status
systemctl is-active ai-rig-clippy-chat.service
systemctl is-active ai-rig-model-slot@devin.service
systemctl is-active devin-backend.service
```

Sul supporto USB attuale il caricamento puo' richiedere diversi minuti. Un ETA
che scende e una finestra Windows responsiva indicano avanzamento; evitare di
interrompere o rilanciare l'app solo perche' il backend non e' ancora attivo.

## 3. Stato pronto e smoke minimo

La shell cockpit deve aprirsi automaticamente soltanto quando il backend e la
health del modello sono realmente pronti. Verificare:

- `devin status` in stato ready con DEVIN residente;
- `devin-backend.service` attivo e Clippy non residente;
- barra superiore, rail Projects/Knowledge/MCP/Swarm/Training, area centrale e
  Goal panel renderizzati senza pagina bianca;
- modello e stato mostrati da contratti backend, non da valori hardcoded;
- una richiesta breve inviata dalla chat restituisce una risposta e non lascia
  operazioni background spurie.

Non applicare patch, avviare training o Goal Mode durante lo smoke base. Queste
operazioni hanno receipt proprie e, correttamente, tengono viva la sessione.

## 4. Chiusura e ritorno a Clippy

Chiudere normalmente la finestra Windows. La chiusura non invia kill al backend
e non interrompe run, training o Goal attivi. Il frontdoor rilascia DEVIN solo
dopo il timeout idle configurato e dopo una risposta valida e vuota da
`/api/operations/active`; payload assente o malformato resta fail-closed.

Durante l'attesa usare soltanto:

```bash
devin status
```

Esito finale atteso:

- backend e model-slot DEVIN inattivi;
- Clippy unico residente e healthy;
- frontdoor e model-slot broker ancora attivi;
- nessun arresto manuale, `SIGKILL` o riavvio del rig.

## 5. Diagnostica mirata

Se la fase non avanza, raccogliere prima stato e code dei soli journal
interessati:

```bash
devin status
journalctl -u ai-rig-devin-frontdoor.service -n 100 --no-pager
journalctl -u ai-rig-model-slot@devin.service -n 100 --no-pager
journalctl -u devin-backend.service -n 100 --no-pager
journalctl -u ai-rig-clippy-chat.service -n 100 --no-pager
```

Non usare un generico riavvio come prima diagnosi. Se esistono operazioni
attive, identificarle dal contratto backend prima di concludere che il rilascio
idle sia bloccato.

## 6. Evidenza

Ogni collaudo live deve lasciare una receipt datata con:

- versione app e commit sorgente;
- stato iniziale/finale e transizioni osservate;
- tempi reali di caricamento e rilascio;
- screenshot della preparazione e del cockpit, se disponibili;
- richiesta/risposta dello smoke senza contenuti sensibili;
- anomalie e follow-up, distinti dai PASS.

Le ricevute packaging e onboarding non sostituiscono il collaudo funzionale:
vedere `WINDOWS_RELEASE_RECEIPT_2026-08-22.md`,
`WINDOWS_ONBOARDING_RECEIPT_2026-08-22.md` e la receipt funzionale indicizzata
in `INDEX.md`.
