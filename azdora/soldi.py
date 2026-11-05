"""Importi in euro e date scritte "all'italiana", come le scrive una persona su Telegram.

Gli importi si tengono in **centesimi interi**: con i float 0,1 + 0,2 non fa 0,3.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

_IMPORTO = re.compile(
    r"""^(?:€\s?)?
    (?P<intero>\d{1,3}(?:[.\s]\d{3})+|\d+)   # 1.234 oppure 1234
    (?:[,.](?P<decimali>\d{1,2}))?           # ,50 oppure .5
    \s?(?:€|eur|euro)?$""",
    re.IGNORECASE | re.VERBOSE,
)
_DATA = re.compile(r"^(?P<g>\d{1,2})[/.-](?P<m>\d{1,2})(?:[/.-](?P<a>\d{2}|\d{4}))?$")


def leggi_importo(testo: str) -> int | None:
    """``"45"``, ``"12,50€"``, ``"1.234,5"`` → centesimi. ``None`` se non è un importo."""
    corrispondenza = _IMPORTO.match(testo.strip())
    if corrispondenza is None:
        return None
    intero = int(re.sub(r"[.\s]", "", corrispondenza["intero"]))
    decimali = (corrispondenza["decimali"] or "0").ljust(2, "0")
    return intero * 100 + int(decimali)


def euro(centesimi: int) -> str:
    """12345 → ``"123,45 €"``; 123456789 → ``"1.234.567,89 €"``."""
    segno = "-" if centesimi < 0 else ""
    interi, cent = divmod(abs(centesimi), 100)
    return f"{segno}{interi:,}".replace(",", ".") + f",{cent:02d} €"


def leggi_data(testo: str, oggi: date) -> date | None:
    """``"31/01"`` (la prossima volta che capita), ``"31/1/27"``, ``"oggi"``, ``"domani"``."""
    parola = testo.strip().lower()
    if parola == "oggi":
        return oggi
    if parola == "domani":
        return oggi + timedelta(days=1)
    corrispondenza = _DATA.match(parola)
    if corrispondenza is None:
        return None
    giorno, mese = int(corrispondenza["g"]), int(corrispondenza["m"])
    anno_testo = corrispondenza["a"]
    try:
        if anno_testo:
            anno = int(anno_testo) + (2000 if len(anno_testo) == 2 else 0)
            return date(anno, mese, giorno)
        candidata = date(oggi.year, mese, giorno)
        return candidata if candidata >= oggi else date(oggi.year + 1, mese, giorno)
    except ValueError:
        return None  # 31/02 e simili


def aggiungi_mesi(giorno: date, mesi: int) -> date:
    """31 gennaio + 1 mese = 28 (o 29) febbraio: il giorno si ferma alla fine del mese."""
    indice = giorno.month - 1 + mesi
    anno, mese = giorno.year + indice // 12, indice % 12 + 1
    ultimo = calendar.monthrange(anno, mese)[1]
    return date(anno, mese, min(giorno.day, ultimo))


def mese_chiave(giorno: date) -> str:
    """``date(2026, 11, 5)`` → ``"2026-11"``."""
    return f"{giorno.year:04d}-{giorno.month:02d}"


def leggi_mese(testo: str, oggi: date) -> date | None:
    """``"11"``, ``"11/2026"``, ``"novembre"`` → primo giorno di quel mese."""
    parola = testo.strip().lower()
    mesi = (
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
    for numero, nome in enumerate(mesi, start=1):
        if nome.startswith(parola) and len(parola) >= 3:
            anno = oggi.year if numero <= oggi.month else oggi.year - 1
            return date(anno, numero, 1)
    corrispondenza = re.match(r"^(\d{1,2})(?:[/.-](\d{2}|\d{4}))?$", parola)
    if corrispondenza is None:
        return None
    mese = int(corrispondenza[1])
    if not 1 <= mese <= 12:
        return None
    if corrispondenza[2]:
        anno = int(corrispondenza[2]) + (2000 if len(corrispondenza[2]) == 2 else 0)
    else:
        anno = oggi.year if mese <= oggi.month else oggi.year - 1
    return date(anno, mese, 1)
