"""Guida TV: lettura delle guide XMLTV, scelte (prima serata, film, adesso), fonti di
riserva, memoria per i giorni dopo e comando /tv."""

import gzip
from datetime import date, datetime, timedelta

import httpx
import pytest
import respx

from azdora.bot import Azdora
from azdora.config import FUSO_ORARIO
from azdora.tv import (
    ErroreGuida,
    GuideXmltv,
    Programma,
    adesso_in_onda,
    canale_di,
    cerca_canale,
    film_della_sera,
    giornata_del_canale,
    leggi_guida,
    leggi_xmltv,
    normalizza_canale,
    prima_serata,
    pulisci_titolo,
    salva_guida,
)
from tests.conftest import PADRONE, messaggio

OGGI = date(2026, 11, 6)
ADESSO = datetime(2026, 11, 6, 11, 15, tzinfo=FUSO_ORARIO)


def ora(h, m=0, giorno=OGGI):
    return datetime(giorno.year, giorno.month, giorno.day, h, m, tzinfo=FUSO_ORARIO)


def utc(h, m=0, giorno=OGGI):
    """Gli orari XMLTV in UTC: a novembre Roma è UTC+1."""
    return f"{giorno:%Y%m%d}{h - 1:02d}{m:02d}00 +0000"


def voce(canale, da, a, titolo, *generi, trama=""):
    categorie = "".join(f"<category>{g}</category>" for g in generi)
    return (
        f'<programme start="{da}" stop="{a}" channel="{canale}"><title>{titolo}</title>'
        f"{categorie}<desc>{trama}</desc></programme>"
    )


def guida(*voci, altri_canali=10):
    # Canali "di contorno" per superare il controllo dei 10 canali minimi.
    contorno = [
        voce(c, utc(20), utc(21), "Tg")
        for c in ("Rai2.it", "Rai3.it", "Rete4.it", "Italia1.it", "La7.it", "Tv8.it", "Nove.it",
                  "Rai4.it", "Rai5.it", "Focus.it")[:altri_canali]
    ]  # fmt: skip
    return ("<tv>" + "".join(voci) + "".join(contorno) + "</tv>").encode()


SERA = guida(
    voce("Rai.1.HD..101.it", utc(20, 35), utc(21, 30), "Stag. 2026 - Affari tuoi", "Gioco"),
    voce("Rai.1.HD..101.it", utc(21, 30), utc(23, 50), "The Voice Senior", "Musica"),
    voce("Rai1.it", utc(21, 30), utc(23, 50), "The Voice Senior"),  # doppione senza generi
    voce("Canale.5.it", utc(20, 40), utc(21, 20), "La Ruota della Fortuna", "Varietà"),
    voce("Canale.5.it", utc(21, 20), utc(23, 59), "Grande Fratello", "Reality show"),
    voce("Iris.it", utc(21, 10), utc(23, 40), "Il texano", "Western", trama="Clint Eastwood"),
    voce("Italia.2.it", utc(21, 10), utc(21, 35), "I Simpson", "Commedia"),  # sitcom
    voce("Rai.Premium..5025.it", utc(21, 20), utc(23, 0), "Doc - Stag. 1 Ep. 2", "Giallo"),
    voce("LA7.CINEMA.it", utc(21, 30), utc(23, 30), "Mistery, Alaska", "Film", "Drammatico"),
    voce("LA7D.it", utc(21, 30), utc(23, 30), "Mistery, Alaska", "Film", "Drammatico"),
    voce("TV8.HD.it", utc(21, 40), utc(23, 0), "Avvocato Ligas 1^TV", "Intrattenimento"),
    voce("Sky.Cinema.Uno.it", utc(21, 15), utc(23, 0), "Tutti tranne te", "Film", "Romantico"),
    voce("Canale.Sconosciuto.it", utc(21), utc(22), "Boh"),
)


def programmi(contenuto=SERA):
    return leggi_xmltv(contenuto)


