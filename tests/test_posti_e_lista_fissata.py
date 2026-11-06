"""Lista fissata in alto nella chat di coppia e promemoria "quando arrivo a…"."""

from datetime import datetime

import pytest

from azdora.bot import Azdora
from azdora.config import FUSO_ORARIO
from azdora.posti import (
    PostiSalvati,
    PromemoriaLuogo,
    RegistroLuoghi,
    a_posto,
    leggi_richiesta,
)
from azdora.telegram import ErroreTelegram
from tests.conftest import COPPIA, PADRONE, FintoMessaggero, messaggio, posizione

ADESSO = datetime(2026, 11, 6, 10, 0, tzinfo=FUSO_ORARIO)
RIMINI = (44.0594, 12.5683)
RICCIONE_CENTRO = (43.9998, 12.6563)
CESENA = (44.1391, 12.2431)


class TelegramConFissa(FintoMessaggero):
    def __init__(self, permesso=True):
        super().__init__()
        self.permesso = permesso
        self.fissati: list[tuple[int, int]] = []
        self.sganciati: list[tuple[int, int]] = []
        self.prossimo_id = 500
        self.cancellati: set[int] = set()

    def invia_e_fissa(self, chat_id, testo, tastiera=None):
        self.invia_messaggio(chat_id, testo, tastiera)
        self.prossimo_id += 1
        if self.permesso:
            self.fissati.append((chat_id, self.prossimo_id))
        return self.prossimo_id, self.permesso

    def sgancia(self, chat_id, id_messaggio):
        self.sganciati.append((chat_id, id_messaggio))

    def modifica_messaggio(self, chat_id, id_messaggio, testo, tastiera=None):
        if id_messaggio in self.cancellati:
            raise ErroreTelegram("editMessageText: Bad Request: message to edit not found")
        super().modifica_messaggio(chat_id, id_messaggio, testo, tastiera)


@pytest.fixture
def telegram():
    return TelegramConFissa()


@pytest.fixture
def azdora(impostazioni, telegram, meteo, luoghi, archivio):
    return Azdora(impostazioni, telegram, meteo, luoghi, archivio=archivio, orologio=lambda: ADESSO)


def gruppo(testo, mittente=PADRONE):
    return messaggio(testo, chat_id=COPPIA, tipo="supergroup", mittente=mittente)


def tocco(dati, id_messaggio, chat_id=COPPIA):
    return {
        "update_id": 9,
        "callback_query": {
            "id": "t1",
            "data": dati,
            "from": {"id": PADRONE},
            "message": {"message_id": id_messaggio, "chat": {"id": chat_id}},
        },
    }


# --- Lista fissata -----------------------------------------------------------------------


def test_lista_fissata_in_alto_e_aggiornata_da_sola(azdora, telegram):
    azdora.gestisci_update(gruppo("/aggiungi latte, pane"))
    azdora.gestisci_update(gruppo("/lista fissa"))
    assert telegram.fissati == [(COPPIA, 501)]
    assert "📌 <b>Lista</b>" in telegram.ultimo and "Si aggiorna da sola" in telegram.ultimo

    inviati_prima = len(telegram.inviati)
    azdora.gestisci_update(gruppo("/aggiungi uova", mittente=7))
    # In chat solo una conferma corta; la lista in alto cambia.
    assert len(telegram.inviati) == inviati_prima + 1
    assert "Aggiunto: uova" in telegram.ultimo
    chat, id_messaggio, testo, tastiera = telegram.modificati[-1]
    assert (chat, id_messaggio) == (COPPIA, 501) and "uova" in testo
    assert len(tastiera["inline_keyboard"]) == 3


