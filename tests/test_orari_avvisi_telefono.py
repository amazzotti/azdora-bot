"""Orari personali, riassunti delle notizie, avvisi "sta per piovere" e posizione dal telefono."""

import json
from dataclasses import dataclass, field, replace
from datetime import datetime, time, timedelta

import httpx
import pytest
import respx

from azdora.bot import Azdora
from azdora.config import FUSO_ORARIO, Impostazioni
from azdora.meteo.imminente import OpenMeteoImminente, Quarto, leggi_quarti, valuta
from azdora.notizie import Notizia
from azdora.orari import (
    ErroreOrari,
    Orari,
    PreferenzeOrari,
    in_orario,
    in_silenzio,
    leggi_ora,
    messaggio,
    modifica,
)
from azdora.posizione import MemoriaPosizione
from azdora.telefono import CollegamentiTelefono, istruzioni
from azdora.web import gestisci_posizione
from tests.conftest import COPPIA, PADRONE, carica
from tests.conftest import messaggio as update_messaggio

GIOVEDI = datetime(2026, 11, 5, 0, 0, tzinfo=FUSO_ORARIO)


def alle(h, m=0, giorni=0):
    return GIOVEDI + timedelta(days=giorni, hours=h, minutes=m)


# --- Orari ---------------------------------------------------------------------------------


def test_orari_predefiniti_e_serializzazione():
    orari = Orari()
    assert orari.mattino == time(7) and orari.sera == time(20)
    assert orari.notizie == tuple(time(h) for h in range(8, 23))  # ogni ora
    assert Orari.da_dizionario(orari.in_dizionario()) == orari
    spento = Orari(mattino=None, silenzio_da=None, silenzio_a=None)
    assert Orari.da_dizionario(spento.in_dizionario()) == spento


@pytest.mark.parametrize(
    ("argomenti", "atteso"),
    [
        (["mattino", "6:30"], {"mattino": time(6, 30)}),
        (["mattino", "off"], {"mattino": None}),
        (["sera", "21"], {"sera": time(21)}),
        (
            ["notizie", "8", "13,19"],
            {"notizie": (time(8), time(13), time(19)), "notizie_subito": False},
        ),
        (["notizie", "subito"], {"notizie_subito": True}),
        (["notizie", "off"], {"notizie": (), "notizie_subito": False}),
        (["silenzio", "23-7"], {"silenzio_da": time(23), "silenzio_a": time(7)}),
        (["silenzio", "22.30", "a", "6"], {"silenzio_da": time(22, 30), "silenzio_a": time(6)}),
        (["silenzio", "no"], {"silenzio_da": None, "silenzio_a": None}),
        (["avvisi", "off"], {"avvisi_meteo": False}),
    ],
)
def test_modifica_orari(argomenti, atteso):
    nuovi = modifica(Orari(), argomenti)
    for campo, valore in atteso.items():
        assert getattr(nuovi, campo) == valore


@pytest.mark.parametrize(
    "argomenti",
    [
        [],
        ["boh", "7"],
        ["mattino", "25"],
        ["mattino", "presto"],
        ["silenzio", "23"],
        ["avvisi", "forse"],
        ["notizie"],
    ],
)
def test_orari_sbagliati(argomenti):
    with pytest.raises(ErroreOrari):
        modifica(Orari(), argomenti)


def test_leggi_ora():
    assert leggi_ora("6") == time(6)
    assert leggi_ora("06.45") == time(6, 45)
    assert leggi_ora("21h") == time(21)


def test_in_orario_e_silenzio():
    assert in_orario(alle(7, 5), time(7))
    assert not in_orario(alle(6, 59), time(7))
    assert not in_orario(alle(10, 1), time(7))  # troppo tardi: meglio niente
    notte = Orari()
    assert in_silenzio(notte, alle(23, 30)) and in_silenzio(notte, alle(3))
    assert not in_silenzio(notte, alle(7)) and not in_silenzio(notte, alle(22, 59))
    pomeriggio = Orari(silenzio_da=time(13), silenzio_a=time(15))
    assert in_silenzio(pomeriggio, alle(14)) and not in_silenzio(pomeriggio, alle(16))
    assert not in_silenzio(Orari(silenzio_da=None, silenzio_a=None), alle(3))