# --- Lettura -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("identificativo", "atteso"),
    [
        ("Rai.1.HD..101.it", "rai1"),
        ("Rai1.it", "rai1"),
        ("Rai 1.it", "rai1"),
        ("Canale.5.it", "canale5"),
        ("LA7.HD.it", "la7"),
        ("NOVE.HD..149.it", "nove"),
        ("Rai.Movie..5024.it", "raimovie"),
        ("Sky.Cinema.Uno.it", "skycinemauno"),
    ],
)
def test_nomi_dei_canali(identificativo, atteso):
    assert normalizza_canale(identificativo) == atteso


def test_legge_solo_i_canali_conosciuti_e_senza_doppioni():
    lista = programmi()
    assert "Boh" not in {p.titolo for p in lista}
    rai1 = [p for p in lista if p.canale == "Rai 1"]
    assert [p.titolo for p in rai1] == ["Affari tuoi", "The Voice Senior"]  # elenco coi generi
    assert rai1[0].inizio == ora(20, 35) and rai1[0].generi == ("Gioco",)


def test_guida_compressa():
    assert len(leggi_xmltv(gzip.compress(SERA))) == len(programmi())


def test_titoli_puliti():
    assert pulisci_titolo("Stag. 2026 - Affari tuoi") == "Affari tuoi"
    assert pulisci_titolo("Avvocato Ligas 1^TV") == "Avvocato Ligas (prima TV)"
    assert pulisci_titolo("Cash or Trash - 1^TV") == "Cash or Trash (prima TV)"


def test_riconosce_i_film():
    per_titolo = {p.titolo: p for p in programmi()}
    assert per_titolo["Il texano"].e_un_film  # "Western" e dura 2 ore e mezza
    assert per_titolo["Mistery, Alaska"].e_un_film  # "Film"
    assert not per_titolo["I Simpson"].e_un_film  # "Commedia" ma 25 minuti
    assert not per_titolo["Doc - Stag. 1 Ep. 2"].e_un_film  # episodio di una serie
    assert not per_titolo["Grande Fratello"].e_un_film
    senza_generi = Programma("Iris", ora(21), ora(23), "Un film qualsiasi")
    assert senza_generi.e_un_film  # canale di cinema, dura 2 ore
    assert not Programma("Rai 1", ora(21), ora(23), "Varietà lungo").e_un_film


# --- Scelte ----------------------------------------------------------------------------------


def test_prima_serata_salta_l_access():
    scelti = {p.canale: p.titolo for p in prima_serata(programmi(), OGGI)}
    assert scelti["Rai 1"] == "The Voice Senior"  # non "Affari tuoi" delle 20:35
    assert scelti["Canale 5"] == "Grande Fratello"
    assert list(scelti)[:2] == ["Rai 1", "Canale 5"]  # nell'ordine del telecomando


def test_film_della_sera_senza_doppioni():
    film = film_della_sera(programmi(), OGGI)
    titoli = [p.titolo for p in film]
    assert titoli.count("Mistery, Alaska") == 1  # su La7 Cinema e La7d: una volta sola
    assert "Il texano" in titoli and "I Simpson" not in titoli
    assert [p.inizio for p in film] == sorted(p.inizio for p in film)


def test_adesso_e_dopo():
    in_onda = dict((a.canale, (a.titolo, d.titolo if d else None)) for a, d in adesso_in_onda(
        programmi(), ora(21, 0)
    ))  # fmt: skip
    assert in_onda["Rai 1"] == ("Affari tuoi", "The Voice Senior")
    assert in_onda["Canale 5"] == ("La Ruota della Fortuna", "Grande Fratello")


def test_giornata_del_canale():
    lista = giornata_del_canale(programmi(), "Canale 5", ora(21, 0))
    assert [p.titolo for p in lista] == ["La Ruota della Fortuna", "Grande Fratello"]
    assert giornata_del_canale(programmi(), "Canale 5", ora(23, 59, OGGI)) == []