def test_spunta_da_un_altro_messaggio_aggiorna_anche_quella_fissata(azdora, telegram):
    azdora.gestisci_update(gruppo("/aggiungi latte"))
    azdora.gestisci_update(gruppo("/lista fissa"))
    azdora.gestisci_update(tocco("voce:1", id_messaggio=42))  # la vecchia lista in chat
    modificati = {m[1]: m[2] for m in telegram.modificati}
    assert "<s>latte</s>" in modificati[42] and "<s>latte</s>" in modificati[501]
    assert "📌" in modificati[501] and "📌" not in modificati[42]


def test_spunta_sulla_lista_fissata_non_la_modifica_due_volte(azdora, telegram):
    azdora.gestisci_update(gruppo("/aggiungi latte"))
    azdora.gestisci_update(gruppo("/lista fissa"))
    azdora.gestisci_update(tocco("voce:1", id_messaggio=501))
    assert [m[1] for m in telegram.modificati] == [501]
    assert "📌" in telegram.modificati[0][2]


def test_senza_permesso_spiega_come_fissarla(impostazioni, meteo, archivio):
    telegram = TelegramConFissa(permesso=False)
    bot = Azdora(impostazioni, telegram, meteo, archivio=archivio, orologio=lambda: ADESSO)
    bot.gestisci_update(gruppo("/lista fissa"))
    assert "non ho il permesso" in telegram.ultimo
    bot.gestisci_update(gruppo("/aggiungi pane"))  # si aggiorna lo stesso, se la fissi a mano
    assert telegram.modificati[-1][1] == 501


def test_lista_fissata_cancellata_a_mano(azdora, telegram, archivio):
    azdora.gestisci_update(gruppo("/lista fissa"))
    telegram.cancellati.add(501)
    azdora.gestisci_update(gruppo("/aggiungi pane"))
    assert archivio.leggi(f"lista-fissata:{COPPIA}") is None
    assert "📝 <b>Lista</b>" in telegram.ultimo  # torna a mandare la lista intera


def test_sgancia_e_rifissa(azdora, telegram, archivio):
    azdora.gestisci_update(gruppo("/fissa"))
    azdora.gestisci_update(gruppo("/fissa"))  # una sola lista fissata per volta
    assert telegram.sganciati == [(COPPIA, 501)]
    azdora.gestisci_update(gruppo("/lista sgancia"))
    assert telegram.sganciati[-1] == (COPPIA, 502)
    assert archivio.leggi(f"lista-fissata:{COPPIA}") is None
    assert "non è più fissata" in telegram.ultimo


def test_svuota_aggiorna_la_lista_fissata(azdora, telegram):
    azdora.gestisci_update(gruppo("/aggiungi latte, pane"))
    azdora.gestisci_update(gruppo("/lista fissa"))
    azdora.gestisci_update(tocco("voce:1", id_messaggio=501))
    azdora.gestisci_update(gruppo("/svuota"))
    assert "latte" not in telegram.modificati[-1][2] and "pane" in telegram.modificati[-1][2]


def test_senza_fissa_nel_messaggero(bot, messaggero):
    bot.gestisci_update(messaggio("/lista fissa"))
    assert "non posso fissare" in messaggero.ultimo


# --- Lettura delle frasi -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("frase", "cosa", "posto"),
    [
        ("ricordami di comprare il pane quando sono a Rimini", "comprare il pane", "Rimini"),
        ("quando torno a casa ricordami di portare fuori la plastica", "portare fuori la plastica", "casa"),  # noqa: E501
        ("ricordami quando arrivo a Cesena di chiamare Marco", "chiamare Marco", "Cesena"),
        ("Ricordami di prendere le chiavi quando arrivo al lavoro.", "prendere le chiavi", "lavoro"),  # noqa: E501
        ("quando arrivo dalla mamma ricordami che le devo dare il vaso", "le devo dare il vaso", "mamma"),  # noqa: E501
        ("ricordami di fare benzina quando passo da Santarcangelo di Romagna", "fare benzina", "Santarcangelo di Romagna"),  # noqa: E501
        ("ricordami quando arrivo all'Esselunga di prendere il latte", "prendere il latte", "Esselunga"),  # noqa: E501
    ],
)  # fmt: skip
def test_frasi_con_un_posto(frase, cosa, posto):
    richiesta = leggi_richiesta(frase)
    assert richiesta is not None and (richiesta.cosa, richiesta.posto) == (cosa, posto)


