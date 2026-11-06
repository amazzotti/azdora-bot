"""Compleanni: frasi, elenco, avvisi una settimana prima, il giorno prima e il giorno stesso."""

from datetime import date, datetime, timedelta

import pytest

from azdora.bot import Azdora
from azdora.compleanni import Compleanno, RegistroCompleanni, leggi_compleanno
from azdora.config import FUSO_ORARIO
from azdora.orari import Orari
from azdora.telegram import ErroreTelegram
from tests.conftest import COPPIA, PADRONE, FintoMessaggero, messaggio


class Orologio:
    def __init__(self, ora: datetime) -> None:
        self.ora = ora

    def __call__(self) -> datetime:
        return self.ora


@pytest.fixture
def orologio():
    return Orologio(datetime(2026, 11, 6, 10, 0, tzinfo=FUSO_ORARIO))


@pytest.fixture
def azdora(impostazioni, messaggero, meteo, archivio, orologio):
    return Azdora(impostazioni, messaggero, meteo, archivio=archivio, orologio=orologio)


@pytest.mark.parametrize(
    ("frase", "atteso"),
    [
        ("ricordami il compleanno della mamma il 14/3", ("mamma", 14, 3, None)),
        ("compleanno di Giulia 2 maggio", ("Giulia", 2, 5, None)),
        ("compleanno babbo 5/6/1960", ("babbo", 5, 6, 1960)),
        ("il compleanno di Marco è il 3 aprile", ("Marco", 3, 4, None)),
        ("ricordami il compleanno di zia Rosa il 29 febbraio 1952", ("zia Rosa", 29, 2, 1952)),
        ("compleanno della nonna il 1° dicembre", ("nonna", 1, 12, None)),
        ("compleanno di Anna 12 sett 88", ("Anna", 12, 9, 1988)),
        ("Compleanno di Luca: 7/1/03", ("Luca", 7, 1, 2003)),
    ],
)
def test_frasi_capite(frase, atteso):
    assert leggi_compleanno(frase) == atteso


@pytest.mark.parametrize(
    "frase",
    [
        "compleanno Luca 31/2",  # data impossibile
        "compleanno 14/3",  # di chi?
        "ricordami domani alle 18 di chiamare",
        "45 benzina",
        "compleanno di Paolo",  # quando?
    ],
)
def test_frasi_non_capite(frase):
    assert leggi_compleanno(frase) is None


def test_29_febbraio_negli_anni_non_bisestili():
    voce = Compleanno(1, "Rosa", 29, 2, 1952)
    assert voce.nel(2027) == date(2027, 2, 28) and voce.nel(2028) == date(2028, 2, 29)
    assert voce.prossimo(date(2027, 3, 1)) == date(2028, 2, 29)
    assert voce.anni(date(2028, 2, 29)) == 76


def test_segna_da_testo_libero_e_comando(azdora, messaggero, archivio):
    azdora.gestisci_update(messaggio("ricordami il compleanno della mamma il 14/3"))
    assert "Segnato: compleanno di <b>mamma</b> il 14 marzo" in messaggero.ultimo
    assert "tra 128 giorni" in messaggero.ultimo
    azdora.gestisci_update(messaggio("/compleanno Giulia 8 novembre 1995"))
    assert "domani" not in messaggero.ultimo and "tra 2 giorni, compie 31 anni" in messaggero.ultimo
    azdora.gestisci_update(messaggio("/ricordami il compleanno di babbo il 6/11"))
    assert "(oggi! 🎉)" in messaggero.ultimo
    assert len(RegistroCompleanni(archivio, PADRONE).tutti()) == 3
    # Non diventa un promemoria singolo
    from azdora.promemoria import RegistroPromemoria

    assert RegistroPromemoria(archivio, PADRONE).tutti() == []


def test_stesso_nome_aggiorna_invece_di_duplicare(azdora, archivio):
    azdora.gestisci_update(messaggio("/compleanno Giulia 8/11"))
    azdora.gestisci_update(messaggio("/compleanno giulia 9/11/1995"))
    voci = RegistroCompleanni(archivio, PADRONE).tutti()
    assert len(voci) == 1 and (voci[0].giorno, voci[0].anno) == (9, 1995)


def test_elenco_dal_piu_vicino_e_cancellazione(azdora, messaggero, archivio):
    for frase in ("/compleanno mamma 14/3", "/compleanno Giulia 8/11", "/compleanno babbo 1/1"):
        azdora.gestisci_update(messaggio(frase))
    azdora.gestisci_update(messaggio("/compleanni"))
    testo = messaggero.ultimo
    assert testo.index("Giulia") < testo.index("babbo") < testo.index("mamma")
    assert "8 novembre – Giulia · tra 2 gg" in testo
    pulsanti = [r[0]["callback_data"] for r in messaggero.tastiere[-1]["inline_keyboard"]]
    assert pulsanti == ["cpdel:2", "cpdel:3", "cpdel:1"]
    azdora.gestisci_update(
        {
            "update_id": 5,
            "callback_query": {
                "id": "x",
                "data": "cpdel:2",
                "from": {"id": PADRONE},
                "message": {"message_id": 9, "chat": {"id": PADRONE}},
            },
        }
    )
    assert messaggero.risposte_pulsanti[-1] == ("x", "🗑 Tolto: Giulia")
    assert "Giulia" not in messaggero.modificati[-1][2]


