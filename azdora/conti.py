"""Conti di gruppo: chi ha pagato cosa e chi deve quanto a chi (spese divise in parti uguali).

Ogni spesa segnata nel gruppo ricorda chi l'ha scritta. ``/conti`` fa i conti dall'ultimo
``/pareggio`` (o dall'inizio) e propone il minor numero di passaggi di soldi.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from html import escape

from azdora.archivio import Archivio
from azdora.soldi import aggiungi_mesi, euro
from azdora.spese import RegistroSpese, Spesa

MESI_MASSIMI = 24


@dataclass(frozen=True)
class Passaggio:
    da: str
    a: str
    importo: int


class MembriGruppo:
    """Chi ha scritto almeno una volta nel gruppo: serve a dividere le spese tra tutti."""

    def __init__(self, archivio: Archivio, chat_id: int) -> None:
        self._archivio = archivio
        self._chiave = f"membri:{chat_id}"

    def tutti(self) -> dict[int, str]:
        return {int(k): v for k, v in (self._archivio.leggi(self._chiave) or {}).items()}

    def ricorda(self, utente_id: int, nome: str) -> None:
        membri = self.tutti()
        if membri.get(utente_id) != nome:
            membri[utente_id] = nome
            self._archivio.scrivi(self._chiave, {str(k): v for k, v in membri.items()})


def spese_da(registro: RegistroSpese, dal: date | None, oggi: date) -> list[Spesa]:
    """Le spese dal giorno indicato (compreso) a oggi."""
    inizio = (dal or aggiungi_mesi(oggi, -MESI_MASSIMI + 1)).replace(day=1)
    spese: list[Spesa] = []
    mese = inizio
    while mese <= oggi:
        spese += [s for s in registro.del_mese(mese) if dal is None or s.data >= dal]
        mese = aggiungi_mesi(mese, 1)
    return spese


def saldi(spese: Iterable[Spesa], membri: Mapping[int, str]) -> dict[int, int]:
    """Per ognuno: quanto ha pagato meno la sua parte (positivo = deve ricevere)."""
    pagato = {m: 0 for m in membri}
    totale_comune = 0
    for s in spese:
        if s.chi_id is None:
            continue
        pagato[s.chi_id] = pagato.get(s.chi_id, 0) + s.importo
        totale_comune += s.importo
    persone = list(pagato)
    if not persone:
        return {}
    quota, resto = divmod(totale_comune, len(persone))
    # Gli eventuali centesimi in più vanno ai primi, così la somma torna sempre a zero.
    return {p: pagato[p] - quota - (1 if i < resto else 0) for i, p in enumerate(persone)}


def passaggi(saldi_persone: Mapping[int, int], nomi: Mapping[int, str]) -> list[Passaggio]:
    """Il minor numero di passaggi di soldi per pareggiare (algoritmo goloso)."""
    debitori = sorted(((-v, p) for p, v in saldi_persone.items() if v < 0), reverse=True)
    creditori = sorted(((v, p) for p, v in saldi_persone.items() if v > 0), reverse=True)
    risultato = []
    i = j = 0
    debiti = [list(x) for x in debitori]
    crediti = [list(x) for x in creditori]
    while i < len(debiti) and j < len(crediti):
        importo = min(debiti[i][0], crediti[j][0])
        if importo > 0:
            risultato.append(
                Passaggio(nomi.get(debiti[i][1], "?"), nomi.get(crediti[j][1], "?"), importo)
            )
        debiti[i][0] -= importo
        crediti[j][0] -= importo
        if debiti[i][0] == 0:
            i += 1
        if crediti[j][0] == 0:
            j += 1
    return risultato


def resoconto(spese: Sequence[Spesa], membri: Mapping[int, str], dal: date | None) -> str:
    da_quando = f"dal {dal.day}/{dal.month}/{dal.year}" if dal else "da quando le segnate"
    comuni = [s for s in spese if s.chi_id is not None]
    if not comuni:
        return (
            f"🤝 Nessuna spesa comune {da_quando}.\n"
            "Nel gruppo scrivete <code>/spesa 60 spesa conad</code>: chi la scrive è chi ha pagato."
        )
    totale_comune = sum(s.importo for s in comuni)
    per_persona = saldi(comuni, membri)
    nomi = {**membri, **{s.chi_id: s.chi for s in comuni if s.chi_id is not None and s.chi}}
    righe = [
        f"🤝 <b>Conti di casa</b> ({da_quando})",
        "",
        f"Spese comuni: <b>{euro(totale_comune)}</b>",
    ]
    quota = totale_comune // max(len(per_persona), 1)
    righe.append(f"A testa: {euro(quota)} ({len(per_persona)} persone)\n")
    for persona in per_persona:
        pagato = sum(s.importo for s in comuni if s.chi_id == persona)
        righe.append(f"• {escape(nomi.get(persona, '?'))} ha pagato {euro(pagato)}")
    da_fare = passaggi(per_persona, nomi)
    righe.append("")
    if da_fare:
        righe += [
            f"👉 <b>{escape(p.da)}</b> dà <b>{euro(p.importo)}</b> a {escape(p.a)}" for p in da_fare
        ]
        righe.append("\nQuando vi siete messi in pari: /pareggio")
    else:
        righe.append("✅ Siete pari!")
    return "\n".join(righe)


class Pareggio:
    def __init__(self, archivio: Archivio, chat_id: int) -> None:
        self._archivio = archivio
        self._chiave = f"pareggio:{chat_id}"

    def dal(self) -> date | None:
        valore = self._archivio.leggi(self._chiave)
        return date.fromisoformat(valore) if valore else None

    def segna(self, giorno: date) -> None:
        self._archivio.scrivi(self._chiave, giorno.isoformat())
