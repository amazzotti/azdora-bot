"""Le migliorie dentro il bot: frasi libere, pulsanti delle spese, conti, promemoria, sera…"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
import pytest

from azdora import messaggi
from azdora.bot import COMANDI, Azdora
from azdora.budget import TOTALE, Budget
from azdora.carburante import Distributore, Offerta
from azdora.config import FUSO_ORARIO
from azdora.meteo.aria import Aria
from azdora.promemoria import RegistroPromemoria
from azdora.rifiuti import Calendario
from azdora.spese import TUTTE_LE_CATEGORIE, RegistroSpese
from tests.conftest import COPPIA, PADRONE, messaggio

ADESSO = datetime(2026, 11, 5, 15, 0, tzinfo=FUSO_ORARIO)  # giovedì
OGGI = ADESSO.date()
GIULIA = 22


class Orologio:
    def __init__(self) -> None:
        self.adesso = ADESSO

    def __call__(self) -> datetime:
        return self.adesso


@dataclass
class AriaFinta:
    risposta: Aria | None = field(default_factory=lambda: Aria(5, 30, {"graminacee": 80}))
    richieste: list[str] = field(default_factory=list)

    def aria(self, localita):
        self.richieste.append(localita.nome)
        return self.risposta


@dataclass
class CarburanteFinto:
    guasto: bool = False
    richieste: list[tuple[str, float, float]] = field(default_factory=list)

    def offerte(self, carburante, lat, lon, adesso):
        self.richieste.append((carburante, lat, lon))
        if self.guasto:
            raise httpx.ConnectError("giù")
        d = Distributore("1", "Ego", "Ego", "Viale Dante 1", "Riccione", 44.0, 12.65)
        return [Offerta(d, 1.799, True, adesso, 1.2)]


@pytest.fixture
def orologio():
    return Orologio()


@pytest.fixture
def aria():
    return AriaFinta()


@pytest.fixture
def carburante():
    return CarburanteFinto()


@pytest.fixture
def bot(impostazioni, messaggero, meteo, luoghi, archivio, orologio, aria, carburante):
    return Azdora(
        impostazioni,
        messaggero,
        meteo,
        luoghi,
        archivio=archivio,
        aria=aria,
        carburante=carburante,
        orologio=orologio,
    )


def tocco(dati, chat_id=PADRONE, id_messaggio=7):
    return {
        "callback_query": {
            "id": "t",
            "message": {"message_id": id_messaggio, "chat": {"id": chat_id, "type": "private"}},
            "data": dati,
        }
    }


def nel_gruppo(testo, chi=PADRONE, nome="Ale"):
    update = messaggio(testo, chat_id=COPPIA, tipo="group", mittente=chi)
    update["message"]["from"]["first_name"] = nome
    return update


def test_menu_completo(bot):
    nomi = {nome for nome, _ in COMANDI}
    assert {"budget", "conti", "promemoria", "rifiuti", "sera", "aria", "benzina"} <= nomi
    assert nomi <= set(bot._azioni)
    assert all(len(descrizione) <= 256 for _, descrizione in COMANDI)


# --- Spese libere e pulsanti ---------------------------------------------------------------


def test_spesa_scritta_come_si_parla(bot, messaggero, archivio):
    bot.gestisci_update(messaggio("ho speso 12,50 per la pizza"))
    assert "Segnata: <b>12,50 €</b> · 🍕 Fuori · pizza" in messaggero.ultimo
    tasti = messaggero.tastiere[-1]["inline_keyboard"][0]
    assert tasti[0]["callback_data"] == "spcat:2026-11:1"
    assert len(RegistroSpese(archivio, PADRONE).del_mese(OGGI)) == 1


def test_cambiare_categoria_con_i_pulsanti_e_imparare(bot, messaggero, archivio):
    bot.gestisci_update(messaggio("20 tigotà"))
    bot.gestisci_update(tocco("spcat:2026-11:1"))
    _, _, testo, tastiera = messaggero.modificati[-1]
    assert "In che categoria metto <b>tigotà</b>" in testo
    indice_casa = TUTTE_LE_CATEGORIE.index("🏠 Casa")
    assert any(
        t["callback_data"] == f"spset:2026-11:1:{indice_casa}"
        for fila in tastiera["inline_keyboard"]
        for t in fila
    )
    bot.gestisci_update(tocco(f"spset:2026-11:1:{indice_casa}"))
    assert "→ <b>🏠 Casa</b>" in messaggero.modificati[-1][2]
    bot.gestisci_update(messaggio("tigotà 9"))
    assert "🏠 Casa" in messaggero.ultimo  # se l'è ricordato


def test_annullare_con_il_pulsante(bot, messaggero, archivio):
    bot.gestisci_update(messaggio("45 benzina"))
    bot.gestisci_update(tocco("spann:2026-11:1"))
    assert "Annullata" in messaggero.modificati[-1][2]
    assert messaggero.modificati[-1][3] == {"inline_keyboard": []}
    assert RegistroSpese(archivio, PADRONE).del_mese(OGGI) == []
    bot.gestisci_update(tocco("spann:2026-11:1"))
    assert messaggero.risposte_pulsanti[-1][1] == "Questa spesa non c'è più"


@pytest.mark.parametrize("dati", ["spset:2026-11:1:99", "spcat:boh", "spset:2026-13:1:0"])
def test_pulsanti_spesa_strani(bot, messaggero, dati):
    bot.gestisci_update(messaggio("45 benzina"))
    bot.gestisci_update(tocco(dati))
    assert len(messaggero.risposte_pulsanti) == 1


def test_nel_gruppo_solo_frasi_con_spesa_o_piu(bot, messaggero, archivio):
    bot.gestisci_update(nel_gruppo("ci vediamo stasera alle 20 per la pizza"))
    assert messaggero.inviati == []
    bot.gestisci_update(nel_gruppo("spesa 60 conad", chi=GIULIA, nome="Giulia"))
    bot.gestisci_update(nel_gruppo("+12 pizza"))
    spese = RegistroSpese(archivio, COPPIA).del_mese(OGGI)
    assert [(s.chi, s.importo) for s in spese] == [("Giulia", 6000), ("Ale", 1200)]
    assert "Pagata da Ale" in messaggero.ultimo


def test_spesa_con_avviso_budget(bot, messaggero, archivio):
    Budget(archivio, PADRONE).imposta("🚗 Auto", 5000)
    bot.gestisci_update(messaggio("/spesa 45 benzina"))
    assert "⚠️ Budget 🚗 Auto al 90%" in messaggero.ultimo


def test_comando_budget(bot, messaggero):
    bot.gestisci_update(messaggio("/budget"))
    assert "Nessun budget" in messaggero.ultimo
    bot.gestisci_update(messaggio("/budget 800"))
    assert "Totale: 0,00 € di 800,00 €" in messaggero.ultimo
    bot.gestisci_update(messaggio("/budget astronavi 10"))
    assert "Categorie:" in messaggero.ultimo


# --- Conti di coppia -----------------------------------------------------------------------


def test_conti_e_pareggio(bot, messaggero, orologio):
    bot.gestisci_update(nel_gruppo("/spesa 60 spesa conad", chi=PADRONE, nome="Ale"))
    bot.gestisci_update(nel_gruppo("/conti", chi=GIULIA, nome="Giulia"))
    assert "👉 <b>Giulia</b> dà <b>30,00 €</b> a Ale" in messaggero.ultimo
    bot.gestisci_update(nel_gruppo("/pareggio"))
    assert messaggero.ultimo == messaggi.PAREGGIO
    orologio.adesso = ADESSO + timedelta(days=1)
    bot.gestisci_update(nel_gruppo("/conti"))
    assert "Nessuna spesa comune" in messaggero.ultimo


def test_conti_in_privato_spiega(bot, messaggero):
    bot.gestisci_update(messaggio("/conti"))
    assert messaggero.ultimo == messaggi.CONTI_SOLO_GRUPPO
    bot.gestisci_update(messaggio("/pareggio"))
    assert messaggero.ultimo == messaggi.CONTI_SOLO_GRUPPO


# --- Promemoria ----------------------------------------------------------------------------


def test_promemoria_scritto_e_consegnato(bot, messaggero, archivio, orologio):
    bot.gestisci_update(messaggio("ricordami tra 20 minuti di girare il sugo"))
    assert "Ti ricordo <b>girare il sugo</b> oggi alle 15:20" in messaggero.ultimo
    assert bot.controlla_promemoria() == 0
    orologio.adesso = ADESSO + timedelta(minutes=30)
    assert bot.controlla_promemoria() == 1
    assert messaggero.ultimo == "⏰ <b>Promemoria</b>: girare il sugo"
    assert bot.controlla_promemoria() == 0  # consegnato una volta sola


def test_comando_ricordami_e_errori(bot, messaggero):
    bot.gestisci_update(messaggio("/ricordami"))
    assert "Dimmi quando e cosa" in messaggero.ultimo
    bot.gestisci_update(messaggio("/ricordami di comprare il latte"))
    assert "Non ho capito quando" in messaggero.ultimo
    bot.gestisci_update(messaggio("/ricordami venerdì alle 9 di prenotare"))
    assert "domani alle 09:00" in messaggero.ultimo


def test_elenco_e_cancellazione_promemoria(bot, messaggero, archivio):
    bot.gestisci_update(messaggio("ricordami domani alle 9 il dentista"))
    bot.gestisci_update(messaggio("/promemoria"))
    assert "domani alle 09:00: il dentista" in messaggero.ultimo
    bot.gestisci_update(tocco("prdel:1"))
    assert "Nessun promemoria" in messaggero.modificati[-1][2]
    assert RegistroPromemoria(archivio, PADRONE).tutti() == []


def test_promemoria_nel_gruppo(bot, messaggero, archivio, orologio):
    bot.gestisci_update(nel_gruppo("ricordami stasera di stendere"))
    assert len(RegistroPromemoria(archivio, COPPIA).tutti()) == 1
    orologio.adesso = ADESSO.replace(hour=20, minute=5)
    bot.controlla_promemoria()
    assert messaggero.inviati[-1][0] == COPPIA


def test_promemoria_senza_memoria_non_consegnati(impostazioni, messaggero, meteo):
    bot = Azdora(impostazioni, messaggero, meteo, orologio=lambda: ADESSO)
    assert bot.controlla_promemoria() == 0


# --- Rifiuti e sera ------------------------------------------------------------------------


def test_comando_rifiuti(bot, messaggero, archivio):
    bot.gestisci_update(messaggio("/rifiuti venerdì plastica, lattine"))
    assert "<b>Venerdì</b>: 🟡 plastica, 🟡 lattine" in messaggero.ultimo
    bot.gestisci_update(messaggio("/rifiuti boh"))
    assert "Dimmi cosa passa" in messaggero.ultimo
    assert Calendario(archivio, PADRONE).del_giorno(4) == ["plastica", "lattine"]


def test_sera_con_rifiuti_e_riepilogo(bot, messaggero, archivio, orologio):
    Calendario(archivio, PADRONE).imposta(4, ["plastica"])  # venerdì = domani
    bot.gestisci_update(messaggio("/sera"))
    assert "<b>attivo</b>" in messaggero.ultimo
    RegistroPromemoria(archivio, PADRONE).aggiungi(ADESSO + timedelta(days=1), "dentista")
    orologio.adesso = ADESSO.replace(hour=19, minute=50)
    assert bot.controlla_sera() == 0  # non è ancora ora
    orologio.adesso = ADESSO.replace(hour=20, minute=5)
    assert bot.controlla_sera() == 1
    testo = messaggero.ultimo
    assert "Stasera porta fuori</b>: 🟡 plastica" in testo
    assert "Buonasera, Ale!" in testo
    assert "Domani, venerdì 6 novembre" in testo
    assert "15:00 dentista" in testo
    orologio.adesso = ADESSO.replace(hour=20, minute=20)
    assert bot.controlla_sera() == 0  # una volta sola per sera


def test_sera_spenta_manda_solo_i_rifiuti(bot, messaggero, archivio, orologio):
    Calendario(archivio, COPPIA).imposta(4, ["vetro"])
    orologio.adesso = ADESSO.replace(hour=20)
    assert bot.controlla_sera() == 1
    chat, testo = messaggero.inviati[-1]
    assert chat == COPPIA and testo == "🗑️ <b>Stasera porta fuori</b>: 🟢 vetro"


def test_comando_sera_on_off(bot, messaggero):
    bot.gestisci_update(messaggio("/sera on"))
    assert "<b>attivo</b>" in messaggero.ultimo
    bot.gestisci_update(messaggio("/sera off"))
    assert "<b>spento</b>" in messaggero.ultimo


def test_controlli_completi_includono_promemoria(bot, messaggero, orologio):
    bot.gestisci_update(messaggio("ricordami tra 5 minuti di uscire"))
    orologio.adesso = ADESSO + timedelta(minutes=15)
    bot.controlli()
    assert "uscire" in messaggero.ultimo


# --- Aria e carburante ---------------------------------------------------------------------


def test_comando_aria(bot, messaggero, aria):
    bot.gestisci_update(messaggio("/aria"))
    assert "Aria a Riccione" in messaggero.ultimo
    assert "graminacee alto" in messaggero.ultimo
    aria.risposta = None
    bot.gestisci_update(messaggio("/aria rimini"))
    assert messaggero.ultimo == messaggi.ARIA_NON_DISPONIBILE


def test_aria_nel_riepilogo(bot, messaggero):
    bot.gestisci_update(messaggio("/oggi"))
    assert "Pollini alti: graminacee" in messaggero.ultimo


def test_senza_servizi_aria_e_benzina(impostazioni, messaggero, meteo):
    bot = Azdora(impostazioni, messaggero, meteo, orologio=lambda: ADESSO)
    bot.gestisci_update(messaggio("/aria"))
    assert messaggero.ultimo == messaggi.ARIA_NON_DISPONIBILE
    bot.gestisci_update(messaggio("/benzina"))
    assert messaggero.ultimo == messaggi.CARBURANTE_NON_DISPONIBILE


def test_comando_benzina(bot, messaggero, carburante):
    bot.gestisci_update(messaggio("/benzina"))
    assert "Benzina più economica vicino a Riccione" in messaggero.ultimo
    assert "1,799 €/l" in messaggero.ultimo
    bot.gestisci_update(messaggio("/benzina gasolio rimini"))
    assert carburante.richieste[-1][0] == "Gasolio"
    assert "Gasolio più economico vicino a Rimini" in messaggero.ultimo
    bot.gestisci_update(messaggio("/benzina diesel"))
    assert carburante.richieste[-1][0] == "Gasolio"


def test_benzina_ministero_giu(bot, messaggero, carburante):
    carburante.guasto = True
    bot.gestisci_update(messaggio("/benzina gpl"))
    assert messaggero.ultimo == messaggi.CARBURANTE_NON_DISPONIBILE


def test_budget_totale_dopo_spesa_libera(bot, messaggero, archivio):
    Budget(archivio, PADRONE).imposta(TOTALE, 1000)
    bot.gestisci_update(messaggio("12 aperitivo"))
    assert "🚨 Budget Totale superato" in messaggero.ultimo