def test_uso_se_manca_qualcosa(azdora, messaggero):
    azdora.gestisci_update(messaggio("/compleanno Paolo"))
    assert "Dimmi di chi e quando" in messaggero.ultimo
    azdora.gestisci_update(messaggio("/compleanni"))
    assert "Nessun compleanno" in messaggero.ultimo


def test_nel_gruppo_le_chiacchiere_non_diventano_compleanni(azdora, messaggero, archivio):
    chiacchiera = messaggio(
        "il compleanno di Giulia è il 2 maggio", chat_id=COPPIA, tipo="group", mittente=PADRONE
    )
    azdora.gestisci_update(chiacchiera)
    assert RegistroCompleanni(archivio, COPPIA).tutti() == []
    richiesta = messaggio(
        "ricordami il compleanno di Giulia il 2 maggio",
        chat_id=COPPIA,
        tipo="group",
        mittente=PADRONE,
    )
    azdora.gestisci_update(richiesta)
    assert len(RegistroCompleanni(archivio, COPPIA).tutti()) == 1


def _avvisi(messaggero, chat=PADRONE):
    return [t for c, t in messaggero.inviati if c == chat and "compleanno di" in t]


def test_avvisi_settimana_prima_giorno_prima_e_giorno_stesso(
    azdora, messaggero, archivio, orologio
):
    RegistroCompleanni(archivio, PADRONE).aggiungi("mamma", 20, 11, 1966)
    for chat in (PADRONE, COPPIA):
        azdora.orari.salva(chat, Orari(mattino=None))
    giorni = {}
    for giorno in range(10, 22):
        orologio.ora = datetime(2026, 11, giorno, 9, 0, tzinfo=FUSO_ORARIO)
        prima = len(messaggero.inviati)
        azdora.controlli()
        azdora.controlli()  # un secondo giro lo stesso giorno non ripete niente
        giorni[giorno] = [t for c, t in messaggero.inviati[prima:] if "compleanno di" in t]
    assert [g for g, testi in giorni.items() if testi] == [13, 19, 20]
    assert "Tra una settimana (20 novembre)" in giorni[13][0] and "regalo" in giorni[13][0]
    assert "Domani è il compleanno di <b>mamma</b> (60 anni)" in giorni[19][0]
    assert "Oggi è il compleanno di <b>mamma</b> (60 anni)! Fai gli auguri" in giorni[20][0]


def test_avvisi_arrivano_col_riepilogo_del_mattino(azdora, messaggero, archivio, orologio):
    RegistroCompleanni(archivio, PADRONE).aggiungi("babbo", 6, 11)
    orologio.ora = datetime(2026, 11, 6, 7, 0, tzinfo=FUSO_ORARIO)
    azdora.controlla_mattino()
    assert len(_avvisi(messaggero)) == 1
    orologio.ora += timedelta(hours=2)
    azdora.controlli()
    assert len(_avvisi(messaggero)) == 1


def test_compleanni_nel_gruppo_arrivano_nel_gruppo(azdora, messaggero, archivio, orologio):
    RegistroCompleanni(archivio, COPPIA).aggiungi("Giulia", 6, 11)
    orologio.ora = datetime(2026, 11, 6, 9, 0, tzinfo=FUSO_ORARIO)
    azdora.controlli()
    assert len(_avvisi(messaggero, COPPIA)) == 1 and _avvisi(messaggero, PADRONE) == []


class TelegramSchizzinoso(FintoMessaggero):
    """Rifiuta i messaggi delle scadenze: i compleanni devono arrivare lo stesso."""

    def invia_messaggio(self, chat_id, testo, tastiera=None):
        if "bollo" in testo.lower():
            raise ErroreTelegram("sendMessage: Telegram non raggiungibile")
        super().invia_messaggio(chat_id, testo, tastiera)


def test_se_le_scadenze_falliscono_i_compleanni_arrivano(impostazioni, meteo, archivio):
    from azdora.scadenze import RegistroScadenze, leggi_scadenza

    telegram = TelegramSchizzinoso()
    adesso = datetime(2026, 11, 6, 9, 0, tzinfo=FUSO_ORARIO)
    bot = Azdora(impostazioni, telegram, meteo, archivio=archivio, orologio=lambda: adesso)
    RegistroScadenze(archivio, PADRONE).aggiungi(leggi_scadenza(("7/11", "bollo"), adesso.date()))
    RegistroCompleanni(archivio, PADRONE).aggiungi("babbo", 6, 11)
    bot.controlli()
    assert len(_avvisi(telegram)) == 1


def test_menu_e_aiuto_hanno_i_compleanni():
    from azdora import messaggi
    from azdora.bot import COMANDI

    assert "compleanni" in {nome for nome, _ in COMANDI}
    assert "compleanno della mamma" in messaggi.AIUTO
