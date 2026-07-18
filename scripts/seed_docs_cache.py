#!/usr/bin/env python3
"""Semina la docs cache con doc ufficiali di base (anti endpoint inventati).

Prima voce: Steam Web API — il failure mode piu' ricorrente del batch MBPP
(host di fantasia). Contenuto verificato dalla doc ufficiale Valve. Aggiungere
qui altre API/librerie che il modello tende ad allucinare.

Uso: venv/bin/python scripts/seed_docs_cache.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from devin.core.docs_cache import DocsCache  # noqa: E402

STEAM_DOC = """\
Steam Web API — endpoint UFFICIALI (fonte: https://developer.valvesoftware.com/wiki/Steam_Web_API)

Base URL UNICA: https://api.steampowered.com
Schema: https://api.steampowered.com/<Interface>/<Method>/v<version>/?key=<APIKEY>&<params>

Non esistono host tipo steamcommunity.<qualcosa>.com/api, api.steamchecker.io,
steamcommunity.games.com ecc.: sono INVENTATI. Usare SEMPRE api.steampowered.com.

Endpoint comuni:
- GetPlayerSummaries (profili giocatore):
  https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/?key=<KEY>&steamids=<STEAMID64>
- GetFriendList:
  https://api.steampowered.com/ISteamUser/GetFriendList/v0001/?key=<KEY>&steamid=<STEAMID64>&relationship=friend
- GetOwnedGames:
  https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/?key=<KEY>&steamid=<STEAMID64>&format=json
- ResolveVanityURL:
  https://api.steampowered.com/ISteamUser/ResolveVanityURL/v0001/?key=<KEY>&vanityurl=<NAME>

Nei test: MAI chiamate reali di rete — usare mock/responses sui suddetti URL.
"""


def main() -> int:
    cache = DocsCache(ROOT / "workspace")
    entry = cache.add_doc(
        title="Steam Web API (ufficiale)",
        content=STEAM_DOC,
        keys=["steam", "steam web api", "getplayersummaries", "steampowered",
              "steamid", "steam profile", "isteamuser"],
        source_url="https://developer.valvesoftware.com/wiki/Steam_Web_API",
    )
    print(f"seminato: {entry['slug']} (chiavi: {', '.join(entry['keys'])})")
    print(f"totale doc in cache: {len(cache.list_docs())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
