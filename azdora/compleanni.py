"""Compleanni: «ricordami il compleanno della mamma il 14/3» e ci pensa l'Azdora, ogni anno.

Avvisa una settimana prima (per il regalo), il giorno prima e il giorno stesso. Se scrivi
anche l'anno di nascita, dice quanti anni compie. Chi è nato il 29 febbraio, negli anni
non bisestili lo festeggia il 28.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from html import escape
from typing import Any

from azdora.archivio import Archivio

MASSIMO_COMPLEANNI = 60  # i pulsanti 🗑 devono stare in un messaggio
GIORNI_PRIMA = (7, 1, 0)

NOMI_MESI = (
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
)
_MESI = (
    {nome: i + 1 for i, nome in enumerate(NOMI_MESI)}
    | {nome[:3]: i + 1 for i, nome in enumerate(NOMI_MESI)}
    | {"sett": 9}
)

_DATA_NUMERI = re.compile(r"\b(?P<g>\d{1,2})[/.-](?P<m>\d{1,2})(?:[/.-](?P<a>\d{4}|\d{2}))?\b")
_DATA_PAROLE = re.compile(
    r"\b(?P<g>\d{1,2})\s*(?:°|º)?\s+(?:di\s+)?(?P<m>"
    + "|".join(sorted(_MESI, key=len, reverse=True))
    + r")\.?(?:\s+(?:del\s+)?(?P<a>\d{4}|\d{2}))?\b",
    re.IGNORECASE,
)
_COMPLEANNO = re.compile(r"\bcompleann[oi]\b", re.IGNORECASE)
_NOME = re.compile(
    r"\bcompleann[oi]\s+(?:(?:di|della|del|dello|dell'|dei|degli|delle|a|al|alla)\s*)?(?P<nome>.+)",
    re.IGNORECASE,
)
_CODA = re.compile(r"(?:\s+(?:il|lo|la|è|e'|e|cade|a|che|di)\b)+\s*$", re.IGNORECASE)
_INIZIO = re.compile(r"^\s*/?(?:ricordami|ricordamelo|ricorda)\b[\s,:]*", re.IGNORECASE)


@dataclass(frozen=True)
class Compleanno:
    id: int
    nome: str
    giorno: int
    mese: int
    anno: int | None = None

    def nel(self, anno: int) -> date:
        """Il giorno della festa in quell'anno (29/2 → 28/2 negli anni non bisestili)."""
        giorno = self.giorno
        if self.mese == 2 and giorno == 29 and not calendar.isleap(anno):
            giorno = 28
        return date(anno, self.mese, giorno)

    def prossimo(self, oggi: date) -> date:
        quest_anno = self.nel(oggi.year)
        return quest_anno if quest_anno >= oggi else self.nel(oggi.year + 1)

    def anni(self, quando: date) -> int | None:
        if self.anno is None or self.anno >= quando.year:
            return None
        return quando.year - self.anno


def leggi_compleanno(testo: str) -> tuple[str, int, int, int | None] | None:
    """``"ricordami il compleanno della mamma il 14/3"`` → ``("mamma", 14, 3, None)``."""
    if not _COMPLEANNO.search(testo):
        return None
    trovata = _DATA_NUMERI.search(testo) or _DATA_PAROLE.search(testo)
    if trovata is None:
        return None
    try:
        giorno = int(trovata["g"])
        valore_mese = trovata["m"]
        mese = int(valore_mese) if valore_mese.isdigit() else _MESI[valore_mese.lower()]
        anno = _anno(trovata["a"])
        date(2024, mese, giorno)  # 2024 è bisestile: il 29/2 è valido
    except (ValueError, KeyError):
        return None
    senza_data = (testo[: trovata.start()] + " " + testo[trovata.end() :]).strip()
    senza_data = _INIZIO.sub("", senza_data)
    nome_trovato = _NOME.search(senza_data)
    if nome_trovato is None:
        return None
    nome = re.split(r"[,.;:!?\n]", nome_trovato["nome"])[0]
    nome = _CODA.sub("", " " + nome.strip()).strip(" '\"")
    if not nome:
        return None
    return nome[:40], giorno, mese, anno


def _anno(testo: str | None) -> int | None:
    if not testo:
        return None
    anno = int(testo)
    if anno < 100:
        anno += 1900 if anno > 30 else 2000
    if not 1900 <= anno <= 2100:
        raise ValueError("anno strano")
    return anno


