"""Feste fisse, /sagre, /costa e promemoria ricorrenti con i pulsanti rimanda/fatto."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pytest

from azdora.archivio import ArchivioMemoria
from azdora.bot import Azdora
from azdora.config import FUSO_ORARIO
from azdora.costa import COSTA
from azdora.feste import FESTE, in_programma, messaggio_sagre, nel_periodo
from azdora.notizie import Notizia
from azdora.promemoria import (
    ErrorePromemoria,
    RegistroPromemoria,
    elenco,
    leggi_ricorrente,
    prossima_volta,
    pulsanti_avviso,
)
from tests.conftest import PADRONE, messaggio

ADESSO = datetime(2026, 11, 5, 15, 0, tzinfo=FUSO_ORARIO)  # giovedì
OGGI = ADESSO.date()


def ora(giorno, h, m=0):
    return datetime(giorno.year, giorno.month, giorno.day, h, m, tzinfo=FUSO_ORARIO)


# --- Feste fisse -------------------------------------------------------------------------


def test_la_fiera_dei_becchi_c_e():
    becchi = next(f for f in FESTE if "Becchi" in f.nome)
    assert becchi.posto == "Santarcangelo"
    assert becchi.periodo(2026) == (date(2026, 11, 7), date(2026, 11, 11))


def test_feste_in_programma():
    nomi = [f.nome for f, _, _ in in_programma(OGGI)]
    assert any("Becchi" in n for n in nomi)
    assert any("Presepe" in n for n in nomi)  # inizia il 29/11, entro un mese
    assert not any("Sapore di Sale" in n for n in nomi)
    settembre = [f.nome for f, _, _ in in_programma(date(2026, 8, 25))]
    assert "Sapore di Sale" in settembre


def test_feste_a_cavallo_dell_anno():
    presepe = next(f for f in FESTE if "Presepe" in f.nome)
    assert presepe.periodo(2026) == (date(2026, 11, 29), date(2027, 1, 6))
    capodanno = [f.nome for f, _, _ in in_programma(date(2027, 1, 2))]
    assert "Presepe della Marineria" in capodanno


def test_feste_nel_weekend():
    nomi = [f.nome for f in nel_periodo(date(2026, 11, 7), date(2026, 11, 8))]
    assert any("Becchi" in n for n in nomi)
    assert nel_periodo(date(2026, 7, 18), date(2026, 7, 19)) == []


def test_messaggio_sagre():
    eventi = [Notizia("Sagra della castagna a Verucchio", "https://a.it", "Altarimini")]
    testo = messaggio_sagre(OGGI, eventi)
    assert "Fiera dei Becchi" in testo and "dal 7/11 al 11/11" in testo
    assert "Sagra della castagna" in testo
    assert "date indicative" in testo
    vuoto = messaggio_sagre(date(2026, 7, 1), [])
    assert "non ho trovato sagre" in vuoto
    in_corso = messaggio_sagre(date(2026, 11, 9), [])
    assert "in corso fino al 11/11" in in_corso


# --- Promemoria ricorrenti -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("frase", "quando", "cosa", "ripeti"),
    [
        (
            "ricordami ogni lunedì alle 20 di portare fuori la plastica",
            ora(date(2026, 11, 9), 20),
            "portare fuori la plastica",
            "settimana",
        ),
        (
            "ricordami ogni giorno alle 8 la pastiglia",
            ora(date(2026, 11, 6), 8),
            "la pastiglia",
            "giorno",
        ),
        ("ricordami ogni sera di chiudere il gas", ora(OGGI, 20), "chiudere il gas", "giorno"),
        ("ricordami ogni mattina di bere", ora(date(2026, 11, 6), 8), "bere", "giorno"),
        (
            "ricordami ogni mese il 5 di pagare l'affitto",
            ora(date(2026, 12, 5), 9),
            "pagare l'affitto",
            "mese",
        ),
        (
            "ricordami ogni mese il 20 la bolletta",
            ora(date(2026, 11, 20), 9),
            "la bolletta",
            "mese",
        ),
        ("ricordami ogni mese il 31 di x", ora(date(2026, 11, 30), 9), "x", "mese"),
        (
            "ricordami tutti i venerdì di fare la spesa",
            ora(date(2026, 11, 6), 9),
            "fare la spesa",
            "settimana",
        ),
        (
            "ricordami ogni anno il 14/2 di san valentino",
            ora(date(2027, 2, 14), 9),
            "san valentino",
            "anno",
        ),
        ("ricordami domani alle 9 x", ora(date(2026, 11, 6), 9), "x", None),
    ],
)
def test_leggi_ricorrente(frase, quando, cosa, ripeti):
    assert leggi_ricorrente(frase, ADESSO) == (quando, cosa, ripeti)


def test_ricorrente_sbagliato():
    with pytest.raises(ErrorePromemoria):
        leggi_ricorrente("ricordami ogni mese il 40 di x", ADESSO)
    with pytest.raises(ErrorePromemoria):
        leggi_ricorrente("ricordami ogni giorno", ADESSO)


def test_prossima_volta():
    base = ora(date(2026, 1, 31), 9)
    assert prossima_volta(base, "giorno") == ora(date(2026, 2, 1), 9)
    assert prossima_volta(base, "settimana") == ora(date(2026, 2, 7), 9)
    assert prossima_volta(base, "mese") == ora(date(2026, 2, 28), 9)
    assert prossima_volta(base, "anno") == ora(date(2027, 1, 31), 9)


def test_ricorrenti_si_ripetono_e_i_singoli_spariscono():
    registro = RegistroPromemoria(ArchivioMemoria(), 1)
    registro.aggiungi(ora(OGGI, 16), "pastiglia", "giorno")
    registro.aggiungi(ora(OGGI, 16), "una volta")
    consegnati = registro.scaduti(ora(OGGI, 16, 5))
    assert [p.testo for p in consegnati] == ["pastiglia", "una volta"]
    (rimasto,) = registro.tutti()
    assert rimasto.quando == ora(date(2026, 11, 6), 16) and rimasto.ripeti == "giorno"
    # Saltato qualche giorno (es. bot fermo): si recupera fino alla prossima volta futura.
    assert registro.scaduti(ora(date(2026, 11, 9), 10))[0].testo == "pastiglia"
    assert registro.tutti()[0].quando == ora(date(2026, 11, 9), 16)
    assert registro.testo_consegnato(consegnati[1].id) == "una volta"
    assert registro.testo_consegnato(99) is None
    assert "🔁 ogni giorno" in elenco(registro.tutti(), ADESSO)


def test_gli_id_non_si_riusano():
    registro = RegistroPromemoria(ArchivioMemoria(), 1)
    a = registro.aggiungi(ora(OGGI, 16), "a")
    registro.scaduti(ora(OGGI, 17))
    b = registro.aggiungi(ora(OGGI, 18), "b")
    assert b.id != a.id


def test_pulsanti_avviso():
    registro = RegistroPromemoria(ArchivioMemoria(), 1)
    voce = registro.aggiungi(ora(OGGI, 16), "x")
    dati = [t["callback_data"] for fila in pulsanti_avviso(voce)["inline_keyboard"] for t in fila]
    assert dati == [f"prpost:{voce.id}:60", f"prpost:{voce.id}:d", f"prok:{voce.id}"]


# --- Dentro il bot -------------------------------------------------------------------------


class Orologio:
    def __init__(self):
        self.adesso = ADESSO

    def __call__(self):
        return self.adesso


@dataclass
class EventiFinti:
    eventi_trovati: list[Notizia] = field(default_factory=list)

    def notizie_per(self, comune, lat, lon, adesso):
        return []

    def del_giorno_in_riviera(self, comune, adesso):
        return []

    def eventi(self, adesso):
        return list(self.eventi_trovati)


@pytest.fixture
def orologio():
    return Orologio()


@pytest.fixture
def bot(impostazioni, messaggero, meteo, luoghi, archivio, orologio):
    return Azdora(
        impostazioni,
        messaggero,
        meteo,
        luoghi,
        archivio=archivio,
        notizie=EventiFinti([Notizia("Sagra a Gradara", "https://g.it", "Altarimini")]),
        orologio=orologio,
    )


def tocco(dati, id_messaggio=9):
    return {
        "callback_query": {
            "id": "t",
            "message": {"message_id": id_messaggio, "chat": {"id": PADRONE, "type": "private"}},
            "data": dati,
        }
    }


def test_promemoria_ricorrente_dal_bot(bot, messaggero, archivio, orologio):
    bot.gestisci_update(messaggio("ricordami ogni sera di chiudere il gas"))
    assert "e poi 🔁 ogni giorno" in messaggero.ultimo
    orologio.adesso = ora(OGGI, 20, 5)
    assert bot.controlla_promemoria() == 1
    assert messaggero.tastiere[-1]["inline_keyboard"][1][0]["callback_data"].startswith("prok:")
    orologio.adesso = ora(date(2026, 11, 6), 20, 5)
    assert bot.controlla_promemoria() == 1  # anche la sera dopo


def test_rimandare_di_un_ora_e_a_domani(bot, messaggero, archivio, orologio):
    bot.gestisci_update(messaggio("ricordami tra 10 minuti di girare il sugo"))
    orologio.adesso = ADESSO + timedelta(minutes=15)
    bot.controlla_promemoria()
    voce_id = messaggero.tastiere[-1]["inline_keyboard"][0][0]["callback_data"].split(":")[1]
    bot.gestisci_update(tocco(f"prpost:{voce_id}:60"))
    assert "te lo ricordo oggi alle 16:15" in messaggero.modificati[-1][2]
    assert messaggero.risposte_pulsanti[-1][1] == "⏰ Rimandato a oggi alle 16:15"
    bot.gestisci_update(tocco(f"prpost:{voce_id}:d"))
    assert "domani alle 15:15" in messaggero.modificati[-1][2]
    testi = [p.testo for p in RegistroPromemoria(archivio, PADRONE).tutti()]
    assert testi == ["girare il sugo", "girare il sugo"]


def test_segnare_fatto(bot, messaggero, orologio):
    bot.gestisci_update(messaggio("ricordami tra 5 minuti di uscire"))
    orologio.adesso = ADESSO + timedelta(minutes=10)
    bot.controlla_promemoria()
    voce_id = messaggero.tastiere[-1]["inline_keyboard"][1][0]["callback_data"].split(":")[1]
    bot.gestisci_update(tocco(f"prok:{voce_id}"))
    assert messaggero.modificati[-1][2] == "✅ <s>uscire</s>"
    assert messaggero.modificati[-1][3] == {"inline_keyboard": []}


def test_rimandare_un_promemoria_sconosciuto(bot, messaggero):
    bot.gestisci_update(tocco("prpost:77:60"))
    assert messaggero.risposte_pulsanti[-1][1] == "Questo promemoria è troppo vecchio"


def test_comando_sagre(bot, messaggero):
    bot.gestisci_update(messaggio("/sagre"))
    assert "Fiera dei Becchi" in messaggero.ultimo
    assert "Sagra a Gradara" in messaggero.ultimo


def test_comando_sagre_senza_notizie(impostazioni, messaggero, meteo):
    Azdora(impostazioni, messaggero, meteo, orologio=lambda: ADESSO).gestisci_update(
        messaggio("/sagre")
    )
    assert "Fiera dei Becchi" in messaggero.ultimo


def test_weekend_con_le_feste(bot, messaggero):
    bot.gestisci_update(messaggio("/weekend"))
    assert "Feste di questo weekend" in messaggero.ultimo
    assert "Fiera dei Becchi" in messaggero.ultimo


def test_comando_costa(bot, messaggero, meteo):
    bot.gestisci_update(messaggio("/costa"))
    testo = messaggero.ultimo
    assert "La costa oggi" in testo and "vento adesso" in testo
    nomi = [loc.nome.split(" ·")[0] for loc in COSTA]
    assert [testo.index(n) for n in nomi] == sorted(testo.index(n) for n in nomi)  # da nord a sud
    assert "🌊" in testo
    bot.gestisci_update(messaggio("/costa domani"))
    assert "La costa domani" in messaggero.ultimo and "vento massimo" in messaggero.ultimo
