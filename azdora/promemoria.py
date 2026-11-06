"""Promemoria scritti come si parla: "ricordami domani alle 18 di chiamare il meccanico".

Anche ricorrenti: "ricordami ogni lunedì alle 8 di portare fuori la plastica",
"ricordami ogni mese il 5 di pagare l'affitto", "ricordami ogni sera di prendere la pastiglia".

Il bot li consegna durante il giro di controlli (ogni 15 minuti), quindi l'orario è
preciso al quarto d'ora.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from html import escape
from typing import Any

from azdora.archivio import Archivio
from azdora.config import FUSO_ORARIO
from azdora.soldi import aggiungi_mesi

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
_OGNI = re.compile(
    r"\b(?:ogni|tutti\s+i|tutte\s+le)\s+(?P<cosa>giorn[oi]|mattin[ae]|ser[ae]|settiman[ae]|"
    r"mes[ei]|ann[oi]|luned[iì]|marted[iì]|mercoled[iì]|gioved[iì]|venerd[iì]|sabat[oi]|"
    r"domeniche|domenica)\b",
    re.IGNORECASE,
)
_GIORNO_DEL_MESE = re.compile(r"\bil\s+(?P<g>\d{1,2})\b(?![/.-]\d)", re.IGNORECASE)
NOMI_RIPETIZIONI = {
    "giorno": "ogni giorno",
    "settimana": "ogni settimana",
    "mese": "ogni mese",
    "anno": "ogni anno",
}


@dataclass(frozen=True)
class Promemoria:
    id: int
    quando: datetime
    testo: str
    ripeti: str | None = None  # giorno, settimana, mese, anno

    def in_dizionario(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "quando": self.quando.isoformat(),
            "testo": self.testo,
            "ripeti": self.ripeti,
        }

    @classmethod
    def da_dizionario(cls, dati: dict[str, Any]) -> Promemoria:
        quando = datetime.fromisoformat(dati["quando"])
        if quando.tzinfo is not None:
            # Salvato con lo scarto fisso (+02:00): torniamo all'ora di Roma, con l'ora legale.
            quando = quando.astimezone(FUSO_ORARIO)
        return cls(dati["id"], quando, dati["testo"], dati.get("ripeti"))


def prossima_volta(quando: datetime, ripeti: str) -> datetime:
    """La volta successiva di un promemoria che si ripete."""
    locale = quando.astimezone(FUSO_ORARIO) if quando.tzinfo else quando
    if ripeti == "giorno":
        giorno = locale.date() + timedelta(days=1)
    elif ripeti == "settimana":
        giorno = locale.date() + timedelta(weeks=1)
    else:
        giorno = aggiungi_mesi(locale.date(), 12 if ripeti == "anno" else 1)
    # Stessa ora "di orologio" anche se nel frattempo è cambiata l'ora legale.
    return datetime.combine(giorno, locale.time(), locale.tzinfo)


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

    def aggiungi(self, quando: datetime, testo: str, ripeti: str | None = None) -> Promemoria:
        voci = self.tutti()
        if len(voci) >= MASSIMO_PROMEMORIA:
            raise ErrorePromemoria(f"Hai già {MASSIMO_PROMEMORIA} promemoria: cancellane qualcuno")
        prossimo_id = max([p.id for p in voci] + [self._ultimo_id()], default=0) + 1
        nuovo = Promemoria(prossimo_id, quando, testo, ripeti)
        self._archivio.scrivi(f"{self._chiave}:ultimo-id", prossimo_id)
        self._salva([*voci, nuovo])
        return nuovo

    def elimina(self, id_promemoria: int) -> Promemoria | None:
        voci = self.tutti()
        trovato = next((p for p in voci if p.id == id_promemoria), None)
        if trovato is not None:
            self._salva([p for p in voci if p.id != id_promemoria])
        return trovato

    def _ultimo_id(self) -> int:
        """Gli id non si riusano mai: i pulsanti "rimanda" di un vecchio avviso restano giusti."""
        return int(self._archivio.leggi(f"{self._chiave}:ultimo-id") or 0)

    def rimetti(self, voci: Sequence[Promemoria]) -> None:
        """Rimette in lista i promemoria singoli che non si sono potuti consegnare.

        I ricorrenti sono già spostati alla volta successiva: quelli restano dove sono.
        """
        singoli = [p for p in voci if not p.ripeti]
        if singoli:
            ids = {p.id for p in singoli}
            self._salva([*(p for p in self.tutti() if p.id not in ids), *singoli])

    def scaduti(self, adesso: datetime) -> list[Promemoria]:
        """Restituisce i promemoria arrivati all'ora giusta.

        Quelli singoli vengono tolti; quelli ricorrenti si spostano alla volta successiva.
        """
        voci = self.tutti()
        da_dire = [p for p in voci if p.quando <= adesso]
        if not da_dire:
            return []
        rimasti = [p for p in voci if p.quando > adesso]
        for p in da_dire:
            if p.ripeti:
                prossima = p.quando
                while prossima <= adesso:
                    prossima = prossima_volta(prossima, p.ripeti)
                rimasti.append(Promemoria(p.id, prossima, p.testo, p.ripeti))
        self._salva(rimasti)
        consegnati = self._archivio.leggi(f"{self._chiave}:consegnati") or {}
        consegnati.update({str(p.id): p.testo for p in da_dire})
        self._archivio.scrivi(f"{self._chiave}:consegnati", consegnati, 7 * 24 * 3600)
        return da_dire

    def testo_consegnato(self, id_promemoria: int) -> str | None:
        """Il testo di un promemoria già arrivato (per i pulsanti "rimanda")."""
        consegnati = self._archivio.leggi(f"{self._chiave}:consegnati") or {}
        return consegnati.get(str(id_promemoria))

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
    ripeti = f", e poi 🔁 {NOMI_RIPETIZIONI[promemoria.ripeti]}" if promemoria.ripeti else ""
    return (
        f"⏰ Ok! Ti ricordo <b>{escape(promemoria.testo)}</b> "
        f"{quando_leggibile(promemoria.quando, adesso)}{ripeti}.\n/promemoria per vederli tutti."
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
    righe += [
        f"• {quando_leggibile(p.quando, adesso)}: {escape(p.testo)}"
        + (f" 🔁 {NOMI_RIPETIZIONI[p.ripeti]}" if p.ripeti else "")
        for p in voci
    ]
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
    "• <i>ricordami ogni lunedì alle 20 di portare fuori la plastica</i>\n"
    "• <i>ricordami ogni mese il 5 di pagare l'affitto</i>\n"
    "Arrivano con una precisione di un quarto d'ora."
)


def leggi_ricorrente(frase: str, adesso: datetime) -> tuple[datetime, str, str | None]:
    """Come ``leggi_promemoria``, ma capisce anche "ogni giorno/lunedì/mese/anno".

    Restituisce (prima volta, cosa ricordare, ripetizione oppure ``None``).
    """
    ogni = _OGNI.search(frase)
    if ogni is None:
        return (*leggi_promemoria(frase, adesso), None)
    parola = ogni["cosa"].lower().replace("ì", "i")
    resto = frase.replace(ogni.group(0), " ")
    if parola.startswith("giorn"):
        ripeti, aggiunta = "giorno", ""
    elif parola.startswith("mattin"):
        ripeti, aggiunta = "giorno", " alle 8"
    elif parola.startswith("ser"):
        ripeti, aggiunta = "giorno", " alle 20"
    elif parola.startswith("settiman"):
        ripeti, aggiunta = "settimana", ""
    elif parola.startswith("mes"):
        ripeti, aggiunta = "mese", ""
        if (giorno := _GIORNO_DEL_MESE.search(resto)) is not None:
            numero = int(giorno["g"])
            if not 1 <= numero <= 31:
                raise ErrorePromemoria("Questo giorno del mese non esiste")
            mese = adesso.date().replace(day=1)
            primo = aggiungi_mesi(mese, 0 if numero >= adesso.day else 1)
            ultimo = calendar.monthrange(primo.year, primo.month)[1]
            primo = primo.replace(day=min(numero, ultimo))  # il 31 a novembre è il 30
            resto = resto.replace(giorno.group(0), f" il {primo.day}/{primo.month}/{primo.year}")
    elif parola.startswith("ann"):
        ripeti, aggiunta = "anno", ""
    else:
        # "ogni lunedì" (o "tutti i lunedì"): la prima volta è il prossimo lunedì.
        ripeti = "settimana"
        nome = next(g for g in _GIORNI_SENZA_ACCENTO if g.startswith(parola[:5]))
        aggiunta = f" {nome}"
    if not _ALLE.search(resto) and not _ALLE.search(aggiunta):
        aggiunta += f" alle {ORA_PREDEFINITA:%H:%M}"
    try:
        quando, cosa = leggi_promemoria(f"{resto}{aggiunta}", adesso)
    except ErrorePromemoria as errore:
        # "ogni mese il 5" detto il 5 pomeriggio: si parte dal mese prossimo.
        if "passato" not in str(errore) or (data := _DATA.search(resto)) is None:
            raise
        prima = date(int(data["a"]), int(data["mese"]), int(data["g"]))
        dopo = aggiungi_mesi(prima, 12 if ripeti == "anno" else 1)
        resto = resto.replace(data.group(0), f"{dopo.day}/{dopo.month}/{dopo.year}")
        quando, cosa = leggi_promemoria(f"{resto}{aggiunta}", adesso)
    return quando, cosa, ripeti


def pulsanti_avviso(promemoria: Promemoria) -> dict[str, Any]:
    """Sotto il promemoria arrivato: rimanda di un'ora, a domani, oppure fatto."""
    return {
        "inline_keyboard": [
            [
                {"text": "⏰ +1 ora", "callback_data": f"prpost:{promemoria.id}:60"},
                {"text": "📅 Domani", "callback_data": f"prpost:{promemoria.id}:d"},
            ],
            [{"text": "✅ Fatto", "callback_data": f"prok:{promemoria.id}"}],
        ]
    }
