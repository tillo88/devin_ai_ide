# DEVIN UI interaction audit — 2026-08-22

## Scopo e vincoli

Audit mirato del cockpit `/app` dopo il primo collaudo della release Windows.
Il backend e' stato avviato soltanto in locale per servire HTML/CSS/JS: nessuna
attivazione DEVIN, nessun cambio residente sul rig, nessun probe SHA/32K/NVML e
nessun accesso ai dischi USB.

## Finding riprodotti

1. **Topbar fuori riga.** Il DOM corrente contiene brand, command status,
   telemetry e status controls, ma una regola legacy dichiarava tre colonne.
   A viewport 1456x959 l'header misurava `0..72px`, mentre `topbar-status`
   misurava `73..148px`: `⌘K` e Diagnostics apparivano sotto il cockpit.
2. **Responsive non bounded.** A 1024 px i cinque figli visibili occupavano tre
   righe fino a 156 px dentro un header alto 74 px. A 540 px il risultato era
   equivalente. Inoltre il toggle Workspace appariva a tablet anche se la rail
   Workspace diventa overlay soltanto sotto 768 px.
3. **Navigazione incoerente.** Knowledge/MCP/Agent Swarm lasciavano Projects
   evidenziato; tornando su Projects la vista centrale restava in Governance.
4. **Web chip non interattivo.** Sembrava un controllo ma era uno `span` e il
   client inviava sempre `use_web_search=true`, nonostante il backend avesse
   gia' il rilevamento conservativo dell'intento web.

## Correzioni

- layer CSS finale con aree topbar esplicite e breakpoint desktop/tablet/mobile;
- toggle Workspace nascosto a tablet e riattivato solo sulla rail mobile;
- stato unico per la navigazione primaria, con ritorno Projects -> Chat;
- `web auto`/`web on` accessibile tramite `aria-pressed`; auto e' il default;
- cache shell e Service Worker incrementati a `v14`.

## Sweep dei controlli

| Superficie | Esito |
| --- | --- |
| Command Palette: apertura, filtro, Escape/chiusura | PASS |
| Chat / Governance | PASS |
| Projects / Knowledge / MCP Tools / Agent Swarm | FIX + PASS |
| Menu composer File / Skill / Goal | PASS |
| Preset `Scrivi i test` -> composer | PASS |
| Goal preset e launch disabled senza progetto | PASS |
| Collasso/riapertura rail sinistra e destra | PASS |
| Modale Nuovo progetto + Annulla | PASS |
| Console browser warning/error durante lo sweep | 0 |

Non sono stati premuti controlli che avrebbero creato dati, inviato chat,
applicato diff o avviato Goal. Quei flussi restano coperti dalle guardie e dai
test dedicati, ma richiedono un collaudo operativo separato.

## Verifica richiesta prima del merge

```text
python -m py_compile devin/ui/routers/chat.py devin/ui/routers/pages.py
python -m pytest -q test_desktop_cockpit_ui.py test_pwa_assets.py
python -m pytest -q test_understory_hybrid.py test_scaffold_resilience.py
```

Poi ripetere lo sweep browser a 1456, 1024, 540 e 360 px e verificare che ogni
figlio visibile della topbar resti entro `0..72px`.

## Esito finale

Rerun completato: `allInside=true` a 1456, 1024, 540 e 360 px. A 1024 px il
toggle Workspace e' correttamente nascosto e il toggle Attivita' visibile; a
540/360 px entrambi i toggle laterali sono visibili. Navigazione primaria,
`web auto`/`web on`, palette, preset Skill, collasso rail e modale annullabile
sono PASS; la console resta senza warning o errori.

I contratti cockpit/PWA/web sono `31 passed`; Understory + scaffold sono
`80 passed, 1 failed`. L'unico failure e' preesistente e specifico di Windows:
il test `test_project_sandbox_can_link_venv_as_lightweight_reference` prova a
creare un symlink `.venv` senza il privilegio Windows richiesto (`WinError
1314`). Non tocca la UI ne' il codice modificato in questo pass.

La suite completa, rieseguita escludendo soltanto quel caso ambientale, chiude
con `645 passed, 7 skipped, 1 deselected`.
