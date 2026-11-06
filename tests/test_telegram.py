import json

import httpx
import pytest
import respx

from azdora.telegram import LUNGHEZZA_MASSIMA, ErroreTelegram, Telegram
from azdora.telegram import testo_visibile as visibile

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
def test_messaggi_troppo_lunghi_divisi_a_fine_riga(telegram):
    rotta = respx.post(f"{BASE}/sendMessage").mock(return_value=_ok())
    link = "https://news.google.com/" + "x" * 300
    righe = [f'<b>{i}</b> <a href="{link}">titolo {i}</a>' for i in range(400)]
    telegram.invia_messaggio(42, "\n".join(righe), {"inline_keyboard": []})
    inviati = [json.loads(c.request.content) for c in rotta.calls]
    assert len(inviati) > 1
    for parte in inviati:
        assert len(visibile(parte["text"])) <= LUNGHEZZA_MASSIMA
        assert parte["text"].count("<a ") == parte["text"].count("</a>")  # nessun link spezzato
    assert "reply_markup" in inviati[-1] and "reply_markup" not in inviati[0]


@respx.mock
def test_messaggi_corti_con_link_lunghi_non_vengono_toccati(telegram):
    rotta = respx.post(f"{BASE}/sendMessage").mock(return_value=_ok())
    link = "https://news.google.com/" + "y" * 500
    testo = "\n".join(f'<a href="{link}">notizia {i}</a>' for i in range(12))
    telegram.invia_messaggio(42, testo)  # 6000 caratteri di HTML, ma pochi visibili
    assert rotta.call_count == 1
    assert json.loads(rotta.calls.last.request.content)["text"] == testo


@respx.mock
def test_html_rifiutato_si_rimanda_come_testo(telegram):
    rotta = respx.post(f"{BASE}/sendMessage").mock(
        side_effect=[
            httpx.Response(400, json={"ok": False, "description": "can't parse entities"}),
            httpx.Response(200, json={"ok": True, "result": {}}),
        ]
    )
    telegram.invia_messaggio(42, "<b>ciao &amp; buongiorno")
    seconda = json.loads(rotta.calls.last.request.content)
    assert seconda["text"] == "ciao & buongiorno" and "parse_mode" not in seconda


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
    assert inviato["allowed_updates"] == [
        "message",
        "edited_message",
        "callback_query",
        "my_chat_member",
    ]


@respx.mock
def test_imposta_comandi(telegram):
    rotta = respx.post(f"{BASE}/setMyCommands").mock(return_value=_ok())
    telegram.imposta_comandi([("meteo", "Il meteo")])
    inviati = [json.loads(c.request.content) for c in rotta.calls]
    assert all(i["commands"] == [{"command": "meteo", "description": "Il meteo"}] for i in inviati)
    ambiti = {(i["scope"]["type"], i.get("language_code")) for i in inviati}
    assert ambiti == {
        (tipo, lingua)
        for tipo in ("default", "all_private_chats", "all_group_chats")
        for lingua in (None, "it")
    }


@respx.mock
def test_leggi_comandi(telegram):
    rotta = respx.post(f"{BASE}/getMyCommands").mock(
        return_value=_ok([{"command": "oggi", "description": "x"}])
    )
    assert telegram.leggi_comandi() == ["oggi"]
    assert json.loads(rotta.calls.last.request.content)["language_code"] == "it"


@respx.mock
def test_info_e_chi_sono(telegram):
    respx.post(f"{BASE}/getWebhookInfo").mock(return_value=_ok({"url": "https://x"}))
    respx.post(f"{BASE}/getMe").mock(return_value=_ok({"username": "azdora_bot"}))
    assert telegram.info_webhook() == {"url": "https://x"}
    assert telegram.chi_sono()["username"] == "azdora_bot"
