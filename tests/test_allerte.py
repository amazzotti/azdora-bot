from datetime import datetime, timedelta

import httpx
import pytest
import respx

from azdora.allerte import (
    URL_ALLERTA_ER,
    Allerta,
    Meteoalarm,
    formatta,
    leggi_allerte,
    normalizza_regione,
    per_regione,
)
from azdora.config import FUSO_ORARIO
from tests.conftest import carica

ADESSO = datetime(2026, 11, 5, 7, 0, tzinfo=FUSO_ORARIO)


def avviso(livello=2, tipo=3, regione="Emilia e Romagna", ore=12, identificativo="id-1"):
    """Un avviso nel formato JSON di Meteoalarm, con testi in inglese e italiano."""
    colori = {1: "green", 2: "yellow", 3: "orange", 4: "red"}
    inizio = ADESSO.isoformat()
    fine = (ADESSO + timedelta(hours=ore)).isoformat()

    def info(lingua, evento):
        return {
            "language": lingua,
            "event": evento,
            "onset": inizio,
            "expires": fine,
            "web": "https://meteoalarm.org/it/live/region/IT?s=emilia",
            "area": [{"areaDesc": regione, "geocode": [{"value": "IT008"}]}],
            "parameter": [
                {"valueName": "awareness_level", "value": f"{livello}; {colori[livello]}; x"},
                {"valueName": "awareness_type", "value": f"{tipo}; tipo"},
            ],
        }

    return {
        "alert": {
            "identifier": identificativo,
            "info": [info("en-GB", "Thunderstorm"), info("it-IT", "Temporali")],
        }
    }


def test_lettura_fixture_reale():
    allerte = leggi_allerte(carica("meteoalarm_italia.json"))
    assert {a.regione for a in allerte} >= {"Emilia e Romagna", "Lombardia"}
    er = [a for a in allerte if a.regione == "Emilia e Romagna"]
    assert er[0].livello == 1
    assert er[0].fenomeno == "Vento"
    assert er[0].link.startswith("https://meteoalarm.org/it/")  # testi italiani preferiti


def test_lettura_avviso_completo():
    (allerta,) = leggi_allerte({"warnings": [avviso(livello=3, tipo=10)]})
    assert allerta == Allerta(
        identificativo="id-1",
        regione="Emilia e Romagna",
        livello=3,
        fenomeno="Pioggia",
        inizio=ADESSO,
        fine=ADESSO + timedelta(hours=12),
        link="https://meteoalarm.org/it/live/region/IT?s=emilia",
    )
    assert allerta.chiave == "allerta:id-1:3"


def test_avvisi_incompleti_ignorati():
    rotto = avviso()
    rotto["alert"]["info"][1]["parameter"] = []
    rotto["alert"]["info"][0]["parameter"] = []
    assert leggi_allerte({"warnings": [rotto, {"alert": {}}, {}]}) == []
    assert leggi_allerte({}) == []


def test_tipo_sconosciuto_usa_il_nome_dell_evento():
    (allerta,) = leggi_allerte({"warnings": [avviso(tipo=99)]})
    assert allerta.fenomeno == "Temporali"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Emilia-Romagna", "Emilia e Romagna"),
        ("Friuli-Venezia Giulia", "Friuli Venezia Giulia"),
        ("Valle d'Aosta", "Valle d’Aosta"),
    ],
)
def test_nomi_delle_regioni_equivalenti(a, b):
    assert normalizza_regione(a) == normalizza_regione(b)


def test_filtro_per_regione_livello_e_scadenza():
    allerte = leggi_allerte(
        {
            "warnings": [
                avviso(livello=1, identificativo="verde"),
                avviso(livello=2, identificativo="gialla"),
                avviso(livello=4, identificativo="rossa"),
                avviso(livello=3, identificativo="scaduta", ore=-1),
                avviso(livello=3, identificativo="altrove", regione="Lombardia"),
            ]
        }
    )
    scelte = per_regione(allerte, "Emilia-Romagna", ADESSO)
    assert [a.identificativo for a in scelte] == ["rossa", "gialla"]


def test_formatta_con_link_ufficiale_per_l_emilia_romagna():
    allerte = leggi_allerte({"warnings": [avviso(livello=3, tipo=1)]})
    testo = formatta(allerte, "Emilia-Romagna", "<b>Allerte</b>")
    assert "🟠 <b>Allerta arancione: Vento</b>" in testo
    assert "dal gio 5/11 07:00 al gio 5/11 19:00" in testo
    assert "Meteoalarm" in testo
    assert URL_ALLERTA_ER in testo
    assert URL_ALLERTA_ER not in formatta(allerte, "Lombardia", "x")


@respx.mock
def test_meteoalarm_via_http():
    rotta = respx.get("https://feeds.meteoalarm.org/api/v1/warnings/feeds-italy").mock(
        return_value=httpx.Response(200, json={"warnings": [avviso()]})
    )
    with httpx.Client() as http:
        assert len(Meteoalarm(http).allerte("IT")) == 1
        assert Meteoalarm(http).allerte("jp") == []  # paese senza feed: nessuna richiesta
    assert rotta.call_count == 1


@respx.mock
def test_meteoalarm_giu():
    respx.get(url__startswith="https://feeds.meteoalarm.org/").mock(
        return_value=httpx.Response(502)
    )
    with httpx.Client() as http:
        assert Meteoalarm(http).allerte("it") == []
