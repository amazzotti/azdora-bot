from dataclasses import replace
from datetime import date, datetime

import pytest

from azdora import messaggi
from azdora.config import FUSO_ORARIO, RICCIONE, RIMINI
from azdora.meteo.openmeteo import MareGiorno, MeteoGiorno, con_localita
from azdora.riepilogo import (
    DatiLocalita,
    consigli,
    riepilogo_domani,
    riepilogo_oggi,
    saluto,
)

GIORNO_TRANQUILLO = MeteoGiorno(
    data=date(2026, 6, 20),
    codice=1,
    t_min=18,
    t_max=26,
    prob_pioggia=5,
    pioggia_mm=0,
    vento_max=10,
    raffiche_max=18,
    direzione_dominante=90,
    alba=None,
    tramonto=None,
    uv_max=5,
)


def test_date_in_italiano():
    assert messaggi.data_breve(date(2026, 11, 5)) == "gio 5/11"
    assert messaggi.data_lunga(date(2026, 11, 8)) == "domenica 8 novembre"


def test_formattatori_con_dati_mancanti():
    assert messaggi.gradi(None) == "n.d."
    assert messaggi.kmh(None) == "n.d."
    assert messaggi.onde(None) == "n.d."
    assert messaggi.onde(0.38, 44) == "0.4 m da NE (poco mosso)"


def test_messaggio_meteo(previsioni):
    testo = messaggi.messaggio_meteo(previsioni)
    assert "<b>Meteo a Riccione</b>" in testo
    assert "☁️ Coperto" in testo
    assert "Maestrale da NO" in testo
    assert "ven 6/11" in testo
    assert "Alba 06:50" in testo
    assert "Open-Meteo.com" in testo


def test_messaggio_vento_con_prossime_ore(previsioni):
    testo = messaggi.messaggio_vento(con_localita(previsioni, RIMINI))
    assert "<b>Vento a Rimini</b>" in testo
    assert "<code>12:00</code>" in testo
    assert "forza 2, brezza leggera" in testo
    assert testo.count("raffiche") >= 4


def test_messaggio_mare(mare):
    testo = messaggi.messaggio_mare(mare)
    assert "onde 0.4 m da NE (poco mosso), periodo 3.7 s" in testo
    assert "Acqua: 17°C" in testo
    assert "sab 7/11: onde fino a 0.9 m da E (mosso)" in testo


def test_messaggio_mare_senza_temperatura(mare):
    mare = replace(mare, attuale=replace(mare.attuale, temperatura_acqua=None))
    assert "Acqua: n.d." in messaggi.messaggio_mare(mare)


@pytest.mark.parametrize(
    ("ora", "atteso"), [(7, "Buongiorno"), (14, "Buon pomeriggio"), (21, "Buonasera")]
)
def test_saluto(ora, atteso):
    assert saluto(datetime(2026, 11, 5, ora, tzinfo=FUSO_ORARIO)) == atteso


def test_consigli_giornata_da_spiaggia():
    mare = MareGiorno(GIORNO_TRANQUILLO.data, altezza_max=0.3, direzione_dominante=90)
    assert consigli(GIORNO_TRANQUILLO, mare) == ["🏖️ Mare calmo e caldo: giornata da spiaggia!"]


def test_consigli_nessuna_sorpresa():
    giorno = replace(GIORNO_TRANQUILLO, t_max=20)
    assert consigli(giorno) == ["✅ Nessuna sorpresa in vista: buona giornata!"]


def test_consigli_giornata_brutta():
    giorno = replace(
        GIORNO_TRANQUILLO,
        codice=95,
        prob_pioggia=90,
        raffiche_max=65,
        t_min=1,
        t_max=8,
        uv_max=1,
    )
    mare = MareGiorno(giorno.data, altezza_max=2.1, direzione_dominante=45)
    testi = " ".join(consigli(giorno, mare))
    assert "temporali" in testi
    assert "ombrello" in testi
    assert "Raffiche fino a 65 km/h" in testi
    assert "ghiaccio" in testi
    assert "niente bagno" in testi
    assert "spiaggia" not in testi


@pytest.mark.parametrize(
    ("modifiche", "frammento"),
    [
        ({"prob_pioggia": 40}, "Possibile qualche goccia (40%)"),
        ({"codice": 80, "prob_pioggia": 20}, "Possibile qualche goccia (20%)"),
        ({"codice": 45}, "Nebbia"),
        ({"codice": 73}, "Neve"),
        ({"raffiche_max": 40}, "Giornata ventosa"),
        ({"t_max": 35}, "Caldo forte"),
        ({"uv_max": 8}, "UV alto (8)"),
    ],
)
def test_singoli_consigli(modifiche, frammento):
    assert frammento in " ".join(consigli(replace(GIORNO_TRANQUILLO, **modifiche)))


def test_riepilogo_oggi_completo(previsioni, mare):
    dati = [
        DatiLocalita(previsioni, mare),
        DatiLocalita(con_localita(previsioni, RIMINI), con_localita(mare, RIMINI)),
    ]
    adesso = datetime(2026, 11, 5, 7, 0, tzinfo=FUSO_ORARIO)
    testo = riepilogo_oggi(dati, adesso, "Ale")
    assert testo.startswith("☀️ <b>Buongiorno Ale!</b>\nOggi è giovedì 5 novembre.")
    assert testo.index("Riccione") < testo.index("Rimini")
    assert "Adesso: Maestrale" in testo
    assert "acqua 17°C" in testo
    assert "<b>Consigli</b>" in testo
    assert "Possibile qualche goccia (28%)" in testo


def test_riepilogo_domani_senza_dati_attuali(previsioni):
    testo = riepilogo_domani([DatiLocalita(previsioni)], date(2026, 11, 6))
    assert "Domani, venerdì 6 novembre" in testo
    assert "Adesso" not in testo
    assert "Mare" not in testo
    assert "Porta l'ombrello: probabilità di pioggia 88%" in testo


def test_riepilogo_per_un_giorno_senza_previsioni(previsioni):
    testo = riepilogo_domani([DatiLocalita(previsioni)], date(2027, 1, 1))
    assert "Previsioni non disponibili" in testo
    assert "Consigli" not in testo


def test_con_localita_non_cambia_l_originale(previsioni):
    copia = con_localita(previsioni, RIMINI)
    assert copia.localita == RIMINI
    assert previsioni.localita == RICCIONE
