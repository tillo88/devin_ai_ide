# SearXNG sul rig — fallback locale v2

SearXNG resta disponibile a DEVIN e ai futuri agenti come provider locale
privacy-first. Il backend lo usa su `http://127.0.0.1:8081`; la porta non è
esposta alla LAN. TinyFish può essere primario o fallback invertendo la catena
`web_search.providers`.

## Perché il runtime non è più sul disco shared

La configurazione storica viveva in `/mnt/ai-rig-shared/searxng`. Durante un
problema USB il relativo `docker compose down` ha fallito leggendo `.env` e ha
lasciato il container vivo nello shutdown. Il v2 installa compose e settings in
`/var/lib/ai-rig/searxng/runtime`, sul filesystem del ruolo corrente. AutoMem e
Understory non sono coinvolti.

L'installer migra una sola volta il `settings.yml` storico, senza stamparne il
secret. Se non esiste, genera un nuovo secret per macchina. L'immagine è pin-nata
al digest già osservato e validato; `--pull never` impedisce aggiornamenti
silenziosi.

## Checkpoint separati

```bash
sudo bash scripts/rig/searxng/install_searxng_service.sh --install
sudo bash scripts/rig/searxng/install_searxng_service.sh --check
```

Questi comandi installano/verificano i file ma non fermano il container. La
mutazione runtime è esplicita e separata:

```bash
sudo bash scripts/rig/searxng/install_searxng_service.sh --activate
```

`--activate` aggiorna la unit, invia una sola `SIGTERM` bounded all'eventuale
container precedente, verifica che sia terminato e avvia il runtime pin-nato.
Non esiste fallback SIGKILL.

## Contratto web

Profilo privacy-first:

```json
{
  "providers": ["searxng", "tinyfish"],
  "searxng_url": "http://127.0.0.1:8081"
}
```

Profilo TinyFish-first: `providers: ["tinyfish", "searxng"]`. Il fallback
scatta soltanto se il provider primario fallisce, non quando restituisce zero
risultati.

## Verifica

```bash
systemctl status ai-rig-searxng.service
curl --fail 'http://127.0.0.1:8081/search?q=test&format=json'
```

Il compose mantiene il tetto di 2 CPU e 1 GiB. Un aggiornamento del digest è una
modifica sorgente revisionata, seguita da install, activation e smoke separati.
