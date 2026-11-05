"""Modalità famiglia: più chat private, ognuna con i suoi dati, la sua zona e il suo saluto."""

from dataclasses import dataclass, field, replace

import pytest

from azdora.allerte import Allerta
from azdora.bot import Azdora
from azdora.config import ConfigurazioneErrata, Impostazioni
from azdora.spese import RegistroSpese
from azdora.telegram import ErroreTelegram
from tests.conftest import ADESSO, BOLOGNA, COPPIA, PADRONE, SCONOSCIUTO, messaggio, posizione

BABBO = 333
FIDANZATA = 444
BOLOGNA_XY = (44.494, 11.343)


@pytest.fixture
def impostazioni(impostazioni):
    return replace(impostazioni, chat_famiglia=(BABBO, FIDANZATA))


@dataclass
class NotizieFinte:
    richieste: list[str] = field(default_factory=list)

    def notizie_per(self, comune, lat, lon, adesso):
        from azdora.notizie import Notizia

        self.richieste.append(comune)
        return [Notizia(f"Novità da {comune}", f"https://esempio.it/{comune}", "x", adesso)]

    def del_giorno_in_riviera(self, comune, adesso):
        return []

    def eventi(self, adesso):
        return []


@dataclass
class AllerteFinte:
    def allerte(self, paese):
        from datetime import timedelta

        return [Allerta("a1", "Emilia e Romagna", 2, "Vento", ADESSO, ADESSO + timedelta(hours=5))]


@pytest.fixture
def bot(impostazioni, messaggero, meteo, luoghi, archivio):
    return Azdora(
        impostazioni,
        messaggero,
        meteo,
        luoghi,
        archivio=archivio,
        notizie=NotizieFinte(),
        allerte=AllerteFinte(),
        orologio=lambda: ADESSO,
    )


# --- Impostazioni --------------------------------------------------------------------------


@pytest.mark.parametrize("valore", ["333,444", "333, 444", "333;444", " 333 444 ", "333,444,333"])
def test_lista_famiglia_dall_ambiente(valore):
    impostazioni = Impostazioni.da_env(
        {"TELEGRAM_CHAT_ID": str(PADRONE), "TELEGRAM_CHAT_ID_FAMIGLIA": valore}
    )
    assert impostazioni.chat_famiglia == (BABBO, FIDANZATA)
    assert impostazioni.persone == (PADRONE, BABBO, FIDANZATA)


def test_lista_famiglia_sbagliata():
    with pytest.raises(ConfigurazioneErrata, match="TELEGRAM_CHAT_ID_FAMIGLIA"):
        Impostazioni.da_env({"TELEGRAM_CHAT_ID_FAMIGLIA": "babbo"})


def test_chi_e_autorizzato(impostazioni):
    assert impostazioni.chat_autorizzate == {PADRONE, BABBO, FIDANZATA, COPPIA}
    solo_famiglia = Impostazioni(token="x", chat_famiglia=(BABBO,))
    assert solo_famiglia.persone == (BABBO,)
    assert solo_famiglia.chat_autorizzate == {BABBO}


# --- Comandi -------------------------------------------------------------------------------


def test_il_babbo_usa_il_bot_in_privato(bot, messaggero):
    bot.gestisci_update(messaggio("/meteo", chat_id=BABBO))
    assert messaggero.inviati[-1][0] == BABBO
    assert "Meteo a Riccione" in messaggero.ultimo


def test_ognuno_ha_le_sue_spese(bot, archivio):
    bot.gestisci_update(messaggio("/spesa 30 benzina", chat_id=BABBO))
    bot.gestisci_update(messaggio("/spesa 12 pizza", chat_id=PADRONE))
    babbo = RegistroSpese(archivio, BABBO).del_mese(ADESSO.date())
    assert [s.descrizione for s in babbo] == ["benzina"]


def test_la_posizione_del_babbo_non_sposta_la_mia_zona(bot, luoghi):
    luoghi.luogo = BOLOGNA
    update = posizione(*BOLOGNA_XY, chat_id=BABBO)
    bot.gestisci_update(update)
    assert bot.zona(BABBO).comune == "Bologna"
    assert bot.zona().comune == "Riccione"


def test_il_babbo_senza_posizione_usa_casa_non_la_mia(bot, messaggero, luoghi):
    luoghi.luogo = BOLOGNA
    bot.gestisci_update(posizione(*BOLOGNA_XY))  # io sono a Bologna
    bot.gestisci_update(messaggio("/meteo", chat_id=BABBO))
    assert "Meteo a Riccione" in messaggero.ultimo


def test_solo_io_posso_aggiungere_il_bot_ai_gruppi(bot, messaggero):
    evento = {
        "my_chat_member": {
            "chat": {"id": -9, "type": "group"},
            "from": {"id": BABBO},
            "new_chat_member": {"status": "member"},
        }
    }
    bot.gestisci_update(evento)
    assert messaggero.uscite == [-9]


def test_il_nome_dei_familiari_viene_ricordato(bot, archivio):
    update = messaggio("/oggi", chat_id=BABBO)
    update["message"]["from"]["first_name"] = "Giorgio"
    bot.gestisci_update(update)
    assert bot.nome_di(BABBO) == "Giorgio"
    assert bot.nome_di(PADRONE) == "Ale"
    assert bot.nome_di(FIDANZATA) == ""
    assert archivio.leggi(f"nome:{PADRONE}") is None


