"""Notizie, avvisi di pioggia e allerte arrivano anche nei gruppi, ognuno coi suoi /orari."""

from dataclasses import replace
from datetime import datetime, time, timedelta

import pytest

from azdora.allerte import Allerta
from azdora.bot import Azdora
from azdora.config import FUSO_ORARIO
from azdora.meteo.imminente import Quarto
from azdora.notizie import Notizia
from azdora.orari import Orari, PreferenzeOrari
from tests.conftest import COPPIA, PADRONE, messaggio, posizione

ADESSO = datetime(2026, 11, 6, 15, 5, tzinfo=FUSO_ORARIO)
BABBO_GRUPPO = -333


class Fonti:
    """Notizie, allerte e previsioni a 15 minuti finte, tutte insieme."""

    def __init__(self):
        self.allerte_attive = [
            Allerta("a1", "Emilia e Romagna", 2, "Temporali", ADESSO, ADESSO + timedelta(hours=6))
        ]
        self.pioggia = [0, 0, 0.6, 0.6]

    # notizie
    def notizie_per(self, comune, lat, lon, adesso):
        return []

    def tutte_di_oggi(self, adesso):
        return [
            Notizia(f"Riccione, notizia {i}", f"https://n/{i}", "Altarimini", ADESSO)
            for i in range(3)
        ]

    def del_giorno_in_riviera(self, comune, adesso):
        return self.tutte_di_oggi(adesso)

    def eventi(self, adesso):
        return []

    def principali(self, sezione, adesso):
        return []

    # allerte
    def allerte(self, paese):
        return list(self.allerte_attive)

    # previsioni a 15 minuti
    def quarti(self, lat, lon):
        return [
            Quarto(ADESSO.replace(minute=0) + timedelta(minutes=15 * i), p, 61 if p else 2, 10.0)
            for i, p in enumerate([*self.pioggia, 0, 0, 0, 0, 0])
        ]


@pytest.fixture
def fonti():
    return Fonti()


@pytest.fixture
def azdora(impostazioni, messaggero, meteo, luoghi, archivio, fonti):
    con_gruppi = replace(impostazioni, chat_gruppi=(BABBO_GRUPPO,))
    return Azdora(
        con_gruppi,
        messaggero,
        meteo,
        luoghi,
        archivio=archivio,
        notizie=fonti,
        allerte=fonti,
        imminente=fonti,
        orologio=lambda: ADESSO,
    )


def chat_con(messaggero, parola):
    return {chat for chat, testo in messaggero.inviati if parola in testo}


@pytest.fixture(autouse=True)
def gruppi_accesi(request, archivio):
    """Nella maggior parte dei test i due gruppi hanno acceso tutto con /orari."""
    if "spenti" in request.node.name:
        return
    for gruppo in (COPPIA, BABBO_GRUPPO):
        PreferenzeOrari(archivio).salva(gruppo, Orari())


def test_gruppi_spenti_di_partenza(azdora, messaggero):
    azdora.controlla_notizie()
    azdora.controlla_imminente()
    azdora.controlla_allerte()
    assert {chat for chat, _ in messaggero.inviati} == {PADRONE}  # niente doppioni


def test_orari_vecchi_del_gruppo_restano_spenti(azdora, archivio, messaggero):
    # Salvati da /orari sera 21 prima di questa versione: c'erano notizie e avvisi "accesi"
    archivio.scrivi(
        f"orari:{COPPIA}", {"sera": "21:00", "notizie": ["08:00"], "avvisi_meteo": True}
    )
    orari = azdora.orari.di(COPPIA)
    assert orari.notizie == () and not orari.avvisi_meteo and not orari.allerte
    assert orari.sera.hour == 21


def test_accendi_nel_gruppo_e_spegni_in_privato(azdora, messaggero, archivio):
    PreferenzeOrari(archivio).salva(COPPIA, Orari(notizie=(), avvisi_meteo=False, allerte=False))
    azdora.gestisci_update(
        messaggio("/orari avvisi on", chat_id=COPPIA, tipo="group", mittente=PADRONE)
    )
    pulsante = messaggero.tastiere[-1]["inline_keyboard"][0][0]
    assert pulsante["callback_data"] == "orpriv:avvisi" and "in privato" in pulsante["text"]
    azdora.gestisci_update(
        {
            "update_id": 7,
            "callback_query": {
                "id": "t",
                "data": "orpriv:avvisi",
                "from": {"id": PADRONE},
                "message": {"message_id": 3, "chat": {"id": COPPIA}},
            },
        }
    )
    assert "solo qui nel gruppo" in messaggero.risposte_pulsanti[-1][1]
    assert not azdora.orari.di(PADRONE).avvisi_meteo
    inviati = len(messaggero.inviati)
    azdora.controlla_imminente()
    nuovi = {chat for chat, _ in messaggero.inviati[inviati:]}
    assert COPPIA in nuovi and PADRONE not in nuovi  # una volta sola, nel gruppo


