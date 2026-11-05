import csv
import io
from datetime import date

import pytest

from azdora.archivio import ArchivioMemoria
from azdora.scadenze import (
    NuovaScadenza,
    RegistroScadenze,
    Scadenza,
    da_ricordare,
    elenco,
    leggi_scadenza,
    pulsanti,
    quando,
)
from azdora.spese import (
    ALTRO,
    RegistroSpese,
    Spesa,
    barra,
    categoria_per,
    csv_spese,
    leggi_spesa,
    per_categoria,
    resoconto,
)
from azdora.spese import elenco as elenco_spese

OGGI = date(2026, 11, 5)

# --- Scadenze ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("testo", "attesa"),
    [
        (
            "31/01 bollo auto 180€ annuale",
            NuovaScadenza("bollo auto", date(2027, 1, 31), 18000, 12),
        ),
        (
            "assicurazione 420 15/03/2027 annuale",
            NuovaScadenza("assicurazione", date(2027, 3, 15), 42000, 12),
        ),
        (
            "20/11 bolletta luce 65,40 bimestrale",
            NuovaScadenza("bolletta luce", date(2026, 11, 20), 6540, 2),
        ),
        ("10/12 revisione", NuovaScadenza("revisione", date(2026, 12, 10), None, 0)),
        ("1/12 affitto 650 ogni mese", NuovaScadenza("affitto", date(2026, 12, 1), 65000, 1)),
        ("domani tari 120 Semestrale", NuovaScadenza("tari", date(2026, 11, 6), 12000, 6)),
    ],
)
def test_leggi_scadenza(testo, attesa):
    assert leggi_scadenza(testo.split(), OGGI) == attesa


@pytest.mark.parametrize("testo", ["", "bollo auto", "31/01", "180 annuale"])
def test_scadenza_incompleta(testo):
    assert leggi_scadenza(testo.split(), OGGI) is None


def test_prossima_scadenza():
    bollo = Scadenza(1, "bollo", date(2026, 1, 31), 18000, 1)
    assert bollo.prossima().data == date(2026, 2, 28)
    assert Scadenza(2, "una tantum", OGGI).prossima() is None


def test_registro_scadenze_completo():
    registro = RegistroScadenze(ArchivioMemoria(), 1)
    bollo = registro.aggiungi(NuovaScadenza("bollo", date(2026, 11, 7), 18000, 12))
    revisione = registro.aggiungi(NuovaScadenza("revisione", date(2026, 11, 6), None, 0))
    assert [s.descrizione for s in registro.tutte()] == ["revisione", "bollo"]
    assert registro.trova(bollo.id) == bollo

    pagata, prossima = registro.paga(bollo.id)
    assert pagata == bollo
    assert prossima.data == date(2027, 11, 7)
    assert registro.trova(bollo.id).data == date(2027, 11, 7)

    pagata, prossima = registro.paga(revisione.id)
    assert prossima is None
    assert registro.trova(revisione.id) is None
    assert registro.paga(99) is None

    assert registro.elimina(bollo.id).descrizione == "bollo"
    assert registro.tutte() == []
    assert registro.elimina(99) is None


def test_gli_id_non_si_ripetono():
    registro = RegistroScadenze(ArchivioMemoria(), 1)
    a = registro.aggiungi(NuovaScadenza("a", OGGI, None, 0))
    registro.aggiungi(NuovaScadenza("b", OGGI, None, 0))
    registro.elimina(a.id)
    c = registro.aggiungi(NuovaScadenza("c", OGGI, None, 0))
    assert c.id == 3


def test_le_chat_non_si_mischiano():
    archivio = ArchivioMemoria()
    RegistroScadenze(archivio, 1).aggiungi(NuovaScadenza("mia", OGGI, None, 0))
    assert RegistroScadenze(archivio, 2).tutte() == []


@pytest.mark.parametrize(
    ("giorni", "si"),
    [
        (30, True),
        (29, False),
        (7, True),
        (1, True),
        (0, True),
        (-1, True),
        (-2, False),
        (-3, True),
        (-7, True),
        (-14, True),
        (-15, False),
        (100, False),
    ],
)
def test_quando_ricordare(giorni, si):
    from datetime import timedelta

    s = Scadenza(1, "x", OGGI + timedelta(days=giorni))
    assert bool(da_ricordare([s], OGGI)) is si


@pytest.mark.parametrize(
    ("giorni", "frammento"),
    [(0, "oggi"), (1, "domani"), (-3, "scaduta da 3 giorni"), (10, "tra 10 giorni")],
)
def test_quando(giorni, frammento):
    assert frammento in quando(giorni, OGGI)


def test_elenco_e_pulsanti():
    assert "Nessuna scadenza" in elenco([], OGGI)
    assert pulsanti([], con_elimina=True) is None
    lunga = Scadenza(7, "assicurazione della macchina nuova di Giulia", OGGI, 42000, 12)
    testo = elenco([lunga], OGGI)
    assert "420,00 €" in testo and "ogni anno" in testo and "oggi" in testo
    (fila,) = pulsanti([lunga], con_elimina=True)["inline_keyboard"]
    assert fila[0]["callback_data"] == "pagato:7"
    assert fila[0]["text"].endswith("…")
    assert fila[1]["callback_data"] == "elimina:7"
    assert len(pulsanti([lunga], con_elimina=False)["inline_keyboard"][0]) == 1


