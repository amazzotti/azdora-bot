"""Moduli nuovi: frasi libere, promemoria, budget, conti, rifiuti, aria e carburante."""

from datetime import date, datetime, timedelta

import httpx
import pytest
import respx

from azdora.archivio import ArchivioMemoria
from azdora.budget import TOTALE, Budget, avviso_dopo_spesa, leggi_budget, stato
from azdora.carburante import (
    URL_ANAGRAFICA,
    URL_PREZZI,
    Osservaprezzi,
    leggi_distributori,
    messaggio,
    migliori_offerte,
)
from azdora.config import FUSO_ORARIO, RICCIONE
from azdora.conti import MembriGruppo, Pareggio, passaggi, resoconto, saldi, spese_da
from azdora.linguaggio import leggi_spesa_libera
from azdora.meteo.aria import (
    Aria,
    OpenMeteoAria,
    avvisi_aria,
    consiglio_uv,
    leggi_aria,
    livello_aqi,
    livello_polline,
    messaggio_aria,
)
from azdora.promemoria import (
    ErrorePromemoria,
    RegistroPromemoria,
    conferma,
    e_un_promemoria,
    elenco,
    leggi_promemoria,
    pulsanti,
    quando_leggibile,
)
from azdora.rifiuti import (
    Calendario,
    avviso_sera,
    con_emoji,
    giorno_della_settimana,
    leggi_rifiuti,
    messaggio_calendario,
)
from azdora.spese import (
    RegistroSpese,
    Spesa,
    categoria_per,
    leggi_riferimento,
    parola_chiave,
    pulsanti_categorie,
    pulsanti_spesa,
    trova_categoria,
)
from tests.conftest import FIXTURES, carica

# Giovedì 5 novembre 2026, ore 15.
ADESSO = datetime(2026, 11, 5, 15, 0, tzinfo=FUSO_ORARIO)
OGGI = ADESSO.date()

# --- Spese scritte come si parla -----------------------------------------------------------


@pytest.mark.parametrize(
    ("frase", "attesa"),
    [
        ("45 benzina", (4500, "benzina")),
        ("benzina 45", (4500, "benzina")),
        ("ho speso 12,50 per la pizza", (1250, "pizza")),
        ("pagato 80 € di bolletta luce", (8000, "bolletta luce")),
        ("spesa conad 63,20", (6320, "spesa conad")),
        ("oggi ho speso 30 euro al bar", (3000, "bar")),
        ("Netflix 13,99€", (1399, "Netflix")),
    ],
)
def test_spese_libere(frase, attesa):
    assert leggi_spesa_libera(frase) == attesa


@pytest.mark.parametrize(
    "frase",
    [
        "ciao",
        "45",
        "ci vediamo alle 8 e 30",
        "2 caffè 3,20",  # due numeri: meglio non indovinare
        "uno due tre quattro cinque sei sette otto nove dieci 5 undici",
        "45 !!!",
    ],
)
def test_frasi_che_non_sono_spese(frase):
    assert leggi_spesa_libera(frase) is None


def test_categorie_imparate_e_parola_chiave():
    assert parola_chiave("la spesa all'Esselunga") == "spesa"
    assert parola_chiave("di per") == ""
    assert categoria_per("Esselunga", {"esselunga": "🏠 Casa"}) == "🏠 Casa"
    assert trova_categoria("auto") == "🚗 Auto"
    assert trova_categoria("Fuori") == "🍕 Fuori"
    assert trova_categoria("au") is None
    assert trova_categoria("astronave") is None


def test_registro_cambia_categoria_e_impara():
    registro = RegistroSpese(ArchivioMemoria(), 1)
    spesa = registro.aggiungi(OGGI, 2000, "Tigotà shampoo")
    assert spesa.categoria == "📦 Altro"
    cambiata = registro.cambia_categoria(OGGI, spesa.id, "🏠 Casa")
    assert cambiata.categoria == "🏠 Casa"
    assert registro.aggiungi(OGGI, 900, "tigotà detersivo").categoria == "🏠 Casa"
    assert registro.cambia_categoria(OGGI, 99, "🏠 Casa") is None
    assert registro.elimina(OGGI, spesa.id) == cambiata
    assert registro.elimina(OGGI, spesa.id) is None
    assert registro.trova(OGGI, 99) is None