def test_messaggio_orari():
    testo = messaggio(Orari())
    assert "07:00" in testo and "ogni ora, dalle 8 alle 22" in testo and "23:00–07:00" in testo
    assert "12:30, 19:00" in messaggio(Orari(notizie=(time(12, 30), time(19))))
    assert modifica(Orari(notizie=()), ["notizie", "ogni", "ora"]).notizie == Orari().notizie
    assert "subito" in messaggio(Orari(notizie_subito=True))
    assert "spente" in messaggio(Orari(notizie=()))
    gruppo = messaggio(Orari(), per_gruppo=True)
    assert "Riepilogo del mattino" not in gruppo and "Sera" in gruppo


# --- Avvisi imminenti ----------------------------------------------------------------------


def quarti(pioggia=(), codici=(), raffiche=(), inizio=None):
    inizio = inizio or alle(15)
    n = max(len(pioggia), len(codici), len(raffiche), 9)
    return [
        Quarto(
            inizio + timedelta(minutes=15 * i),
            (list(pioggia) + [0.0] * n)[i],
            (list(codici) + [2] * n)[i],
            (list(raffiche) + [10.0] * n)[i],
        )
        for i in range(n)
    ]


def test_leggi_quarti_veri():
    lista = leggi_quarti(carica("minutely_riccione.json"))
    assert len(lista) == 12 and lista[0].codice == 3 and lista[0].raffiche == pytest.approx(15.1)
    assert leggi_quarti({}) == []


def test_sta_per_piovere():
    (avviso,) = valuta(quarti(pioggia=[0, 0, 0.4, 0.8, 1.0]), "Riccione", alle(15))
    assert avviso.tipo == "pioggia"
    assert "tra circa 30 minuti" in avviso.testo and "moderata" in avviso.testo
    assert "2,2 mm" in avviso.testo


def test_se_piove_gia_niente_avviso_pioggia():
    assert valuta(quarti(pioggia=[0.5, 0.5, 0.5]), "Riccione", alle(15)) == []


def test_temporale_neve_raffiche():
    avvisi = valuta(quarti(codici=[2, 2, 2, 2, 95], raffiche=[10, 20, 70]), "Rimini", alle(15))
    tipi = [a.tipo for a in avvisi]
    assert tipi == ["temporale", "raffiche"]
    assert "verso le 16:00" in avvisi[0].testo
    assert "70 km/h" in avvisi[1].testo
    assert valuta(quarti(codici=[2, 73]), "Cervia", alle(15))[0].tipo == "neve"
    assert valuta([], "x", alle(15)) == []


@respx.mock
def test_imminente_via_http_una_volta_per_posto():
    rotta = respx.get(url__startswith="https://api.open-meteo.com/").mock(
        return_value=httpx.Response(200, json=carica("minutely_riccione.json"))
    )
    with httpx.Client() as http:
        fonte = OpenMeteoImminente(http)
        assert len(fonte.quarti(44.0, 12.65)) == 12
        fonte.quarti(44.0, 12.65)
    assert rotta.call_count == 1
    respx.get(url__startswith="https://api.open-meteo.com/").mock(return_value=httpx.Response(500))
    with httpx.Client() as http:
        assert OpenMeteoImminente(http).quarti(1, 1) == []


# --- Dentro il bot -------------------------------------------------------------------------


class Orologio:
    def __init__(self, adesso):
        self.adesso = adesso

    def __call__(self):
        return self.adesso


@dataclass
class NotizieFinte:
    lista: list[Notizia] = field(default_factory=list)

    def notizie_per(self, comune, lat, lon, adesso):
        return [n for n in self.lista if comune.lower() in n.titolo.lower()]

    def del_giorno_in_riviera(self, comune, adesso):
        return list(self.lista)

    def eventi(self, adesso):
        return []


@dataclass
class ImminenteFinto:
    risposta: list[Quarto] = field(default_factory=list)

    def quarti(self, lat, lon):
        return list(self.risposta)


