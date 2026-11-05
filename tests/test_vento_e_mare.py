import pytest

from azdora.meteo.codici_wmo import SCONOSCIUTA, condizione
from azdora.meteo.mare import stato_mare
from azdora.meteo.vento import (
    beaufort,
    descrivi_vento,
    direzione_vento,
    freccia,
    kmh_in_nodi,
    nome_vento,
    punto_cardinale,
)


@pytest.mark.parametrize(
    ("gradi", "punto", "nome"),
    [
        (0, "N", "Tramontana"),
        (22.4, "N", "Tramontana"),
        (22.5, "NE", "Bora"),
        (45, "NE", "Bora"),
        (90, "E", "Levante"),
        (135, "SE", "Scirocco"),
        (180, "S", "Ostro"),
        (225, "SO", "Garbino"),
        (270, "O", "Ponente"),
        (298, "NO", "Maestrale"),
        (337.4, "NO", "Maestrale"),
        (337.5, "N", "Tramontana"),
        (360, "N", "Tramontana"),
        (-45, "NO", "Maestrale"),
    ],
)
def test_punti_cardinali_e_nomi_dei_venti(gradi, punto, nome):
    assert punto_cardinale(gradi) == punto
    assert nome_vento(gradi) == nome


def test_la_freccia_indica_dove_va_il_vento():
    assert freccia(0) == "↓"  # la tramontana spinge verso sud
    assert freccia(225) == "↗"  # il garbino spinge verso nord-est
    assert direzione_vento(225) == "Garbino da SO ↗"


@pytest.mark.parametrize(
    ("kmh", "forza", "descrizione"),
    [
        (0, 0, "calma"),
        (0.9, 0, "calma"),
        (1, 1, "bava di vento"),
        (5.9, 1, "bava di vento"),
        (6, 2, "brezza leggera"),
        (19.9, 3, "brezza tesa"),
        (20, 4, "vento moderato"),
        (38.9, 5, "vento teso"),
        (49, 6, "vento fresco"),
        (61, 7, "vento forte"),
        (74, 8, "burrasca moderata"),
        (88, 9, "burrasca forte"),
        (102, 10, "tempesta"),
        (117, 11, "fortunale"),
        (118, 12, "uragano"),
        (200, 12, "uragano"),
    ],
)
def test_scala_beaufort(kmh, forza, descrizione):
    risultato = beaufort(kmh)
    assert (risultato.forza, risultato.descrizione) == (forza, descrizione)


def test_nodi():
    assert kmh_in_nodi(18.52) == pytest.approx(10)


def test_descrizione_completa_del_vento():
    testo = descrivi_vento(18, 225, 30)
    assert testo == ("Garbino da SO ↗ 18 km/h (10 nodi), raffiche 30 km/h · forza 3, brezza tesa")


def test_descrizione_senza_raffiche_ne_direzione():
    assert descrivi_vento(25, None, None) == "25 km/h (13 nodi) · forza 4, vento moderato"


def test_raffiche_non_mostrate_se_non_superano_la_media():
    assert "raffiche" not in descrivi_vento(10, 90, 10)


def test_calma_e_dati_mancanti():
    assert descrivi_vento(0.5, 90, 2) == "calma piatta, niente vento"
    assert descrivi_vento(None, None, None) == "dati sul vento non disponibili"


@pytest.mark.parametrize(
    ("metri", "grado", "descrizione"),
    [
        (0, 0, "calmo"),
        (0.05, 1, "quasi calmo"),
        (0.38, 2, "poco mosso"),
        (0.5, 2, "poco mosso"),
        (0.86, 3, "mosso"),
        (2, 4, "molto mosso"),
        (3, 5, "agitato"),
        (5, 6, "molto agitato"),
        (8, 7, "grosso"),
        (12, 8, "molto grosso"),
        (15, 9, "tempestoso"),
    ],
)
def test_scala_douglas(metri, grado, descrizione):
    stato = stato_mare(metri)
    assert (stato.grado, stato.descrizione) == (grado, descrizione)


def test_codici_wmo():
    assert str(condizione(0)) == "☀️ Sereno"
    assert condizione(95).descrizione == "Temporale"
    assert condizione(12345) is SCONOSCIUTA
    assert condizione(None) is SCONOSCIUTA
