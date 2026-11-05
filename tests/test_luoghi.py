import httpx
import pytest
import respx

from azdora.luoghi import (
    URL_GEOCODING,
    URL_NOMINATIM,
    ErroreLuoghi,
    Luoghi,
    Luogo,
    distanza_km,
    leggi_luogo_inverso,
    leggi_ricerca,
    localita_qui,
)
from tests.conftest import carica


def test_distanza_riccione_rimini():
    # In linea d'aria tra i due centri ci sono quasi 10 km.
    assert distanza_km(43.9993, 12.6566, 44.0594, 12.5683) == pytest.approx(9.7, abs=0.5)


def test_distanza_nulla_e_simmetrica():
    assert distanza_km(44, 12, 44, 12) == 0
    assert distanza_km(44, 12, 45, 13) == pytest.approx(distanza_km(45, 13, 44, 12))


def test_ricerca_misano():
    localita = leggi_ricerca(carica("geocoding_misano.json"))
    assert localita is not None
    assert localita.nome == "Misano Adriatico"
    assert localita.lat == pytest.approx(43.97714)
    assert (localita.mare_lat, localita.mare_lon) == (localita.lat, localita.lon)


def test_ricerca_senza_risultati():
    assert leggi_ricerca({"generationtime_ms": 0.1}) is None


def test_ricerca_all_estero_mostra_il_paese():
    dati = {
        "results": [
            {
                "id": 1,
                "name": "Nizza",
                "latitude": 43.7,
                "longitude": 7.27,
                "country_code": "FR",
                "country": "Francia",
            }
        ]
    }
    assert leggi_ricerca(dati).nome == "Nizza (Francia)"


def test_ricerca_di_uno_stato_citta_non_ripete_il_nome():
    dati = {
        "results": [
            {
                "id": 2,
                "name": "San Marino",
                "latitude": 43.94,
                "longitude": 12.45,
                "country_code": "SM",
                "country": "San Marino",
            }
        ]
    }
    assert leggi_ricerca(dati).nome == "San Marino"


def test_luogo_inverso_misano():
    luogo = leggi_luogo_inverso(carica("nominatim_misano.json"))
    assert luogo == Luogo("Misano Adriatico", "RN", "Emilia-Romagna", "it", "Italia")
    assert luogo.etichetta == "Misano Adriatico (RN)"


@pytest.mark.parametrize(
    ("dati", "atteso"),
    [
        ({"address": {"city": "Rimini", "ISO3166-2-lvl6": "IT-RN"}}, "Rimini (RN)"),
        ({"address": {"village": "Montefiore Conca"}}, "Montefiore Conca"),
        (
            {"address": {"city": "Nice", "country": "Francia", "country_code": "fr"}},
            "Nice (Francia)",
        ),
        ({"address": {}, "name": "Mare Adriatico"}, "Mare Adriatico"),
        ({}, None),
    ],
)
def test_luogo_inverso_casi(dati, atteso):
    luogo = leggi_luogo_inverso(dati)
    assert (luogo.etichetta if luogo else None) == atteso


def test_localita_qui():
    localita = localita_qui(44.0, 12.6, "Qui")
    assert (localita.lat, localita.mare_lat, localita.nome) == (44.0, 44.0, "Qui")


@respx.mock
def test_cerca_via_http():
    rotta = respx.get(URL_GEOCODING).mock(
        return_value=httpx.Response(200, json=carica("geocoding_misano.json"))
    )
    with httpx.Client() as http:
        localita = Luoghi(http).cerca("misano")
    assert localita.nome == "Misano Adriatico"
    parametri = rotta.calls.last.request.url.params
    assert parametri["name"] == "misano"
    assert parametri["language"] == "it"


@respx.mock
def test_cerca_con_servizio_giu():
    respx.get(URL_GEOCODING).mock(return_value=httpx.Response(500))
    with httpx.Client() as http, pytest.raises(ErroreLuoghi):
        Luoghi(http).cerca("misano")


@respx.mock
def test_luogo_di_via_http():
    rotta = respx.get(URL_NOMINATIM).mock(
        return_value=httpx.Response(200, json=carica("nominatim_misano.json"))
    )
    with httpx.Client() as http:
        assert Luoghi(http).luogo_di(43.978, 12.697).etichetta == "Misano Adriatico (RN)"
    assert rotta.calls.last.request.url.params["accept-language"] == "it"


@respx.mock
def test_luogo_di_con_nominatim_giu_non_blocca():
    respx.get(URL_NOMINATIM).mock(side_effect=httpx.ConnectError("giù"))
    with httpx.Client() as http:
        assert Luoghi(http).luogo_di(43.978, 12.697) is None
