"""Spese: ``/spesa 45 benzina``, riepilogo del mese, ultime spese, esportazione CSV.

Ogni chat ha le sue spese: quelle personali nella chat privata, quelle di casa nel
gruppo di coppia. Sono salvate nell'archivio, un elenco per ogni mese.
"""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from html import escape
from typing import Any

from azdora.archivio import Archivio
from azdora.soldi import euro, leggi_importo, mese_chiave

MESI = (
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

# Categoria → parole che la fanno riconoscere (radici, in minuscolo).
CATEGORIE: dict[str, tuple[str, ...]] = {
    "🚗 Auto": (
        "benzin",
        "gasolio",
        "diesel",
        "carburant",
        "metano",
        "gpl",
        "bollo",
        "assicuraz",
        "revision",
        "tagliand",
        "gomm",
        "meccanic",
        "autostrad",
        "telepass",
        "parchegg",
        "multa",
        "lavaggio",
    ),
    "🛒 Spesa": (
        "spesa",
        "supermercat",
        "conad",
        "coop",
        "esselunga",
        "lidl",
        "eurospin",
        "despar",
        "carrefour",
        "famila",
        "mercato",
        "alimentar",
        "pane",
        "frutta",
    ),
    "💡 Bollette": (
        "bollett",
        "luce",
        "gas",
        "acqua",
        "hera",
        "enel",
        "internet",
        "fibra",
        "telefon",
        "tari",
        "rifiut",
    ),
    "🏠 Casa": ("affitto", "mutuo", "condominio", "mobili", "ikea", "bricolage", "casa"),
    "🍕 Fuori": (
        "ristorant",
        "pizz",
        "piadin",
        "bar",
        "aperitiv",
        "caff",
        "cena",
        "pranzo",
        "gelat",
        "sushi",
        "birra",
    ),
    "💊 Salute": ("farmac", "medic", "dentist", "visita", "ottic", "palestra", "analisi"),
    "🎉 Svago": (
        "cinema",
        "concert",
        "teatro",
        "viaggi",
        "vacanz",
        "hotel",
        "regal",
        "libr",
        "museo",
        "biglietti",
    ),
    "📺 Abbonamenti": ("netflix", "spotify", "prime", "disney", "dazn", "abbonament", "icloud"),
    "👕 Vestiti": ("vestit", "scarpe", "maglia", "giacca", "abbigliament", "zalando"),
}
ALTRO = "📦 Altro"


TUTTE_LE_CATEGORIE: tuple[str, ...] = (*CATEGORIE, ALTRO)

# Parole che non dicono niente sulla categoria ("la pizza" → "pizza").
_PAROLE_VUOTE = frozenset(
    {
        "il",
        "lo",
        "la",
        "i",
        "gli",
        "le",
        "un",
        "uno",
        "una",
        "di",
        "da",
        "del",
        "della",
        "dei",
        "al",
        "alla",
        "allo",
        "ai",
        "per",
        "con",
        "su",
        "in",
        "e",
        "a",
    }
)


def parola_chiave(descrizione: str) -> str:
    """La prima parola che conta: ``"la spesa all'Esselunga"`` → ``"spesa"``."""
    parole = [p for p in re.findall(r"\w+", descrizione.lower()) if p not in _PAROLE_VUOTE]
    return parole[0] if parole else ""


def trova_categoria(nome: str) -> str | None:
    """``"auto"``, ``"Bollette"``, ``"fuori"`` → la categoria completa con l'emoji."""
    cercato = nome.strip().lower()
    if len(cercato) < 3:
        return None
    for categoria in TUTTE_LE_CATEGORIE:
        if categoria.split(" ", 1)[1].lower().startswith(cercato):
            return categoria
    return None


def categoria_per(descrizione: str, apprese: Mapping[str, str] | None = None) -> str:
    """Indovina la categoria (``"benzina"`` → ``"🚗 Auto"``).

    Prima guarda le correzioni fatte da te in passato (``apprese``), poi le parole note.
    """
    chiave = parola_chiave(descrizione)
    if apprese and chiave in apprese:
        return apprese[chiave]
    parole = re.findall(r"\w+", descrizione.lower())
    for categoria, radici in CATEGORIE.items():
        if any(parola.startswith(radice) for parola in parole for radice in radici):
            return categoria
    return ALTRO


@dataclass(frozen=True)
class Spesa:
    id: int
    data: date
    importo: int  # centesimi
    categoria: str
    descrizione: str
    chi: str = ""  # nel gruppo: chi ha pagato
    chi_id: int | None = None

    def in_dizionario(self) -> dict[str, Any]:
        return {**asdict(self), "data": self.data.isoformat()}

    @classmethod
    def da_dizionario(cls, dati: dict[str, Any]) -> Spesa:
        return cls(**{**dati, "data": date.fromisoformat(dati["data"])})


def leggi_spesa(argomenti: Sequence[str]) -> tuple[int, str] | None:
    """``("45", "benzina")`` o ``("benzina", "45€")`` → (4500, "benzina")."""
    for i, parola in enumerate(argomenti):
        importo = leggi_importo(parola)
        if importo is not None and importo > 0:
            descrizione = " ".join([*argomenti[:i], *argomenti[i + 1 :]]).strip()
            return importo, descrizione or "varie"
    return None


class RegistroSpese:
    def __init__(self, archivio: Archivio, chat_id: int) -> None:
        self._archivio = archivio
        self._chat_id = chat_id

    def _chiave(self, mese: str) -> str:
        return f"spese:{self._chat_id}:{mese}"

    def del_mese(self, giorno: date) -> list[Spesa]:
        dati = self._archivio.leggi(self._chiave(mese_chiave(giorno))) or []
        return [Spesa.da_dizionario(d) for d in dati]

    def _salva(self, giorno: date, spese: Iterable[Spesa]) -> None:
        self._archivio.scrivi(self._chiave(mese_chiave(giorno)), [s.in_dizionario() for s in spese])

    def aggiungi(
        self,
        giorno: date,
        importo: int,
        descrizione: str,
        categoria: str | None = None,
        *,
        chi: str = "",
        chi_id: int | None = None,
    ) -> Spesa:
        spese = self.del_mese(giorno)
        nuova = Spesa(
            id=max((s.id for s in spese), default=0) + 1,
            data=giorno,
            importo=importo,
            categoria=categoria or categoria_per(descrizione, self.apprese()),
            descrizione=descrizione,
            chi=chi,
            chi_id=chi_id,
        )
        self._salva(giorno, [*spese, nuova])
        return nuova

    def trova(self, giorno: date, id_spesa: int) -> Spesa | None:
        return next((s for s in self.del_mese(giorno) if s.id == id_spesa), None)

    def elimina(self, giorno: date, id_spesa: int) -> Spesa | None:
        spese = self.del_mese(giorno)
        trovata = next((s for s in spese if s.id == id_spesa), None)
        if trovata is not None:
            self._salva(giorno, [s for s in spese if s.id != id_spesa])
        return trovata

    def cambia_categoria(self, giorno: date, id_spesa: int, categoria: str) -> Spesa | None:
        """Corregge la categoria e se la ricorda per le prossime spese simili."""
        spese = self.del_mese(giorno)
        trovata = next((s for s in spese if s.id == id_spesa), None)
        if trovata is None:
            return None
        cambiata = replace(trovata, categoria=categoria)
        self._salva(giorno, [cambiata if s.id == id_spesa else s for s in spese])
        if chiave := parola_chiave(trovata.descrizione):
            self._archivio.scrivi(self._chiave_apprese, {**self.apprese(), chiave: categoria})
        return cambiata

    @property
    def _chiave_apprese(self) -> str:
        return f"categorie:{self._chat_id}"

    def apprese(self) -> dict[str, str]:
        """Le categorie che hai corretto tu: parola → categoria."""
        return self._archivio.leggi(self._chiave_apprese) or {}

    def annulla_ultima(self, giorno: date) -> Spesa | None:
        spese = self.del_mese(giorno)
        if not spese:
            return None
        ultima = max(spese, key=lambda s: s.id)
        self._salva(giorno, [s for s in spese if s.id != ultima.id])
        return ultima

    def dell_anno(self, anno: int) -> list[Spesa]:
        return [s for mese in range(1, 13) for s in self.del_mese(date(anno, mese, 1))]


def quante(numero: int) -> str:
    return f"{numero} spesa" if numero == 1 else f"{numero} spese"


def totale(spese: Iterable[Spesa]) -> int:
    return sum(s.importo for s in spese)


def per_categoria(spese: Iterable[Spesa]) -> list[tuple[str, int, int]]:
    """[(categoria, totale, quante)] dalla categoria più cara."""
    somme: dict[str, int] = defaultdict(int)
    conteggi: dict[str, int] = defaultdict(int)
    for s in spese:
        somme[s.categoria] += s.importo
        conteggi[s.categoria] += 1
    return sorted(((c, somme[c], conteggi[c]) for c in somme), key=lambda voce: (-voce[1], voce[0]))


def barra(parte: int, tutto: int, larghezza: int = 10) -> str:
    pieni = round(larghezza * parte / tutto) if tutto else 0
    return "▓" * pieni + "░" * (larghezza - pieni)


def nome_mese(giorno: date) -> str:
    return f"{MESI[giorno.month - 1]} {giorno.year}"


def resoconto(spese: Sequence[Spesa], mese: date, precedente: int | None = None) -> str:
    """Totale del mese, divisione per categoria e confronto con il mese prima."""
    titolo = f"💶 <b>Spese di {nome_mese(mese)}</b>"
    if not spese:
        return f"{titolo}\nNessuna spesa segnata. Usa <code>/spesa 45 benzina</code>."
    somma = totale(spese)
    righe = [titolo, f"Totale: <b>{euro(somma)}</b> in {quante(len(spese))}", ""]
    for categoria, valore, numero in per_categoria(spese):
        percento = round(100 * valore / somma)
        righe.append(
            f"{escape(categoria)} {euro(valore)} ({numero})\n"
            f"<code>{barra(valore, somma)}</code> {percento}%"
        )
    if precedente:
        differenza = somma - precedente
        verso = "📈 più" if differenza > 0 else "📉 meno"
        righe += ["", f"{verso} del mese prima ({euro(precedente)}): {euro(abs(differenza))}"]
    return "\n".join(righe)


def elenco(spese: Sequence[Spesa], quante: int = 10) -> str:
    if not spese:
        return "💶 Nessuna spesa questo mese."
    ultime = sorted(spese, key=lambda s: s.id)[-quante:]
    righe = ["💶 <b>Ultime spese</b>"]
    for s in reversed(ultime):
        righe.append(
            f"{s.data.day}/{s.data.month} · {euro(s.importo)} · "
            f"{escape(s.categoria)} · {escape(s.descrizione)}"
        )
    righe.append(f"\nTotale del mese: <b>{euro(totale(spese))}</b>")
    return "\n".join(righe)


def csv_spese(spese: Iterable[Spesa]) -> bytes:
    """CSV con ``;`` e virgola decimale: Excel in italiano lo apre senza storie."""
    buffer = io.StringIO()
    scrittore = csv.writer(buffer, delimiter=";")
    scrittore.writerow(["data", "importo", "categoria", "descrizione"])
    for s in sorted(spese, key=lambda s: (s.data, s.id)):
        importo = euro(s.importo).removesuffix(" €").replace(".", "")
        categoria = s.categoria.split(" ", 1)[-1]  # senza emoji
        scrittore.writerow([s.data.strftime("%d/%m/%Y"), importo, categoria, s.descrizione])
    # BOM iniziale: così Excel riconosce l'UTF-8 e gli accenti.
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def pulsanti_spesa(spesa: Spesa) -> dict[str, Any]:
    """Sotto ogni spesa segnata: cambia categoria o annulla."""
    rif = f"{mese_chiave(spesa.data)}:{spesa.id}"
    return {
        "inline_keyboard": [
            [
                {"text": "🏷️ Cambia categoria", "callback_data": f"spcat:{rif}"},
                {"text": "↩️ Annulla", "callback_data": f"spann:{rif}"},
            ]
        ]
    }


def pulsanti_categorie(spesa: Spesa) -> dict[str, Any]:
    """Tutte le categorie, due per riga, per correggere una spesa."""
    rif = f"{mese_chiave(spesa.data)}:{spesa.id}"
    tasti = [
        {"text": c, "callback_data": f"spset:{rif}:{i}"} for i, c in enumerate(TUTTE_LE_CATEGORIE)
    ]
    return {"inline_keyboard": [tasti[i : i + 2] for i in range(0, len(tasti), 2)]}


def leggi_riferimento(valore: str) -> tuple[date, int, int | None]:
    """``"2026-11:3"`` o ``"2026-11:3:5"`` → (1° del mese, id spesa, indice categoria)."""
    parti = valore.split(":")
    anno, mese = (int(x) for x in parti[0].split("-"))
    indice = int(parti[2]) if len(parti) > 2 else None
    return date(anno, mese, 1), int(parti[1]), indice
