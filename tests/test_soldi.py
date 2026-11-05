from datetime import date

import pytest

from azdora.soldi import aggiungi_mesi, euro, leggi_data, leggi_importo, leggi_mese, mese_chiave

OGGI = date(2026, 11, 5)


@pytest.mark.parametrize(
    ("testo", "centesimi"),
    [
        ("45", 4500),
        ("45€", 4500),
        ("€45", 4500),
        ("12,50", 1250),
        ("12.5", 1250),
        ("12,5 €", 1250),
        ("0,99", 99),
        ("1.234,56", 123456),
        ("1234", 123400),
        ("180 euro", 18000),
        ("65eur", 6500),
    ],
)
def test_leggi_importo(testo, centesimi):
    assert leggi_importo(testo) == centesimi


@pytest.mark.parametrize("testo", ["", "benzina", "12,345", "1,2,3", "-5", "12/11"])
def test_non_sono_importi(testo):
    assert leggi_importo(testo) is None


@pytest.mark.parametrize(
    ("centesimi", "testo"),
    [(0, "0,00 €"), (5, "0,05 €"), (4500, "45,00 €"), (123456, "1.234,56 €"), (-250, "-2,50 €")],
)
def test_euro(centesimi, testo):
    assert euro(centesimi) == testo


@pytest.mark.parametrize(
    ("testo", "attesa"),
    [
        ("oggi", OGGI),
        ("Domani", date(2026, 11, 6)),
        ("31/12", date(2026, 12, 31)),
        ("5/11", OGGI),  # oggi conta ancora come quest'anno
        ("31/01", date(2027, 1, 31)),  # già passato: l'anno prossimo
        ("4.11", date(2027, 11, 4)),
        ("15/3/2028", date(2028, 3, 15)),
        ("15-03-28", date(2028, 3, 15)),
        ("1/1/2025", date(2025, 1, 1)),  # con l'anno esplicito resta nel passato
    ],
)
def test_leggi_data(testo, attesa):
    assert leggi_data(testo, OGGI) == attesa


@pytest.mark.parametrize("testo", ["31/02", "bollo", "13/13", "12", ""])
def test_date_non_valide(testo):
    assert leggi_data(testo, OGGI) is None


@pytest.mark.parametrize(
    ("partenza", "mesi", "arrivo"),
    [
        (date(2026, 1, 31), 1, date(2026, 2, 28)),
        (date(2028, 1, 31), 1, date(2028, 2, 29)),
        (date(2026, 11, 15), 2, date(2027, 1, 15)),
        (date(2026, 11, 7), 12, date(2027, 11, 7)),
        (date(2026, 3, 31), -1, date(2026, 2, 28)),
        (date(2026, 1, 10), -1, date(2025, 12, 10)),
    ],
)
def test_aggiungi_mesi(partenza, mesi, arrivo):
    assert aggiungi_mesi(partenza, mesi) == arrivo


def test_mese_chiave():
    assert mese_chiave(OGGI) == "2026-11"


@pytest.mark.parametrize(
    ("testo", "attesa"),
    [
        ("10", date(2026, 10, 1)),
        ("11", date(2026, 11, 1)),
        ("12", date(2025, 12, 1)),  # dicembre non è ancora arrivato: quello scorso
        ("3/2025", date(2025, 3, 1)),
        ("ottobre", date(2026, 10, 1)),
        ("Nov", date(2026, 11, 1)),
        ("dicembre", date(2025, 12, 1)),
    ],
)
def test_leggi_mese(testo, attesa):
    assert leggi_mese(testo, OGGI) == attesa


@pytest.mark.parametrize("testo", ["13", "0", "boh", "ma"])
def test_mesi_non_validi(testo):
    assert leggi_mese(testo, OGGI) is None