def n(i, titolo=None):
    return Notizia(titolo or f"Notizia {i} dalla Riviera", f"https://n/{i}", "Altarimini", alle(6))


@pytest.fixture
def orologio():
    return Orologio(alle(7, 5))


@pytest.fixture
def notizie():
    return NotizieFinte([n(1, "Riccione, nuova piazza"), n(2), n(3), n(4)])


@pytest.fixture
def imminente():
    return ImminenteFinto()


@pytest.fixture
def bot(impostazioni, messaggero, meteo, luoghi, archivio, orologio, notizie, imminente):
    return Azdora(
        replace_impostazioni(impostazioni),
        messaggero,
        meteo,
        luoghi,
        archivio=archivio,
        notizie=notizie,
        imminente=imminente,
        orologio=orologio,
    )


def replace_impostazioni(impostazioni):
    from dataclasses import replace

    return replace(impostazioni, indirizzo="https://azdora-bot.vercel.app")


def test_comando_orari(bot, messaggero, archivio):
    bot.gestisci_update(update_messaggio("/orari"))
    assert "I tuoi orari" in messaggero.ultimo
    bot.gestisci_update(update_messaggio("/orari mattino 6:30"))
    assert "06:30" in messaggero.ultimo
    assert PreferenzeOrari(archivio).di(PADRONE).mattino == time(6, 30)
    bot.gestisci_update(update_messaggio("/orari mattino presto"))
    assert "non è un orario" in messaggero.ultimo
    bot.gestisci_update(update_messaggio("/orari", chat_id=COPPIA, tipo="group", mittente=7))
    assert "Riepilogo del mattino" not in messaggero.ultimo


def test_mattino_all_ora_scelta_una_volta(bot, messaggero, archivio, orologio):
    PreferenzeOrari(archivio).salva(PADRONE, Orari(mattino=time(6, 30)))
    orologio.adesso = alle(6, 15)
    assert bot.controlla_mattino() == 0
    orologio.adesso = alle(6, 30)
    assert bot.controlla_mattino() == 1
    riepilogo = next(t for c, t in messaggero.inviati if c == PADRONE and "Buongiorno" in t)
    assert "Le prime notizie" in riepilogo and "Riccione, nuova piazza" in riepilogo
    orologio.adesso = alle(6, 45)
    assert bot.controlla_mattino() == 0  # già mandato oggi
    orologio.adesso = alle(6, 30, giorni=1)
    assert bot.controlla_mattino() == 1  # il giorno dopo sì


def test_mattino_spento(bot, archivio):
    PreferenzeOrari(archivio).salva(PADRONE, Orari(mattino=None))
    assert bot.controlla_mattino() == 0


def test_mattino_con_promemoria_di_oggi(bot, messaggero, orologio):
    orologio.adesso = alle(6)
    bot.gestisci_update(update_messaggio("ricordami oggi alle 18 di chiamare il meccanico"))
    orologio.adesso = alle(7, 5)
    bot.controlla_mattino()
    assert "18:00 chiamare il meccanico" in messaggero.ultimo or any(
        "18:00 chiamare il meccanico" in t for _, t in messaggero.inviati
    )


def test_riassunto_notizie_agli_orari_e_completo(bot, messaggero, archivio, orologio, notizie):
    PreferenzeOrari(archivio).salva(PADRONE, Orari(notizie=(time(12, 30), time(19))))
    orologio.adesso = alle(12, 40)
    assert bot.controlla_notizie() == 4  # tutte in un messaggio solo
    testo = messaggero.ultimo
    assert "Le notizie di mezzogiorno" in testo
    assert testo.count("https://n/") == 4
    assert testo.index("Riccione, nuova piazza") < testo.index("Notizia 2")
    orologio.adesso = alle(12, 55)
    assert bot.controlla_notizie() == 0  # stesso orario: una volta sola
    notizie.lista.append(n(5))
    orologio.adesso = alle(19, 5)
    assert bot.controlla_notizie() == 1  # solo quella nuova
    orologio.adesso = alle(16)
    notizie.lista.append(n(6))
    assert bot.controlla_notizie() == 0  # fuori orario


