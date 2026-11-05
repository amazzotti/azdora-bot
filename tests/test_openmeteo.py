from datetime import date, datetime

import httpx
import pytest
import respx

from azdora.config import FUSO_ORARIO, RICCIONE, RIMINI
from azdora.meteo.openmeteo import (
    URL_MARE,
    URL_PREVISIONI,
    ErroreMeteo,
    OpenMeteo,
    leggi_mare,
    leggi_previsioni,
)


def test_lettura_previsioni_reali(previsioni):
    a = previsioni.attuale
    assert a.ora == datetime(2026, 11, 5, 9, 15, tzinfo=FUSO_ORARIO)
    assert a.temperatura == pytest.approx(11.2)
    assert a.codice == 3
    assert a.direzione_vento == 298

    assert len(previsioni.giorni) == 3
    oggi = previsioni.giorni[0]
    assert oggi.data == date(2026, 11, 5)
    assert oggi.alba == datetime(2026, 11, 5, 6, 50, tzinfo=FUSO_ORARIO)
    assert oggi.t_min is not None and oggi.t_max is not None
    assert oggi.t_min < oggi.t_max
    assert len(previsioni.ore) == 72


def test_prossime_ore_partono_dopo_l_ora_attuale(previsioni):
    ore = previsioni.prossime_ore(quante=12, passo=3)
    assert [o.ora.hour for o in ore] == [12, 15, 18, 21]


def test_lettura_mare_reale(mare):
    assert mare.attuale.altezza_onde == pytest.approx(0.38)
    assert mare.attuale.temperatura_acqua == pytest.approx(16.9)
    assert [g.data.day for g in mare.giorni] == [5, 6, 7]


def test_valori_mancanti_diventano_none(json_previsioni):
    json_previsioni["current"]["wind_gusts_10m"] = None
    del json_previsioni["daily"]["uv_index_max"]
    previsioni = leggi_previsioni(json_previsioni, RICCIONE)
    assert previsioni.attuale.raffiche is None
    assert all(g.uv_max is None for g in previsioni.giorni)


@pytest.mark.parametrize("sezione", ["current", "hourly", "daily"])
def test_sezione_mancante(json_previsioni, sezione):
    del json_previsioni[sezione]
    with pytest.raises(ErroreMeteo, match=sezione):
        leggi_previsioni(json_previsioni, RICCIONE)


def test_mare_senza_ora(json_mare):
    json_mare["current"]["time"] = None
    with pytest.raises(ErroreMeteo):
        leggi_mare(json_mare, RICCIONE)


@respx.mock
def test_chiamata_previsioni_con_i_parametri_giusti(json_previsioni):
    rotta = respx.get(URL_PREVISIONI).mock(return_value=httpx.Response(200, json=json_previsioni))
    with httpx.Client() as http:
        risultato = OpenMeteo(http).previsioni(RIMINI)

    assert risultato.localita == RIMINI
    parametri = rotta.calls.last.request.url.params
    assert parametri["latitude"] == str(RIMINI.lat)
    assert parametri["timezone"] == "Europe/Rome"
    assert "wind_gusts_10m" in parametri["current"]
    assert "sunrise" in parametri["daily"]


@respx.mock
def test_il_mare_usa_il_punto_in_acqua(json_mare):
    rotta = respx.get(URL_MARE).mock(return_value=httpx.Response(200, json=json_mare))
    with httpx.Client() as http:
        OpenMeteo(http).mare(RICCIONE)
    parametri = rotta.calls.last.request.url.params
    assert parametri["latitude"] == str(RICCIONE.mare_lat)
    assert parametri["longitude"] == str(RICCIONE.mare_lon)


@respx.mock
def test_errore_http():
    respx.get(URL_PREVISIONI).mock(return_value=httpx.Response(503))
    with httpx.Client() as http, pytest.raises(ErroreMeteo, match="non disponibile"):
        OpenMeteo(http).previsioni(RICCIONE)


@respx.mock
def test_errore_dichiarato_da_open_meteo():
    respx.get(URL_PREVISIONI).mock(
        return_value=httpx.Response(200, json={"error": True, "reason": "Parametro sbagliato"})
    )
    with httpx.Client() as http, pytest.raises(ErroreMeteo, match="Parametro sbagliato"):
        OpenMeteo(http).previsioni(RICCIONE)


@respx.mock
def test_rete_assente():
    respx.get(URL_MARE).mock(side_effect=httpx.ConnectError("niente rete"))
    with httpx.Client() as http, pytest.raises(ErroreMeteo):
        OpenMeteo(http).mare(RICCIONE)