@pytest.mark.parametrize(
    ("testo", "canale"),
    [("rai1", "Rai 1"), ("canale 5", "Canale 5"), ("la 7", "La7"), ("Iris", "Iris"),
     ("sky cinema uno", "Sky Cinema Uno"), ("pippo", None)],
)  # fmt: skip
def test_cerca_canale(testo, canale):
    assert cerca_canale(testo) == canale


def test_canale_di_un_identificativo():
    assert canale_di("Rai.Movie..5024.it") == "Rai Movie" and canale_di("Boh.it") is None


# --- Fonti di riserva ---------------------------------------------------------------------

FONTI = (("prima", "https://uno.example/guida.xml.gz"), ("riserva", "https://due.example/g.xml"))


@respx.mock
def test_se_la_prima_fonte_non_risponde_usa_la_riserva():
    respx.get(FONTI[0][1]).mock(return_value=httpx.Response(503))
    respx.get(FONTI[1][1]).mock(return_value=httpx.Response(200, content=SERA))
    with httpx.Client() as http:
        fonte, lista = GuideXmltv(http, FONTI).scarica()
    assert fonte == "riserva" and lista


@respx.mock
def test_guida_quasi_vuota_o_rotta_non_vale():
    respx.get(FONTI[0][1]).mock(return_value=httpx.Response(200, content=b"<html>302</html"))
    respx.get(FONTI[1][1]).mock(
        return_value=httpx.Response(200, content=guida(altri_canali=2))  # troppi pochi canali
    )
    with httpx.Client() as http, pytest.raises(ErroreGuida):
        GuideXmltv(http, FONTI).scarica()


@respx.mock
def test_rete_giu():
    respx.get(FONTI[0][1]).mock(side_effect=httpx.ConnectError("giù"))
    respx.get(FONTI[1][1]).mock(side_effect=httpx.ReadTimeout("lenta"))
    with httpx.Client() as http, pytest.raises(ErroreGuida):
        GuideXmltv(http, FONTI).scarica()


# --- Memoria --------------------------------------------------------------------------------


def test_salva_e_rilegge_per_giorno_e_gruppo(archivio):
    giorni = salva_guida(archivio, programmi(), "prova", ADESSO)
    assert giorni == 1
    gratis = leggi_guida(archivio, [OGGI], "gratis")
    assert {p.canale for p in gratis} >= {"Rai 1", "Canale 5", "Iris"}
    assert "Sky Cinema Uno" not in {p.canale for p in gratis}
    assert [p.canale for p in leggi_guida(archivio, [OGGI], "sky")] == ["Sky Cinema Uno"]
    assert gratis[0].inizio.tzinfo is not None
    assert archivio.leggi("tv-aggiornata")["fonte"] == "prova"


# --- Il comando /tv ---------------------------------------------------------------------


class GuidaFinta:
    def __init__(self, contenuto=SERA, guasta=False):
        self.contenuto = contenuto
        self.guasta = guasta
        self.scaricamenti = 0

    def scarica(self):
        self.scaricamenti += 1
        if self.guasta:
            raise ErroreGuida("giù")
        return "finta", leggi_xmltv(self.contenuto)


class Orologio:
    def __init__(self, quando=ADESSO):
        self.quando = quando

    def __call__(self):
        return self.quando


@pytest.fixture
def guida_finta():
    return GuidaFinta()


@pytest.fixture
def orologio():
    return Orologio()


@pytest.fixture
def azdora(impostazioni, messaggero, meteo, archivio, guida_finta, orologio):
    return Azdora(
        impostazioni, messaggero, meteo, archivio=archivio, guida_tv=guida_finta, orologio=orologio
    )


def test_tv_stasera(azdora, messaggero, guida_finta):
    azdora.gestisci_update(messaggio("/tv"))
    testo = messaggero.ultimo
    assert "In TV stasera" in testo and "<b>Rai 1</b> 21:30 · The Voice Senior" in testo
    assert "Sky" not in testo and "/tv film" in testo
    azdora.gestisci_update(messaggio("/tv film"))
    assert "Film stasera" in messaggero.ultimo and "Il texano" in messaggero.ultimo
    assert guida_finta.scaricamenti == 1  # la seconda volta usa quella salvata


