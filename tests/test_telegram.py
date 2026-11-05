import json

import httpx
import pytest
import respx

from azdora.telegram import LUNGHEZZA_MASSIMA, ErroreTelegram, Telegram

TOKEN = "123:segretissimo"
BASE = f"https://api.telegram.org/bot{TOKEN}"


@pytest.fixture
def telegram():
    with httpx.Client() as http:
        yield Telegram(TOKEN, http)


def _ok(result=True):
    return httpx.Response(200, json={"ok": True, "result": result})


def test_serve_un_token():
    with httpx.Client() as http, pytest.raises(ValueError):
        Telegram("", http)


@respx.mock
def test_invia_messaggio_in_html_senza_anteprime(telegram):
    rotta = respx.post(f"{BASE}/sendMessage").mock(return_value=_ok({"message_id": 1}))
    telegram.invia_messaggio(42, "<b>ciao</b>")
    inviato = json.loads(rotta.calls.last.request.content)
    assert inviato == {
        "chat_id": 42,
        "text": "<b>ciao</b>",
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }


@respx.mock
def test_invia_messaggio_con_tastiera(telegram):
    rotta = respx.post(f"{BASE}/sendMessage").mock(return_value=_ok())
    tastiera = {"remove_keyboard": True}
    telegram.invia_messaggio(42, "ok", tastiera)
    assert json.loads(rotta.calls.last.request.content)["reply_markup"] == tastiera


@respx.mock
def test_messaggi_troppo_lunghi_vengono_tagliati(telegram):
    rotta = respx.post(f"{BASE}/sendMessage").mock(return_value=_ok())
    telegram.invia_messaggio(42, "x" * 5000)
    assert len(json.loads(rotta.calls.last.request.content)["text"]) == LUNGHEZZA_MASSIMA


@respx.mock
def test_errore_dichiarato_da_telegram(telegram):
    respx.post(f"{BASE}/sendMessage").mock(
        return_value=httpx.Response(
            400, json={"ok": False, "description": "Bad Request: chat not found"}
        )
    )
    with pytest.raises(ErroreTelegram, match="chat not found"):
        telegram.invia_messaggio(42, "ciao")


@respx.mock
def test_errore_di_rete_non_espone_il_token(telegram):
    respx.post(f"{BASE}/leaveChat").mock(side_effect=httpx.ConnectError(f"errore su {BASE}"))
    with pytest.raises(ErroreTelegram) as errore:
        telegram.esci_da_chat(-1)
    assert TOKEN not in str(errore.value)
    assert errore.value.__cause__ is None


@respx.mock
def test_imposta_webhook_con_segreto(telegram):
    rotta = respx.post(f"{BASE}/setWebhook").mock(return_value=_ok())
    telegram.imposta_webhook("https://esempio.vercel.app/api/webhook", "abc")
    inviato = json.loads(rotta.calls.last.request.content)
    assert inviato["secret_token"] == "abc"
    assert inviato["allowed_updates"] == ["message", "my_chat_member"]


@respx.mock
def test_imposta_comandi(telegram):
    rotta = respx.post(f"{BASE}/setMyCommands").mock(return_value=_ok())
    telegram.imposta_comandi([("meteo", "Il meteo")])
    inviato = json.loads(rotta.calls.last.request.content)
    assert inviato == {"commands": [{"command": "meteo", "description": "Il meteo"}]}


@respx.mock
def test_info_e_chi_sono(telegram):
    respx.post(f"{BASE}/getWebhookInfo").mock(return_value=_ok({"url": "https://x"}))
    respx.post(f"{BASE}/getMe").mock(return_value=_ok({"username": "azdora_bot"}))
    assert telegram.info_webhook() == {"url": "https://x"}
    assert telegram.chi_sono()["username"] == "azdora_bot"
