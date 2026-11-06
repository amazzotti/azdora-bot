"""Montaggio dei pezzi reali: Telegram + Open-Meteo + impostazioni dall'ambiente."""

from __future__ import annotations

import logging

import httpx

from azdora.allerte import Meteoalarm
from azdora.archivio import Archivio, ArchivioMemoria, ArchivioUpstash
from azdora.bot import Azdora
from azdora.carburante import Osservaprezzi
from azdora.config import Impostazioni
from azdora.luoghi import Luoghi
from azdora.meteo.aria import OpenMeteoAria
from azdora.meteo.imminente import OpenMeteoImminente
from azdora.meteo.openmeteo import OpenMeteo
from azdora.notizie import LettoreNotizie
from azdora.telegram import Telegram

USER_AGENT = "azdora-bot (+https://github.com/amazzotti/azdora-bot)"


def configura_log() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # httpx scrive l'URL di ogni chiamata, che per Telegram contiene il token.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def nuovo_client_http() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(8.0, connect=4.0), headers={"User-Agent": USER_AGENT})


def crea_archivio(impostazioni: Impostazioni, http: httpx.Client) -> Archivio:
    if impostazioni.archivio_url and impostazioni.archivio_token:
        return ArchivioUpstash(http, impostazioni.archivio_url, impostazioni.archivio_token)
    logging.getLogger(__name__).warning(
        "Upstash non configurato: il bot non ricorderà posizione e notizie già mandate"
    )
    return ArchivioMemoria()


def crea_azdora(impostazioni: Impostazioni, http: httpx.Client) -> Azdora:
    return Azdora(
        impostazioni=impostazioni,
        messaggero=Telegram(impostazioni.richiedi_token(), http),
        meteo=OpenMeteo(http),
        luoghi=Luoghi(http),
        archivio=crea_archivio(impostazioni, http),
        notizie=LettoreNotizie(http),
        allerte=Meteoalarm(http),
        aria=OpenMeteoAria(http),
        carburante=Osservaprezzi(http),
        imminente=OpenMeteoImminente(http),
    )
