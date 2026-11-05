"""Promemoria scritti come si parla: "ricordami domani alle 18 di chiamare il meccanico".

Il bot li consegna durante il giro di controlli (ogni 15 minuti), quindi l'orario è
preciso al quarto d'ora.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from html import escape
from typing import Any

from azdora.archivio import Archivio
from azdora.config import FUSO_ORARIO

GIORNI_SETTIMANA = ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica")
_GIORNI_SENZA_ACCENTO = {g.replace("ì", "i"): i for i, g in enumerate(GIORNI_SETTIMANA)}
ORA_PREDEFINITA = time(9, 0)
MASSIMO_PROMEMORIA = 50

_NUMERI = {
    "un": 1,
    "uno": 1,
    "una": 1,
    "due": 2,
    "tre": 3,
    "quattro": 4,
    "cinque": 5,
    "dieci": 10,
    "venti": 20,
    "trenta": 30,
    "quaranta": 40,
}

_TRA = re.compile(
    r"\b(?:tra|fra)\s+(?P<n>\d+|un|uno|una|due|tre|quattro|cinque|dieci|venti|trenta|quaranta|"
    r"mezz['’]?ora)\s*(?P<unita>minut[oi]|or[ae]|giorn[oi]|settiman[ae])?\b",
    re.IGNORECASE,
)
_ALLE = re.compile(r"\b(?:alle|all['’]|ore)\s*(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))?\b", re.IGNORECASE)
_DATA = re.compile(r"\b(?:il\s+)?(?P<g>\d{1,2})[/.-](?P<mese>\d{1,2})(?:[/.-](?P<a>\d{2,4}))?\b")
_GIORNO_PAROLA = re.compile(
    r"\b(?P<parola>oggi|domani|dopodomani|stasera|stanotte|stamattina|domattina|"
    r"luned[iì]|marted[iì]|mercoled[iì]|gioved[iì]|venerd[iì]|sabato|domenica)\b",
    re.IGNORECASE,
)
_INIZIO = re.compile(r"^\s*(?:/ricordami|ricordami|ricordamelo|ricorda(?:mi)?)\b[\s,:]*", re.I)
_RIEMPITIVI = re.compile(r"^(?:di|che|a|ad|per|:|,)\s+", re.IGNORECASE)


@dataclass(frozen=True)
class Promemoria:
    id: int
    quando: datetime
    testo: str

    def in_dizionario(self) -> dict[str, Any]:
        return {"id": self.id, "quando": self.quando.isoformat(), "testo": self.testo}

    @classmethod
    def da_dizionario(cls, dati: dict[str, Any]) -> Promemoria:
        return cls(dati["id"], datetime.fromisoformat(dati["quando"]), dati["testo"])


class ErrorePromemoria(ValueError):
    """La frase non contiene un momento valido, o il momento è già passato."""


def e_un_promemoria(testo: str) -> bool:
    return bool(_INIZIO.match(testo))


def _prossimo(giorno_settimana: int, adesso: datetime) -> date:
    avanti = (giorno_settimana - adesso.weekday()) % 7 or 7
    return adesso.date() + timedelta(days=avanti)


def leggi_promemoria(frase: str, adesso: datetime) -> tuple[datetime, str]:
    """Capisce quando e cosa. Solleva ``ErrorePromemoria`` se non riesce."""
    testo = _INIZIO.sub("", frase.strip())
    quando: datetime | None = None

    if (tra := _TRA.search(testo)) is not None:
        numero = tra["n"].lower()
        if numero.startswith("mezz"):
            delta = timedelta(minutes=30)
        else:
            n = int(numero) if numero.isdigit() else _NUMERI[numero]
            unita = (tra["unita"] or "minuti").lower()
            delta = {
                "m": timedelta(minutes=n),
                "o": timedelta(hours=n),
                "g": timedelta(days=n),
                "s": timedelta(weeks=n),
            }[unita[0]]
        quando = adesso + delta
        testo = testo.replace(tra.group(0), " ")
    else:
        giorno: date | None = None
        ora: time | None = None
        # L'ora va letta per prima: altrimenti "alle 16.30" sembrerebbe la data 16/30.
        ora_esplicita: time | None = None
        if (alle := _ALLE.search(testo)) is not None:
            ore, minuti = int(alle["h"]), int(alle["m"] or 0)
            if ore > 23 or minuti > 59:
                raise ErrorePromemoria("Quest'ora non esiste")
            ora_esplicita = time(ore, minuti)
            testo = testo.replace(alle.group(0), " ")
        if (data := _DATA.search(testo)) is not None:
            anno = data["a"]
            anno_intero = (int(anno) + (2000 if len(anno) == 2 else 0)) if anno else adesso.year
            try:
                giorno = date(anno_intero, int(data["mese"]), int(data["g"]))
            except ValueError as errore:
                raise ErrorePromemoria("Questa data non esiste") from errore
            if not anno and giorno < adesso.date():
                giorno = giorno.replace(year=giorno.year + 1)
            testo = testo.replace(data.group(0), " ")
        if (parola := _GIORNO_PAROLA.search(testo)) is not None:
            p = parola["parola"].lower().replace("ì", "i")
            oggi = adesso.date()
            if p in _GIORNI_SENZA_ACCENTO:
                giorno = _prossimo(_GIORNI_SENZA_ACCENTO[p], adesso)
            else:
                giorno, ora_parola = {
                    "oggi": (oggi, None),
                    "domani": (oggi + timedelta(days=1), None),
                    "dopodomani": (oggi + timedelta(days=2), None),
                    "stasera": (oggi, time(20, 0)),
                    "stanotte": (oggi, time(22, 0)),
                    "stamattina": (oggi, time(9, 0)),
                    "domattina": (oggi + timedelta(days=1), time(8, 0)),
                }[p]
                ora = ora_parola
            testo = testo.replace(parola.group(0), " ")
        if ora_esplicita is not None:
            ora = ora_esplicita  # "stasera alle 21": vince l'ora scritta
        if giorno is None and ora is None:
            raise ErrorePromemoria("Non ho capito quando")
        if giorno is None:
            # Solo l'ora: oggi, oppure domani se è già passata.
            giorno = adesso.date()
            if datetime.combine(giorno, ora, FUSO_ORARIO) <= adesso:  # type: ignore[arg-type]
                giorno += timedelta(days=1)
        quando = datetime.combine(giorno, ora or ORA_PREDEFINITA, FUSO_ORARIO)

    cosa = re.sub(r"\s+", " ", testo).strip(" ,.:")
    while (pulito := _RIEMPITIVI.sub("", cosa)) != cosa:
        cosa = pulito
    if not cosa:
        raise ErrorePromemoria("Non ho capito cosa ricordarti")
    if quando <= adesso:
        raise ErrorePromemoria("Quel momento è già passato")
    return quando, cosa


class RegistroPromemoria:
    def __init__(self, archivio: Archivio, chat_id: int) -> None:
        self._archivio = archivio
        self._chiave = f"promemoria:{chat_id}"

    def tutti(self) -> list[Promemoria]:
        dati = self._archivio.leggi(self._chiave) or []
        return sorted((Promemoria.da_dizionario(d) for d in dati), key=lambda p: p.quando)

    def _salva(self, voci: Sequence[Promemoria]) -> None:
        self._archivio.scrivi(self._chiave, [p.in_dizionario() for p in voci])

    def aggiungi(self, quando: datetime, testo: str) -> Promemoria:
        voci = self.tutti()
        if len(voci) >= MASSIMO_PROMEMORIA:
            raise ErrorePromemoria(f"Hai già {MASSIMO_PROMEMORIA} promemoria: cancellane qualcuno")
        nuovo = Promemoria(max((p.id for p in voci), default=0) + 1, quando, testo)
        self._salva([*voci, nuovo])
        return nuovo

    def elimina(self, id_promemoria: int) -> Promemoria | None:
        voci = self.tutti()
        trovato = next((p for p in voci if p.id == id_promemoria), None)
        if trovato is not None:
            self._salva([p for p in voci if p.id != id_promemoria])
        return trovato

    def scaduti(self, adesso: datetime) -> list[Promemoria]:
        """Toglie e restituisce i promemoria arrivati all'ora giusta."""
        voci = self.tutti()
        da_dire = [p for p in voci if p.quando <= adesso]
        if da_dire:
            self._salva([p for p in voci if p.quando > adesso])
        return da_dire

    def del_giorno(self, giorno: date) -> list[Promemoria]:
        return [p for p in self.tutti() if p.quando.astimezone(FUSO_ORARIO).date() == giorno]


