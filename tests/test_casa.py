"""Tappe 3-5 dentro il bot: comandi, pulsanti, promemoria delle 7 e resoconto del mese."""

from dataclasses import dataclass, field
from datetime import date, datetime

import pytest

from azdora import messaggi
from azdora.archivio import ArchivioMemoria, ErroreArchivio
from azdora.bot import COMANDI, Azdora
from azdora.config import FUSO_ORARIO
from azdora.notizie import Notizia
from azdora.scadenze import NuovaScadenza, RegistroScadenze
from azdora.spese import RegistroSpese
from tests.conftest import ADESSO, COPPIA, PADRONE, SCONOSCIUTO, messaggio

OGGI = ADESSO.date()


@dataclass
class EventiFinti:
    eventi_settimana: list[Notizia] = field(default_factory=list)

    def notizie_per(self, comune, lat, lon, adesso):
        return []

    def del_giorno_in_riviera(self, comune, adesso):
        return []

    def eventi(self, adesso):
        return list(self.eventi_settimana)


@pytest.fixture
def notizie():
    return EventiFinti()


@pytest.fixture
def bot(impostazioni, messaggero, meteo, luoghi, archivio, notizie):
    return Azdora(
        impostazioni,
        messaggero,
        meteo,
        luoghi,
        archivio=archivio,
        notizie=notizie,
        orologio=lambda: ADESSO,
    )


def tocco(dati, chat_id=PADRONE, id_messaggio=42):
    return {
        "update_id": 7,
        "callback_query": {
            "id": "tocco-1",
            "from": {"id": PADRONE, "first_name": "Ale"},
            "message": {"message_id": id_messaggio, "chat": {"id": chat_id, "type": "private"}},
            "data": dati,
        },
    }


def test_tutti_i_comandi_del_menu_esistono(bot):
    for nome, _ in COMANDI:
        assert nome in bot._azioni
    assert len(COMANDI) <= 100  # limite di Telegram


# --- Scadenze ------------------------------------------------------------------------------


def test_segnare_e_vedere_le_scadenze(bot, messaggero):
    bot.gestisci_update(messaggio("/scadenza 31/01 bollo auto 180€ annuale"))
    assert "Segnata: <b>bollo auto</b> · 180,00 €" in messaggero.ultimo
    bot.gestisci_update(messaggio("/scadenze"))
    assert "Le tue scadenze" in messaggero.ultimo
    tastiera = messaggero.tastiere[-1]["inline_keyboard"]
    assert tastiera[0][0]["callback_data"] == "pagato:1"


def test_scadenza_scritta_male(bot, messaggero):
    bot.gestisci_update(messaggio("/scadenza bollo"))
    assert "Per segnare una scadenza" in messaggero.ultimo


def test_pulsante_pagato_registra_la_spesa(bot, messaggero, archivio):
    bot.gestisci_update(messaggio("/scadenza 7/11 bollo auto 180€ annuale"))
    bot.gestisci_update(tocco("pagato:1"))
    (_, avviso) = messaggero.risposte_pulsanti[-1]
    assert avviso == "✅ bollo auto: pagato. Prossima: 7/11/2027"
    chat_id, id_messaggio, testo, _ = messaggero.modificati[-1]
    assert (chat_id, id_messaggio) == (PADRONE, 42)
    assert "7/11/2027" in testo
    (spesa,) = RegistroSpese(archivio, PADRONE).del_mese(OGGI)
    assert (spesa.importo, spesa.categoria) == (18000, "🚗 Auto")


def test_pagato_senza_importo_non_crea_spese(bot, archivio):
    bot.gestisci_update(messaggio("/scadenza 10/12 revisione"))
    bot.gestisci_update(tocco("pagato:1"))
    assert RegistroSpese(archivio, PADRONE).del_mese(OGGI) == []
    assert RegistroScadenze(archivio, PADRONE).tutte() == []


def test_pulsante_elimina_e_scadenza_sparita(bot, messaggero):
    bot.gestisci_update(messaggio("/scadenza 10/12 revisione"))
    bot.gestisci_update(tocco("elimina:1"))
    assert messaggero.risposte_pulsanti[-1][1] == "🗑 Tolta: revisione"
    assert "Nessuna scadenza" in messaggero.modificati[-1][2]
    bot.gestisci_update(tocco("pagato:1"))
    assert messaggero.risposte_pulsanti[-1][1] == "Questa scadenza non c'è più"


