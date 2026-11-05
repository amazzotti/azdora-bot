"""Simulatore: prova il bot dal terminale, senza Telegram e senza token.

Ogni argomento è un messaggio che "scrivi" al bot; le risposte vengono stampate a schermo.
Il meteo è quello vero di Open-Meteo, oppure quello salvato nei file di esempio con ``--offline``.

Esempi::

    python -m azdora.simula /oggi
    python -m azdora.simula "/vento rimini" /mare
    python -m azdora.simula mattino            # il messaggio delle 7
    python -m azdora.simula --offline /domani  # senza internet
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from html import unescape
from pathlib import Path

from azdora.app import nuovo_client_http
from azdora.bot import Azdora
from azdora.config import FUSO_ORARIO, Impostazioni, Localita
from azdora.meteo.openmeteo import (
    FonteMeteo,
    OpenMeteo,
    Previsioni,
    PrevisioniMare,
    leggi_mare,
    leggi_previsioni,
)

CHAT_SIMULATA = 1
CARTELLA_ESEMPI = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
_TAG_HTML = re.compile(r"<[^>]+>")


def html_in_testo(testo: str) -> str:
    """Toglie i tag HTML di Telegram per leggere il messaggio nel terminale."""
    return unescape(_TAG_HTML.sub("", testo))


class Console:
    """Messaggero che stampa invece di spedire."""

    def __init__(self, html: bool = False) -> None:
        self.html = html

    def invia_messaggio(self, chat_id: int, testo: str) -> None:
        print("─" * 60)
        print(testo if self.html else html_in_testo(testo))

    def esci_da_chat(self, chat_id: int) -> None:
        print(f"(il bot esce dalla chat {chat_id})")


class FonteMeteoDaFile:
    """Fonte meteo che legge risposte di Open-Meteo salvate su disco."""

    def __init__(self, cartella: Path = CARTELLA_ESEMPI) -> None:
        self._previsioni = json.loads((cartella / "previsioni_riccione.json").read_text("utf-8"))
        self._mare = json.loads((cartella / "mare_riccione.json").read_text("utf-8"))

    def previsioni(self, localita: Localita) -> Previsioni:
        return leggi_previsioni(self._previsioni, localita)

    def mare(self, localita: Localita) -> PrevisioniMare:
        return leggi_mare(self._mare, localita)

    def ora_dei_dati(self) -> datetime:
        return datetime.fromisoformat(self._previsioni["current"]["time"]).replace(
            tzinfo=FUSO_ORARIO
        )


def main(argomenti: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # emoji anche nel terminale di Windows

    parser = argparse.ArgumentParser(description="Prova l'Azdora dal terminale.")
    parser.add_argument("messaggi", nargs="*", default=["/aiuto"], help="es. /oggi o mattino")
    parser.add_argument("--offline", action="store_true", help="usa i dati di esempio salvati")
    parser.add_argument("--html", action="store_true", help="mostra l'HTML grezzo di Telegram")
    scelte = parser.parse_args(argomenti)

    impostazioni = Impostazioni(token="simulato", chat_personale=CHAT_SIMULATA)
    with nuovo_client_http() as http:
        meteo: FonteMeteo
        if scelte.offline:
            da_file = FonteMeteoDaFile()
            meteo, orologio = da_file, da_file.ora_dei_dati
            bot = Azdora(impostazioni, Console(scelte.html), meteo, orologio=orologio)
        else:
            bot = Azdora(impostazioni, Console(scelte.html), OpenMeteo(http))

        for testo in scelte.messaggi:
            print(f"\n👤 Tu: {testo}")
            if testo == "mattino":
                bot.invia_riepilogo_mattutino()
                continue
            bot.gestisci_update(
                {
                    "update_id": 0,
                    "message": {
                        "message_id": 0,
                        "chat": {"id": CHAT_SIMULATA, "type": "private"},
                        "from": {"id": CHAT_SIMULATA, "first_name": "Tu"},
                        "text": testo,
                    },
                }
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