def test_elenco_scadenze_html_sicuro():
    assert "&lt;b&gt;" in elenco([Scadenza(1, "<b>", OGGI)], OGGI)


# --- Spese -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("descrizione", "categoria"),
    [
        ("benzina", "🚗 Auto"),
        ("Gasolio Esso", "🚗 Auto"),
        ("bollo auto", "🚗 Auto"),
        ("spesa Conad", "🛒 Spesa"),
        ("bolletta luce", "💡 Bollette"),
        ("pizza con Giulia", "🍕 Fuori"),
        ("piadina", "🍕 Fuori"),
        ("farmacia", "💊 Salute"),
        ("Netflix", "📺 Abbonamenti"),
        ("cinema", "🎉 Svago"),
        ("scarpe", "👕 Vestiti"),
        ("regalo compleanno", "🎉 Svago"),
        ("boh", ALTRO),
    ],
)
def test_categorie(descrizione, categoria):
    assert categoria_per(descrizione) == categoria


@pytest.mark.parametrize(
    ("parole", "attesa"),
    [
        (["45", "benzina"], (4500, "benzina")),
        (["pizza", "12,50€"], (1250, "pizza")),
        (["30"], (3000, "varie")),
        (["benzina"], None),
        (["0", "niente"], None),
        ([], None),
    ],
)
def test_leggi_spesa(parole, attesa):
    assert leggi_spesa(parole) == attesa


def test_registro_spese():
    registro = RegistroSpese(ArchivioMemoria(), 1)
    a = registro.aggiungi(OGGI, 4500, "benzina")
    b = registro.aggiungi(OGGI, 1250, "pizza")
    registro.aggiungi(date(2026, 10, 3), 6000, "bolletta luce")
    assert a.categoria == "🚗 Auto" and b.id == 2
    assert [s.descrizione for s in registro.del_mese(OGGI)] == ["benzina", "pizza"]
    assert len(registro.dell_anno(2026)) == 3
    assert registro.annulla_ultima(OGGI) == b
    assert registro.annulla_ultima(date(2026, 9, 1)) is None
    assert registro.aggiungi(OGGI, 100, "x", categoria="🏠 Casa").categoria == "🏠 Casa"


def test_spesa_serializzata():
    s = Spesa(1, OGGI, 4500, "🚗 Auto", "benzina")
    assert Spesa.da_dizionario(s.in_dizionario()) == s


def test_resoconto_e_categorie():
    spese = [
        Spesa(1, OGGI, 4500, "🚗 Auto", "benzina"),
        Spesa(2, OGGI, 1500, "🍕 Fuori", "pizza"),
        Spesa(3, OGGI, 4000, "🚗 Auto", "tagliando"),
    ]
    assert per_categoria(spese) == [("🚗 Auto", 8500, 2), ("🍕 Fuori", 1500, 1)]
    testo = resoconto(spese, date(2026, 11, 1), precedente=12000)
    assert "Spese di novembre 2026" in testo
    assert "Totale: <b>100,00 €</b> in 3 spese" in testo
    assert "85%" in testo and "15%" in testo
    assert "📉 meno del mese prima (120,00 €): 20,00 €" in testo
    assert "📈 più" in resoconto(spese, date(2026, 11, 1), precedente=5000)
    assert "in 1 spesa" in resoconto(spese[:1], date(2026, 11, 1))
    assert "Nessuna spesa" in resoconto([], date(2026, 11, 1))


def test_barra():
    assert barra(5, 10) == "▓▓▓▓▓░░░░░"
    assert barra(0, 0) == "░" * 10


def test_elenco_ultime_spese():
    spese = [Spesa(i, OGGI, 100 * i, ALTRO, f"cosa {i}") for i in range(1, 15)]
    testo = elenco_spese(spese)
    assert "cosa 14" in testo and "cosa 5" in testo and "cosa 4" not in testo
    assert testo.index("cosa 14") < testo.index("cosa 13")
    assert "Nessuna spesa" in elenco_spese([])


def test_csv_per_excel_italiano():
    spese = [
        Spesa(2, date(2026, 11, 5), 123456, "🚗 Auto", 'tagliando "grande"'),
        Spesa(1, date(2026, 10, 1), 1250, "🍕 Fuori", "pizza; birra"),
    ]
    contenuto = csv_spese(spese)
    assert contenuto.startswith("﻿".encode())
    righe = list(csv.reader(io.StringIO(contenuto.decode("utf-8-sig")), delimiter=";"))
    assert righe[0] == ["data", "importo", "categoria", "descrizione"]
    assert righe[1] == ["01/10/2026", "12,50", "Fuori", "pizza; birra"]
    assert righe[2] == ["05/11/2026", "1234,56", "Auto", 'tagliando "grande"']
