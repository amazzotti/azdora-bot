"""Montaggio dei pezzi reali: Telegram + Open-Meteo + impostazioni dall'ambiente."""

from __future__ import annotations

import logging

import httpx

from azdora.bot import Azdora
from azdora.config import Impostazioni
from azdora.meteo.openmeteo import OpenMeteo
from azdora.telegram import Telegram

USER_AGENT = "azdora-bot (+https://github.com/amazzotti/azdora-bot)"


def configura_log() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # httpx scrive l'URL di ogni chiamata, che per Telegram contiene il token.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def nuovo_client_http() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(10.0), headers={"User-Agent": USER_AGENT})


def crea_azdora(impostazioni: Impostazioni, http: httpx.Client) -> Azdora:
    return Azdora(
        impostazioni=impostazioni,
        messaggero=Telegram(impostazioni.richiedi_token(), http),
        meteo=OpenMeteo(http),
    )
