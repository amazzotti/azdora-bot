"""Le feste fisse della zona, da Cervia a Pesaro, e le sagre trovate nei giornali.

Le date delle feste fisse sono **indicative**: cambiano un po' ogni anno, quindi il bot
dice sempre di controllare il programma. Le sagre "di questi giorni" invece arrivano
dalle notizie (Altarimini e Google News), quindi sono aggiornate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from html import escape

from azdora.notizie import Notizia, formatta_breve

GIORNI_AVANTI = 30  # le feste del prossimo mese


@dataclass(frozen=True)
class Festa:
    nome: str
    posto: str
    inizio: tuple[int, int]  # (mese, giorno)
    fine: tuple[int, int]
    descrizione: str
    link: str = ""

    def periodo(self, anno: int) -> tuple[date, date]:
        """Il periodo nell'anno dato; se finisce l'anno dopo (es. dicembre–gennaio) lo segue."""
        inizio = date(anno, *self.inizio)
        fine = date(anno, *self.fine)
        if fine < inizio:
            fine = date(anno + 1, *self.fine)
        return inizio, fine


FESTE: tuple[Festa, ...] = (
    Festa(
        "Fiera di San Martino, la «Fiera dei Becchi»",
        "Santarcangelo",
        (11, 7),
        (11, 11),
        "la fiera più antica della Romagna: bancarelle, castagne, vino nuovo e le corna "
        "appese sotto l'arco",
        "https://lapiazzarimini.it/2024/santarcangelo-di-romagna-fiera-di-san-martino-tutti-gli-eventi",
    ),
    Festa(
        "Festa di San Martino",
        "Riccione",
        (11, 7),
        (11, 11),
        "il patrono: tornei, concerti e la processione",
    ),
    Festa(
        "Il Pesce fa Festa",
        "Cesenatico",
        (10, 28),
        (11, 2),
        "gli stand del pesce dell'Adriatico lungo il porto canale",
        "https://visitcesenatico.it/en/live-cesenatico/events/the-fish-festival",
    ),
    Festa(
        "Presepe della Marineria",
        "Cesenatico",
        (11, 29),
        (1, 6),
        "le statue del presepe sulle barche storiche del porto canale",
    ),
    Festa(
        "Sapore di Sale",
        "Cervia",
        (9, 1),
        (9, 10),
        "la festa del sale dolce di Cervia, con la Rimessa del sale e il mercato",
        "https://www.italia.it/it/emilia-romagna/ravenna/cosa-fare/evento-sapore-di-sale-cervia",
    ),
    Festa(
        "Fiera del Tartufo Bianco",
        "Sant'Agata Feltria",
        (10, 1),
        (10, 31),
        "le domeniche di ottobre, tra tartufi, polenta e castagne nel borgo",
    ),
)


def in_programma(oggi: date, giorni: int = GIORNI_AVANTI) -> list[tuple[Festa, date, date]]:
    """Le feste in corso o che iniziano entro ``giorni`` giorni, in ordine di data."""
    limite = oggi + timedelta(days=giorni)
    trovate = []
    for festa in FESTE:
        for anno in (oggi.year - 1, oggi.year, oggi.year + 1):
            inizio, fine = festa.periodo(anno)
            if inizio <= limite and fine >= oggi:
                trovate.append((festa, inizio, fine))
    return sorted(trovate, key=lambda voce: voce[1])


def nel_periodo(primo: date, ultimo: date) -> list[Festa]:
    """Le feste che si sovrappongono a un periodo (es. un weekend)."""
    return [
        festa
        for festa in FESTE
        for anno in (primo.year - 1, primo.year)
        if (p := festa.periodo(anno))[0] <= ultimo and p[1] >= primo
    ]


def _quando(oggi: date, inizio: date, fine: date) -> str:
    if inizio <= oggi:
        return f"in corso fino al {fine.day}/{fine.month}"
    if inizio == fine:
        return f"il {inizio.day}/{inizio.month}"
    return f"dal {inizio.day}/{inizio.month} al {fine.day}/{fine.month}"


def riga_festa(festa: Festa, testo_quando: str = "") -> str:
    nome = (
        f'<a href="{escape(festa.link, quote=True)}">{escape(festa.nome)}</a>'
        if festa.link
        else escape(festa.nome)
    )
    quando = f" · {testo_quando}" if testo_quando else ""
    return f"• <b>{nome}</b>, {escape(festa.posto)}{quando}\n  {escape(festa.descrizione)}"


def messaggio_sagre(oggi: date, eventi: Sequence[Notizia]) -> str:
    feste = in_programma(oggi)
    righe = ["🎪 <b>Sagre, fiere e feste da Cervia a Pesaro</b>"]
    if feste:
        righe += ["", "📅 <b>Le feste di sempre</b> <i>(date indicative)</i>"]
        righe += [riga_festa(f, _quando(oggi, i, fi)) for f, i, fi in feste]
    if eventi:
        righe += ["", formatta_breve(eventi[:8], "🗞️ <b>Dai giornali, in questi giorni</b>")]
    if not feste and not eventi:
        righe.append("\nIn questi giorni non ho trovato sagre in zona. Riprova verso il weekend!")
    return "\n".join(righe)
