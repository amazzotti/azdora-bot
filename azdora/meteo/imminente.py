"""Avvisi "sta per succedere": pioggia, temporali, neve e raffiche nelle prossime 2 ore.

Usa le previsioni a 15 minuti di Open-Meteo (in Europa vengono dai modelli ad alta
risoluzione, aggiornati più volte all'ora): non è un radar vero, ma ci si avvicina.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Any, Protocol

import httpx

from azdora.config import FUSO_ORARIO
from azdora.meteo.codici_wmo import CODICI_NEVE, CODICI_PIOGGIA, CODICI_TEMPORALE

URL = "https://api.open-meteo.com/v1/forecast"
QUARTI_AVANTI = 8  # 2 ore
PIOGGIA_MINIMA = 0.2  # mm in 15 minuti
RAFFICHE_FORTI = 60  # km/h


@dataclass(frozen=True)
class Quarto:
    ora: datetime
    pioggia: float | None
    codice: int | None
    raffiche: float | None


@dataclass(frozen=True)
class Avviso:
    tipo: str  # pioggia, temporale, neve, raffiche
    testo: str


def leggi_quarti(dati: dict[str, Any]) -> list[Quarto]:
    blocco = dati.get("minutely_15") or {}
    tempi = blocco.get("time") or []

    def colonna(nome: str) -> list[Any]:
        return blocco.get(nome) or [None] * len(tempi)

    pioggia, codici, raffiche = (
        colonna("precipitation"),
        colonna("weather_code"),
        colonna("wind_gusts_10m"),
    )
    return [
        Quarto(
            datetime.fromisoformat(t).replace(tzinfo=FUSO_ORARIO),
            None if pioggia[i] is None else float(pioggia[i]),
            None if codici[i] is None else int(codici[i]),
            None if raffiche[i] is None else float(raffiche[i]),
        )
        for i, t in enumerate(tempi)
    ]


def _minuti(da: datetime, a: datetime) -> int:
    return max(0, round((a - da).total_seconds() / 60 / 5) * 5)


def _quando(adesso: datetime, ora: datetime) -> str:
    minuti = _minuti(adesso, ora)
    if minuti <= 10:
        return "a minuti"
    if minuti < 60:
        return f"tra circa {minuti} minuti"
    return f"verso le {ora:%H:%M}"


def valuta(quarti: list[Quarto], posto: str, adesso: datetime) -> list[Avviso]:
    """Cosa sta per succedere nelle prossime 2 ore, dal più grave."""
    if not quarti:
        return []
    ora_attuale = quarti[0]
    prossimi = [q for q in quarti[1 : QUARTI_AVANTI + 1] if q.ora > adesso]
    posto = escape(posto)
    avvisi: list[Avviso] = []

    if (t := next((q for q in prossimi if q.codice in CODICI_TEMPORALE), None)) is not None:
        avvisi.append(
            Avviso(
                "temporale",
                f"⛈️ <b>Temporale in arrivo</b> a {posto} {_quando(adesso, t.ora)}. "
                "Se sei fuori, cerca un riparo.",
            )
        )
    if (n := next((q for q in prossimi if q.codice in CODICI_NEVE), None)) is not None:
        avvisi.append(Avviso("neve", f"❄️ <b>Neve</b> a {posto} {_quando(adesso, n.ora)}."))

    piove_ora = (ora_attuale.pioggia or 0) >= PIOGGIA_MINIMA or ora_attuale.codice in CODICI_PIOGGIA
    if not piove_ora and not any(a.tipo == "temporale" for a in avvisi):
        inizio = next((q for q in prossimi if (q.pioggia or 0) >= PIOGGIA_MINIMA), None)
        if inizio is not None:
            totale = sum(q.pioggia or 0 for q in prossimi)
            intensita = "debole" if totale < 1 else "moderata" if totale < 4 else "forte"
            millimetri = f"{totale:.1f}".replace(".", ",")
            avvisi.append(
                Avviso(
                    "pioggia",
                    f"☔ <b>Sta per piovere</b> a {posto}: {_quando(adesso, inizio.ora)}, "
                    f"pioggia {intensita} (circa {millimetri} mm nelle prossime 2 ore). "
                    "Ombrello!",
                )
            )
    raffiche = [q.raffiche for q in prossimi if q.raffiche is not None]
    if raffiche and max(raffiche) >= RAFFICHE_FORTI:
        avvisi.append(
            Avviso(
                "raffiche",
                f"💨 <b>Raffiche fino a {max(raffiche):.0f} km/h</b> a {posto} nelle prossime "
                "2 ore: occhio a vasi, ombrelloni e scooter.",
            )
        )
    return avvisi


class FonteImminente(Protocol):
    def quarti(self, lat: float, lon: float) -> list[Quarto]: ...


class OpenMeteoImminente:
    def __init__(self, http: httpx.Client) -> None:
        self._http = http
        self._gia: dict[tuple[float, float], list[Quarto]] = {}

    def quarti(self, lat: float, lon: float) -> list[Quarto]:
        """Le previsioni a 15 minuti; una sola richiesta per posto a ogni giro."""
        chiave = (round(lat, 2), round(lon, 2))
        if chiave not in self._gia:
            try:
                risposta = self._http.get(
                    URL,
                    params={
                        "latitude": lat,
                        "longitude": lon,
                        "minutely_15": "precipitation,weather_code,wind_gusts_10m",
                        "forecast_minutely_15": QUARTI_AVANTI + 1,
                        "timezone": "Europe/Rome",
                    },
                )
                risposta.raise_for_status()
                self._gia[chiave] = leggi_quarti(risposta.json())
            except (httpx.HTTPError, ValueError):
                self._gia[chiave] = []
        return self._gia[chiave]
