"""Le principali notizie dall'Italia e dal mondo, oltre a quelle locali."""

from dataclasses import dataclass, field
from datetime import datetime, time

import httpx
import respx

from azdora.bot import Azdora
from azdora.config import FUSO_ORARIO
from azdora.notizie import FONTI_PRINCIPALI, LettoreNotizie, Notizia
from azdora.orari import Orari, PreferenzeOrari
from tests.conftest import PADRONE, messaggio, zitto_il_gruppo

ADESSO = datetime(2026, 11, 5, 19, 45, tzinfo=FUSO_ORARIO)


def _feed(nome):
    from tests.conftest import FIXTURES

    return (FIXTURES / nome).read_bytes()


@respx.mock
def test_principali_in_ordine_di_importanza():
    italia, _ = FONTI_PRINCIPALI["italia"]
    respx.get(italia.url).mock(return_value=httpx.Response(200, content=_feed("rss_italia.xml")))
    with httpx.Client() as http:
        voci = LettoreNotizie(http).principali("italia", ADESSO)
    assert voci and voci[0].titolo.startswith("Decreto Bollette")
    assert voci[0].fonte != "Google News"  # il nome vero del giornale


@respx.mock
def test_principali_ripiegano_sulla_riserva():
    mondo, ansa = FONTI_PRINCIPALI["mondo"]
    respx.get(mondo.url).mock(return_value=httpx.Response(503))
    respx.get(ansa.url).mock(return_value=httpx.Response(200, content=_feed("rss_mondo.xml")))
    with httpx.Client() as http:
        assert LettoreNotizie(http).principali("mondo", ADESSO)
        assert LettoreNotizie(http).principali("marte", ADESSO) == []


@dataclass
class FonteFinta:
    italia: list[Notizia] = field(default_factory=list)
    mondo: list[Notizia] = field(default_factory=list)

    def notizie_per(self, comune, lat, lon, adesso):
        return []

    def del_giorno_in_riviera(self, comune, adesso):
        return [
            Notizia("Riccione, apre la pista di pattinaggio", "https://l/1", "Altarimini", ADESSO)
        ]

    def eventi(self, adesso):
        return []

    def principali(self, sezione, adesso):
        return list(getattr(self, sezione))


def voce(i, sezione):
    return Notizia(f"{sezione} notizia {i}", f"https://{sezione}/{i}", "ANSA", ADESSO)


def crea(impostazioni, messaggero, meteo, archivio, fonte, adesso=ADESSO):
    return Azdora(
        impostazioni, messaggero, meteo, archivio=archivio, notizie=fonte, orologio=lambda: adesso
    )


def test_comandi_italia_e_mondo(impostazioni, messaggero, meteo, archivio):
    fonte = FonteFinta([voce(i, "italia") for i in range(12)], [voce(1, "mondo")])
    bot = crea(impostazioni, messaggero, meteo, archivio, fonte)
    bot.gestisci_update(messaggio("/italia"))
    assert "principali notizie dall'Italia" in messaggero.ultimo
    assert messaggero.ultimo.count("https://italia/") == 8
    bot.gestisci_update(messaggio("/mondo"))
    assert "dal mondo" in messaggero.ultimo
    fonte.mondo = []
    bot.gestisci_update(messaggio("/mondo"))
    assert "non riesco a leggere" in messaggero.ultimo


def test_notizie_locali_con_italia_e_mondo_in_coda(impostazioni, messaggero, meteo, archivio):
    fonte = FonteFinta([voce(i, "italia") for i in range(5)], [voce(i, "mondo") for i in range(5)])
    crea(impostazioni, messaggero, meteo, archivio, fonte).gestisci_update(messaggio("/notizie"))
    testo = messaggero.ultimo
    assert (
        testo.index("pattinaggio")
        < testo.index("🇮🇹 <b>Italia</b>")
        < testo.index("🌍 <b>Mondo</b>")
    )
    assert testo.count("https://italia/") == 3 and testo.count("https://mondo/") == 3


def test_riassunto_con_italia_e_mondo_senza_ripetizioni(impostazioni, messaggero, meteo, archivio):
    zitto_il_gruppo(archivio)
    PreferenzeOrari(archivio).salva(
        PADRONE, Orari(notizie=(time(19, 30),), silenzio_da=None, silenzio_a=None)
    )
    fonte = FonteFinta([voce(i, "italia") for i in range(5)], [voce(i, "mondo") for i in range(5)])
    bot = crea(impostazioni, messaggero, meteo, archivio, fonte)
    assert bot.controlla_notizie() == 1 + 3 + 3
    testo = messaggero.ultimo
    assert "🇮🇹 <b>Italia</b>" in testo and "🌍 <b>Mondo</b>" in testo
    PreferenzeOrari(archivio).salva(
        PADRONE, Orari(notizie=(time(19, 45),), silenzio_da=None, silenzio_a=None)
    )
    assert bot.controlla_notizie() == 2 + 2  # solo quelle non ancora mandate


