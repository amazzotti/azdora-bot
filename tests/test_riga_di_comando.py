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


def test_simulatore_posizione_e_localita_qualsiasi(capsys):
    simula.main(["--offline", "/qui", "posizione:43.978,12.697", "/meteo misano"])
    uscita = capsys.readouterr().out
    assert "[ 📍 Manda la mia posizione ]" in uscita
    assert "Misano Adriatico (RN)" in uscita
    assert "Meteo a Misano Adriatico" in uscita


def test_update_simulato_con_posizione():
    update = simula.update_simulato("posizione:44.06,12.57")
    assert update["message"]["location"] == {"latitude": 44.06, "longitude": 12.57}
    assert "text" not in update["message"]


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
    respx.get(url__startswith="https://air-quality-api.open-meteo.com/").mock(
        return_value=httpx.Response(200, json={"hourly": {}})
    )
    respx.get(url__startswith="https://feeds.meteoalarm.org/").mock(
        return_value=httpx.Response(200, json={"warnings": []})
    )
    invio = respx.post(f"{BASE}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    assert lavori.main(["mattino"]) == 0
    assert invio.call_count == 1
    assert "completato" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("env", "frammento"),
    [
        ({}, "TELEGRAM_BOT_TOKEN"),
        ({"TELEGRAM_BOT_TOKEN": "123:abc"}, "TELEGRAM_WEBHOOK_SECRET"),
    ],
)
def test_configura_senza_secret_spiega_cosa_fare(monkeypatch, capsys, env, frammento):
    for nome in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET"):
        monkeypatch.delenv(nome, raising=False)
    for nome, valore in env.items():
        monkeypatch.setenv(nome, valore)
    assert configura.main(["--url", "https://a.vercel.app"]) == 1
    uscita = capsys.readouterr().out
    assert f"❌ Manca {frammento}" in uscita
    assert "👉" in uscita


@respx.mock
def test_configura_token_sbagliato(env_telegram, capsys):
    respx.post(f"{BASE}/getMe").mock(
        return_value=httpx.Response(401, json={"ok": False, "description": "Unauthorized"})
    )
    assert configura.main(["--url", "https://a.vercel.app"]) == 1
    uscita = capsys.readouterr().out
    assert "Telegram non riconosce il token" in uscita
    assert "123:abc" not in uscita


def test_configura_indirizzo_sbagliato(env_telegram, capsys):
    assert configura.main(["--url", "azdora-bot.vercel.app"]) == 1
    assert "https://azdora-bot.vercel.app" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("errore", "frammento"),
    [
        ("setWebhook: Bad Request: secret token contains unallowed characters", "lettere, numeri"),
        ("qualcos'altro", "Controlla i secret"),
    ],
)
def test_consigli(errore, frammento):
    assert frammento in configura.consiglio(errore)


@respx.mock
def test_configura_solo_info(env_telegram, capsys):
    respx.post(f"{BASE}/getMe").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"username": "azdora_bot"}})
    )
    respx.post(f"{BASE}/getWebhookInfo").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "result": {"url": "", "last_error_message": "timeout"}}
        )
    )
    assert configura.main(["--solo-info"]) == 0
    uscita = capsys.readouterr().out
    assert "(nessuno)" in uscita and "Ultimo errore: timeout" in uscita