def test_tv_sky_adesso_domani_canale(azdora, messaggero, orologio):
    azdora.gestisci_update(messaggio("/tv film sky"))
    assert "Tutti tranne te" in messaggero.ultimo and "· Sky" in messaggero.ultimo
    orologio.quando = ora(21, 0)
    azdora.gestisci_update(messaggio("/tv adesso"))
    assert "In onda adesso</b> (21:00)" in messaggero.ultimo
    assert "poi 21:30 The Voice Senior" in messaggero.ultimo
    azdora.gestisci_update(messaggio("/tv domani"))
    assert "domani sera" in messaggero.ultimo
    azdora.gestisci_update(messaggio("/tv canale 5"))
    assert "<b>Canale 5</b>, da adesso" in messaggero.ultimo
    assert "21:20 · Grande Fratello" in messaggero.ultimo


def test_tv_canale_sconosciuto_e_aiuto(azdora, messaggero):
    azdora.gestisci_update(messaggio("/tv pippo"))
    assert "Non conosco il canale «pippo»" in messaggero.ultimo
    azdora.gestisci_update(messaggio("/tv aiuto"))
    assert "/tv film" in messaggero.ultimo and "/tv adesso" in messaggero.ultimo


def test_tv_con_le_fonti_giu_usa_la_guida_salvata(azdora, messaggero, archivio, guida_finta):
    salva_guida(archivio, programmi(), "di ieri", ADESSO - timedelta(hours=20))
    guida_finta.guasta = True
    azdora.gestisci_update(messaggio("/tv"))
    assert "The Voice Senior" in messaggero.ultimo


def test_tv_senza_niente(impostazioni, messaggero, meteo, archivio):
    bot = Azdora(
        impostazioni,
        messaggero,
        meteo,
        archivio=archivio,
        guida_tv=GuidaFinta(guasta=True),
        orologio=Orologio(),
    )
    bot.gestisci_update(messaggio("/tv"))
    assert "non è disponibile" in messaggero.ultimo


def test_aggiornamento_una_volta_ogni_12_ore(azdora, guida_finta, orologio):
    assert azdora.controlla_tv()  # 11:15
    assert not azdora.controlla_tv()
    orologio.quando = ora(23, 30)  # 12 ore dopo
    assert azdora.controlla_tv() and guida_finta.scaricamenti == 2
    orologio.quando = datetime(2026, 11, 7, 12, 0, tzinfo=FUSO_ORARIO)
    assert azdora.controlla_tv() and guida_finta.scaricamenti == 3


def test_di_notte_aspetta_le_5(azdora, guida_finta, orologio, archivio):
    azdora.controlla_tv()
    orologio.quando = datetime(2026, 11, 7, 3, 0, tzinfo=FUSO_ORARIO)  # 16 ore dopo, ma è notte
    assert not azdora.controlla_tv()
    orologio.quando = datetime(2026, 11, 7, 5, 15, tzinfo=FUSO_ORARIO)
    assert azdora.controlla_tv()


def test_aggiornamento_fallito_non_rompe_i_controlli(azdora, guida_finta):
    guida_finta.guasta = True
    assert azdora.controlla_tv() is False
    azdora.controlli()  # nessuna eccezione


def test_stato_dice_della_guida(azdora, messaggero):
    azdora.gestisci_update(messaggio("/stato"))
    assert "Guida TV: ⚠️ non ancora scaricata" in messaggero.ultimo
    azdora.controlla_tv()
    azdora.gestisci_update(messaggio("/stato"))
    assert "Guida TV: ✅ aggiornata il 06/11 alle 11:15 (finta" in messaggero.ultimo
    assert messaggero.inviati[-1][0] == PADRONE
