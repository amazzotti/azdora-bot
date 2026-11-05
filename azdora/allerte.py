"""Allerte meteo da Meteoalarm (https://meteoalarm.org), gratis e senza chiave.

Per l'Italia i dati arrivano dal Servizio Meteorologico dell'Aeronautica Militare e sono
divisi per regione. Non sostituiscono le allerte ufficiali della Protezione Civile: per
l'Emilia-Romagna il bot aggiunge sempre il link ad Allerta Meteo Emilia-Romagna.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Any, Protocol

import httpx

from azdora.config import FUSO_ORARIO

log = logging.getLogger(__name__)

URL_METEOALARM = "https://feeds.meteoalarm.org/api/v1/warnings/feeds-{paese}"
URL_ALLERTA_ER = "https://allertameteo.regione.emilia-romagna.it/"

# Codice ISO del paese (come lo dà Nominatim) → nome del feed di Meteoalarm.
FEED_PER_PAESE = {
    "it": "italy",
    "fr": "france",
    "de": "germany",
    "at": "austria",
    "ch": "switzerland",
    "es": "spain",
    "pt": "portugal",
    "hr": "croatia",
    "si": "slovenia",
    "gr": "greece",
    "nl": "netherlands",
    "be": "belgium",
}

LIVELLO_MINIMO = 2  # 1 = verde (nessun pericolo), 2 = giallo, 3 = arancione, 4 = rosso
COLORI = {2: ("🟡", "gialla"), 3: ("🟠", "arancione"), 4: ("🔴", "rossa")}
FENOMENI = {
    1: "Vento",
    2: "Neve e ghiaccio",
    3: "Temporali",
    4: "Nebbia",
    5: "Caldo",
    6: "Freddo",
    7: "Mareggiate",
    8: "Incendi boschivi",
    9: "Valanghe",
    10: "Pioggia",
    12: "Alluvioni",
    13: "Piene e alluvioni",
}
_PAROLE_VUOTE = frozenset({"e", "di", "del", "della", "and"})


@dataclass(frozen=True)
class Allerta:
    identificativo: str
    regione: str
    livello: int
    fenomeno: str
    inizio: datetime | None
    fine: datetime | None
    link: str | None = None

    @property
    def chiave(self) -> str:
        """Per non mandare due volte la stessa allerta (cambia se cambia il livello)."""
        return f"allerta:{self.identificativo}:{self.livello}"

    def attiva(self, adesso: datetime) -> bool:
        return self.fine is None or self.fine > adesso


def normalizza_regione(nome: str) -> frozenset[str]:
    """``"Emilia-Romagna"`` e ``"Emilia e Romagna"`` diventano lo stesso insieme di parole."""
    parole = re.split(r"[^a-zà-ù]+", nome.lower())
    return frozenset(p for p in parole if p and p not in _PAROLE_VUOTE)


def _numero_iniziale(valore: str | None) -> int | None:
    """``"2; yellow; Moderate"`` → 2."""
    if not valore:
        return None
    testa = valore.split(";", 1)[0].strip()
    return int(testa) if testa.isdigit() else None


def _data(testo: str | None) -> datetime | None:
    if not testo:
        return None
    try:
        return datetime.fromisoformat(testo)
    except ValueError:
        return None


def leggi_allerte(dati: dict[str, Any]) -> list[Allerta]:
    """Trasforma la risposta JSON di Meteoalarm in allerte, preferendo i testi in italiano."""
    allerte = []
    for avviso in dati.get("warnings") or []:
        allerta = avviso.get("alert") or {}
        infos = allerta.get("info") or []
        if not infos:
            continue
        info = next((i for i in infos if str(i.get("language", "")).startswith("it")), infos[0])
        parametri = {p.get("valueName"): p.get("value") for p in info.get("parameter") or []}
        livello = _numero_iniziale(parametri.get("awareness_level"))
        if livello is None:
            continue
        tipo = _numero_iniziale(parametri.get("awareness_type"))
        fenomeno = FENOMENI.get(tipo or -1, info.get("event") or "Fenomeni meteo")
        for area in info.get("area") or []:
            allerte.append(
                Allerta(
                    identificativo=str(allerta.get("identifier", "")),
                    regione=area.get("areaDesc", ""),
                    livello=livello,
                    fenomeno=fenomeno,
                    inizio=_data(info.get("onset") or info.get("effective")),
                    fine=_data(info.get("expires")),
                    link=info.get("web"),
                )
            )
    return allerte


def per_regione(
    allerte: Iterable[Allerta], regione: str, adesso: datetime, livello_minimo: int = LIVELLO_MINIMO
) -> list[Allerta]:
    cercata = normalizza_regione(regione)
    scelte = [
        a
        for a in allerte
        if a.livello >= livello_minimo
        and a.attiva(adesso)
        and normalizza_regione(a.regione) == cercata
    ]
    return sorted(scelte, key=lambda a: (-a.livello, a.inizio or adesso))


def _quando(data: datetime | None) -> str:
    if data is None:
        return "?"
    giorni = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")
    locale = data.astimezone(FUSO_ORARIO)
    return f"{giorni[locale.weekday()]} {locale.day}/{locale.month} {locale:%H:%M}"


def riga_allerta(allerta: Allerta) -> str:
    emoji, colore = COLORI.get(allerta.livello, ("⚠️", "di livello sconosciuto"))
    return (
        f"{emoji} <b>Allerta {colore}: {escape(allerta.fenomeno)}</b>\n"
        f"dal {_quando(allerta.inizio)} al {_quando(allerta.fine)}"
    )


def formatta(allerte: Sequence[Allerta], regione: str, titolo: str) -> str:
    righe = [titolo, ""]
    righe += [riga_allerta(a) for a in allerte]
    link = next((a.link for a in allerte if a.link), None)
    righe.append("")
    if link:
        righe.append(f'Dettagli: <a href="{escape(link, quote=True)}">Meteoalarm</a>')
    if normalizza_regione(regione) == normalizza_regione("Emilia-Romagna"):
        righe.append(
            f'Allerte ufficiali: <a href="{URL_ALLERTA_ER}">Allerta Meteo Emilia-Romagna</a>'
        )
    return "\n".join(righe)


class FonteAllerte(Protocol):
    def allerte(self, paese: str) -> list[Allerta]: ...


class Meteoalarm:
    def __init__(self, http: httpx.Client) -> None:
        self._http = http
        self._gia_scaricate: dict[str, list[Allerta]] = {}

    def allerte(self, paese: str) -> list[Allerta]:
        """Allerte del paese; scaricate una volta sola per giro di controlli."""
        chiave = paese.lower()
        if chiave not in self._gia_scaricate:
            self._gia_scaricate[chiave] = self._scarica(chiave)
        return self._gia_scaricate[chiave]

    def _scarica(self, paese: str) -> list[Allerta]:
        feed = FEED_PER_PAESE.get(paese)
        if feed is None:
            return []
        try:
            risposta = self._http.get(URL_METEOALARM.format(paese=feed))
            risposta.raise_for_status()
            return leggi_allerte(risposta.json())
        except (httpx.HTTPError, ValueError) as errore:
            log.warning("Meteoalarm non disponibile: %s", errore)
            return []