def test_silenzio_ferma_notizie_e_avvisi(bot, messaggero, archivio, orologio, imminente):
    PreferenzeOrari(archivio).salva(
        PADRONE, Orari(notizie_subito=True, silenzio_da=time(22), silenzio_a=time(8))
    )
    imminente.risposta = quarti(pioggia=[0, 0.5, 0.5], inizio=alle(23))
    orologio.adesso = alle(23)
    assert bot.controlla_notizie() == 0
    assert bot.controlla_imminente() == 0


def test_avviso_pioggia_una_volta_ogni_4_ore(bot, messaggero, archivio, orologio, imminente):
    imminente.risposta = quarti(pioggia=[0, 0, 0.6, 0.6], inizio=alle(15))
    orologio.adesso = alle(15)
    assert bot.controlla_imminente() == 1
    assert "Sta per piovere" in messaggero.ultimo and "Riccione" in messaggero.ultimo
    assert bot.controlla_imminente() == 0
    archivio.cancella("imminente:111:pioggia")  # passate le 4 ore di pausa
    orologio.adesso = alle(19, 30)
    imminente.risposta = quarti(pioggia=[0, 0, 0.6], inizio=alle(19, 30))
    assert bot.controlla_imminente() == 1


def test_avvisi_meteo_spenti(bot, archivio, orologio, imminente):
    PreferenzeOrari(archivio).salva(PADRONE, Orari(avvisi_meteo=False))
    imminente.risposta = quarti(pioggia=[0, 0.6], inizio=alle(15))
    orologio.adesso = alle(15)
    assert bot.controlla_imminente() == 0


def test_allerte_di_notte_solo_gravi(impostazioni, messaggero, meteo, archivio):
    from azdora.allerte import Allerta

    class Fonte:
        def allerte(self, paese):
            fine = alle(12, giorni=1)
            return [
                Allerta("gialla", "Emilia e Romagna", 2, "Vento", alle(0), fine),
                Allerta("rossa", "Emilia e Romagna", 4, "Pioggia", alle(0), fine),
            ]

    bot = Azdora(
        impostazioni,
        messaggero,
        meteo,
        archivio=archivio,
        allerte=Fonte(),
        orologio=lambda: alle(2),
    )
    assert bot.controlla_allerte() == 1
    assert "Allerta rossa" in messaggero.ultimo and "gialla" not in messaggero.ultimo


def test_sera_all_ora_scelta(bot, messaggero, archivio, orologio):
    from azdora.rifiuti import Calendario

    Calendario(archivio, PADRONE).imposta(4, ["carta"])  # venerdì
    PreferenzeOrari(archivio).salva(PADRONE, Orari(sera=time(21, 30)))
    orologio.adesso = alle(20, 10)
    assert bot.controlla_sera() == 0
    orologio.adesso = alle(21, 35)
    assert bot.controlla_sera() == 1


def test_lavoro_mattino_rispetta_gli_orari(bot, messaggero, orologio):
    from azdora.web import LAVORI

    orologio.adesso = alle(15)
    LAVORI["mattino"](bot)
    assert not any("Buongiorno" in t for _, t in messaggero.inviati)
    LAVORI["mattino-subito"](bot)
    assert any("Buon" in t for _, t in messaggero.inviati)


# --- Telefono ------------------------------------------------------------------------------


def test_collegamenti_telefono(archivio):
    collegamenti = CollegamentiTelefono(archivio)
    chiave = collegamenti.crea(PADRONE)
    assert collegamenti.chat_di(chiave) == PADRONE
    assert not any(chiave in k for k in archivio.chiavi())  # mai in chiaro
    nuova = collegamenti.crea(PADRONE)
    assert collegamenti.chat_di(chiave) is None and collegamenti.chat_di(nuova) == PADRONE
    assert collegamenti.revoca(PADRONE) and not collegamenti.revoca(PADRONE)
    assert collegamenti.chat_di(nuova) is None and collegamenti.chat_di("") is None
    assert "/api/posizione" in istruzioni("https://x.vercel.app/", "abc")