@pytest.mark.parametrize(
    "frase",
    ["ricordami domani alle 18 di chiamare", "quando arrivo a Rimini ti scrivo", "45 benzina"],
)
def test_frasi_senza_posto(frase):
    assert leggi_richiesta(frase) is None


def test_preposizioni_giuste():
    assert a_posto("lavoro") == "al lavoro" and a_posto("mamma") == "da mamma"
    assert a_posto("palestra") == "alla palestra" and a_posto("Rimini") == "a Rimini"


# --- Promemoria legati a un posto ---------------------------------------------------------


def test_promemoria_scatta_quando_arrivi(azdora, telegram, luoghi):
    luoghi.luogo = None
    azdora.gestisci_update(posizione(*CESENA))  # sei a Cesena
    azdora.gestisci_update(
        messaggio("ricordami di comprare il pane quando sono a Misano Adriatico")
    )
    assert "quando arrivi a Misano Adriatico" in telegram.ultimo
    assert "posizione in tempo reale" in telegram.ultimo  # non c'è la posizione automatica

    inviati = len(telegram.inviati)
    azdora.salva_posizione(PADRONE, *RIMINI)  # ancora lontano (più di 3 km)
    assert len(telegram.inviati) == inviati
    azdora.salva_posizione(PADRONE, 43.9790, 12.7000)  # dentro Misano
    assert "Sei a Misano Adriatico" in telegram.ultimo and "comprare il pane" in telegram.ultimo
    assert telegram.tastiere[-1]["inline_keyboard"][0][0]["callback_data"] == f"plrip:{PADRONE}:1"
    azdora.salva_posizione(PADRONE, 43.9791, 12.7001)  # una volta sola
    assert len([t for _, t in telegram.inviati if "Sei a Misano" in t]) == 1


def test_se_ci_sei_gia_aspetta_che_esci_e_rientri(azdora, telegram, archivio):
    azdora.salva_posizione(PADRONE, *RICCIONE_CENTRO)
    azdora.gestisci_update(messaggio("quando torno a casa ricordami di portare fuori la plastica"))
    assert "Adesso ci sei già" in telegram.ultimo
    assert "posizione in tempo reale" not in telegram.ultimo  # arriva già da sola
    azdora.salva_posizione(PADRONE, *RICCIONE_CENTRO)
    assert not any("Sei a casa" in t for _, t in telegram.inviati)
    azdora.salva_posizione(PADRONE, *RIMINI)  # esci
    azdora.salva_posizione(PADRONE, *RICCIONE_CENTRO)  # rientri
    assert "Sei a casa" in telegram.ultimo


def test_anche_la_prossima_volta(azdora, telegram, archivio):
    azdora.gestisci_update(messaggio("ricordami di salutare quando arrivo a casa"))
    azdora.salva_posizione(PADRONE, *RICCIONE_CENTRO)
    assert RegistroLuoghi(archivio, PADRONE).tutti() == []
    azdora.gestisci_update(tocco(f"plrip:{PADRONE}:1", id_messaggio=77, chat_id=PADRONE))
    assert "prossima volta" in telegram.risposte_pulsanti[-1][1]
    attivi = RegistroLuoghi(archivio, PADRONE).tutti()
    assert len(attivi) == 1 and attivi[0].dentro  # ci sei: scatta al prossimo arrivo
    azdora.gestisci_update(tocco(f"plrip:{PADRONE}:1", id_messaggio=77, chat_id=PADRONE))
    assert "già attivo" in telegram.risposte_pulsanti[-1][1]


