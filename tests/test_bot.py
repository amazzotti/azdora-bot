import pytest

from azdora.bot import COMANDI, Azdora, leggi_comando
from azdora.config import Impostazioni
from tests.conftest import ADESSO, COPPIA, PADRONE, SCONOSCIUTO, messaggio


@pytest.mark.parametrize(
    ("testo", "nome", "argomenti", "destinatario"),
    [
        ("/meteo", "meteo", (), None),
        ("/Vento  Rimini", "vento", ("Rimini",), None),
        ("/vento@azdora_bot rn", "vento", ("rn",), "azdora_bot"),
    ],
)
def test_leggi_comando(testo, nome, argomenti, destinatario):
    comando = leggi_comando(testo)
    assert comando is not None
    assert (comando.nome, comando.argomenti, comando.destinatario) == (
        nome,
        argomenti,
        destinatario,
    )


@pytest.mark.parametrize("testo", [None, "", "ciao", "/", "/@bot"])
def test_testi_che_non_sono_comandi(testo):
    assert leggi_comando(testo) is None


def test_ogni_comando_del_menu_ha_un_azione(bot):
    for nome, _ in COMANDI:
        assert nome in bot._azioni


@pytest.mark.parametrize(
    ("testo", "frammento"),
    [
        ("/start", "Ciao, sono l'Azdora"),
        ("/aiuto", "/vento"),
        ("/meteo", "Meteo a Riccione"),
        ("/meteo rimini", "Meteo a Rimini"),
        ("/vento rn", "Vento a Rimini"),
        ("/mare", "Mare a Riccione"),
        ("/oggi", "Oggi, giovedì 5 novembre"),
        ("/domani", "Domani, venerdì 6 novembre"),
        ("/id", f"<code>{PADRONE}</code>"),
    ],
)
def test_comandi_nella_chat_personale(bot, messaggero, testo, frammento):
    bot.gestisci_update(messaggio(testo))
    assert len(messaggero.inviati) == 1
    chat_id, testo_inviato = messaggero.inviati[0]
    assert chat_id == PADRONE
    assert frammento in testo_inviato


def test_il_gruppo_di_coppia_puo_usare_i_comandi(bot, messaggero):
    bot.gestisci_update(messaggio("/vento", chat_id=COPPIA, tipo="supergroup", mittente=7))
    assert messaggero.inviati[0][0] == COPPIA
    assert "Vento a Riccione" in messaggero.ultimo


def test_localita_sconosciuta_con_html_neutralizzato(bot, messaggero, meteo):
    bot.gestisci_update(messaggio("/meteo <b>Bologna</b>"))
    assert "&lt;b&gt;Bologna&lt;/b&gt;" in messaggero.ultimo
    assert "Riccione, Rimini" in messaggero.ultimo
    assert meteo.richieste == []


def test_comando_sconosciuto(bot, messaggero):
    bot.gestisci_update(messaggio("/pizza"))
    assert "Non conosco questo comando" in messaggero.ultimo


def test_testo_normale_ignorato(bot, messaggero):
    bot.gestisci_update(messaggio("ciao azdora"))
    assert messaggero.inviati == []


def test_comando_per_un_altro_bot_ignorato(bot, messaggero):
    bot.gestisci_update(messaggio("/meteo@altro_bot", chat_id=COPPIA, tipo="group"))
    assert messaggero.inviati == []


def test_comando_con_il_mio_username(bot, messaggero):
    bot.gestisci_update(messaggio("/meteo@Azdora_Test_Bot", chat_id=COPPIA, tipo="group"))
    assert "Meteo a Riccione" in messaggero.ultimo


def test_open_meteo_giu(bot, messaggero, meteo):
    meteo.guasto = True
    bot.gestisci_update(messaggio("/vento"))
    assert "Open-Meteo non risponde" in messaggero.ultimo


