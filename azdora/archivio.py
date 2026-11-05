"""Archivio: la memoria del bot (posizione, notizie già mandate, scadenze, spese…).

Su Vercel ogni richiesta parte da zero, quindi i dati vanno salvati fuori. Si usa
Upstash Redis (piano gratuito, senza carta) tramite la sua API REST: bastano ``httpx``
e due variabili d'ambiente, nessun driver.

Per i test e il simulatore c'è ``ArchivioMemoria``, che tiene tutto in un dizionario.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import httpx


class ErroreArchivio(RuntimeError):
    """L'archivio non risponde o ha risposto con un errore."""


class Archivio(Protocol):
    @property
    def persistente(self) -> bool:
        """``True`` se i dati sopravvivono tra una richiesta e l'altra."""
        ...

    def leggi(self, chiave: str) -> Any | None: ...

    def scrivi(self, chiave: str, valore: Any, scadenza_s: int | None = None) -> None: ...

    def cancella(self, chiave: str) -> None: ...

    def segna_nuovi(self, chiavi: Sequence[str], scadenza_s: int) -> list[bool]:
        """Segna le chiavi come "viste"; per ognuna dice se era nuova (mai vista prima)."""
        ...


class ArchivioMemoria:
    """Archivio in memoria, con scadenze vere. Non sopravvive alla fine del processo.

    ``persistente=True`` serve solo a test e simulatore, per fingere di avere Upstash.
    """

    def __init__(
        self, orologio: Callable[[], float] = time.monotonic, persistente: bool = False
    ) -> None:
        self._dati: dict[str, tuple[str, float | None]] = {}
        self._orologio = orologio
        self.persistente = persistente

    def _valido(self, chiave: str) -> tuple[str, float | None] | None:
        voce = self._dati.get(chiave)
        if voce is None:
            return None
        _, scade = voce
        if scade is not None and self._orologio() >= scade:
            del self._dati[chiave]
            return None
        return voce

    def leggi(self, chiave: str) -> Any | None:
        voce = self._valido(chiave)
        return None if voce is None else json.loads(voce[0])

    def scrivi(self, chiave: str, valore: Any, scadenza_s: int | None = None) -> None:
        scade = None if scadenza_s is None else self._orologio() + scadenza_s
        self._dati[chiave] = (json.dumps(valore, ensure_ascii=False), scade)

    def cancella(self, chiave: str) -> None:
        self._dati.pop(chiave, None)

    def segna_nuovi(self, chiavi: Sequence[str], scadenza_s: int) -> list[bool]:
        risultati = []
        for chiave in chiavi:
            nuova = self._valido(chiave) is None
            if nuova:
                self.scrivi(chiave, 1, scadenza_s)
            risultati.append(nuova)
        return risultati

    def chiavi(self) -> list[str]:
        """Solo per i test: le chiavi ancora valide."""
        return [k for k in list(self._dati) if self._valido(k) is not None]


class ArchivioUpstash:
    """Archivio su Upstash Redis via REST (https://upstash.com/docs/redis/features/restapi)."""

    persistente = True

    def __init__(self, http: httpx.Client, url: str, token: str, prefisso: str = "azdora:") -> None:
        self._http = http
        self._url = url.rstrip("/")
        self._intestazioni = {"Authorization": f"Bearer {token}"}
        self._prefisso = prefisso

    def _k(self, chiave: str) -> str:
        return f"{self._prefisso}{chiave}"

    def _pipeline(self, comandi: list[list[Any]]) -> list[Any]:
        """Esegue più comandi Redis in una sola richiesta HTTP."""
        if not comandi:
            return []
        try:
            risposta = self._http.post(
                f"{self._url}/pipeline", json=comandi, headers=self._intestazioni
            )
            risposta.raise_for_status()
            risultati = risposta.json()
        except (httpx.HTTPError, ValueError) as errore:
            raise ErroreArchivio(f"Upstash non disponibile: {type(errore).__name__}") from None
        if not isinstance(risultati, list) or len(risultati) != len(comandi):
            raise ErroreArchivio("Risposta di Upstash non valida")
        valori = []
        for r in risultati:
            if not isinstance(r, dict) or "error" in r:
                errore = r.get("error") if isinstance(r, dict) else r
                raise ErroreArchivio(f"Upstash: {errore}")
            valori.append(r.get("result"))
        return valori

    def leggi(self, chiave: str) -> Any | None:
        (valore,) = self._pipeline([["GET", self._k(chiave)]])
        return None if valore is None else json.loads(valore)

    def scrivi(self, chiave: str, valore: Any, scadenza_s: int | None = None) -> None:
        comando: list[Any] = ["SET", self._k(chiave), json.dumps(valore, ensure_ascii=False)]
        if scadenza_s is not None:
            comando += ["EX", int(scadenza_s)]
        self._pipeline([comando])

    def cancella(self, chiave: str) -> None:
        self._pipeline([["DEL", self._k(chiave)]])

    def segna_nuovi(self, chiavi: Sequence[str], scadenza_s: int) -> list[bool]:
        # SET ... NX risponde "OK" solo se la chiave non esisteva.
        comandi = [["SET", self._k(c), "1", "NX", "EX", int(scadenza_s)] for c in chiavi]
        return [r == "OK" for r in self._pipeline(comandi)]
