"""Tappa 2: posizione ricordata, notizie e allerte della zona, controlli automatici."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest

from azdora import messaggi
from azdora.allerte import Allerta
from azdora.archivio import ArchivioMemoria, ErroreArchivio
from azdora.bot import Azdora
from azdora.notizie import Notizia
from azdora.posizione import ZONA_CASA, MemoriaPosizione, Posizione
from tests.conftest import (
    ADESSO,
    BOLOGNA,
    COPPIA,
    PADRONE,
    SCONOSCIUTO,
    messaggio,
    posizione,
)

MISANO_XY = (43.978, 12.697)
BOLOGNA_XY = (44.494, 11.343)


@dataclass
class FinteNotizie:
    notizie: list[Notizia] = field(default_factory=list)
    del_giorno: list[Notizia] = field(default_factory=list)
    richieste: list[str] = field(default_factory=list)
    richieste_giorno: list[str] = field(default_factory=list)

    def notizie_per(self, comune, lat, lon, adesso):
        self.richieste.append(comune)
        return list(self.notizie)

    def del_giorno_in_riviera(self, comune, adesso):
        self.richieste_giorno.append(comune)
        return list(self.del_giorno)

    def eventi(self, adesso):
        return []


@dataclass
class FinteAllerte:
    allerte_attive: list[Allerta] = field(default_factory=list)

    def allerte(self, paese):
        return list(self.allerte_attive) if paese == "it" else []


def notizia(n: int) -> Notizia:
    return Notizia(f"Notizia numero {n}", f"https://esempio.it/{n}", "Rimini Today", ADESSO)


def allerta(livello=2, regione="Emilia e Romagna", ident="a1", ore=10) -> Allerta:
    return Allerta(ident, regione, livello, "Temporali", ADESSO, ADESSO + timedelta(hours=ore))


@pytest.fixture
def notizie() -> FinteNotizie:
    return FinteNotizie()


@pytest.fixture
def allerte() -> FinteAllerte:
    return FinteAllerte()


@pytest.fixture
def orologio():
    class Orologio:
        adesso = ADESSO

        def __call__(self):
            return self.adesso

    return Orologio()


@pytest.fixture
def bot(impostazioni, messaggero, meteo, luoghi, archivio, notizie, allerte, orologio) -> Azdora:
    return Azdora(
        impostazioni,
        messaggero,
        meteo,
        luoghi,
        archivio=archivio,
        notizie=notizie,
        allerte=allerte,
        orologio=orologio,
    )


def _aggiornamento_live(lat, lon, chat_id=PADRONE):
    return {
        "update_id": 9,
        "edited_message": {
            "message_id": 3,
            "chat": {"id": chat_id, "type": "private"},
            "location": {"latitude": lat, "longitude": lon, "live_period": 3600},
        },
    }


# --- Posizione ricordata -----------------------------------------------------------------


def test_la_posizione_viene_ricordata(bot, messaggero, archivio):
    bot.gestisci_update(posizione(*MISANO_XY))
    salvata = MemoriaPosizione(archivio).leggi(PADRONE)
    assert salvata.comune == "Misano Adriatico"
    assert salvata.regione == "Emilia-Romagna"
    assert salvata.quando == ADESSO
    assert not salvata.in_tempo_reale
    assert messaggi.POSIZIONE_RICORDATA in messaggero.ultimo


def test_posizione_in_tempo_reale(bot, messaggero, archivio):
    update = posizione(*MISANO_XY)
    update["message"]["location"]["live_period"] = 3600
    bot.gestisci_update(update)
    assert MemoriaPosizione(archivio).leggi(PADRONE).in_tempo_reale
    assert messaggi.POSIZIONE_LIVE in messaggero.ultimo


def test_aggiornamenti_live_salvati_in_silenzio(bot, messaggero, archivio, luoghi):
    bot.gestisci_update(posizione(*MISANO_XY))
    inviati = len(messaggero.inviati)
    luoghi.luogo = BOLOGNA
    bot.gestisci_update(_aggiornamento_live(*BOLOGNA_XY))
    assert len(messaggero.inviati) == inviati
    salvata = MemoriaPosizione(archivio).leggi(PADRONE)
    assert (salvata.comune, salvata.in_tempo_reale) == ("Bologna", True)


def test_piccoli_spostamenti_live_non_richiedono_il_nome(bot, archivio, luoghi):
    bot.gestisci_update(posizione(*MISANO_XY))
    luoghi.luogo = BOLOGNA  # se venisse chiesto, il nome cambierebbe
    bot.gestisci_update(_aggiornamento_live(MISANO_XY[0] + 0.001, MISANO_XY[1]))
    assert MemoriaPosizione(archivio).leggi(PADRONE).comune == "Misano Adriatico"


def test_aggiornamenti_live_da_sconosciuti_ignorati(bot, archivio):
    bot.gestisci_update(_aggiornamento_live(*BOLOGNA_XY, chat_id=SCONOSCIUTO))
    assert [k for k in archivio.chiavi() if not k.startswith("orari:")] == []


def test_messaggi_modificati_senza_posizione_ignorati(bot, messaggero):
    bot.gestisci_update({"edited_message": {"chat": {"id": PADRONE}, "text": "ops"}})
    assert messaggero.inviati == []


def test_dove_e_dimentica(bot, messaggero):
    bot.gestisci_update(messaggio("/dove"))
    assert messaggero.ultimo == messaggi.NESSUNA_POSIZIONE
    bot.gestisci_update(posizione(*MISANO_XY))
    bot.gestisci_update(messaggio("/dove"))
    assert "Misano Adriatico (RN)" in messaggero.ultimo
    assert "gio 5/11 alle 07:00" in messaggero.ultimo
    bot.gestisci_update(messaggio("/dimentica"))
    assert messaggero.ultimo == messaggi.POSIZIONE_DIMENTICATA
    bot.gestisci_update(messaggio("/dove"))
    assert messaggero.ultimo == messaggi.NESSUNA_POSIZIONE


def test_dove_con_posizione_vecchia(bot, messaggero, orologio):
    bot.gestisci_update(posizione(*MISANO_XY))
    orologio.adesso = ADESSO + timedelta(days=4)
    bot.gestisci_update(messaggio("/dove"))
    assert "troppo tempo" in messaggero.ultimo


def test_zona_casa_poi_posizione_poi_scadenza(bot, orologio):
    assert bot.zona() == ZONA_CASA
    bot.gestisci_update(posizione(*MISANO_XY))
    zona = bot.zona()
    assert (zona.comune, zona.etichetta, zona.da_posizione) == (
        "Misano Adriatico",
        "Misano Adriatico (RN)",
        True,
    )
    orologio.adesso = ADESSO + timedelta(days=3, minutes=1)
    assert bot.zona() == ZONA_CASA


def test_posizione_senza_nome_non_cambia_zona(bot, luoghi):
    luoghi.luogo = None
    bot.gestisci_update(posizione(*MISANO_XY))
    assert bot.zona() == ZONA_CASA


def test_posizione_serializzata_e_riletta():
    p = Posizione(44.0, 12.6, ADESSO, "Rimini", "RN", "Emilia-Romagna", "it", True)
    assert Posizione.da_dizionario(p.in_dizionario()) == p
    assert Posizione(44.0, 12.6, ADESSO).etichetta == "44.000, 12.600"


class ArchivioGuasto(ArchivioMemoria):
    def leggi(self, chiave):
        raise ErroreArchivio("giù")

    def scrivi(self, chiave, valore, scadenza_s=None):
        raise ErroreArchivio("giù")


def test_archivio_giu_avvisa(impostazioni, messaggero, meteo, luoghi):
    bot = Azdora(impostazioni, messaggero, meteo, luoghi, archivio=ArchivioGuasto())
    bot.gestisci_update(messaggio("/dove"))
    assert messaggero.ultimo == messaggi.ARCHIVIO_NON_DISPONIBILE
    bot.gestisci_update(_aggiornamento_live(*BOLOGNA_XY))  # non solleva errori


def test_riepilogo_mattutino_anche_con_archivio_giu(impostazioni, messaggero, meteo):
    bot = Azdora(
        impostazioni, messaggero, meteo, archivio=ArchivioGuasto(), orologio=lambda: ADESSO
    )
    bot.invia_riepilogo_mattutino()
    assert "Buongiorno" in messaggero.ultimo


# --- Notizie -------------------------------------------------------------------------------


def test_notizie_della_zona_di_casa(bot, messaggero, notizie):
    notizie.notizie = [notizia(i) for i in range(12)]
    bot.gestisci_update(messaggio("/notizie"))
    assert notizie.richieste_giorno == ["Riccione"]  # prima le notizie del giorno…
    assert notizie.richieste == ["Riccione"]  # …che qui mancano: allora quelle del comune
    assert "Ultime notizie da Riccione" in messaggero.ultimo
    assert messaggero.ultimo.count("📰") == 10


def test_notizie_seguono_la_posizione(bot, messaggero, notizie):
    notizie.notizie = [notizia(1)]
    bot.gestisci_update(posizione(*MISANO_XY))
    bot.gestisci_update(messaggio("/notizie"))
    assert notizie.richieste == ["Misano Adriatico"]


def test_notizie_di_un_comune_scritto(bot, notizie, messaggero):
    bot.gestisci_update(messaggio("/notizie bologna"))
    assert notizie.richieste == ["Bologna"]
    assert "Nessuna notizia" in messaggero.ultimo


def test_notizie_nel_gruppo_usano_la_zona_del_padrone(bot, notizie):
    bot.gestisci_update(posizione(*MISANO_XY))
    bot.gestisci_update(messaggio("/notizie", chat_id=COPPIA, tipo="group", mittente=7))
    assert notizie.richieste == ["Misano Adriatico"]


def test_notizie_comune_sconosciuto(bot, notizie, messaggero):
    bot.gestisci_update(messaggio("/notizie paperopoli"))
    assert notizie.richieste == []
    assert "Non trovo" in messaggero.ultimo


def test_notizie_senza_fonte(impostazioni, messaggero, meteo):
    bot = Azdora(impostazioni, messaggero, meteo)
    bot.gestisci_update(messaggio("/notizie"))
    assert "Nessuna notizia" in messaggero.ultimo


def test_controllo_notizie_prima_volta_poche_poi_solo_nuove(bot, messaggero, notizie):
    notizie.notizie = [notizia(i) for i in range(10)]
    assert bot.controlla_notizie() == 3  # zona nuova: niente arretrato
    assert "Novità da Riccione" in messaggero.ultimo
    assert bot.controlla_notizie() == 0  # già viste
    notizie.notizie = [notizia(100), notizia(101), *notizie.notizie]
    assert bot.controlla_notizie() == 2
    assert "Notizia numero 100" in messaggero.ultimo
    assert "Notizia numero 1<" not in messaggero.ultimo


def test_controllo_notizie_al_massimo_cinque(bot, notizie):
    bot.controlla_notizie()  # la zona non è più nuova
    notizie.notizie = [notizia(i) for i in range(20)]
    assert bot.controlla_notizie() == 5


def test_controlli_senza_memoria_non_mandano_nulla(impostazioni, messaggero, meteo, notizie):
    notizie.notizie = [notizia(1)]
    bot = Azdora(impostazioni, messaggero, meteo, notizie=notizie, allerte=FinteAllerte())
    assert bot.controlla_notizie() == 0
    assert bot.controlla_allerte() == 0
    assert messaggero.inviati == []


# --- Allerte -------------------------------------------------------------------------------


def test_comando_allerte_nessuna(bot, messaggero):
    bot.gestisci_update(messaggio("/allerte"))
    assert messaggero.ultimo == messaggi.nessuna_allerta("Emilia-Romagna")


def test_comando_allerte_attive(bot, messaggero, allerte):
    allerte.allerte_attive = [allerta(3)]
    bot.gestisci_update(messaggio("/allerte"))
    assert "Allerta arancione: Temporali" in messaggero.ultimo
    assert "Allerta Meteo Emilia-Romagna" in messaggero.ultimo


def test_comando_allerte_senza_fonte(impostazioni, messaggero, meteo):
    Azdora(impostazioni, messaggero, meteo).gestisci_update(messaggio("/allerte"))
    assert messaggero.ultimo == messaggi.ALLERTE_NON_DISPONIBILI


def test_controllo_allerte_una_volta_sola_e_quando_sale_di_livello(bot, messaggero, allerte):
    allerte.allerte_attive = [allerta(2)]
    assert bot.controlla_allerte() == 1
    assert "Allerta gialla" in messaggero.ultimo
    assert bot.controlla_allerte() == 0
    allerte.allerte_attive = [allerta(3)]
    assert bot.controlla_allerte() == 1
    assert "Allerta arancione" in messaggero.ultimo


def test_controllo_allerte_segue_la_regione(bot, allerte, luoghi):
    allerte.allerte_attive = [allerta(3, regione="Lombardia")]
    assert bot.controlla_allerte() == 0
    luoghi.luogo = luoghi.luogo.__class__("Milano", "MI", "Lombardia", "it", "Italia")
    bot.gestisci_update(posizione(45.46, 9.19))
    assert bot.controlla_allerte() == 1


def test_controlli_completi(bot, messaggero, notizie, allerte):
    notizie.notizie = [notizia(1)]
    allerte.allerte_attive = [allerta(4)]
    bot.controlli()  # alle 7: riepilogo del mattino (con le notizie), poi le allerte
    testi = [t for _, t in messaggero.inviati]
    riepilogo = next(t for t in testi if "Buongiorno" in t)
    assert "Notizia numero 1" in riepilogo  # le prime notizie stanno nel riepilogo
    assert any("Allerta rossa" in t for t in testi)
    assert not any("Novità da Riccione" in t for t in testi)  # già date: niente doppioni


def test_controlli_resistono_ai_guasti(impostazioni, messaggero, meteo, notizie):
    bot = Azdora(impostazioni, messaggero, meteo, archivio=ArchivioGuasto(), notizie=notizie)
    ArchivioGuasto.persistente = True
    try:
        bot.controlli()  # nessuna eccezione
    finally:
        ArchivioGuasto.persistente = False


# --- Riepilogo mattutino con zona e allerte -------------------------------------------------


def test_riepilogo_con_allerte(bot, messaggero, allerte):
    allerte.allerte_attive = [allerta(2)]
    bot.invia_riepilogo_mattutino()
    assert "Allerta gialla: Temporali" in messaggero.ultimo


def test_riepilogo_lontano_da_casa_aggiunge_dove_sei(bot, messaggero, luoghi):
    luoghi.luogo = BOLOGNA
    bot.gestisci_update(posizione(*BOLOGNA_XY))
    bot.invia_riepilogo_mattutino()
    testo = messaggero.ultimo
    assert testo.index("Bologna (BO)") < testo.index("Riccione") < testo.index("Rimini")


def test_riepilogo_vicino_a_casa_non_ripete(bot, messaggero):
    bot.gestisci_update(posizione(*MISANO_XY))  # Misano è a pochi km da Riccione
    bot.invia_riepilogo_mattutino()
    assert "Misano" not in messaggero.ultimo


def test_chat_personale_mancante(messaggero, meteo, notizie):
    from azdora.config import Impostazioni

    bot = Azdora(Impostazioni(token="x"), messaggero, meteo, notizie=notizie)
    with pytest.raises(RuntimeError, match="TELEGRAM_CHAT_ID"):
        bot.controlla_notizie()


def test_ora_italiana_nei_dati_di_prova():
    assert ADESSO.tzinfo is not None and ADESSO.utcoffset() == timedelta(hours=1)
    assert isinstance(ADESSO, datetime)


# --- I comandi senza località seguono la posizione ------------------------------------------


def test_meteo_senza_localita_usa_dove_sei(bot, messaggero, luoghi):
    luoghi.luogo = BOLOGNA
    bot.gestisci_update(posizione(*BOLOGNA_XY))
    for comando, titolo in [("/meteo", "Meteo a Bologna (BO)"), ("/vento", "Vento a Bologna (BO)")]:
        bot.gestisci_update(messaggio(comando))
        assert titolo in messaggero.ultimo
    bot.gestisci_update(messaggio("/mare"))
    assert "Bologna (BO) è lontano dal mare" in messaggero.ultimo


def test_localita_scritta_vince_sulla_posizione(bot, messaggero, luoghi):
    luoghi.luogo = BOLOGNA
    bot.gestisci_update(posizione(*BOLOGNA_XY))
    bot.gestisci_update(messaggio("/meteo rimini"))
    assert "Meteo a Rimini" in messaggero.ultimo


def test_senza_posizione_si_torna_a_casa(bot, messaggero, orologio, luoghi):
    luoghi.luogo = BOLOGNA
    bot.gestisci_update(posizione(*BOLOGNA_XY))
    orologio.adesso = ADESSO + timedelta(days=4)
    bot.gestisci_update(messaggio("/meteo"))
    assert "Meteo a Riccione" in messaggero.ultimo


def test_il_gruppo_usa_la_posizione_del_padrone(bot, messaggero, luoghi):
    luoghi.luogo = BOLOGNA
    bot.gestisci_update(posizione(*BOLOGNA_XY))
    bot.gestisci_update(messaggio("/vento", chat_id=COPPIA, tipo="group", mittente=7))
    assert "Vento a Bologna (BO)" in messaggero.ultimo


def test_oggi_e_domani_lontano_da_casa(bot, messaggero, luoghi):
    luoghi.luogo = BOLOGNA
    bot.gestisci_update(posizione(*BOLOGNA_XY))
    bot.gestisci_update(messaggio("/oggi"))
    assert messaggero.ultimo.index("Bologna (BO)") < messaggero.ultimo.index("Riccione")
    bot.gestisci_update(messaggio("/domani"))
    assert messaggero.ultimo.index("Bologna (BO)") < messaggero.ultimo.index("Riccione")


def test_comandi_con_archivio_giu_usano_casa(impostazioni, messaggero, meteo):
    bot = Azdora(
        impostazioni, messaggero, meteo, archivio=ArchivioGuasto(), orologio=lambda: ADESSO
    )
    bot.gestisci_update(messaggio("/meteo"))
    assert "Meteo a Riccione" in messaggero.ultimo


def test_notizie_del_giorno_in_riviera(bot, messaggero, notizie):
    notizie.del_giorno = [
        Notizia("A Riccione nuova piazza", "https://a.it/1", "Altarimini", ADESSO),
        Notizia("Rimini, lavori sul lungomare", "https://a.it/2", "il Resto del Carlino", ADESSO),
    ]
    bot.gestisci_update(messaggio("/notizie"))
    testo = messaggero.ultimo
    assert testo.startswith("🗞️ <b>Le notizie di oggi</b>")
    assert "Cervia a Pesaro" not in testo
    assert testo.index("📍 <b>Riccione</b>") < testo.index("🎡 <b>Rimini</b>")
    assert '<a href="https://a.it/1">' in testo and '<a href="https://a.it/2">' in testo
    assert notizie.richieste == []  # non serve la ricerca per comune


def test_notizie_con_localita_scritta_restano_filtrate(bot, notizie):
    notizie.del_giorno = [notizia(1)]
    bot.gestisci_update(messaggio("/notizie rimini"))
    assert notizie.richieste_giorno == []
    assert notizie.richieste == ["Rimini"]


def test_fuori_riviera_niente_giornali_locali(bot, notizie, luoghi):
    notizie.del_giorno = [notizia(1)]
    luoghi.luogo = BOLOGNA
    bot.gestisci_update(posizione(*BOLOGNA_XY))
    bot.gestisci_update(messaggio("/notizie"))
    assert notizie.richieste_giorno == []
    assert notizie.richieste == ["Bologna"]


def test_notizie_di_una_frazione_senza_notizie_usano_il_comune(bot, messaggero, notizie, luoghi):
    from azdora.config import Localita

    luoghi.cerca = lambda nome: Localita("geo:mm", "Milano Marittima", 44.27, 12.35, 44.27, 12.35)

    class SoloCervia(type(notizie)):
        def notizie_per(self, comune, lat, lon, adesso):
            self.richieste.append(comune)
            return [notizia(1)] if comune == "Cervia" else []

    bot.notizie = SoloCervia()
    bot.gestisci_update(messaggio("/notizie mima"))
    assert bot.notizie.richieste == ["Milano Marittima", "Cervia"]
    assert "Cervia (per Milano Marittima)" in messaggero.ultimo


@pytest.fixture(autouse=True)
def notizie_subito(archivio):
    """In questi test le notizie arrivano "subito" (una volta al giro), come prima di /orari."""
    from azdora.orari import Orari, PreferenzeOrari

    preferenze = PreferenzeOrari(archivio)
    for chat in (111, 333, 444):
        preferenze.salva(chat, Orari(notizie_subito=True, silenzio_da=None, silenzio_a=None))
