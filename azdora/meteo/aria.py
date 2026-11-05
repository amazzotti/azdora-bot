"""Indice UV, qualità dell'aria e pollini, dall'API gratuita di Open-Meteo (dati CAMS europei)."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any, Protocol

import httpx

from azdora.config import Localita

URL_ARIA = "https://air-quality-api.open-meteo.com/v1/air-quality"

POLLINI = {
    "grass_pollen": "graminacee",
    "birch_pollen": "betulla",
    "alder_pollen": "ontano",
    "olive_pollen": "olivo",
    "mugwort_pollen": "artemisia",
    "ragweed_pollen": "ambrosia",
}
# Granuli per m³: soglie indicative comuni a tutti i pollini.
SOGLIE_POLLINI = ((10, "basso"), (50, "medio"), (200, "alto"))
LIVELLI_AQI = ((20, "ottima"), (40, "buona"), (60, "discreta"), (80, "scarsa"), (100, "cattiva"))


@dataclass(frozen=True)
class Aria:
    uv_max: float | None
    aqi_max: float | None
    pollini: dict[str, float]  # nome italiano → massimo di oggi (granuli/m³)


def _massimo(valori: list[Any] | None) -> float | None:
    numeri = [float(v) for v in valori or [] if v is not None]
    return max(numeri) if numeri else None


def leggi_aria(dati: dict[str, Any]) -> Aria:
    orario = dati.get("hourly") or {}
    pollini = {}
    for chiave, nome in POLLINI.items():
        valore = _massimo(orario.get(chiave))
        if valore is not None:
            pollini[nome] = valore
    return Aria(_massimo(orario.get("uv_index")), _massimo(orario.get("european_aqi")), pollini)


def livello_polline(valore: float) -> str:
    for soglia, nome in SOGLIE_POLLINI:
        if valore < soglia:
            return nome
    return "molto alto"


def livello_aqi(valore: float) -> str:
    for soglia, nome in LIVELLI_AQI:
        if valore <= soglia:
            return nome
    return "pessima"


def consiglio_uv(uv: float) -> str:
    if uv < 3:
        return "basso, niente crema"
    if uv < 6:
        return "moderato: crema se stai fuori a lungo"
    if uv < 8:
        return "alto: crema, cappello e occhiali"
    return "molto alto: evita il sole tra le 12 e le 16"


def messaggio_aria(aria: Aria, nome_posto: str) -> str:
    righe = [f"🌿 <b>Aria a {escape(nome_posto)}</b>", ""]
    if aria.uv_max is not None:
        righe.append(f"☀️ UV massimo {aria.uv_max:.0f}: {consiglio_uv(aria.uv_max)}")
    if aria.aqi_max is not None:
        righe.append(
            f"😮‍💨 Qualità dell'aria {livello_aqi(aria.aqi_max)} "
            f"(indice europeo {aria.aqi_max:.0f})"
        )
    presenti = {n: v for n, v in aria.pollini.items() if v >= SOGLIE_POLLINI[0][0]}
    if presenti:
        righe.append(
            "🤧 Pollini: " + ", ".join(f"{n} {livello_polline(v)}" for n, v in presenti.items())
        )
    elif aria.pollini:
        righe.append("🤧 Pollini: bassi, respira tranquillo")
    righe += ["", '<i>Dati: <a href="https://open-meteo.com/">Open-Meteo.com</a> · CAMS</i>']
    return "\n".join(righe)


def avvisi_aria(aria: Aria) -> list[str]:
    """Righe per il riepilogo, solo quando c'è qualcosa da dire."""
    righe = []
    alti = [n for n, v in aria.pollini.items() if v >= SOGLIE_POLLINI[1][0]]
    if alti:
        righe.append(f"🤧 Pollini alti: {', '.join(alti)}. Antistaminico a portata di mano.")
    if aria.aqi_max is not None and aria.aqi_max > 60:
        righe.append(f"😷 Aria {livello_aqi(aria.aqi_max)}: meglio evitare sport all'aperto.")
    return righe


class FonteAria(Protocol):
    def aria(self, localita: Localita) -> Aria | None: ...


class OpenMeteoAria:
    def __init__(self, http: httpx.Client) -> None:
        self._http = http

    def aria(self, localita: Localita) -> Aria | None:
        """``None`` se il servizio non risponde: l'aria non è indispensabile."""
        try:
            risposta = self._http.get(
                URL_ARIA,
                params={
                    "latitude": localita.lat,
                    "longitude": localita.lon,
                    "hourly": ",".join(["uv_index", "european_aqi", *POLLINI]),
                    "timezone": "Europe/Rome",
                    "forecast_days": 1,
                },
            )
            risposta.raise_for_status()
            return leggi_aria(risposta.json())
        except (httpx.HTTPError, ValueError):
            return None
