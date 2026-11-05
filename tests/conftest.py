"""Strumenti comuni ai test: dati Open-Meteo reali salvati su file e finti Telegram/meteo."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from azdora.bot import Azdora
from azdora.config import FUSO_ORARIO, RICCIONE, Impostazioni, Localita
from azdora.meteo.openmeteo import (
    ErroreMeteo,
    Previsioni,
    PrevisioniMare,
    leggi_mare,
    leggi_previsioni,
)

FIXTURES = Path(__file__).parent / "fixtures"
PADRONE = 111
COPPIA = -222
SCONOSCIUTO = 999
# Le fixture sono state scaricate giovedì 5 novembre 2026 alle 9:15.
ADESSO = datetime(2026, 11, 5, 7, 0, tzinfo=FUSO_ORARIO)


def carica(nome: str) -> dict[str, Any]:
    return json.loads((FIXTURES / nome).read_text(encoding="utf-8"))


@pytest.fixture
def json_previsioni() -> dict[str, Any]:
    return carica("previsioni_riccione.json")


@pytest.fixture
def json_mare() -> dict[str, Any]:
    return carica("mare_riccione.json")


@pytest.fixture
def previsioni(json_previsioni: dict[str, Any]) -> Previsioni:
    return leggi_previsioni(json_previsioni, RICCIONE)


@pytest.fixture
def mare(json_mare: dict[str, Any]) -> PrevisioniMare:
    return leggi_mare(json_mare, RICCIONE)


@dataclass
class FintoMessaggero:
    """Registra i messaggi invece di spedirli."""

    inviati: list[tuple[int, str]] = field(default_factory=list)
    uscite: list[int] = field(default_factory=list)

    def invia_messaggio(self, chat_id: int, testo: str) -> None:
        self.inviati.append((chat_id, testo))

    def esci_da_chat(self, chat_id: int) -> None:
        self.uscite.append(chat_id)

    @property
    def ultimo(self) -> str:
        assert self.inviati, "nessun messaggio inviato"
        return self.inviati[-1][1]


@dataclass
class FintoMeteo:
    """Restituisce sempre i dati salvati, attribuiti alla località richiesta."""

    dati_previsioni: dict[str, Any]
    dati_mare: dict[str, Any]
    guasto: bool = False
    mare_guasto: bool = False
    richieste: list[str] = field(default_factory=list)

    def previsioni(self, localita: Localita) -> Previsioni:
        self.richieste.append(f"previsioni:{localita.chiave}")
        if self.guasto:
            raise ErroreMeteo("finto guasto")
        return leggi_previsioni(self.dati_previsioni, localita)

    def mare(self, localita: Localita) -> PrevisioniMare:
        self.richieste.append(f"mare:{localita.chiave}")
        if self.guasto or self.mare_guasto:
            raise ErroreMeteo("finto guasto del mare")
        return leggi_mare(self.dati_mare, localita)


@pytest.fixture
def impostazioni() -> Impostazioni:
    return Impostazioni(
        token="123:finto",
        chat_personale=PADRONE,
        chat_coppia=COPPIA,
        segreto_webhook="segreto-webhook",
        segreto_cron="segreto-cron",
        username_bot="azdora_test_bot",
        nome_utente="Ale",
    )


@pytest.fixture
def messaggero() -> FintoMessaggero:
    return FintoMessaggero()


@pytest.fixture
def meteo(json_previsioni: dict[str, Any], json_mare: dict[str, Any]) -> FintoMeteo:
    return FintoMeteo(json_previsioni, json_mare)


@pytest.fixture
def bot(impostazioni: Impostazioni, messaggero: FintoMessaggero, meteo: FintoMeteo) -> Azdora:
    return Azdora(impostazioni, messaggero, meteo, orologio=lambda: ADESSO)


def messaggio(
    testo: str, chat_id: int = PADRONE, tipo: str = "private", mittente: int | None = None
) -> dict[str, Any]:
    """Costruisce un update di Telegram come quelli veri."""
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": chat_id, "type": tipo},
            "from": {"id": mittente if mittente is not None else chat_id, "first_name": "Test"},
            "text": testo,
        },
    }