def test_sconosciuti_restano_fuori(bot, messaggero):
    bot.gestisci_update(messaggio("/meteo", chat_id=SCONOSCIUTO))
    assert messaggero.inviati == []
    bot.gestisci_update(messaggio("/id", chat_id=SCONOSCIUTO))
    assert "TELEGRAM_CHAT_ID_FAMIGLIA" in messaggero.ultimo


# --- Riepilogo e controlli per persona -----------------------------------------------------


def test_riepilogo_delle_7_a_ognuno_con_il_suo_nome(bot, messaggero):
    update = messaggio("/aiuto", chat_id=BABBO)
    update["message"]["from"]["first_name"] = "Giorgio"
    bot.gestisci_update(update)
    messaggero.inviati.clear()
    bot.invia_riepilogo_mattutino()
    saluti = {chat: testo.split("\n")[0] for chat, testo in messaggero.inviati}
    assert "Buongiorno Ale!" in saluti[PADRONE]
    assert "Buongiorno Giorgio!" in saluti[BABBO]
    assert "Oggi, " in saluti[FIDANZATA]  # nome sconosciuto: niente saluto personale


def test_il_meteo_si_scarica_una_volta_sola_per_tutti(bot, meteo):
    bot.invia_riepilogo_mattutino()
    assert meteo.richieste.count("previsioni:riccione") == 1
    assert meteo.richieste.count("mare:rimini") == 1


def test_controlli_per_ognuno_nella_sua_zona(bot, messaggero, luoghi):
    luoghi.luogo = BOLOGNA
    bot.gestisci_update(posizione(*BOLOGNA_XY, chat_id=BABBO))
    messaggero.inviati.clear()
    assert bot.controlla_notizie() == 3
    per_chat = {chat: testo for chat, testo in messaggero.inviati}
    assert "Novità da Riccione" in per_chat[PADRONE]
    assert "Novità da Bologna (BO)" in per_chat[BABBO]
    assert bot.controlla_notizie() == 0  # già mandate, a ognuno le sue


def test_allerte_a_ognuno_una_volta(bot, messaggero):
    assert bot.controlla_allerte() == 3
    assert {chat for chat, _ in messaggero.inviati} == {PADRONE, BABBO, FIDANZATA}
    assert bot.controlla_allerte() == 0


class MessaggeroCheFallisceCon:
    def __init__(self, base, chat_rotta):
        self.base, self.chat_rotta = base, chat_rotta

    def __getattr__(self, nome):
        return getattr(self.base, nome)

    def invia_messaggio(self, chat_id, testo, tastiera=None):
        if chat_id == self.chat_rotta:
            raise ErroreTelegram("sendMessage: Forbidden: bot was blocked by the user")
        self.base.invia_messaggio(chat_id, testo, tastiera)


def test_se_qualcuno_blocca_il_bot_gli_altri_ricevono_lo_stesso(
    impostazioni, messaggero, meteo, archivio
):
    rotto = MessaggeroCheFallisceCon(messaggero, BABBO)
    bot = Azdora(
        impostazioni,
        rotto,
        meteo,
        archivio=archivio,
        notizie=NotizieFinte(),
        allerte=AllerteFinte(),
        orologio=lambda: ADESSO,
    )
    assert bot.controlla_notizie() == 2
    bot.invia_riepilogo_mattutino()
    assert {chat for chat, _ in messaggero.inviati} >= {PADRONE, FIDANZATA}
    assert bot.controlla_allerte() == 2


# --- Altri gruppi --------------------------------------------------------------------------

GRUPPO_BABBO = -777


def test_gruppi_extra_dall_ambiente():
    impostazioni = Impostazioni.da_env(
        {
            "TELEGRAM_CHAT_ID": str(PADRONE),
            "TELEGRAM_CHAT_ID_COPPIA": str(COPPIA),
            "TELEGRAM_CHAT_ID_GRUPPI": "-777, -888",
        }
    )
    assert impostazioni.gruppi == (COPPIA, -777, -888)
    assert impostazioni.chat_autorizzate == {PADRONE, COPPIA, -777, -888}
    assert impostazioni.persone == (PADRONE,)  # i gruppi non ricevono il riepilogo meteo


def test_il_gruppo_con_il_babbo_ha_la_sua_lista(impostazioni, messaggero, meteo, archivio):
    bot = Azdora(
        replace(impostazioni, chat_gruppi=(GRUPPO_BABBO,)),
        messaggero,
        meteo,
        archivio=archivio,
        orologio=lambda: ADESSO,
    )
    bot.gestisci_update(
        messaggio("/aggiungi potare l'ulivo", chat_id=GRUPPO_BABBO, tipo="group", mittente=BABBO)
    )
    assert messaggero.inviati[-1][0] == GRUPPO_BABBO
    assert "potare l&#x27;ulivo" in messaggero.ultimo
    bot.gestisci_update(messaggio("/lista", chat_id=COPPIA, tipo="group", mittente=PADRONE))
    assert "potare" not in messaggero.ultimo
    assert messaggero.uscite == []


def test_gruppo_non_in_lista_resta_fuori(bot, messaggero):
    bot.gestisci_update(messaggio("/lista", chat_id=-999, tipo="group", mittente=BABBO))
    assert messaggero.uscite == [-999]


@pytest.fixture(autouse=True)
def notizie_subito(archivio):
    """In questi test le notizie arrivano "subito" (una volta al giro), come prima di /orari."""
    from azdora.orari import Orari, PreferenzeOrari

    preferenze = PreferenzeOrari(archivio)
    for chat in (111, 333, 444):
        preferenze.salva(chat, Orari(notizie_subito=True, silenzio_da=None, silenzio_a=None))
