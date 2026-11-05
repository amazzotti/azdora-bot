"""Test dei programmi da terminale: simulatore, configurazione e lavori programmati."""

import httpx
import pytest
import respx

from azdora import configura, lavori, simula
from azdora.config import ConfigurazioneErrata

BASE = "https://api.telegram.org/bot123:abc"


def test_simulatore_offline(capsys):
    assert simula.main(["--offline", "/vento rimini", "mattino"]) == 0
    uscita = capsys.readouterr().out
    assert "👤 Tu: /vento rimini" in uscita
    assert "Vento a Rimini" in uscita
    assert "Buongiorno Ale!" in uscita
    assert "<b>" not in uscita  # i tag HTML vengono tolti


def test_simulatore_html_grezzo(capsys):
    simula.main(["--offline", "--html", "/mare"])
    assert "<b>Mare a Riccione</b>" in capsys.readouterr().out


def test_html_in_testo():
    assert simula.html_in_testo("<b>Ciao</b> &amp; <i>buongiorno</i>") == "Ciao & buongiorno"


@pytest.mark.parametrize(
    ("url", "atteso"),
    [
        ("https://azdora-bot.vercel.app", "https://azdora-bot.vercel.app/api/webhook"),
        ("https://azdora-bot.vercel.app/", "https://azdora-bot.vercel.app/api/webhook"),
        ("https://x.vercel.app/api/webhook", "https://x.vercel.app/api/webhook"),
    ],
)
def test_indirizzo_webhook(url, atteso):
    assert configura.indirizzo_webhook(url) == atteso


def test_indirizzo_webhook_solo_https():
    with pytest.raises(ConfigurazioneErrata):
        configura.indirizzo_webhook("http://azdora-bot.vercel.app")


@pytest.fixture
def env_telegram(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "segreto")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")


@respx.mock
def test_configura_imposta_webhook_e_comandi(env_telegram, capsys):
    respx.post(f"{BASE}/getMe").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"username": "azdora_bot"}})
    )
    webhook = respx.post(f"{BASE}/setWebhook").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True})
    )
    comandi = respx.post(f"{BASE}/setMyCommands").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True})
    )
    respx.post(f"{BASE}/getWebhookInfo").mock(
        return_value=httpx.Response(
            200,
            json={"ok": True, "result": {"url": "https://a.vercel.app/api/webhook"}},
        )
    )
    assert configura.main(["--url", "https://a.vercel.app"]) == 0
    assert webhook.called and comandi.called
    uscita = capsys.readouterr().out
    assert "@azdora_bot" in uscita
    assert "123:abc" not in uscita


@respx.mock
def test_lavoro_mattino_da_riga_di_comando(env_telegram, json_previsioni, json_mare, capsys):
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json=json_previsioni)
    )
    respx.get("https://marine-api.open-meteo.com/v1/marine").mock(
        return_value=httpx.Response(200, json=json_mare)
    )
    invio = respx.post(f"{BASE}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    assert lavori.main(["mattino"]) == 0
    assert invio.call_count == 1
    assert "completato" in capsys.readouterr().out
