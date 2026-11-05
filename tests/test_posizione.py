"""Meteo in base a dove ti trovi: posizione condivisa, /qui e località qualsiasi."""

from azdora import messaggi
from azdora.bot import Azdora
from azdora.luoghi import Luogo
from tests.conftest import (
    ADESSO,
    COPPIA,
    PADRONE,
    SCONOSCIUTO,
    messaggio,
    posizione,
)
from tests.conftest import (
    BOLOGNA as LUOGO_BOLOGNA,
)

MISANO = (43.978, 12.697)
BOLOGNA = (44.494, 11.343)


def test_posizione_sulla_costa(bot, messaggero):
    bot.gestisci_update(posizione(*MISANO))
    chat_id, testo = messaggero.inviati[0]
    assert chat_id == PADRONE
    assert "Meteo dove sei" in testo
    assert "📍 <b>Misano Adriatico (RN)</b>" in testo
    assert "Adesso: Maestrale" in testo
    assert "Mare: onde fino a 0.4 m" in testo
    assert "<b>Consigli</b>" in testo
    assert "OpenStreetMap" in testo
    # Dopo l'uso il pulsante "Manda la mia posizione" sparisce.
    assert messaggero.tastiere[0] == messaggi.TOGLI_TASTIERA


def test_posizione_nell_entroterra_senza_mare(bot, messaggero, luoghi):
    luoghi.luogo = LUOGO_BOLOGNA
    bot.gestisci_update(posizione(*BOLOGNA))
    assert "Bologna (BO)" in messaggero.ultimo
    assert "Mare:" not in messaggero.ultimo


def test_posizione_senza_nome_del_luogo(bot, messaggero, luoghi):
    luoghi.luogo = None
    bot.gestisci_update(posizione(*MISANO))
    assert "📍 <b>La tua posizione</b>" in messaggero.ultimo
    assert "OpenStreetMap" not in messaggero.ultimo


def test_nome_del_luogo_con_html_neutralizzato(bot, messaggero, luoghi):
    luoghi.luogo = Luogo("<script>")
    bot.gestisci_update(posizione(*MISANO))
    assert "&lt;script&gt;" in messaggero.ultimo
    assert "<script>" not in messaggero.ultimo


def test_posizione_nel_gruppo_di_coppia(bot, messaggero):
    bot.gestisci_update(posizione(*MISANO, chat_id=COPPIA, tipo="supergroup"))
    assert messaggero.inviati[0][0] == COPPIA
    assert messaggero.tastiere[0] is None  # nei gruppi non c'è tastiera da togliere


def test_posizione_da_uno_sconosciuto_ignorata(bot, messaggero, meteo):
    bot.gestisci_update(posizione(*MISANO, chat_id=SCONOSCIUTO))
    assert messaggero.inviati == []
    assert meteo.richieste == []


def test_posizione_con_meteo_giu(bot, messaggero, meteo):
    meteo.guasto = True
    bot.gestisci_update(posizione(*MISANO))
    assert messaggero.ultimo == messaggi.METEO_NON_DISPONIBILE


def test_posizione_senza_servizio_luoghi(impostazioni, messaggero, meteo):
    bot = Azdora(impostazioni, messaggero, meteo, orologio=lambda: ADESSO)
    bot.gestisci_update(posizione(*MISANO))
    assert "La tua posizione" in messaggero.ultimo


def test_qui_in_privato_mostra_il_pulsante(bot, messaggero):
    bot.gestisci_update(messaggio("/qui"))
    assert messaggero.ultimo == messaggi.QUI_PRIVATA
    pulsante = messaggero.tastiere[0]["keyboard"][0][0]
    assert pulsante["request_location"] is True


def test_qui_nel_gruppo_spiega_come_fare(bot, messaggero):
    bot.gestisci_update(messaggio("/qui", chat_id=COPPIA, tipo="group", mittente=PADRONE))
    assert messaggero.ultimo == messaggi.QUI_GRUPPO
    assert messaggero.tastiere[0] is None


def test_meteo_di_una_localita_qualsiasi(bot, messaggero, luoghi):
    bot.gestisci_update(messaggio("/meteo Misano Adriatico"))
    assert luoghi.cercati == ["Misano Adriatico"]
    assert "Meteo a Misano Adriatico" in messaggero.ultimo


def test_riccione_e_rimini_non_passano_dalla_ricerca(bot, luoghi):
    bot.gestisci_update(messaggio("/vento rn"))
    assert luoghi.cercati == []


def test_mare_di_una_localita_nell_entroterra(bot, messaggero):
    bot.gestisci_update(messaggio("/mare bologna"))
    assert messaggero.ultimo == messaggi.mare_lontano("Bologna")


def test_mare_di_una_localita_costiera(bot, messaggero):
    bot.gestisci_update(messaggio("/mare misano adriatico"))
    assert "Mare a Misano Adriatico" in messaggero.ultimo


def test_mare_senza_dati_open_meteo(bot, messaggero, meteo):
    for chiave in ("wave_height", "wave_direction", "wave_period"):
        meteo.dati_mare["current"][chiave] = None
    meteo.dati_mare["daily"]["wave_height_max"] = [None, None, None]
    bot.gestisci_update(messaggio("/mare"))
    assert "lontano dal mare" in messaggero.ultimo


def test_ricerca_luoghi_guasta(bot, messaggero, luoghi):
    luoghi.guasto = True
    bot.gestisci_update(messaggio("/meteo cesenatico"))
    assert messaggero.ultimo == messaggi.METEO_NON_DISPONIBILE
