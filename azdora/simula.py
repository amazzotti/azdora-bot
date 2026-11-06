"""Simulatore: prova il bot dal terminale, senza Telegram e senza token.

Ogni argomento è un messaggio che "scrivi" al bot; le risposte vengono stampate a schermo.
Il meteo è quello vero di Open-Meteo, oppure quello salvato nei file di esempio con ``--offline``.

Esempi::

    python -m azdora.simula /oggi
    python -m azdora.simula "/vento rimini" "/meteo cesenatico" /mare
    python -m azdora.simula posizione:43.978,12.697   # come se mandassi la posizione
    python -m azdora.simula /notizie /allerte         # notizie e allerte della zona
    python -m azdora.simula posizione:44.49,11.34 controlli   # i controlli ogni 15 minuti
    python -m azdora.simula "/scadenza 7/11 bollo 180€ annuale" /scadenze tocca:pagato:1
    python -m azdora.simula "/spesa 45 benzina" /mese "/aggiungi latte, pane" /lista /weekend
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

from azdora.allerte import Allerta, Meteoalarm, leggi_allerte
from azdora.app import nuovo_client_http
from azdora.archivio import ArchivioMemoria
from azdora.bot import Azdora
from azdora.carburante import Osservaprezzi
from azdora.config import FUSO_ORARIO, Impostazioni, Localita
from azdora.luoghi import Luoghi, Luogo, leggi_luogo_inverso, leggi_ricerca
from azdora.meteo.aria import OpenMeteoAria
from azdora.meteo.imminente import OpenMeteoImminente
from azdora.meteo.openmeteo import (
    FonteMeteo,
    OpenMeteo,
    Previsioni,
    PrevisioniMare,
    leggi_mare,
    leggi_previsioni,
)
from azdora.notizie import (
    NOTIZIE_DEL_GIORNO,
    LettoreNotizie,
    Notizia,
    alternate,
    del_giorno,
    e_un_evento,
    leggi_feed,
    ordina,
    parla_di,
    prima_quelle_di,
)
from azdora.tv import GuideXmltv

CHAT_SIMULATA = 1
CARTELLA_ESEMPI = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
PREFISSO_POSIZIONE = "posizione:"
PREFISSO_PULSANTE = "tocca:"
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
        tasti = (tastiera or {}).get("keyboard") or (tastiera or {}).get("inline_keyboard") or []
        for riga in tasti:
            print("   " + "  ".join(f"[ {pulsante['text']} ]" for pulsante in riga))

    def esci_da_chat(self, chat_id: int) -> None:
        print(f"(il bot esce dalla chat {chat_id})")

    def invia_e_fissa(
        self, chat_id: int, testo: str, tastiera: dict[str, Any] | None = None
    ) -> tuple[int, bool]:
        self.invia_messaggio(chat_id, testo, tastiera)
        print("(📌 messaggio fissato in alto nella chat)")
        return 1, True

    def sgancia(self, chat_id: int, id_messaggio: int) -> None:
        print("(📌 messaggio sganciato)")

    def rispondi_pulsante(self, id_pulsante: str, testo: str | None = None) -> None:
        if testo:
            print(f"(avviso: {testo})")

    def modifica_messaggio(
        self, chat_id: int, id_messaggio: int, testo: str, tastiera: dict[str, Any] | None = None
    ) -> None:
        print("(messaggio aggiornato)")
        self.invia_messaggio(chat_id, testo, tastiera)

    def invia_documento(
        self, chat_id: int, nome_file: str, contenuto: bytes, didascalia: str = ""
    ) -> None:
        print("─" * 60)
        print(f"📎 {nome_file} ({len(contenuto)} byte) — {html_in_testo(didascalia)}")


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

    def luogo_di(self, lat: float, lon: float) -> Luogo | None:
        return leggi_luogo_inverso(self._inverso)


class NotizieDaFile:
    """Notizie lette dai feed salvati, filtrate sul comune come quelle vere."""

    FILE = (
        ("rss_riminitoday.xml", "Rimini Today"),
        ("rss_altarimini.xml", "Altarimini"),
        ("rss_chiamamicitta.xml", "Chiamami Città"),
        ("rss_carlino.xml", "il Resto del Carlino"),
    )

    def __init__(self, cartella: Path = CARTELLA_ESEMPI) -> None:
        self._notizie = [
            n
            for nome, fonte in self.FILE
            for n in leggi_feed((cartella / nome).read_bytes(), fonte)
        ]

    def notizie_per(self, comune: str, lat: float, lon: float, adesso: datetime) -> list[Notizia]:
        return ordina(n for n in self._notizie if parla_di(n, comune))

    def del_giorno_in_riviera(self, comune: str, adesso: datetime) -> list[Notizia]:
        oggi = del_giorno(self._notizie, adesso)
        return prima_quelle_di(alternate(oggi, NOTIZIE_DEL_GIORNO), comune)

    def eventi(self, adesso: datetime) -> list[Notizia]:
        return ordina(n for n in self._notizie if e_un_evento(n))

    def principali(self, sezione: str, adesso: datetime) -> list[Notizia]:
        file = {"italia": "rss_italia.xml", "mondo": "rss_mondo.xml"}.get(sezione)
        return leggi_feed((CARTELLA_ESEMPI / file).read_bytes(), "Google News") if file else []


class AllerteDaFile:
    def __init__(self, cartella: Path = CARTELLA_ESEMPI) -> None:
        self._allerte = leggi_allerte(_leggi_json("meteoalarm_italia.json", cartella))

    def allerte(self, paese: str) -> list[Allerta]:
        return self._allerte if paese == "it" else []


def update_simulato(testo: str) -> dict[str, Any]:
    """Il messaggio che Telegram manderebbe al webhook (testo o posizione)."""
    messaggio: dict[str, Any] = {
        "message_id": 0,
        "chat": {"id": CHAT_SIMULATA, "type": "private"},
        "from": {"id": CHAT_SIMULATA, "first_name": "Tu"},
    }
    if testo.startswith(PREFISSO_PULSANTE):
        # Tocco su un pulsante inline, es. "tocca:pagato:1".
        return {
            "update_id": 0,
            "callback_query": {
                "id": "simulato",
                "from": messaggio["from"],
                "message": {**messaggio, "message_id": 1},
                "data": testo.removeprefix(PREFISSO_PULSANTE),
            },
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
    # Memoria finta ma "persistente" per tutta la simulazione, così i controlli funzionano.
    archivio = ArchivioMemoria(persistente=True)
    with nuovo_client_http() as http:
        if scelte.offline:
            da_file = FonteMeteoDaFile()
            meteo: FonteMeteo = da_file
            bot = Azdora(
                impostazioni,
                console,
                meteo,
                LuoghiDaFile(),
                archivio=archivio,
                notizie=NotizieDaFile(),
                allerte=AllerteDaFile(),
                orologio=da_file.ora_dei_dati,
            )
        else:
            bot = Azdora(
                impostazioni,
                console,
                OpenMeteo(http),
                Luoghi(http),
                archivio=archivio,
                notizie=LettoreNotizie(http),
                allerte=Meteoalarm(http),
                aria=OpenMeteoAria(http),
                carburante=Osservaprezzi(http),
                imminente=OpenMeteoImminente(http),
                guida_tv=GuideXmltv(http),
            )

        for testo in scelte.messaggi:
            print(f"\n👤 Tu: {testo}")
            if testo == "mattino":
                bot.invia_riepilogo_mattutino()
            elif testo == "controlli":
                bot.controlli()
                print("(controlli finiti)")
            else:
                bot.gestisci_update(update_simulato(testo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
