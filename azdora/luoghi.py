"""Luoghi: da nome a coordinate e da coordinate a nome, con servizi gratuiti e senza chiave.

- Ricerca per nome ("Cesenatico" → coordinate): API di geocoding di Open-Meteo.
- Ricerca inversa (coordinate → "Misano Adriatico (RN)"): Nominatim di OpenStreetMap.
  Le regole d'uso chiedono un User-Agent riconoscibile, al massimo una richiesta al secondo
  e l'attribuzione "© OpenStreetMap": a un bot personale basta e avanza.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Protocol

import httpx

from azdora.config import Localita
from azdora.meteo.openmeteo import ErroreMeteo

log = logging.getLogger(__name__)

URL_GEOCODING = "https://geocoding-api.open-meteo.com/v1/search"
URL_NOMINATIM = "https://nominatim.openstreetmap.org/reverse"
RAGGIO_TERRA_KM = 6371.0

# Dal più grande al più piccolo: si usa il primo che Nominatim conosce.
CHIAVI_INDIRIZZO = ("city", "town", "village", "municipality", "suburb", "hamlet")


class ErroreLuoghi(ErroreMeteo):
    """Il servizio di geocoding non risponde."""


def distanza_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distanza in linea d'aria tra due punti (formula dell'emisenoverso)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * RAGGIO_TERRA_KM * math.asin(math.sqrt(a))


def localita_qui(lat: float, lon: float, nome: str) -> Localita:
    """Una località costruita al volo da una posizione mandata su Telegram."""
    return Localita(chiave="posizione", nome=nome, lat=lat, lon=lon, mare_lat=lat, mare_lon=lon)


def leggi_ricerca(dati: dict[str, Any]) -> Localita | None:
    """Primo risultato della ricerca per nome di Open-Meteo, o ``None`` se non trova nulla."""
    risultati = dati.get("results") or []
    if not risultati:
        return None
    primo = risultati[0]
    nome = primo["name"]
    paese = primo.get("country")
    if primo.get("country_code") not in (None, "IT") and paese and paese != nome:
        nome = f"{nome} ({paese})"
    lat, lon = float(primo["latitude"]), float(primo["longitude"])
    return Localita(
        chiave=f"geo:{primo.get('id', nome)}",
        nome=nome,
        lat=lat,
        lon=lon,
        mare_lat=lat,
        mare_lon=lon,
    )


def leggi_nome_inverso(dati: dict[str, Any]) -> str | None:
    """Nome del comune (con la sigla della provincia, se italiana) da una risposta Nominatim."""
    indirizzo = dati.get("address") or {}
    nome = next((indirizzo[k] for k in CHIAVI_INDIRIZZO if indirizzo.get(k)), None)
    if nome is None:
        return dati.get("name") or None
    provincia = indirizzo.get("ISO3166-2-lvl6", "")
    if provincia.startswith("IT-"):
        return f"{nome} ({provincia.removeprefix('IT-')})"
    paese = indirizzo.get("country_code")
    if paese and paese != "it" and indirizzo.get("country"):
        return f"{nome} ({indirizzo['country']})"
    return nome


class CercaLuoghi(Protocol):
    def cerca(self, nome: str) -> Localita | None: ...

    def nome_di(self, lat: float, lon: float) -> str | None: ...


class Luoghi:
    """Implementazione reale con Open-Meteo e Nominatim."""

    def __init__(self, http: httpx.Client) -> None:
        self._http = http

    def cerca(self, nome: str) -> Localita | None:
        try:
            risposta = self._http.get(
                URL_GEOCODING,
                params={"name": nome, "count": 1, "language": "it", "format": "json"},
            )
            risposta.raise_for_status()
            return leggi_ricerca(risposta.json())
        except (httpx.HTTPError, ValueError, KeyError) as errore:
            raise ErroreLuoghi(f"Ricerca di {nome!r} non riuscita: {errore}") from errore

    def nome_di(self, lat: float, lon: float) -> str | None:
        """Il nome non è indispensabile: se Nominatim non risponde si restituisce ``None``."""
        try:
            risposta = self._http.get(
                URL_NOMINATIM,
                params={
                    "lat": lat,
                    "lon": lon,
                    "format": "jsonv2",
                    "zoom": 14,
                    "accept-language": "it",
                },
            )
            risposta.raise_for_status()
            return leggi_nome_inverso(risposta.json())
        except (httpx.HTTPError, ValueError) as errore:
            log.warning("Nominatim non disponibile: %s", errore)
            return None
