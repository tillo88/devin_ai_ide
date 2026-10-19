"""Il sorvegliante delle ripetizioni: DEVIN lavora da solo, si ferma solo se gira in tondo.

19/10. Prima la chat aveva un tetto fisso: dopo 10 passaggi con gli strumenti glieli
toglieva in silenzio. Nel test del supporto da telefono DEVIN stava lavorando bene
(aveva appena cambiato metodo grazie a una nota), il tetto gli ha tolto il
modellatore, lui ha ripetuto tre volte la stessa ricerca (l'unico strumento rimasto)
e alla fine ha scritto la chiamata come testo. Un tetto fisso ferma il lavoro buono
e non riconosce quello cattivo.

Il sorvegliante non conta i passaggi: guarda se portano informazione nuova.
E' codice, non un secondo modello: sul rig c'e' un solo posto per un modello alla
volta, e le ripetizioni si riconoscono con esattezza senza intelligenza.

- **Chiamata ripetuta**: stesso strumento, stessi argomenti E stesso risultato di una
  chiamata gia' fatta. Lo stesso ``pytest`` rilanciato dopo aver corretto un file da'
  un risultato diverso e non conta: conta solo cio' che non porta niente di nuovo.
  Nei risultati non conta cio' che cambia da solo (cartelle con data e ora, id lunghi,
  orari, durate); misure e conteggi invece contano: "3 failed" -> "2 failed" e'
  un passo avanti, non una ripetizione.
- **Ricerca ripetuta**: una ricerca web uguale o quasi uguale (stesse parole) a una
  gia' fatta non si riesegue: i risultati sono gia' nella conversazione.
- **Stesso errore**: lo stesso errore (stessa prima riga, cifre a parte) che torna in
  giri consecutivi vuol dire che sta riprovando la stessa cosa.

La prima volta il sorvegliante **avvisa** il modello ("ti stai ripetendo su X, cambia
approccio"); se dopo l'avviso succede di nuovo, **ferma**: chi lo usa toglie gli
strumenti e chiede una risposta con quello che c'e', e lo dice all'utente.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

RIPETIZIONI = 2            # chiamate senza niente di nuovo prima di intervenire
ERRORI_UGUALI = 3          # giri consecutivi con lo stesso errore prima di intervenire
SIMILITUDINE_RICERCHE = 0.8
SEGNO = "[messaggio del sistema, non dell'utente]"
_CIFRE = re.compile(r"\d+")
# cio' che cambia da solo fra due esecuzioni identiche: numeri lunghi (cartelle
# 20261019-092149, id), orari 10:03:16, durate "0.52s"
_VOLATILE = re.compile(r"\d{6,}|\b\d{1,2}:\d{2}(?::\d{2})?\b|\b\d+(?:\.\d+)?\s?(?:ms|s)\b")
_PAROLE = re.compile(r"\w+", re.UNICODE)


@dataclass
class Verdetto:
    stato: str                 # "ok" | "avviso" | "stop"
    motivo: str = ""


def _normale(valore: Any) -> Any:
    if isinstance(valore, dict):
        return {k: _normale(valore[k]) for k in sorted(valore)}
    if isinstance(valore, list):
        return [_normale(v) for v in valore]
    if isinstance(valore, str):
        return " ".join(valore.split())
    return valore


def firma_chiamata(nome: str, argomenti: Any) -> str:
    """Strumento + argomenti, a meno di spazi e ordine delle chiavi."""
    if isinstance(argomenti, str):
        try:
            argomenti = json.loads(argomenti or "{}")
        except ValueError:
            pass
    testo = json.dumps(_normale(argomenti), sort_keys=True, ensure_ascii=False)
    return f"{nome}:{hashlib.sha256(testo.encode()).hexdigest()[:16]}"


def firma_risultato(risultato: Any) -> str:
    testo = _VOLATILE.sub("#", " ".join(str(risultato or "").split()))
    return hashlib.sha256(testo.encode()).hexdigest()[:16]


def firma_errore(risultato: Any) -> Optional[str]:
    """La prima riga di un errore, cifre a parte; None se non e' un errore."""
    testo = str(risultato or "").strip()
    if not testo.upper().startswith("ERRORE") and not testo.startswith("Traceback"):
        return None
    righe = [r.strip() for r in testo.splitlines() if r.strip()]
    if testo.startswith("Traceback"):
        riga = righe[-1] if righe else testo
    else:
        # "ERRORE nel codice del pezzo (exit 1):" da sola non distingue: si prende
        # anche la riga dopo, che dice quale errore
        riga = " ".join(righe[:2])
    return _CIFRE.sub("#", riga)[:300]