def test_comando_telefono(bot, messaggero, archivio):
    bot.gestisci_update(update_messaggio("/telefono"))
    assert "https://azdora-bot.vercel.app/api/posizione" in messaggero.ultimo
    bot.gestisci_update(update_messaggio("/telefono off"))
    assert "disattivato" in messaggero.ultimo
    bot.gestisci_update(update_messaggio("/telefono off"))
    assert "Non c'era" in messaggero.ultimo
    bot.gestisci_update(
        update_messaggio("/telefono", chat_id=COPPIA, tipo="group", mittente=PADRONE)
    )
    assert "solo nella chat privata" in messaggero.ultimo


def _chiama(bot, chiave, corpo):
    intestazioni = {"Authorization": f"Bearer {chiave}"} if chiave is not None else {}
    return gestisci_posizione(intestazioni, json.dumps(corpo).encode(), lambda: bot)


def test_posizione_dal_telefono(bot, archivio, luoghi):
    chiave = CollegamentiTelefono(archivio).crea(PADRONE)
    risposta = _chiama(bot, chiave, {"lat": 43.978, "lon": 12.697})
    assert risposta.stato == 200 and risposta.corpo["luogo"] == "Misano Adriatico (RN)"
    salvata = MemoriaPosizione(archivio).leggi(PADRONE)
    assert (salvata.lat, salvata.comune) == (43.978, "Misano Adriatico")
    assert _chiama(bot, chiave, {"latitude": 44.0, "longitude": 12.6}).stato == 200


@pytest.mark.parametrize(
    ("chiave", "corpo", "stato"),
    [
        (None, {"lat": 1, "lon": 1}, 401),
        ("sbagliata", {"lat": 1, "lon": 1}, 401),
        ("", {"lat": 1, "lon": 1}, 401),
        ("buona", {"lat": "x"}, 400),
        ("buona", {"lat": 100, "lon": 1}, 400),
    ],
)
def test_posizione_dal_telefono_errori(bot, archivio, chiave, corpo, stato):
    vera = CollegamentiTelefono(archivio).crea(PADRONE)
    assert _chiama(bot, vera if chiave == "buona" else chiave, corpo).stato == stato


def test_posizione_di_una_chat_non_piu_autorizzata(messaggero, meteo, archivio):
    chiave = CollegamentiTelefono(archivio).crea(999)
    bot = Azdora(
        Impostazioni(token="x", chat_personale=PADRONE), messaggero, meteo, archivio=archivio
    )
    assert _chiama(bot, chiave, {"lat": 44, "lon": 12}).stato == 401


def test_impostazioni_indirizzo():
    assert Impostazioni.da_env({"AZDORA_URL": "https://a.it/"}).indirizzo == "https://a.it"
    env = {"VERCEL_PROJECT_PRODUCTION_URL": "azdora-bot.vercel.app"}
    assert Impostazioni.da_env(env).indirizzo == "https://azdora-bot.vercel.app"
    assert Impostazioni.da_env({}).indirizzo == ""


def test_vecchio_orario_predefinito_delle_notizie_diventa_ogni_ora(archivio):
    vecchio = {"mattino": "06:30", "sera": "20:00", "notizie": ["12:30", "19:00"]}
    archivio.scrivi(f"orari:{PADRONE}", vecchio)
    orari = PreferenzeOrari(archivio).di(PADRONE)
    assert orari.notizie == Orari().notizie and orari.mattino == time(6, 30)
    # Chi invece sceglie 12:30 e 19 adesso, se li tiene.
    PreferenzeOrari(archivio).salva(PADRONE, replace(orari, notizie=(time(12, 30), time(19))))
    assert PreferenzeOrari(archivio).di(PADRONE).notizie == (time(12, 30), time(19))
    # Notizie spente restano spente; senza la voce si usa l'orario predefinito.
    assert Orari.da_dizionario({"notizie": []}).notizie == ()
    assert Orari.da_dizionario({}).notizie == Orari().notizie