def test_riepilogo_senza_mare_funziona_lo_stesso(bot, messaggero, meteo):
    meteo.mare_guasto = True
    bot.gestisci_update(messaggio("/oggi"))
    assert "Riccione" in messaggero.ultimo
    assert "Mare:" not in messaggero.ultimo


# --- Sicurezza: il bot risponde solo alle chat autorizzate --------------------------------


def test_sconosciuto_in_privato_non_riceve_il_meteo(bot, messaggero, meteo):
    bot.gestisci_update(messaggio("/meteo", chat_id=SCONOSCIUTO))
    assert messaggero.inviati == []
    assert meteo.richieste == []


@pytest.mark.parametrize("testo", ["/start", "/id"])
def test_sconosciuto_puo_solo_sapere_il_suo_id(bot, messaggero, testo):
    bot.gestisci_update(messaggio(testo, chat_id=SCONOSCIUTO))
    chat_id, testo_inviato = messaggero.inviati[0]
    assert chat_id == SCONOSCIUTO
    assert f"<code>{SCONOSCIUTO}</code>" in testo_inviato
    assert "non è ancora autorizzata" in testo_inviato


def test_gruppo_sconosciuto_di_estranei_il_bot_esce(bot, messaggero):
    bot.gestisci_update(messaggio("/meteo", chat_id=-555, tipo="group", mittente=SCONOSCIUTO))
    assert messaggero.uscite == [-555]
    assert messaggero.inviati == []


def test_gruppo_nuovo_del_padrone_mostra_l_id(bot, messaggero):
    bot.gestisci_update(messaggio("/id", chat_id=-555, tipo="group", mittente=PADRONE))
    assert messaggero.uscite == []
    assert "<code>-555</code>" in messaggero.ultimo


def _aggiunto(chat_id, da, stato="member", tipo="group"):
    return {
        "update_id": 2,
        "my_chat_member": {
            "chat": {"id": chat_id, "type": tipo},
            "from": {"id": da},
            "new_chat_member": {"status": stato, "user": {"id": 1, "is_bot": True}},
        },
    }


def test_aggiunto_a_un_gruppo_da_un_estraneo_esce(bot, messaggero):
    bot.gestisci_update(_aggiunto(-777, da=SCONOSCIUTO))
    assert messaggero.uscite == [-777]


def test_aggiunto_a_un_gruppo_dal_padrone_resta_e_dice_l_id(bot, messaggero):
    bot.gestisci_update(_aggiunto(-777, da=PADRONE))
    assert messaggero.uscite == []
    assert "<code>-777</code>" in messaggero.ultimo


def test_aggiunto_al_gruppo_gia_autorizzato_non_fa_nulla(bot, messaggero):
    bot.gestisci_update(_aggiunto(COPPIA, da=PADRONE, tipo="supergroup"))
    assert messaggero.inviati == []
    assert messaggero.uscite == []


def test_rimosso_da_un_gruppo_non_fa_nulla(bot, messaggero):
    bot.gestisci_update(_aggiunto(-777, da=SCONOSCIUTO, stato="left"))
    assert messaggero.uscite == []


# --- Riepilogo mattutino -----------------------------------------------------------------


def test_riepilogo_mattutino(bot, messaggero):
    bot.invia_riepilogo_mattutino()
    chat_id, testo = messaggero.inviati[0]
    assert chat_id == PADRONE
    assert testo.startswith("☀️ <b>Buongiorno Ale!</b>")


def test_riepilogo_mattutino_con_meteo_giu_avvisa_comunque(bot, messaggero, meteo):
    meteo.guasto = True
    bot.invia_riepilogo_mattutino()
    assert "Open-Meteo non risponde" in messaggero.ultimo


def test_riepilogo_mattutino_senza_chat_configurata(messaggero, meteo):
    bot = Azdora(Impostazioni(token="x"), messaggero, meteo, orologio=lambda: ADESSO)
    with pytest.raises(RuntimeError, match="TELEGRAM_CHAT_ID"):
        bot.invia_riepilogo_mattutino()
