"""Il distributore più economico vicino a te, dagli open data del Ministero (MIMIT).

Ogni mattina il Ministero pubblica due file: l'elenco dei distributori (con le coordinate)
e i prezzi comunicati. Licenza IODL 2.0: si possono usare citando la fonte.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from typing import Protocol

import httpx

from azdora.config import FUSO_ORARIO
from azdora.luoghi import distanza_km

log = logging.getLogger(__name__)

URL_ANAGRAFICA = "https://www.mimit.gov.it/images/exportCSV/anagrafica_impianti_attivi.csv"
URL_PREZZI = "https://www.mimit.gov.it/images/exportCSV/prezzo_alle_8.csv"

# Cosa scrivi → come si chiama nei dati del Ministero.
CARBURANTI = {
    "benzina": "Benzina",
    "gasolio": "Gasolio",
    "diesel": "Gasolio",
    "gpl": "GPL",
    "metano": "Metano",
}
RAGGIO_KM = 7
QUANTI = 5
PREZZO_VECCHIO = timedelta(days=7)


@dataclass(frozen=True)
class Distributore:
    id: str
    bandiera: str
    nome: str
    indirizzo: str
    comune: str
    lat: float
    lon: float


@dataclass(frozen=True)
class Offerta:
    distributore: Distributore
    prezzo: float  # euro al litro (o al kg per il metano)
    self_service: bool
    aggiornato: datetime | None
    distanza_km: float


def _righe(testo: str) -> Iterable[list[str]]:
    """Salta la prima riga ("Estrazione del …") e l'intestazione; divide per ``|``."""
    for numero, riga in enumerate(testo.splitlines()):
        if numero < 2 or not riga.strip():
            continue
        yield [campo.strip() for campo in riga.split("|")]


def leggi_distributori(
    testo: str, lat: float, lon: float, raggio_km: float
) -> dict[str, tuple[Distributore, float]]:
    """I distributori entro ``raggio_km``, con la distanza."""
    vicini = {}
    for campi in _righe(testo):
        if len(campi) < 10:
            continue
        try:
            d_lat, d_lon = float(campi[8]), float(campi[9])
        except ValueError:
            continue
        distanza = distanza_km(lat, lon, d_lat, d_lon)
        if distanza <= raggio_km:
            d = Distributore(campi[0], campi[2], campi[4], campi[5], campi[6].title(), d_lat, d_lon)
            vicini[d.id] = (d, distanza)
    return vicini


def _data(testo: str) -> datetime | None:
    try:
        return datetime.strptime(testo, "%d/%m/%Y %H:%M:%S").replace(tzinfo=FUSO_ORARIO)
    except ValueError:
        return None


def migliori_offerte(
    testo_prezzi: str,
    vicini: dict[str, tuple[Distributore, float]],
    carburante: str,
    adesso: datetime,
    quanti: int = QUANTI,
) -> list[Offerta]:
    """Le offerte più economiche: per ogni distributore il prezzo migliore (self se c'è)."""
    migliori: dict[str, Offerta] = {}
    for campi in _righe(testo_prezzi):
        if len(campi) < 5 or campi[0] not in vicini or campi[1] != carburante:
            continue
        try:
            prezzo = float(campi[2])
        except ValueError:
            continue
        aggiornato = _data(campi[4])
        if prezzo <= 0 or (aggiornato and adesso - aggiornato > PREZZO_VECCHIO):
            continue
        distributore, distanza = vicini[campi[0]]
        offerta = Offerta(distributore, prezzo, campi[3] == "1", aggiornato, distanza)
        attuale = migliori.get(distributore.id)
        if attuale is None or offerta.prezzo < attuale.prezzo:
            migliori[distributore.id] = offerta
    return sorted(migliori.values(), key=lambda o: (o.prezzo, o.distanza_km))[:quanti]


def messaggio(offerte: list[Offerta], carburante: str, posto: str) -> str:
    if not offerte:
        return (
            f"⛽ Nessun prezzo recente di {carburante.lower()} "
            f"entro {RAGGIO_KM} km da {escape(posto)}."
        )
    aggettivo = "economica" if carburante == "Benzina" else "economico"
    unita = "kg" if carburante == "Metano" else "l"
    righe = [f"⛽ <b>{escape(carburante)} più {aggettivo} vicino a {escape(posto)}</b>", ""]
    for i, o in enumerate(offerte, start=1):
        d = o.distributore
        mappa = f"https://www.google.com/maps/search/?api=1&query={d.lat},{d.lon}"
        modo = "self" if o.self_service else "servito"
        quando = f" · {o.aggiornato:%d/%m}" if o.aggiornato else ""
        prezzo = f"{o.prezzo:.3f}".replace(".", ",")
        distanza = f"{o.distanza_km:.1f}".replace(".", ",")
        righe.append(
            f"{i}. <b>{prezzo} €/{unita}</b> ({modo}) · {escape(d.bandiera or d.nome)}\n"
            f'   <a href="{escape(mappa, quote=True)}">'
            f"{escape(d.indirizzo.title())}, {escape(d.comune)}</a>"
            f" · {distanza} km{quando}"
        )
    righe += ["", "<i>Dati: Ministero delle Imprese (osservaprezzi), aggiornati ogni mattina</i>"]
    return "\n".join(righe)


USO = (
    "⛽ Scrivi <code>/benzina</code>, <code>/benzina gasolio</code>, <code>/benzina gpl</code> "
    "o <code>/benzina metano</code>; aggiungi un posto per cercare altrove: "
    "<code>/benzina gasolio cesena</code>"
)


class FonteCarburante(Protocol):
    def offerte(
        self, carburante: str, lat: float, lon: float, adesso: datetime
    ) -> list[Offerta]: ...


class Osservaprezzi:
    def __init__(self, http: httpx.Client) -> None:
        self._http = http

    def _scarica(self, url: str) -> str:
        risposta = self._http.get(url, timeout=httpx.Timeout(25.0), follow_redirects=True)
        risposta.raise_for_status()
        return risposta.content.decode("utf-8", errors="replace")

    def offerte(self, carburante: str, lat: float, lon: float, adesso: datetime) -> list[Offerta]:
        vicini = leggi_distributori(self._scarica(URL_ANAGRAFICA), lat, lon, RAGGIO_KM)
        if not vicini:
            return []
        return migliori_offerte(self._scarica(URL_PREZZI), vicini, carburante, adesso)
