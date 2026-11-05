import pytest

from azdora.config import (
    RICCIONE,
    RIMINI,
    ConfigurazioneErrata,
    Impostazioni,
    trova_localita,
)


@pytest.mark.parametrize(
    ("testo", "attesa"),
    [
        ("", RICCIONE),
        (None, RICCIONE),
        ("  ", RICCIONE),
        ("Rimini", RIMINI),
        ("RN", RIMINI),
        (" riccione ", RICCIONE),
        ("ric", RICCIONE),
        ("bologna", None),
    ],
)
def test_trova_localita(testo, attesa):
    assert trova_localita(testo) == attesa


def test_impostazioni_da_env():
    impostazioni = Impostazioni.da_env(
        {
            "TELEGRAM_BOT_TOKEN": " 123:abc ",
            "TELEGRAM_CHAT_ID": "42",
            "TELEGRAM_CHAT_ID_COPPIA": "-100123",
            "TELEGRAM_WEBHOOK_SECRET": "w",
            "CRON_SECRET": "c",
            "TELEGRAM_BOT_USERNAME": "@azdora_bot",
            "AZDORA_NOME": "Alessandro",
        }
    )
    assert impostazioni.token == "123:abc"
    assert impostazioni.chat_personale == 42
    assert impostazioni.chat_coppia == -100123
    assert impostazioni.chat_autorizzate == {42, -100123}
    assert impostazioni.username_bot == "azdora_bot"
    assert impostazioni.nome_utente == "Alessandro"


def test_impostazioni_vuote_hanno_valori_prudenti():
    impostazioni = Impostazioni.da_env({})
    assert impostazioni.chat_autorizzate == frozenset()
    assert impostazioni.segreto_webhook == ""
    assert impostazioni.nome_utente == "Ale"
    with pytest.raises(ConfigurazioneErrata, match="TELEGRAM_BOT_TOKEN"):
        impostazioni.richiedi_token()


def test_chat_id_non_numerico():
    with pytest.raises(ConfigurazioneErrata, match="TELEGRAM_CHAT_ID"):
        Impostazioni.da_env({"TELEGRAM_CHAT_ID": "il mio"})
