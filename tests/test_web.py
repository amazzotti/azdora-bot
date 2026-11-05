import json

import pytest

from azdora.web import gestisci_cron, gestisci_webhook
from tests.conftest import PADRONE, messaggio


@pytest.fixture
def crea_bot(bot):
    return lambda: bot


def _corpo(update):
    return json.dumps(update).encode()


def test_webhook_valido(impostazioni, crea_bot, messaggero):
    risposta = gestisci_webhook(
        {"X-Telegram-Bot-Api-Secret-Token": "segreto-webhook"},
        _corpo(messaggio("/mare")),
        impostazioni,
        crea_bot,
    )
    assert risposta.stato == 200
    assert messaggero.inviati[0][0] == PADRONE


@pytest.mark.parametrize("intestazioni", [{}, {"X-Telegram-Bot-Api-Secret-Token": "sbagliato"}])
def test_webhook_rifiuta_segreto_sbagliato(impostazioni, crea_bot, messaggero, intestazioni):
    risposta = gestisci_webhook(intestazioni, _corpo(messaggio("/mare")), impostazioni, crea_bot)
    assert risposta.stato == 401
    assert messaggero.inviati == []


def test_webhook_senza_segreto_configurato_rifiuta_tutto(impostazioni, crea_bot):
    from dataclasses import replace

    senza = replace(impostazioni, segreto_webhook="")
    risposta = gestisci_webhook({"X-Telegram-Bot-Api-Secret-Token": ""}, b"{}", senza, crea_bot)
    assert risposta.stato == 401


@pytest.mark.parametrize("corpo", [b"non json", b"[1, 2]"])
def test_webhook_json_non_valido(impostazioni, crea_bot, corpo):
    intestazioni = {"x-telegram-bot-api-secret-token": "segreto-webhook"}
    assert gestisci_webhook(intestazioni, corpo, impostazioni, crea_bot).stato == 400


def test_webhook_risponde_200_anche_se_il_bot_va_in_errore(impostazioni):
    def bot_rotto():
        raise RuntimeError("boom")

    risposta = gestisci_webhook(
        {"X-Telegram-Bot-Api-Secret-Token": "segreto-webhook"},
        _corpo(messaggio("/oggi")),
        impostazioni,
        bot_rotto,
    )
    assert risposta.stato == 200


def test_cron_mattino(impostazioni, crea_bot, messaggero):
    risposta = gestisci_cron(
        {"Authorization": "Bearer segreto-cron"}, {"job": "mattino"}, impostazioni, crea_bot
    )
    assert risposta.stato == 200
    assert risposta.corpo == {"ok": True, "job": "mattino"}
    assert "Buongiorno Ale" in messaggero.ultimo


@pytest.mark.parametrize(
    "intestazioni",
    [{}, {"Authorization": "segreto-cron"}, {"Authorization": "Bearer altro"}],
)
def test_cron_non_autorizzato(impostazioni, crea_bot, messaggero, intestazioni):
    risposta = gestisci_cron(intestazioni, {"job": "mattino"}, impostazioni, crea_bot)
    assert risposta.stato == 401
    assert messaggero.inviati == []


def test_cron_lavoro_sconosciuto(impostazioni, crea_bot):
    risposta = gestisci_cron(
        {"authorization": "Bearer segreto-cron"}, {"job": "pulizie"}, impostazioni, crea_bot
    )
    assert risposta.stato == 404


def test_cron_errore_nel_lavoro(impostazioni):
    def bot_rotto():
        raise RuntimeError("boom")

    risposta = gestisci_cron(
        {"Authorization": "Bearer segreto-cron"}, {"job": "mattino"}, impostazioni, bot_rotto
    )
    assert risposta.stato == 500


def test_risposta_json_con_accenti():
    from azdora.web import Risposta

    assert json.loads(Risposta(200, {"testo": "città"}).json()) == {"testo": "città"}
