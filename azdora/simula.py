"""Simulatore: prova il bot dal terminale, senza Telegram e senza token.

Ogni argomento è un messaggio che "scrivi" al bot; le risposte vengono stampate a schermo.
Il meteo è quello vero di Open-Meteo, oppure quello salvato nei file di esempio con ``--offline``.

Esempi::

    python -m azdora.simula /oggi
    python -m azdora.simula "/vento rimini" "/meteo cesenatico" /mare
    python -m azdora.simula posizione:43.978,12.697   # come se mandassi la posizione
    python -m azdora.simula mattino                   # il messaggio delle 7
    python -m azdora.simula --offline /domani         # senza internet
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any

from azdora.app import nuovo_client_http
from azdora.bot import Azdora
from azdora.config import FUSO_ORARIO, Impostazioni, Localita
from azdora.luoghi import CercaLuoghi, Luoghi, leggi_nome_inverso, leggi_ricerca
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
PREFISSO_POSIZIONE = "posizione:"
_TAG_HTML = re.compile(r"<[^>]+>")


def html_in_testo(testo: str) -> str:
    """Toglie i tag HTML di Telegram per leggere il messaggio nel terminale."""
    return unescape(_TAG_HTML.sub("", testo))


def _leggi_json(nome: str, cartella: Path) -> dict[str, Any]:
    return json.loads((cartella / nome).read_text(encoding="utf-8"))


class Console:
    """Messaggero che stampa invece di spedire."""

    def __init__(self, html: bool = False) -> None:
        self.html = html

    def invia_messaggio(
        self, chat_id: int, testo: str, tastiera: dict[str, Any] | None = None
    ) -> None:
        print("─" * 60)
        print(testo if self.html else html_in_testo(testo))
        for riga in (tastiera or {}).get("keyboard", []):
            print("   " + "  ".join(f"[ {pulsante['text']} ]" for pulsante in riga))

    def esci_da_chat(self, chat_id: int) -> None:
        print(f"(il bot esce dalla chat {chat_id})")


class FonteMeteoDaFile:
    """Fonte meteo che legge risposte di Open-Meteo salvate su disco."""

    def __init__(self, cartella: Path = CARTELLA_ESEMPI) -> None:
        self._previsioni = _leggi_json("previsioni_riccione.json", cartella)
        self._mare = _leggi_json("mare_riccione.json", cartella)

    def previsioni(self, localita: Localita) -> Previsioni:
        return leggi_previsioni(self._previsioni, localita)

    def mare(self, localita: Localita) -> PrevisioniMare:
        return leggi_mare(self._mare, localita)

    def ora_dei_dati(self) -> datetime:
        return datetime.fromisoformat(self._previsioni["current"]["time"]).replace(
            tzinfo=FUSO_ORARIO
        )


class LuoghiDaFile:
    """Luoghi che rispondono sempre con i file di esempio (Misano Adriatico)."""

    def __init__(self, cartella: Path = CARTELLA_ESEMPI) -> None:
        self._ricerca = _leggi_json("geocoding_misano.json", cartella)
        self._inverso = _leggi_json("nominatim_misano.json", cartella)

    def cerca(self, nome: str) -> Localita | None:
        return leggi_ricerca(self._ricerca)

    def nome_di(self, lat: float, lon: float) -> str | None:
        return leggi_nome_inverso(self._inverso)


def update_simulato(testo: str) -> dict[str, Any]:
    """Il messaggio che Telegram manderebbe al webhook (testo o posizione)."""
    messaggio: dict[str, Any] = {
        "message_id": 0,
        "chat": {"id": CHAT_SIMULATA, "type": "private"},
        "from": {"id": CHAT_SIMULATA, "first_name": "Tu"},
    }
    if testo.startswith(PREFISSO_POSIZIONE):
        lat, _, lon = testo.removeprefix(PREFISSO_POSIZIONE).partition(",")
        messaggio["location"] = {"latitude": float(lat), "longitude": float(lon)}
    else:
        messaggio["text"] = testo
    return {"update_id": 0, "message": messaggio}


def main(argomenti: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # emoji anche nel terminale di Windows

    parser = argparse.ArgumentParser(description="Prova l'Azdora dal terminale.")
    parser.add_argument(
        "messaggi", nargs="*", default=["/aiuto"], help="es. /oggi, posizione:44.06,12.57, mattino"
    )
    parser.add_argument("--offline", action="store_true", help="usa i dati di esempio salvati")
    parser.add_argument("--html", action="store_true", help="mostra l'HTML grezzo di Telegram")
    scelte = parser.parse_args(argomenti)

    impostazioni = Impostazioni(token="simulato", chat_personale=CHAT_SIMULATA)
    console = Console(scelte.html)
    with nuovo_client_http() as http:
        if scelte.offline:
            da_file = FonteMeteoDaFile()
            meteo: FonteMeteo = da_file
            luoghi: CercaLuoghi = LuoghiDaFile()
            bot = Azdora(impostazioni, console, meteo, luoghi, orologio=da_file.ora_dei_dati)
        else:
            bot = Azdora(impostazioni, console, OpenMeteo(http), Luoghi(http))

        for testo in scelte.messaggi:
            print(f"\n👤 Tu: {testo}")
            if testo == "mattino":
                bot.invia_riepilogo_mattutino()
            else:
                bot.gestisci_update(update_simulato(testo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