class RegistroCompleanni:
    def __init__(self, archivio: Archivio, chat_id: int) -> None:
        self._archivio = archivio
        self._chiave = f"compleanni:{chat_id}"

    def tutti(self) -> list[Compleanno]:
        return [Compleanno(**d) for d in self._archivio.leggi(self._chiave) or []]

    def _salva(self, voci: Sequence[Compleanno]) -> None:
        self._archivio.scrivi(self._chiave, [asdict(v) for v in voci])

    def aggiungi(self, nome: str, giorno: int, mese: int, anno: int | None = None) -> Compleanno:
        voci = self.tutti()
        # Stesso nome: si aggiorna la data invece di fare un doppione.
        stesso = next((v for v in voci if v.nome.lower() == nome.lower()), None)
        if stesso is None and len(voci) >= MASSIMO_COMPLEANNI:
            raise ValueError(f"Hai già {MASSIMO_COMPLEANNI} compleanni: togline qualcuno")
        nuovo_id = stesso.id if stesso else max((v.id for v in voci), default=0) + 1
        nuovo = Compleanno(nuovo_id, nome, giorno, mese, anno)
        self._salva([nuovo if v.id == nuovo_id else v for v in voci] + ([] if stesso else [nuovo]))
        return nuovo

    def elimina(self, id_voce: int) -> Compleanno | None:
        voci = self.tutti()
        trovato = next((v for v in voci if v.id == id_voce), None)
        if trovato is not None:
            self._salva([v for v in voci if v.id != id_voce])
        return trovato

    def da_ricordare(self, oggi: date) -> list[tuple[Compleanno, int]]:
        """I compleanni di oggi, di domani e tra una settimana, con quanti giorni mancano."""
        risultato = []
        for v in self.tutti():
            mancano = (v.prossimo(oggi) - oggi).days
            if mancano in GIORNI_PRIMA:
                risultato.append((v, mancano))
        return sorted(risultato, key=lambda x: (x[1], x[0].nome.lower()))


# --- Testi ------------------------------------------------------------------------------


def data_breve(giorno: int, mese: int) -> str:
    return f"{giorno} {NOMI_MESI[mese - 1]}"


def _eta(voce: Compleanno, quando: date) -> str:
    anni = voce.anni(quando)
    return f" ({anni} anni)" if anni else ""


def conferma(voce: Compleanno, oggi: date) -> str:
    prossimo = voce.prossimo(oggi)
    mancano = (prossimo - oggi).days
    quando = "oggi! 🎉" if mancano == 0 else "domani" if mancano == 1 else f"tra {mancano} giorni"
    eta = f", compie {voce.anni(prossimo)} anni" if voce.anni(prossimo) else ""
    return (
        f"🎂 Segnato: compleanno di <b>{escape(voce.nome)}</b> il "
        f"{data_breve(voce.giorno, voce.mese)} ({quando}{eta}).\n"
        "Ti avviso una settimana prima, il giorno prima e il giorno stesso, ogni anno. "
        "/compleanni per vederli tutti."
    )


def avviso(da_dire: Sequence[tuple[Compleanno, int]], oggi: date) -> str:
    righe = []
    for voce, mancano in da_dire:
        nome = f"<b>{escape(voce.nome)}</b>"
        festa = voce.prossimo(oggi)
        if mancano == 0:
            righe.append(f"🎂 Oggi è il compleanno di {nome}{_eta(voce, festa)}! Fai gli auguri 🎉")
        elif mancano == 1:
            righe.append(f"🎈 Domani è il compleanno di {nome}{_eta(voce, festa)}")
        else:
            righe.append(
                f"🎁 Tra una settimana ({data_breve(voce.giorno, voce.mese)}) è il compleanno di "
                f"{nome}{_eta(voce, festa)}: pensa al regalo"
            )
    return "\n".join(righe)


def elenco(voci: Sequence[Compleanno], oggi: date) -> str:
    if not voci:
        return (
            "🎂 Nessun compleanno segnato.\n"
            "Scrivimi ad esempio: <i>ricordami il compleanno della mamma il 14/3</i>\n"
            "oppure <code>/compleanno Giulia 2 maggio 1995</code>"
        )
    ordinati = sorted(voci, key=lambda v: v.prossimo(oggi))
    righe = ["🎂 <b>Compleanni</b>, dal più vicino", ""]
    for v in ordinati:
        prossimo = v.prossimo(oggi)
        mancano = (prossimo - oggi).days
        quando = "oggi 🎉" if mancano == 0 else "domani" if mancano == 1 else f"tra {mancano} gg"
        righe.append(
            f"• {data_breve(v.giorno, v.mese)} – {escape(v.nome)}{_eta(v, prossimo)} · {quando}"
        )
    righe += ["", "Tocca 🗑 per toglierne uno."]
    return "\n".join(righe)


def pulsanti(voci: Sequence[Compleanno], oggi: date) -> dict[str, Any] | None:
    if not voci:
        return None
    ordinati = sorted(voci, key=lambda v: v.prossimo(oggi))
    return {
        "inline_keyboard": [
            [{"text": f"🗑 {v.nome[:28]}", "callback_data": f"cpdel:{v.id}"}] for v in ordinati
        ]
    }


USO = (
    "🎂 Dimmi di chi e quando:\n"
    "• <i>ricordami il compleanno della mamma il 14/3</i>\n"
    "• <code>/compleanno Giulia 2 maggio 1995</code> (con l'anno ti dico quanti ne compie)\n"
    "/compleanni per vederli tutti."
)
