"""Traduzione dei codici meteo WMO (usati da Open-Meteo) in testo italiano ed emoji.

Riferimento: tabella "WMO Weather interpretation codes" nella documentazione di Open-Meteo.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Condizione:
    descrizione: str
    emoji: str

    def __str__(self) -> str:
        return f"{self.emoji} {self.descrizione}"


CONDIZIONI: dict[int, Condizione] = {
    0: Condizione("Sereno", "☀️"),
    1: Condizione("Prevalentemente sereno", "🌤️"),
    2: Condizione("Parzialmente nuvoloso", "⛅"),
    3: Condizione("Coperto", "☁️"),
    45: Condizione("Nebbia", "🌫️"),
    48: Condizione("Nebbia con brina", "🌫️"),
    51: Condizione("Pioviggine leggera", "🌦️"),
    53: Condizione("Pioviggine", "🌦️"),
    55: Condizione("Pioviggine intensa", "🌧️"),
    56: Condizione("Pioviggine gelata", "🌧️"),
    57: Condizione("Pioviggine gelata intensa", "🌧️"),
    61: Condizione("Pioggia debole", "🌧️"),
    63: Condizione("Pioggia", "🌧️"),
    65: Condizione("Pioggia forte", "🌧️"),
    66: Condizione("Pioggia gelata", "🧊"),
    67: Condizione("Pioggia gelata forte", "🧊"),
    71: Condizione("Neve debole", "🌨️"),
    73: Condizione("Neve", "🌨️"),
    75: Condizione("Neve forte", "❄️"),
    77: Condizione("Granelli di neve", "🌨️"),
    80: Condizione("Rovesci deboli", "🌦️"),
    81: Condizione("Rovesci", "🌧️"),
    82: Condizione("Rovesci violenti", "⛈️"),
    85: Condizione("Rovesci di neve", "🌨️"),
    86: Condizione("Forti rovesci di neve", "❄️"),
    95: Condizione("Temporale", "⛈️"),
    96: Condizione("Temporale con grandine", "⛈️"),
    99: Condizione("Temporale con grandine forte", "⛈️"),
}

SCONOSCIUTA = Condizione("Condizioni non disponibili", "❔")

CODICI_PIOGGIA = frozenset({51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82})
CODICI_NEVE = frozenset({71, 73, 75, 77, 85, 86})
CODICI_TEMPORALE = frozenset({95, 96, 99})
CODICI_NEBBIA = frozenset({45, 48})


def condizione(codice: int | None) -> Condizione:
    """Restituisce la condizione per un codice WMO, o una generica se è sconosciuto."""
    if codice is None:
        return SCONOSCIUTA
    return CONDIZIONI.get(codice, SCONOSCIUTA)