def test_pulsanti_delle_spese():
    spesa = Spesa(3, OGGI, 4500, "🚗 Auto", "benzina")
    (fila,) = pulsanti_spesa(spesa)["inline_keyboard"]
    assert [p["callback_data"] for p in fila] == ["spcat:2026-11:3", "spann:2026-11:3"]
    categorie = pulsanti_categorie(spesa)["inline_keyboard"]
    assert all(len(fila) <= 2 for fila in categorie)
    assert categorie[0][0]["callback_data"] == "spset:2026-11:3:0"
    assert leggi_riferimento("2026-11:3") == (date(2026, 11, 1), 3, None)
    assert leggi_riferimento("2026-11:3:5") == (date(2026, 11, 1), 3, 5)


# --- Promemoria ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("frase", "quando", "cosa"),
    [
        (
            "ricordami domani alle 18 di chiamare il meccanico",
            datetime(2026, 11, 6, 18, 0),
            "chiamare il meccanico",
        ),
        (
            "ricordami tra 2 ore di togliere la torta",
            datetime(2026, 11, 5, 17, 0),
            "togliere la torta",
        ),
        ("ricordami tra mezz'ora di uscire", datetime(2026, 11, 5, 15, 30), "uscire"),
        ("ricordami tra 10 minuti il forno", datetime(2026, 11, 5, 15, 10), "il forno"),
        ("ricordami tra 3 giorni la bolletta", datetime(2026, 11, 8, 15, 0), "la bolletta"),
        ("ricordami venerdì di prenotare", datetime(2026, 11, 6, 9, 0), "prenotare"),
        ("ricordami giovedì di x", datetime(2026, 11, 12, 9, 0), "x"),  # oggi è giovedì
        ("ricordami il 12/11 alle 9 il dentista", datetime(2026, 11, 12, 9, 0), "il dentista"),
        ("ricordami 2/1 auguri", datetime(2027, 1, 2, 9, 0), "auguri"),
        ("Ricordami stasera la spazzatura", datetime(2026, 11, 5, 20, 0), "la spazzatura"),
        ("ricordami domattina il pane", datetime(2026, 11, 6, 8, 0), "il pane"),
        ("ricordami alle 10 di pagare", datetime(2026, 11, 6, 10, 0), "pagare"),
        ("ricordami alle 16.30 di partire", datetime(2026, 11, 5, 16, 30), "partire"),
        ("/ricordami dopodomani alle 7 sveglia", datetime(2026, 11, 7, 7, 0), "sveglia"),
    ],
)
def test_leggi_promemoria(frase, quando, cosa):
    assert leggi_promemoria(frase, ADESSO) == (quando.replace(tzinfo=FUSO_ORARIO), cosa)


@pytest.mark.parametrize(
    ("frase", "errore"),
    [
        ("ricordami di comprare il latte", "quando"),
        ("ricordami domani", "cosa"),
        ("ricordami alle 25 di x", "ora"),
        ("ricordami il 31/02 di x", "data"),
        ("ricordami oggi alle 9 di x", "passato"),
        ("ricordami il 1/1/2020 di x", "passato"),
    ],
)
def test_promemoria_sbagliati(frase, errore):
    with pytest.raises(ErrorePromemoria, match=errore):
        leggi_promemoria(frase, ADESSO)


def test_riconoscere_un_promemoria():
    assert e_un_promemoria("Ricordami domani di x")
    assert e_un_promemoria("ricordamelo stasera")
    assert not e_un_promemoria("ti ricordi?")


def test_registro_promemoria():
    registro = RegistroPromemoria(ArchivioMemoria(), 1)
    a = registro.aggiungi(ADESSO + timedelta(minutes=5), "a")
    b = registro.aggiungi(ADESSO + timedelta(days=1), "b")
    assert [p.testo for p in registro.tutti()] == ["a", "b"]
    assert registro.del_giorno(OGGI + timedelta(days=1)) == [b]
    assert registro.scaduti(ADESSO) == []
    assert registro.scaduti(ADESSO + timedelta(minutes=10)) == [a]
    assert registro.tutti() == [b]
    assert registro.elimina(b.id) == b
    assert registro.elimina(b.id) is None


