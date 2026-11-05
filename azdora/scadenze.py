"""Scadenze: bollo, assicurazione, revisione, bollette… con promemoria e pulsante "Pagato".

Esempi di comando::

    /scadenza 31/01 bollo auto 180€ annuale
    /scadenza 15/03/2027 assicurazione 420 annuale
    /scadenza 20/11 bolletta luce 65 bimestrale
    /scadenza 10/12 revisione

Quando una scadenza ricorrente viene pagata, si sposta da sola alla volta successiva.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from html import escape
from typing import Any

from azdora.archivio import Archivio
from azdora.soldi import aggiungi_mesi, euro, leggi_data, leggi_importo

# Parole che indicano ogni quanti mesi si ripete una scadenza.
RICORRENZE: dict[str, int] = {
    "mensile": 1,
    "bimestrale": 2,
    "trimestrale": 3,
    "quadrimestrale": 4,
    "semestrale": 6,
    "annuale": 12,
    "annuo": 12,
    "annua": 12,
    "biennale": 24,
}
NOMI_RICORRENZE = {
    1: "ogni mese",
    2: "ogni 2 mesi",
    3: "ogni 3 mesi",
    4: "ogni 4 mesi",
    6: "ogni 6 mesi",
    12: "ogni anno",
    24: "ogni 2 anni",
}

# Giorni prima della scadenza in cui arriva il promemoria.
PREAVVISI = (30, 7, 1, 0)

GIORNI_BREVI = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")


@dataclass(frozen=True)
class Scadenza:
    id: int
    descrizione: str
    data: date
    importo: int | None = None  # centesimi
    mesi: int = 0  # 0 = una volta sola

    def in_dizionario(self) -> dict[str, Any]:
        return {**asdict(self), "data": self.data.isoformat()}

    @classmethod
    def da_dizionario(cls, dati: dict[str, Any]) -> Scadenza:
        return cls(**{**dati, "data": date.fromisoformat(dati["data"])})

    def prossima(self) -> Scadenza | None:
        """La stessa scadenza alla volta successiva, o ``None`` se non si ripete."""
        if not self.mesi:
            return None
        return replace(self, data=aggiungi_mesi(self.data, self.mesi))


@dataclass(frozen=True)
class NuovaScadenza:
    descrizione: str
    data: date
    importo: int | None
    mesi: int


def leggi_scadenza(argomenti: Sequence[str], oggi: date) -> NuovaScadenza | None:
    """Capisce data, importo e ricorrenza in qualsiasi ordine; il resto è la descrizione."""
    data = importo = None
    mesi = 0
    resto: list[str] = []
    parole = list(argomenti)
    i = 0
    while i < len(parole):
        parola = parole[i]
        minuscola = parola.lower()
        if data is None and (trovata := leggi_data(parola, oggi)) is not None:
            data = trovata
        elif minuscola in RICORRENZE:
            mesi = RICORRENZE[minuscola]
        elif minuscola == "ogni" and i + 1 < len(parole):
            successiva = parole[i + 1].lower()
            mesi = {"mese": 1, "anno": 12, "settimana": 0}.get(successiva, mesi)
            i += 1
        elif importo is None and (cifra := leggi_importo(parola)) is not None:
            importo = cifra
        else:
            resto.append(parola)
        i += 1
    descrizione = " ".join(resto).strip()
    if data is None or not descrizione:
        return None
    return NuovaScadenza(descrizione, data, importo, mesi)


class RegistroScadenze:
    def __init__(self, archivio: Archivio, chat_id: int) -> None:
        self._archivio = archivio
        self._chiave = f"scadenze:{chat_id}"

    def tutte(self) -> list[Scadenza]:
        dati = self._archivio.leggi(self._chiave) or []
        return sorted((Scadenza.da_dizionario(d) for d in dati), key=lambda s: (s.data, s.id))

    def _salva(self, scadenze: Sequence[Scadenza]) -> None:
        self._archivio.scrivi(self._chiave, [s.in_dizionario() for s in scadenze])

    def aggiungi(self, nuova: NuovaScadenza) -> Scadenza:
        scadenze = self.tutte()
        scadenza = Scadenza(
            id=max((s.id for s in scadenze), default=0) + 1,
            descrizione=nuova.descrizione,
            data=nuova.data,
            importo=nuova.importo,
            mesi=nuova.mesi,
        )
        self._salva([*scadenze, scadenza])
        return scadenza

    def trova(self, id_scadenza: int) -> Scadenza | None:
        return next((s for s in self.tutte() if s.id == id_scadenza), None)

    def elimina(self, id_scadenza: int) -> Scadenza | None:
        scadenze = self.tutte()
        trovata = next((s for s in scadenze if s.id == id_scadenza), None)
        if trovata is not None:
            self._salva([s for s in scadenze if s.id != id_scadenza])
        return trovata

    def paga(self, id_scadenza: int) -> tuple[Scadenza, Scadenza | None] | None:
        """Segna pagata: restituisce (pagata, prossima) oppure ``None`` se non esiste."""
        scadenze = self.tutte()
        pagata = next((s for s in scadenze if s.id == id_scadenza), None)
        if pagata is None:
            return None
        prossima = pagata.prossima()
        rimaste = [s for s in scadenze if s.id != id_scadenza]
        self._salva([*rimaste, prossima] if prossima else rimaste)
        return pagata, prossima


def da_ricordare(scadenze: Sequence[Scadenza], oggi: date) -> list[tuple[Scadenza, int]]:
    """Scadenze di cui parlare oggi: (scadenza, giorni che mancano; negativi se è passata).

    Si avvisa a 30, 7, 1 e 0 giorni; se è già passata e non è pagata, dopo 1, 3, 7 giorni
    e poi una volta a settimana.
    """
    risultato = []
    for s in scadenze:
        giorni = (s.data - oggi).days
        ritardo = -giorni
        if giorni in PREAVVISI or (ritardo > 0 and (ritardo in (1, 3) or ritardo % 7 == 0)):
            risultato.append((s, giorni))
    return risultato


def quando(giorni: int, data: date) -> str:
    giorno = f"{GIORNI_BREVI[data.weekday()]} {data.day}/{data.month}/{data.year}"
    if giorni == 0:
        return f"⚠️ <b>oggi</b> ({giorno})"
    if giorni == 1:
        return f"⏰ <b>domani</b> ({giorno})"
    if giorni < 0:
        return f"❗ <b>scaduta da {-giorni} giorni</b> ({giorno})"
    return f"tra {giorni} giorni ({giorno})"


def riga(scadenza: Scadenza, oggi: date) -> str:
    parti = [f"<b>{escape(scadenza.descrizione)}</b>"]
    if scadenza.importo is not None:
        parti.append(euro(scadenza.importo))
    parti.append(quando((scadenza.data - oggi).days, scadenza.data))
    if scadenza.mesi:
        parti.append(NOMI_RICORRENZE.get(scadenza.mesi, f"ogni {scadenza.mesi} mesi"))
    return " · ".join(parti)


def pulsanti(scadenze: Sequence[Scadenza], con_elimina: bool) -> dict[str, Any] | None:
    """Tastiera inline: "✅ Pagato" (e "🗑") per ogni scadenza."""
    if not scadenze:
        return None
    righe = []
    for s in scadenze:
        nome = s.descrizione if len(s.descrizione) <= 24 else s.descrizione[:23] + "…"
        fila = [{"text": f"✅ {nome}", "callback_data": f"pagato:{s.id}"}]
        if con_elimina:
            fila.append({"text": "🗑", "callback_data": f"elimina:{s.id}"})
        righe.append(fila)
    return {"inline_keyboard": righe}


def elenco(scadenze: Sequence[Scadenza], oggi: date) -> str:
    if not scadenze:
        return (
            "📅 Nessuna scadenza segnata.\n"
            "Aggiungine una: <code>/scadenza 31/01 bollo auto 180€ annuale</code>"
        )
    righe = ["📅 <b>Le tue scadenze</b>", ""]
    righe += [f"• {riga(s, oggi)}" for s in scadenze]
    righe += ["", "Tocca ✅ quando hai pagato, 🗑 per togliere una scadenza."]
    return "\n".join(righe)


def promemoria(da_dire: Sequence[tuple[Scadenza, int]], oggi: date) -> str:
    righe = ["🔔 <b>Promemoria scadenze</b>", ""]
    righe += [f"• {riga(s, oggi)}" for s, _ in da_dire]
    righe += ["", "Tocca ✅ quando hai pagato."]
    return "\n".join(righe)


USO = (
    "📅 Per segnare una scadenza scrivi data, cosa, e se vuoi importo e ricorrenza:\n"
    "<code>/scadenza 31/01 bollo auto 180€ annuale</code>\n"
    "<code>/scadenza 20/11 bolletta luce 65 bimestrale</code>\n"
    "<code>/scadenza 10/12/2027 revisione</code>\n"
    "Ricorrenze: mensile, bimestrale, trimestrale, semestrale, annuale, biennale."
)