def test_spegnere_nel_gruppo_non_mostra_il_pulsante(azdora, messaggero):
    azdora.gestisci_update(
        messaggio("/orari notizie off", chat_id=COPPIA, tipo="group", mittente=PADRONE)
    )
    assert messaggero.tastiere[-1] is None


def test_notizie_anche_nei_due_gruppi(azdora, messaggero):
    assert azdora.controlla_notizie() == 9  # 3 notizie × (tu + 2 gruppi)
    assert chat_con(messaggero, "notizia 0") == {PADRONE, COPPIA, BABBO_GRUPPO}
    assert azdora.controlla_notizie() == 0  # una volta sola


def test_pioggia_anche_nei_gruppi(azdora, messaggero):
    assert azdora.controlla_imminente() == 3
    assert chat_con(messaggero, "Sta per piovere") == {PADRONE, COPPIA, BABBO_GRUPPO}


def test_allerte_anche_nei_gruppi(azdora, messaggero):
    assert azdora.controlla_allerte() == 3
    assert chat_con(messaggero, "Temporali") == {PADRONE, COPPIA, BABBO_GRUPPO}


def test_ogni_gruppo_li_spegne_dai_suoi_orari(azdora, messaggero, archivio):
    gruppo = messaggio("/orari notizie off", chat_id=BABBO_GRUPPO, tipo="group", mittente=PADRONE)
    azdora.gestisci_update(gruppo)
    assert "Gli orari di questo gruppo" in messaggero.ultimo and "spente" in messaggero.ultimo
    for comando in ("/orari avvisi off", "/orari allerte off"):
        azdora.gestisci_update(replace_testo(gruppo, comando))
    assert "Allerte ufficiali: <b>spente</b>" in messaggero.ultimo
    azdora.controlla_notizie()
    azdora.controlla_imminente()
    azdora.controlla_allerte()
    destinatari = {chat for chat, _ in messaggero.inviati[4:]}
    assert BABBO_GRUPPO not in destinatari and {PADRONE, COPPIA} <= destinatari


def replace_testo(update, testo):
    nuovo = {**update, "message": {**update["message"], "text": testo}}
    return nuovo


def test_silenzio_del_gruppo(azdora, messaggero, archivio):
    PreferenzeOrari(archivio).salva(COPPIA, Orari(silenzio_da=None, silenzio_a=None))
    # Il gruppo col babbo ha il silenzio dalle 14 alle 18: alle 15 niente avvisi lì.
    PreferenzeOrari(archivio).salva(BABBO_GRUPPO, Orari(silenzio_da=time(14), silenzio_a=time(18)))
    azdora.controlla_imminente()
    assert chat_con(messaggero, "Sta per piovere") == {PADRONE, COPPIA}


def test_gruppo_usa_la_sua_posizione_o_la_tua(azdora, archivio, luoghi):
    assert azdora.zona(COPPIA).comune == "Riccione"  # nessuna posizione: casa
    azdora.gestisci_update(posizione(43.97, 12.70))  # tu sei a Misano
    assert azdora.zona(COPPIA).comune == "Misano Adriatico"  # il gruppo segue te
    luoghi.luogo = luoghi.luogo.__class__("Bologna", "BO", "Emilia-Romagna", "it", "Italia")
    azdora.gestisci_update(posizione(44.49, 11.34, chat_id=BABBO_GRUPPO, tipo="group"))
    assert azdora.zona(BABBO_GRUPPO).comune == "Bologna"  # posizione condivisa nel gruppo
    assert azdora.zona(COPPIA).comune == "Misano Adriatico"


def test_un_gruppo_rotto_non_ferma_gli_altri(azdora, messaggero, monkeypatch):
    originale = azdora.messaggero.invia_messaggio

    def invia(chat_id, testo, tastiera=None):
        if chat_id == COPPIA:
            raise RuntimeError("dati strani")
        originale(chat_id, testo, tastiera)

    monkeypatch.setattr(azdora.messaggero, "invia_messaggio", invia)
    azdora.controlla_allerte()
    assert chat_con(messaggero, "Temporali") == {PADRONE, BABBO_GRUPPO}