def test_pulsanti_da_chat_sconosciute_ignorati(bot, messaggero, archivio):
    RegistroScadenze(archivio, SCONOSCIUTO).aggiungi(NuovaScadenza("x", OGGI, 100, 0))
    bot.gestisci_update(tocco("pagato:1", chat_id=SCONOSCIUTO))
    assert messaggero.risposte_pulsanti == [("tocco-1", None)]
    assert RegistroScadenze(archivio, SCONOSCIUTO).tutte() != []


@pytest.mark.parametrize("dati", ["pagato:abc", "boh:1", "", "voce:"])
def test_pulsanti_strani(bot, messaggero, dati):
    bot.gestisci_update(tocco(dati))
    assert len(messaggero.risposte_pulsanti) == 1  # risponde sempre, senza esplodere


def test_pulsante_senza_messaggio(bot, messaggero):
    update = tocco("pagato:1")
    del update["callback_query"]["message"]
    bot.gestisci_update(update)
    assert messaggero.risposte_pulsanti == [("tocco-1", None)]


def test_promemoria_alle_7(bot, messaggero, archivio):
    RegistroScadenze(archivio, PADRONE).aggiungi(NuovaScadenza("bollo", OGGI, 18000, 12))
    RegistroScadenze(archivio, PADRONE).aggiungi(
        NuovaScadenza("lontana", date(2027, 6, 1), None, 0)
    )
    RegistroScadenze(archivio, COPPIA).aggiungi(
        NuovaScadenza("bolletta gas", date(2026, 11, 12), 9000, 2)
    )
    bot.invia_riepilogo_mattutino()
    destinatari = [chat for chat, _ in messaggero.inviati]
    assert destinatari == [PADRONE, PADRONE, COPPIA]
    assert "Promemoria scadenze" in messaggero.inviati[1][1]
    assert "bollo" in messaggero.inviati[1][1] and "lontana" not in messaggero.inviati[1][1]
    assert "bolletta gas" in messaggero.inviati[2][1]
    assert len(messaggero.tastiere[1]["inline_keyboard"][0]) == 1  # solo "Pagato"


# --- Spese ---------------------------------------------------------------------------------


def test_spesa_e_riepilogo(bot, messaggero):
    bot.gestisci_update(messaggio("/spesa 45 benzina"))
    assert "Segnata: <b>45,00 €</b> · 🚗 Auto · benzina" in messaggero.ultimo
    bot.gestisci_update(messaggio("/spesa pizza 12,50"))
    assert "Questo mese: 57,50 €" in messaggero.ultimo
    bot.gestisci_update(messaggio("/mese"))
    assert "Spese di novembre 2026" in messaggero.ultimo
    bot.gestisci_update(messaggio("/spese"))
    assert "pizza" in messaggero.ultimo
    bot.gestisci_update(messaggio("/annulla"))
    assert "Tolta: 12,50 € · pizza" in messaggero.ultimo


def test_spesa_senza_importo(bot, messaggero):
    bot.gestisci_update(messaggio("/spesa benzina"))
    assert messaggero.ultimo == messaggi.USO_SPESA


def test_annulla_senza_spese(bot, messaggero):
    bot.gestisci_update(messaggio("/annulla"))
    assert messaggero.ultimo == messaggi.NIENTE_DA_ANNULLARE


def test_mese_passato_con_confronto(bot, messaggero, archivio):
    registro = RegistroSpese(archivio, PADRONE)
    registro.aggiungi(date(2026, 10, 3), 10000, "spesa conad")
    registro.aggiungi(date(2026, 9, 3), 5000, "spesa conad")
    bot.gestisci_update(messaggio("/mese ottobre"))
    assert "Spese di ottobre 2026" in messaggero.ultimo
    assert "📈 più del mese prima (50,00 €): 50,00 €" in messaggero.ultimo
    bot.gestisci_update(messaggio("/mese tredici"))
    assert messaggero.ultimo == messaggi.USO_MESE


def test_esporta(bot, messaggero, archivio):
    bot.gestisci_update(messaggio("/esporta"))
    assert "Nessuna spesa segnata nel 2026" in messaggero.ultimo
    RegistroSpese(archivio, PADRONE).aggiungi(OGGI, 4500, "benzina")
    bot.gestisci_update(messaggio("/esporta 2026"))
    chat_id, nome, contenuto, didascalia = messaggero.documenti[-1]
    assert (chat_id, nome) == (PADRONE, "spese-2026.csv")
    assert b"benzina" in contenuto
    assert "1 spesa" in didascalia


def test_spese_di_coppia_separate(bot, archivio):
    bot.gestisci_update(messaggio("/spesa 80 spesa conad", chat_id=COPPIA, tipo="group"))
    assert RegistroSpese(archivio, PADRONE).del_mese(OGGI) == []
    assert len(RegistroSpese(archivio, COPPIA).del_mese(OGGI)) == 1