def quando_leggibile(quando: datetime, adesso: datetime) -> str:
    locale = quando.astimezone(FUSO_ORARIO)
    giorni = (locale.date() - adesso.date()).days
    ora = f"alle {locale:%H:%M}"
    if giorni == 0:
        return f"oggi {ora}"
    if giorni == 1:
        return f"domani {ora}"
    if 1 < giorni < 7:
        return f"{GIORNI_SETTIMANA[locale.weekday()]} {ora}"
    return f"il {locale.day}/{locale.month}/{locale.year} {ora}"


def conferma(promemoria: Promemoria, adesso: datetime) -> str:
    return (
        f"⏰ Ok! Ti ricordo <b>{escape(promemoria.testo)}</b> "
        f"{quando_leggibile(promemoria.quando, adesso)}.\n/promemoria per vederli tutti."
    )


def avviso(promemoria: Promemoria) -> str:
    return f"⏰ <b>Promemoria</b>: {escape(promemoria.testo)}"


def elenco(voci: Sequence[Promemoria], adesso: datetime) -> str:
    if not voci:
        return (
            "⏰ Nessun promemoria.\n"
            "Scrivimi ad esempio: <i>ricordami domani alle 18 di chiamare il meccanico</i>"
        )
    righe = ["⏰ <b>I tuoi promemoria</b>", ""]
    righe += [f"• {quando_leggibile(p.quando, adesso)}: {escape(p.testo)}" for p in voci]
    righe += ["", "Tocca 🗑 per cancellarne uno."]
    return "\n".join(righe)


def pulsanti(voci: Sequence[Promemoria]) -> dict[str, Any] | None:
    if not voci:
        return None
    return {
        "inline_keyboard": [
            [{"text": f"🗑 {p.testo[:28]}", "callback_data": f"prdel:{p.id}"}] for p in voci
        ]
    }


USO = (
    "⏰ Dimmi quando e cosa, come lo diresti a voce:\n"
    "• <i>ricordami domani alle 18 di chiamare il meccanico</i>\n"
    "• <i>ricordami tra 2 ore di togliere la torta dal forno</i>\n"
    "• <i>ricordami venerdì di prenotare il ristorante</i>\n"
    "• <i>ricordami il 12/11 alle 9 il dentista</i>\n"
    "Arrivano con una precisione di un quarto d'ora."
)
