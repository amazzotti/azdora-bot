"""Robustezza: messaggi ripetuti da Telegram, autoriparazione, /stato, giornali in parallelo."""

import json
from dataclasses import replace
from datetime import datetime

import httpx
import respx

from azdora.bot import Azdora
from azdora.config import FUSO_ORARIO
from azdora.notizie import FONTI_RIVIERA, LettoreNotizie
from azdora.web import gestisci_webhook
from tests.conftest import PADRONE, FintoMessaggero, messaggio

ADESSO = datetime(2026, 11, 5, 21, 0, tzinfo=FUSO_ORARIO)


class TelegramFinto(FintoMessaggero):
    def __init__(self, info):
        super().__init__()
        self.info = info
        self.webhook_impostati = []

    def info_webhook(self):
        return self.info

    def imposta_webhook(self, url, segreto):
        self.webhook_impostati.append((url, segreto))


def test_update_ripetuto_saltato(impostazioni, bot, messaggero):
    corpo = json.dumps(messaggio("/id")).encode()
    intestazioni = {"X-Telegram-Bot-Api-Secret-Token": "segreto-webhook"}
    assert gestisci_webhook(intestazioni, corpo, impostazioni, lambda: bot).corpo == {"ok": True}
    seconda = gestisci_webhook(intestazioni, corpo, impostazioni, lambda: bot)
    assert seconda.corpo == {"ok": True, "gia_ricevuto": True}
    assert len(messaggero.inviati) == 1  # risposto una volta sola


def test_senza_memoria_niente_controllo_ripetuti(impostazioni, messaggero, meteo):
    bot = Azdora(impostazioni, messaggero, meteo)
    corpo = json.dumps(messaggio("/id")).encode()
    intestazioni = {"X-Telegram-Bot-Api-Secret-Token": "segreto-webhook"}
    gestisci_webhook(intestazioni, corpo, impostazioni, lambda: bot)
    gestisci_webhook(intestazioni, corpo, impostazioni, lambda: bot)
    assert len(messaggero.inviati) == 2


def _bot(impostazioni, meteo, archivio, info):
    telegram = TelegramFinto(info)
    return Azdora(
        impostazioni, telegram, meteo, archivio=archivio, orologio=lambda: ADESSO
    ), telegram


def test_si_ripara_da_solo(impostazioni, meteo, archivio):
    info = {
        "url": "https://azdora-bot.vercel.app/api/webhook",
        "pending_update_count": 4,
        "last_error_date": ADESSO.timestamp() - 600,
        "last_error_message": "Read timeout expired",
    }
    bot, telegram = _bot(impostazioni, meteo, archivio, info)
    assert bot.controlla_salute() is False  # al primo giro aspetta: magari si smaltisce
    assert telegram.webhook_impostati == []
    assert bot.controlla_salute() is True  # ancora bloccata 15 minuti dopo: si ripara
    assert telegram.webhook_impostati == [(info["url"], "segreto-webhook")]
    assert "Mi ero inceppata" in telegram.ultimo and "4 messaggi" in telegram.ultimo


def test_niente_da_riparare(impostazioni, meteo, archivio):
    sano = {"url": "https://x/api/webhook", "pending_update_count": 0}
    vecchio = {
        "url": "https://x",
        "pending_update_count": 2,
        "last_error_date": ADESSO.timestamp() - 7200,
    }
    for info in (sano, vecchio, {}):
        bot, telegram = _bot(impostazioni, meteo, archivio, info)
        assert bot.controlla_salute() is False
        assert telegram.webhook_impostati == []
    senza_segreto = replace(impostazioni, segreto_webhook="")
    bot = Azdora(
        senza_segreto,
        TelegramFinto(
            {"url": "x", "pending_update_count": 5, "last_error_date": ADESSO.timestamp()}
        ),
        meteo,
    )
    assert bot.controlla_salute() is False


def test_salute_con_messaggero_semplice(bot):
    assert bot.controlla_salute() is False  # il finto dei test non sa nulla del webhook


def test_comando_stato(impostazioni, meteo, archivio, monkeypatch):
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "abcdef1234")
    info = {
        "url": "x",
        "pending_update_count": 1,
        "last_error_date": ADESSO.timestamp(),
        "last_error_message": "Read timeout expired",
    }
    bot, telegram = _bot(impostazioni, meteo, archivio, info)
    bot.gestisci_update(messaggio("/stato"))
    testo = telegram.ultimo
    assert "abcdef1" in testo and "✅ collegata" in testo
    assert "Messaggi in coda su Telegram: 1" in testo and "Read timeout expired" in testo
    telegram.info = {"url": "x", "pending_update_count": 0}
    bot.gestisci_update(messaggio("/stato"))
    assert "Nessun errore recente" in telegram.ultimo
    assert telegram.inviati[0][0] == PADRONE


@respx.mock
def test_giornali_scaricati_in_parallelo_una_volta():
    rotte = [
        respx.get(f.url).mock(return_value=httpx.Response(200, content=b"<rss><channel/></rss>"))
        for f in FONTI_RIVIERA
    ]
    with httpx.Client() as http:
        lettore = LettoreNotizie(http)
        lettore.tutte_di_oggi(ADESSO)
        lettore.tutte_di_oggi(ADESSO)
    assert all(r.call_count == 1 for r in rotte)