def test_resoconto_il_primo_del_mese(impostazioni, messaggero, meteo, archivio):
    primo = datetime(2026, 12, 1, 7, 0, tzinfo=FUSO_ORARIO)
    RegistroSpese(archivio, PADRONE).aggiungi(date(2026, 11, 20), 4500, "benzina")
    bot = Azdora(impostazioni, messaggero, meteo, archivio=archivio, orologio=lambda: primo)
    bot.invia_riepilogo_mattutino()
    assert any("Spese di novembre 2026" in testo for _, testo in messaggero.inviati)
    assert sum("Spese di" in testo for _, testo in messaggero.inviati) == 1  # coppia: nulla


def test_promemoria_resistono_all_archivio_giu(impostazioni, messaggero, meteo):
    class Guasto(ArchivioMemoria):
        def leggi(self, chiave):
            raise ErroreArchivio("giù")

    bot = Azdora(impostazioni, messaggero, meteo, archivio=Guasto(), orologio=lambda: ADESSO)
    bot.invia_riepilogo_mattutino()
    assert len(messaggero.inviati) == 1  # il meteo arriva comunque


def test_archivio_giu_durante_un_comando(impostazioni, messaggero, meteo):
    class Guasto(ArchivioMemoria):
        def leggi(self, chiave):
            raise ErroreArchivio("giù")

    Azdora(impostazioni, messaggero, meteo, archivio=Guasto()).gestisci_update(
        messaggio("/spesa 10 caffè")
    )
    assert messaggero.ultimo == messaggi.ARCHIVIO_NON_DISPONIBILE


# --- Lista ---------------------------------------------------------------------------------


def test_lista_nel_gruppo_con_chi_aggiunge(bot, messaggero):
    bot.gestisci_update(
        messaggio("/aggiungi latte, pane", chat_id=COPPIA, tipo="group", mittente=7)
    )
    assert "⬜ latte <i>(Test)</i>" in messaggero.ultimo
    bot.gestisci_update(tocco("voce:1", chat_id=COPPIA))
    assert "✅ <s>latte</s>" in messaggero.modificati[-1][2]
    bot.gestisci_update(tocco("lista:pulisci", chat_id=COPPIA))
    assert "latte" not in messaggero.modificati[-1][2]
    bot.gestisci_update(messaggio("/lista", chat_id=COPPIA, tipo="group", mittente=7))
    assert "pane" in messaggero.ultimo


def test_lista_privata_senza_nomi(bot, messaggero):
    bot.gestisci_update(messaggio("/aggiungi ritirare il pacco"))
    assert "<i>(" not in messaggero.ultimo


def test_aggiungi_vuoto_e_svuota(bot, messaggero):
    bot.gestisci_update(messaggio("/aggiungi"))
    assert messaggero.ultimo == messaggi.USO_AGGIUNGI
    bot.gestisci_update(messaggio("/svuota"))
    assert messaggero.ultimo == messaggi.lista_svuotata(0)
    bot.gestisci_update(messaggio("/aggiungi a, b"))
    bot.gestisci_update(tocco("voce:1"))
    bot.gestisci_update(messaggio("/svuota"))
    assert messaggero.ultimo == messaggi.lista_svuotata(1)


def test_lista_vuota_dopo_pulizia_toglie_i_pulsanti(bot, messaggero):
    bot.gestisci_update(messaggio("/aggiungi a"))
    bot.gestisci_update(tocco("voce:1"))
    bot.gestisci_update(tocco("lista:pulisci"))
    assert messaggero.modificati[-1][3] == {"inline_keyboard": []}


# --- Weekend -------------------------------------------------------------------------------


def test_weekend(bot, messaggero, notizie):
    notizie.eventi_settimana = [Notizia("Sagra della castagna", "https://a.it", "Altarimini")]
    bot.gestisci_update(messaggio("/weekend"))
    testo = messaggero.ultimo
    assert "Cosa facciamo nel weekend" in testo
    assert "Sabato: 🌧️ Pioggia" in testo  # sabato 7/11 nelle previsioni salvate
    assert "Domenica: previsioni non ancora disponibili" in testo
    assert "Sagra della castagna" in testo


def test_weekend_senza_notizie(impostazioni, messaggero, meteo):
    Azdora(impostazioni, messaggero, meteo, orologio=lambda: ADESSO).gestisci_update(
        messaggio("/weekend")
    )
    assert "Sagre" not in messaggero.ultimo