def _parole(testo: str) -> set:
    return set(_PAROLE.findall(str(testo or "").lower()))


def simili(a: str, b: str, soglia: float = SIMILITUDINE_RICERCHE) -> bool:
    pa, pb = _parole(a), _parole(b)
    if not pa or not pb:
        return False
    return len(pa & pb) / len(pa | pb) >= soglia


class Sorvegliante:
    def __init__(self, ripetizioni: int = RIPETIZIONI, errori_uguali: int = ERRORI_UGUALI):
        self.soglia_ripetizioni = ripetizioni
        self.soglia_errori = errori_uguali
        self._viste: Dict[str, str] = {}          # firma chiamata+risultato -> etichetta
        self._ricerche: List[str] = []
        self._ripetizioni: List[str] = []         # dall'ultimo intervento
        self._errore_corrente: Optional[str] = None
        self._errore_giri = 0
        self._errori_del_giro: List[str] = []
        self.avvisato = False
        self.interventi: List[Verdetto] = []

    # --- prima di eseguire ---------------------------------------------------
    def ricerca_gia_fatta(self, domanda: str) -> Optional[str]:
        """La ricerca uguale o quasi gia' fatta (e la conta come ripetizione), oppure None."""
        for vecchia in self._ricerche:
            if simili(vecchia, domanda):
                self._ripetizioni.append(f"la ricerca '{str(domanda)[:80]}'")
                return vecchia
        self._ricerche.append(str(domanda))
        return None

    # --- dopo aver eseguito --------------------------------------------------
    def registra(self, nome: str, argomenti: Any, risultato: Any) -> bool:
        """Registra una chiamata eseguita; True se non ha portato niente di nuovo."""
        chiave = firma_chiamata(nome, argomenti) + "|" + firma_risultato(risultato)
        errore = firma_errore(risultato)
        if errore:
            self._errori_del_giro.append(errore)
        if chiave in self._viste:
            self._ripetizioni.append(f"{nome} con gli stessi argomenti e lo stesso risultato")
            return True
        self._viste[chiave] = nome
        return False

    def fine_giro(self) -> Verdetto:
        """Alla fine di ogni giro con strumenti: ok, avviso o stop."""
        errori, self._errori_del_giro = self._errori_del_giro, []
        if errori and len(set(errori)) == 1 and errori[0] == self._errore_corrente:
            self._errore_giri += 1
        elif errori and len(set(errori)) == 1:
            self._errore_corrente, self._errore_giri = errori[0], 1
        else:
            self._errore_corrente, self._errore_giri = None, 0

        motivo = ""
        if len(self._ripetizioni) >= self.soglia_ripetizioni:
            motivo = "ripete " + "; ".join(dict.fromkeys(self._ripetizioni))
        elif self._errore_giri >= self.soglia_errori:
            motivo = (f"lo stesso errore da {self._errore_giri} giri di fila: "
                      f"{self._errore_corrente[:160]}")
        if not motivo:
            return Verdetto("ok")
        self._ripetizioni = []
        self._errore_giri = 0
        verdetto = Verdetto("stop" if self.avvisato else "avviso", motivo)
        self.avvisato = True
        self.interventi.append(verdetto)
        return verdetto


def messaggio_avviso(motivo: str) -> Dict[str, str]:
    return {"role": "user", "content": (
        f"{SEGNO} Ti stai ripetendo: {motivo}. Ripetere non porta informazioni nuove. "
        "Cambia approccio: rileggi l'errore o il risultato, prova un metodo diverso, oppure se sei "
        "bloccato rispondi all'utente spiegando dove. Se ti ripeti ancora il lavoro verra' fermato.")}


def messaggio_stop(motivo: str) -> Dict[str, str]:
    return {"role": "user", "content": (
        f"{SEGNO} Il lavoro e' stato fermato perche' girava in tondo: {motivo}. In questo turno non "
        "hai piu' strumenti: non scrivere chiamate, non verrebbero eseguite. Rispondi all'utente con "
        "quello che hai: cosa hai fatto, l'ultimo risultato valido, dove ti sei bloccato e cosa "
        "proveresti di diverso. Se stavi scrivendo codice per uno strumento riporta l'ultima versione: "
        "se l'utente scrive 'continua' riparti da li'.")}
