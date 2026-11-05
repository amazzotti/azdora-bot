"""Prova le funzioni ``api/*.py`` come le esegue Vercel, dentro un vero server HTTP locale.

Le chiamate verso Telegram e Open-Meteo sono intercettate da respx; il client del test usa
``urllib`` per non essere intercettato a sua volta.
"""

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import respx

from tests.conftest import PADRONE, messaggio

API = Path(__file__).resolve().parent.parent / "api"
TELEGRAM = "https://api.telegram.org/bot123:abc"


def _carica_handler(nome: str):
    spec = importlib.util.spec_from_file_location(f"api_{nome}", API / f"{nome}.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo.handler


@pytest.fixture
def ambiente(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", str(PADRONE))
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "segreto-webhook")
    monkeypatch.setenv("CRON_SECRET", "segreto-cron")


@pytest.fixture
def servizi_finti(json_previsioni, json_mare):
    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.open-meteo.com/v1/forecast").mock(
            return_value=httpx.Response(200, json=json_previsioni)
        )
        router.get("https://marine-api.open-meteo.com/v1/marine").mock(
            return_value=httpx.Response(200, json=json_mare)
        )
        router.get(url__startswith="https://feeds.meteoalarm.org/").mock(
            return_value=httpx.Response(200, json={"warnings": []})
        )
        invii = router.post(f"{TELEGRAM}/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True, "result": {}})
        )
        yield invii


def _server(nome: str):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _carica_handler(nome))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _chiama(server, percorso, *, metodo="GET", corpo=None, intestazioni=None):
    host, porta = server.server_address
    richiesta = urllib.request.Request(
        f"http://{host}:{porta}{percorso}", data=corpo, method=metodo, headers=intestazioni or {}
    )
    try:
        with urllib.request.urlopen(richiesta, timeout=10) as risposta:
            return risposta.status, json.loads(risposta.read())
    except urllib.error.HTTPError as errore:
        return errore.code, json.loads(errore.read())


def test_webhook_su_vercel(ambiente, servizi_finti):
    server = _server("webhook")
    try:
        assert _chiama(server, "/api/webhook") == (200, {"ok": True, "servizio": "azdora-bot"})

        corpo = json.dumps(messaggio("/vento")).encode()
        stato, _ = _chiama(
            server,
            "/api/webhook",
            metodo="POST",
            corpo=corpo,
            intestazioni={
                "Content-Type": "application/json",
                "X-Telegram-Bot-Api-Secret-Token": "segreto-webhook",
            },
        )
        assert stato == 200
        inviato = json.loads(servizi_finti.calls.last.request.content)
        assert inviato["chat_id"] == PADRONE
        assert "Vento a Riccione" in inviato["text"]

        stato, _ = _chiama(server, "/api/webhook", metodo="POST", corpo=corpo)
        assert stato == 401
    finally:
        server.shutdown()


def test_cron_su_vercel(ambiente, servizi_finti):
    server = _server("cron")
    try:
        stato, corpo = _chiama(
            server, "/api/cron?job=mattino", intestazioni={"Authorization": "Bearer segreto-cron"}
        )
        assert (stato, corpo) == (200, {"ok": True, "job": "mattino"})
        assert "Ale!</b>" in json.loads(servizi_finti.calls.last.request.content)["text"]

        stato, _ = _chiama(server, "/api/cron?job=mattino", metodo="POST")
        assert stato == 401
    finally:
        server.shutdown()