def test_troppi_promemoria():
    registro = RegistroPromemoria(ArchivioMemoria(), 1)
    for i in range(50):
        registro.aggiungi(ADESSO + timedelta(hours=1), str(i))
    with pytest.raises(ErrorePromemoria, match="50"):
        registro.aggiungi(ADESSO + timedelta(hours=1), "uno di troppo")


def test_testi_dei_promemoria():
    registro = RegistroPromemoria(ArchivioMemoria(), 1)
    voce = registro.aggiungi(ADESSO + timedelta(days=1, hours=3), "chiamare <Mario>")
    assert "domani alle 18:00" in conferma(voce, ADESSO)
    assert "&lt;Mario&gt;" in conferma(voce, ADESSO)
    assert quando_leggibile(ADESSO + timedelta(hours=1), ADESSO) == "oggi alle 16:00"
    assert quando_leggibile(ADESSO + timedelta(days=3), ADESSO).startswith("domenica")
    assert quando_leggibile(ADESSO + timedelta(days=30), ADESSO).startswith("il 5/12/2026")
    assert "Nessun promemoria" in elenco([], ADESSO)
    assert pulsanti([]) is None
    assert pulsanti([voce])["inline_keyboard"][0][0]["callback_data"] == f"prdel:{voce.id}"


# --- Budget --------------------------------------------------------------------------------


def test_leggi_budget():
    assert leggi_budget(["800"]) == (TOTALE, 80000)
    assert leggi_budget(["auto", "150"]) == ("🚗 Auto", 15000)
    assert leggi_budget(["fuori", "0"]) == ("🍕 Fuori", 0)
    assert leggi_budget(["astronave", "10"]) is None
    assert leggi_budget(["auto"]) is None
    assert leggi_budget([]) is None


def test_budget_imposta_e_avvisa():
    budget = Budget(ArchivioMemoria(), 1)
    budget.imposta(TOTALE, 10000)
    budget.imposta("🚗 Auto", 5000)
    budget.imposta("🍕 Fuori", 0)
    assert budget.tutti() == {TOTALE: 10000, "🚗 Auto": 5000}
    budget.imposta("🚗 Auto", 0)
    assert budget.tutti() == {TOTALE: 10000}

    limiti = {TOTALE: 10000, "🚗 Auto": 5000}
    benzina = Spesa(1, OGGI, 4500, "🚗 Auto", "benzina")
    pizza = Spesa(2, OGGI, 1000, "🍕 Fuori", "pizza")
    testo = avviso_dopo_spesa(limiti, [benzina], benzina)
    assert "⚠️ Budget 🚗 Auto al 90%: restano 5,00 €" in testo
    assert "📊 Totale: 45,00 € di 100,00 €" in testo
    oltre = Spesa(3, OGGI, 6000, "🚗 Auto", "gomme")
    assert "🚨 Budget Totale superato" in avviso_dopo_spesa(limiti, [benzina, oltre], oltre)
    assert avviso_dopo_spesa({}, [pizza], pizza) == ""
    assert "Fuori" not in avviso_dopo_spesa({"🚗 Auto": 5000}, [pizza], pizza)


def test_stato_budget():
    assert "Nessun budget" in stato({}, [])
    testo = stato({TOTALE: 10000, "🚗 Auto": 5000}, [Spesa(1, OGGI, 6000, "🚗 Auto", "x")])
    assert "✅ Totale: 60,00 € di 100,00 € (60%)" in testo
    assert "🚨 🚗 Auto: 60,00 € di 50,00 € (120%)" in testo


# --- Conti di gruppo -----------------------------------------------------------------------

ALE, GIULIA, BABBO = 1, 2, 3


def test_saldi_e_passaggi():
    membri = {ALE: "Ale", GIULIA: "Giulia"}
    spese = [
        Spesa(1, OGGI, 6000, "🛒 Spesa", "conad", "Ale", ALE),
        Spesa(2, OGGI, 2000, "🍕 Fuori", "pizza", "Giulia", GIULIA),
    ]
    assert saldi(spese, membri) == {ALE: 2000, GIULIA: -2000}
    (p,) = passaggi(saldi(spese, membri), membri)
    assert (p.da, p.a, p.importo) == ("Giulia", "Ale", 2000)


