import json

import httpx
import pytest
import respx

from azdora.app import crea_archivio
from azdora.archivio import ArchivioMemoria, ArchivioUpstash, ErroreArchivio
from azdora.config import Impostazioni

URL = "https://esempio.upstash.io"


class Orologio:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_memoria_leggi_scrivi_cancella():
    archivio = ArchivioMemoria()
    assert archivio.persistente is False
    assert archivio.leggi("x") is None
    archivio.scrivi("x", {"città": "Riccione", "n": [1, 2]})
    assert archivio.leggi("x") == {"città": "Riccione", "n": [1, 2]}
    archivio.cancella("x")
    archivio.cancella("x")  # cancellare due volte non è un errore
    assert archivio.leggi("x") is None


def test_memoria_scadenze():
    orologio = Orologio()
    archivio = ArchivioMemoria(orologio)
    archivio.scrivi("breve", 1, scadenza_s=10)
    archivio.scrivi("per sempre", 2)
    orologio.t += 10
    assert archivio.leggi("breve") is None
    assert archivio.leggi("per sempre") == 2
    assert archivio.chiavi() == ["per sempre"]


def test_memoria_segna_nuovi():
    orologio = Orologio()
    archivio = ArchivioMemoria(orologio)
    assert archivio.segna_nuovi(["a", "b"], 60) == [True, True]
    assert archivio.segna_nuovi(["a", "c"], 60) == [False, True]
    orologio.t += 61
    assert archivio.segna_nuovi(["a"], 60) == [True]


@pytest.fixture
def upstash():
    with httpx.Client() as http:
        yield ArchivioUpstash(http, URL + "/", "token-segreto")


def _risposta(*risultati):
    return httpx.Response(200, json=[{"result": r} for r in risultati])


@respx.mock
def test_upstash_scrivi_con_scadenza(upstash):
    rotta = respx.post(f"{URL}/pipeline").mock(return_value=_risposta("OK"))
    upstash.scrivi("posizione:1", {"lat": 44.0}, scadenza_s=60)
    richiesta = rotta.calls.last.request
    assert richiesta.headers["Authorization"] == "Bearer token-segreto"
    assert json.loads(richiesta.content) == [
        ["SET", "azdora:posizione:1", '{"lat": 44.0}', "EX", 60]
    ]
    assert upstash.persistente is True


@respx.mock
def test_upstash_leggi(upstash):
    respx.post(f"{URL}/pipeline").mock(side_effect=[_risposta('{"a": 1}'), _risposta(None)])
    assert upstash.leggi("x") == {"a": 1}
    assert upstash.leggi("y") is None


@respx.mock
def test_upstash_cancella(upstash):
    rotta = respx.post(f"{URL}/pipeline").mock(return_value=_risposta(1))
    upstash.cancella("x")
    assert json.loads(rotta.calls.last.request.content) == [["DEL", "azdora:x"]]


@respx.mock
def test_upstash_segna_nuovi_in_una_sola_richiesta(upstash):
    rotta = respx.post(f"{URL}/pipeline").mock(return_value=_risposta("OK", None))
    assert upstash.segna_nuovi(["a", "b"], 3600) == [True, False]
    assert rotta.call_count == 1
    comandi = json.loads(rotta.calls.last.request.content)
    assert comandi[0] == ["SET", "azdora:a", "1", "NX", "EX", 3600]


def test_upstash_niente_da_fare(upstash):
    assert upstash.segna_nuovi([], 60) == []  # nessuna richiesta HTTP


@respx.mock
@pytest.mark.parametrize(
    "risposta",
    [
        httpx.Response(500),
        httpx.Response(200, json={"non": "lista"}),
        httpx.Response(200, json=[{"error": "WRONGTYPE"}]),
        httpx.Response(200, json=[]),
    ],
)
def test_upstash_errori(upstash, risposta):
    respx.post(f"{URL}/pipeline").mock(return_value=risposta)
    with pytest.raises(ErroreArchivio):
        upstash.leggi("x")


@respx.mock
def test_upstash_errore_di_rete_non_espone_il_token(upstash):
    respx.post(f"{URL}/pipeline").mock(side_effect=httpx.ConnectError("token-segreto"))
    with pytest.raises(ErroreArchivio) as errore:
        upstash.leggi("x")
    assert "token-segreto" not in str(errore.value)


def test_crea_archivio_sceglie_upstash_solo_se_configurato():
    with httpx.Client() as http:
        assert isinstance(crea_archivio(Impostazioni(token="x"), http), ArchivioMemoria)
        configurato = Impostazioni(token="x", archivio_url=URL, archivio_token="t")
        assert isinstance(crea_archivio(configurato, http), ArchivioUpstash)


@pytest.mark.parametrize(
    "env",
    [
        {"UPSTASH_REDIS_REST_URL": URL, "UPSTASH_REDIS_REST_TOKEN": "t"},
        {"KV_REST_API_URL": URL, "KV_REST_API_TOKEN": "t"},
    ],
)
def test_impostazioni_archivio_con_entrambi_i_nomi(env):
    impostazioni = Impostazioni.da_env(env)
    assert (impostazioni.archivio_url, impostazioni.archivio_token) == (URL, "t")
