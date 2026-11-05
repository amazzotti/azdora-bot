"""Posizione salvata e "zona attuale": il posto su cui si basano notizie, allerte e riepilogo.

Se hai mandato la posizione negli ultimi giorni (anche in tempo reale), la zona è quella;
altrimenti si torna a casa, a Riccione.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from azdora.archivio import Archivio
from azdora.config import RICCIONE

# Dopo questo tempo una posizione vecchia non conta più per notizie e allerte.
VALIDITA_ZONA = timedelta(days=3)
# Quanto a lungo l'archivio conserva la posizione.
CONSERVAZIONE_S = 30 * 24 * 3600
# Con la posizione in tempo reale si richiede il nome del comune solo dopo uno spostamento.
SPOSTAMENTO_MINIMO_KM = 2.0


@dataclass(frozen=True)
class Posizione:
    lat: float
    lon: float
    quando: datetime
    comune: str | None = None
    provincia: str | None = None
    regione: str | None = None
    paese: str | None = None
    in_tempo_reale: bool = False

    @property
    def etichetta(self) -> str:
        if not self.comune:
            return f"{self.lat:.3f}, {self.lon:.3f}"
        return f"{self.comune} ({self.provincia})" if self.provincia else self.comune

    def in_dizionario(self) -> dict[str, Any]:
        dati = asdict(self)
        dati["quando"] = self.quando.isoformat()
        return dati

    @classmethod
    def da_dizionario(cls, dati: dict[str, Any]) -> Posizione:
        return cls(**{**dati, "quando": datetime.fromisoformat(dati["quando"])})


@dataclass(frozen=True)
class Zona:
    """Il posto "attuale": dove sei, oppure casa."""

    comune: str
    lat: float
    lon: float
    regione: str | None
    paese: str | None
    etichetta: str
    da_posizione: bool


ZONA_CASA = Zona(
    comune=RICCIONE.nome,
    lat=RICCIONE.lat,
    lon=RICCIONE.lon,
    regione="Emilia-Romagna",
    paese="it",
    etichetta=RICCIONE.nome,
    da_posizione=False,
)


class MemoriaPosizione:
    def __init__(self, archivio: Archivio) -> None:
        self._archivio = archivio

    @staticmethod
    def _chiave(chat_id: int) -> str:
        return f"posizione:{chat_id}"

    def salva(self, chat_id: int, posizione: Posizione) -> None:
        self._archivio.scrivi(self._chiave(chat_id), posizione.in_dizionario(), CONSERVAZIONE_S)

    def leggi(self, chat_id: int) -> Posizione | None:
        dati = self._archivio.leggi(self._chiave(chat_id))
        return None if dati is None else Posizione.da_dizionario(dati)

    def dimentica(self, chat_id: int) -> None:
        self._archivio.cancella(self._chiave(chat_id))

    def zona(self, chat_id: int | None, adesso: datetime) -> Zona:
        posizione = self.leggi(chat_id) if chat_id is not None else None
        if posizione is None or not posizione.comune or adesso - posizione.quando > VALIDITA_ZONA:
            return ZONA_CASA
        return Zona(
            comune=posizione.comune,
            lat=posizione.lat,
            lon=posizione.lon,
            regione=posizione.regione,
            paese=posizione.paese,
            etichetta=posizione.etichetta,
            da_posizione=True,
        )