def test_conti_a_tre_con_centesimi():
    membri = {ALE: "Ale", GIULIA: "Giulia", BABBO: "Babbo"}
    spese = [Spesa(1, OGGI, 1000, "x", "x", "Ale", ALE)]
    valori = saldi(spese, membri)
    assert sum(valori.values()) == 0
    risultato = passaggi(valori, membri)
    assert sum(p.importo for p in risultato) == 1000 - 334  # Ale riceve quanto ha anticipato
    assert {p.da for p in risultato} == {"Giulia", "Babbo"}


def test_saldi_senza_spese_comuni():
    assert saldi([Spesa(1, OGGI, 100, "x", "x")], {}) == {}
    assert passaggi({ALE: 0}, {ALE: "Ale"}) == []


def test_resoconto_dei_conti():
    membri = {ALE: "Ale", GIULIA: "Giulia"}
    spese = [Spesa(1, OGGI, 6000, "🛒 Spesa", "conad", "Ale", ALE)]
    testo = resoconto(spese, membri, None)
    assert "Spese comuni: <b>60,00 €</b>" in testo
    assert "👉 <b>Giulia</b> dà <b>30,00 €</b> a Ale" in testo
    pari = [*spese, Spesa(2, OGGI, 6000, "x", "x", "Giulia", GIULIA)]
    assert "Siete pari" in resoconto(pari, membri, date(2026, 11, 1))
    assert "Nessuna spesa comune" in resoconto([], membri, None)


def test_membri_pareggio_e_spese_da():
    archivio = ArchivioMemoria()
    membri = MembriGruppo(archivio, -1)
    membri.ricorda(ALE, "Ale")
    membri.ricorda(ALE, "Ale")
    membri.ricorda(GIULIA, "Giulia")
    assert membri.tutti() == {ALE: "Ale", GIULIA: "Giulia"}
    pareggio = Pareggio(archivio, -1)
    assert pareggio.dal() is None
    pareggio.segna(date(2026, 10, 15))
    assert pareggio.dal() == date(2026, 10, 15)
    registro = RegistroSpese(archivio, -1)
    registro.aggiungi(date(2026, 10, 10), 100, "prima del pareggio")
    registro.aggiungi(date(2026, 10, 20), 200, "dopo")
    registro.aggiungi(OGGI, 300, "oggi")
    assert [s.descrizione for s in spese_da(registro, pareggio.dal(), OGGI)] == ["dopo", "oggi"]
    assert len(spese_da(registro, None, OGGI)) == 3


# --- Rifiuti -------------------------------------------------------------------------------


def test_rifiuti():
    assert giorno_della_settimana("mar") == 1
    assert giorno_della_settimana("Venerdi") == 4
    assert giorno_della_settimana("xx") is None
    assert leggi_rifiuti(["martedì", "plastica,", "carta"]) == (1, ["plastica", "carta"])
    assert leggi_rifiuti(["lunedì", "organico", "e", "vetro"]) == (0, ["organico", "vetro"])
    assert leggi_rifiuti(["martedì", "-"]) == (1, [])
    assert leggi_rifiuti(["boh", "carta"]) is None
    assert leggi_rifiuti(["martedì"]) is None
    assert leggi_rifiuti([]) is None
    assert con_emoji("plastica") == "🟡 plastica"
    assert con_emoji("pile") == "♻️ pile"

    calendario = Calendario(ArchivioMemoria(), 1)
    assert "Dimmi cosa passa" in messaggio_calendario(calendario.tutto())
    calendario.imposta(1, ["plastica"])
    calendario.imposta(3, ["carta"])
    calendario.imposta(3, [])
    assert calendario.tutto() == {1: ["plastica"]}
    assert calendario.del_giorno(1) == ["plastica"]
    assert "<b>Martedì</b>: 🟡 plastica" in messaggio_calendario(calendario.tutto())
    assert avviso_sera(["vetro"]) == "🗑️ <b>Stasera porta fuori</b>: 🟢 vetro"


# --- Aria ----------------------------------------------------------------------------------


def test_aria_dai_dati_veri():
    aria = leggi_aria(carica("aria_riccione.json"))
    assert aria.uv_max == pytest.approx(1.5)
    assert aria.aqi_max == 41
    assert "graminacee" in aria.pollini
    testo = messaggio_aria(aria, "Riccione")
    assert "UV massimo 2: basso" in testo
    assert "Qualità dell'aria discreta" in testo
    assert "Pollini: bassi" in testo
    assert avvisi_aria(aria) == []