def test_mattino_con_italia_e_mondo(impostazioni, messaggero, meteo, archivio):
    fonte = FonteFinta([voce(i, "italia") for i in range(5)], [voce(i, "mondo") for i in range(5)])
    mattino = datetime(2026, 11, 5, 7, 5, tzinfo=FUSO_ORARIO)
    bot = crea(impostazioni, messaggero, meteo, archivio, fonte, adesso=mattino)
    bot.controlla_mattino()
    riepilogo = next(t for c, t in messaggero.inviati if c == PADRONE and "Buongiorno" in t)
    assert riepilogo.count("https://italia/") == 2 and riepilogo.count("https://mondo/") == 2


# --- Notizie divise per città e notizie "già viste" ----------------------------------------

from azdora.archivio import ArchivioMemoria  # noqa: E402
from azdora.notizie import citta_del_comune, citta_di, formatta_per_citta, raggruppa  # noqa: E402


def loc(titolo, fonte="Altarimini", area="", minuti=0, sommario=""):
    from datetime import timedelta

    return Notizia(
        titolo,
        f"https://x/{hash(titolo)}",
        fonte,
        ADESSO - timedelta(minutes=minuti),
        sommario,
        area=area,
    )


def test_citta_di_una_notizia():
    assert citta_di(loc("Rimini, lavori sul lungomare")) == "Rimini"
    assert citta_di(loc("Mima, apre la pineta a Milano Marittima")) == "Cervia"
    assert (
        citta_di(loc("Il presepe della marineria", fonte="La Voce di Cesenatico")) == "Cesenatico"
    )
    assert citta_di(loc("Derby tra Cervia e Cesenatico")) == "Cervia"  # la prima nominata
    assert citta_di(loc("Consiglio comunale", area="Pesaro")) == "Pesaro"
    assert citta_di(loc("Notizia qualunque")) == "Dintorni"
    assert citta_di(loc("Gabicce, nuovo lungomare")) == "Cattolica"


def test_citta_del_comune():
    assert citta_del_comune("Misano Adriatico") == "Misano"
    assert citta_del_comune("Riccione") == "Riccione"
    assert citta_del_comune("Bologna") is None


def test_raggruppa_la_tua_citta_prima_e_tutte_rappresentate():
    notizie = [loc(f"Rimini, notizia {i}", minuti=i) for i in range(6)]
    notizie += [loc(f"Riccione, notizia {i}", minuti=i) for i in range(6)]
    notizie += [loc("Pesaro, una notizia", minuti=50), loc("Cervia, una notizia", minuti=60)]
    gruppi = raggruppa(notizie, "Riccione", per_citta=2, nella_prima=4, massimo=9)
    assert [c for c, _ in gruppi] == ["Riccione", "Cervia", "Rimini", "Pesaro"]
    assert dict((c, len(v)) for c, v in gruppi) == {
        "Riccione": 4,
        "Cervia": 1,
        "Rimini": 2,
        "Pesaro": 1,
    }
    testo = formatta_per_citta(gruppi, "🗞️ <b>Le notizie di oggi</b>", "Riccione")
    assert (
        "📍 <b>Riccione</b>" in testo
        and "🎡 <b>Rimini</b>" in testo
        and "🧂 <b>Cervia</b>" in testo
    )


def test_sono_nuovi_non_segna():
    archivio = ArchivioMemoria()
    assert archivio.sono_nuovi(["a", "b"]) == [True, True]
    archivio.segna_nuovi(["a"], 60)
    assert archivio.sono_nuovi(["a", "b"]) == [False, True]
    assert archivio.sono_nuovi(["b"]) == [True]  # controllare non la segna


def test_le_notizie_non_mostrate_restano_per_dopo(impostazioni, messaggero, meteo, archivio):
    zitto_il_gruppo(archivio)
    from azdora.bot import Azdora

    class TanteNotizie(FonteFinta):
        def tutte_di_oggi(self, adesso):
            return [loc(f"Riccione, notizia {i}", minuti=i) for i in range(10)]

    PreferenzeOrari(archivio).salva(
        PADRONE, Orari(notizie=(time(19, 30),), silenzio_da=None, silenzio_a=None)
    )
    bot = Azdora(
        impostazioni,
        messaggero,
        meteo,
        archivio=archivio,
        notizie=TanteNotizie(),
        orologio=lambda: ADESSO,
    )
    assert bot.controlla_notizie() == 4  # 4 sul tuo comune nel primo riassunto
    PreferenzeOrari(archivio).salva(
        PADRONE, Orari(notizie=(time(19, 45),), silenzio_da=None, silenzio_a=None)
    )
    assert bot.controlla_notizie() == 4  # le altre non sono andate perse