def test_posto_salvato_con_nome(azdora, telegram, archivio):
    azdora.gestisci_update(messaggio("/posto lavoro"))
    assert "mandami la posizione" in telegram.ultimo
    azdora.gestisci_update(posizione(44.0010, 12.6500))
    azdora.gestisci_update(messaggio("/posto lavoro"))
    assert "Salvato «lavoro»" in telegram.ultimo and "al lavoro" in telegram.ultimo
    azdora.gestisci_update(messaggio("ricordami di timbrare quando arrivo al lavoro"))
    voce = RegistroLuoghi(archivio, PADRONE).tutti()[0]
    assert voce.posto == "lavoro" and voce.raggio_km == 0.3 and voce.dentro
    azdora.salva_posizione(PADRONE, 44.0100, 12.6500)  # 1 km più in là: fuori
    azdora.salva_posizione(PADRONE, 44.0011, 12.6501)
    assert "Sei al lavoro" in telegram.ultimo and "timbrare" in telegram.ultimo
    azdora.gestisci_update(messaggio("/posto"))
    assert "• lavoro" in telegram.ultimo
    azdora.gestisci_update(messaggio("/posto lavoro off"))
    assert "Tolto «lavoro»" in telegram.ultimo
    assert PostiSalvati(archivio, PADRONE).tutti() == []


def test_posto_sconosciuto(azdora, telegram):
    azdora.gestisci_update(messaggio("ricordami di comprare i fiori quando arrivo dal fioraio"))
    assert "Non so dov'è «fioraio»" in telegram.ultimo
    assert "/posto fioraio" in telegram.ultimo


def test_nel_gruppo_segue_chi_lo_scrive_e_risponde_nel_gruppo(azdora, telegram, archivio):
    azdora.gestisci_update(gruppo("ricordami di prendere il pane quando arrivo a casa"))
    voce = RegistroLuoghi(archivio, PADRONE).tutti()[0]
    assert voce.chat == COPPIA
    azdora.salva_posizione(PADRONE, *RICCIONE_CENTRO)
    assert telegram.inviati[-1][0] == COPPIA and "Sei a casa" in telegram.inviati[-1][1]


def test_comando_ricordami_con_posto(azdora, telegram, archivio):
    azdora.gestisci_update(messaggio("/ricordami di chiamare quando arrivo a casa"))
    assert len(RegistroLuoghi(archivio, PADRONE).tutti()) == 1


def test_elenco_e_cancellazione(azdora, telegram, archivio):
    azdora.gestisci_update(messaggio("ricordami domani alle 18 di chiamare il meccanico"))
    azdora.gestisci_update(messaggio("ricordami di comprare il pane quando arrivo a casa"))
    azdora.gestisci_update(messaggio("/promemoria"))
    assert "Quando arrivi in un posto" in telegram.ultimo and "meccanico" in telegram.ultimo
    pulsanti = [p[0]["callback_data"] for p in telegram.tastiere[-1]["inline_keyboard"]]
    assert pulsanti == ["prdel:1", f"pldel:{PADRONE}:1"]
    azdora.gestisci_update(tocco(f"pldel:{PADRONE}:1", id_messaggio=88, chat_id=PADRONE))
    assert RegistroLuoghi(archivio, PADRONE).tutti() == []
    assert "Quando arrivi" not in telegram.modificati[-1][2]


def test_promemoria_luogo_non_consegnato_non_rompe_la_posizione(azdora, archivio):
    RegistroLuoghi(archivio, PADRONE).aggiungi(
        PromemoriaLuogo(0, "x", "casa", *RICCIONE_CENTRO, 2.0, chat=12345)
    )

    def rotto(*argomenti):
        raise ErroreTelegram("sendMessage: Forbidden")

    azdora.messaggero.invia_messaggio = rotto
    assert azdora.salva_posizione(PADRONE, *RICCIONE_CENTRO) is not None