def test_aria_con_pollini_e_smog():
    aria = Aria(uv_max=9, aqi_max=85, pollini={"graminacee": 120, "betulla": 5})
    testo = messaggio_aria(aria, "Rimini")
    assert "graminacee alto" in testo and "betulla" not in testo
    avvisi = " ".join(avvisi_aria(aria))
    assert "Pollini alti: graminacee" in avvisi and "Aria cattiva" in avvisi
    assert "evita il sole" in consiglio_uv(9)
    assert "crema se stai fuori" in consiglio_uv(4)
    assert livello_polline(300) == "molto alto"
    assert livello_aqi(150) == "pessima"
    assert leggi_aria({}) == Aria(None, None, {})


@respx.mock
def test_aria_via_http():
    respx.get(url__startswith="https://air-quality-api.open-meteo.com/").mock(
        side_effect=[
            httpx.Response(200, json=carica("aria_riccione.json")),
            httpx.Response(500),
        ]
    )
    with httpx.Client() as http:
        fonte = OpenMeteoAria(http)
        assert fonte.aria(RICCIONE).aqi_max == 41
        assert fonte.aria(RICCIONE) is None


# --- Carburante ----------------------------------------------------------------------------

ANAGRAFICA = (FIXTURES / "mimit_anagrafica.csv").read_text(encoding="utf-8")
PREZZI = (FIXTURES / "mimit_prezzi.csv").read_text(encoding="utf-8")
GIORNO_DATI = datetime(2026, 11, 5, 9, 0, tzinfo=FUSO_ORARIO)


def test_distributori_vicini():
    vicini = leggi_distributori(ANAGRAFICA, RICCIONE.lat, RICCIONE.lon, 7)
    assert len(vicini) == 38
    assert all(distanza <= 7 for _, distanza in vicini.values())
    assert len(leggi_distributori(ANAGRAFICA, RICCIONE.lat, RICCIONE.lon, 1)) < 38
    assert (
        leggi_distributori("riga\nintestazione\n1|x|y\n2|a|b|c|d|e|f|g|nonnumero|12\n", 0, 0, 5)
        == {}
    )


@pytest.mark.parametrize("carburante", ["Benzina", "Gasolio", "GPL", "Metano"])
def test_offerte_ordinate(carburante):
    vicini = leggi_distributori(ANAGRAFICA, RICCIONE.lat, RICCIONE.lon, 7)
    offerte = migliori_offerte(PREZZI, vicini, carburante, GIORNO_DATI)
    assert 1 <= len(offerte) <= 5
    prezzi = [o.prezzo for o in offerte]
    assert prezzi == sorted(prezzi)
    assert len({o.distributore.id for o in offerte}) == len(offerte)


def test_prezzi_vecchi_scartati():
    vicini = leggi_distributori(ANAGRAFICA, RICCIONE.lat, RICCIONE.lon, 7)
    tra_un_anno = GIORNO_DATI + timedelta(days=365)
    assert migliori_offerte(PREZZI, vicini, "Benzina", tra_un_anno) == []


def test_messaggio_carburante():
    vicini = leggi_distributori(ANAGRAFICA, RICCIONE.lat, RICCIONE.lon, 7)
    offerte = migliori_offerte(PREZZI, vicini, "Benzina", GIORNO_DATI)
    testo = messaggio(offerte, "Benzina", "Riccione")
    assert "Benzina più economica vicino a Riccione" in testo
    assert "https://www.google.com/maps/search/?api=1&amp;query=" in testo
    assert "Ministero" in testo
    assert "Nessun prezzo recente" in messaggio([], "GPL", "Riccione")


@respx.mock
def test_osservaprezzi_via_http():
    respx.get(URL_ANAGRAFICA).mock(return_value=httpx.Response(200, text=ANAGRAFICA))
    respx.get(URL_PREZZI).mock(return_value=httpx.Response(200, text=PREZZI))
    with httpx.Client() as http:
        offerte = Osservaprezzi(http).offerte("Gasolio", RICCIONE.lat, RICCIONE.lon, GIORNO_DATI)
        assert offerte
        lontano = Osservaprezzi(http).offerte("Gasolio", 45.46, 9.19, GIORNO_DATI)
    assert lontano == []
