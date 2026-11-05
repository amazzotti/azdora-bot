from dataclasses import replace
from datetime import date

import pytest

from azdora.archivio import ArchivioMemoria
from azdora.lista import MASSIMO_VOCI, Lista, Voce, leggi_voci, messaggio_lista, pulsanti_lista
from azdora.meteo.openmeteo import MeteoGiorno
from azdora.notizie import Notizia, e_in_riviera, e_un_evento
from azdora.weekend import (
    IDEE,
    brutto_tempo,
    messaggio_weekend,
    prossimo_weekend,
    scegli_idee,
)

SABATO = date(2026, 11, 7)
BEL_TEMPO = MeteoGiorno(SABATO, 1, 12, 20, 5, 0, 10, 20, 90, None, None, 3)
PIOGGIA = replace(BEL_TEMPO, codice=63, prob_pioggia=85)

# --- Lista --------------------------------------------------------------------------------


def test_leggi_voci():
    assert leggi_voci("latte, pane;uova\n- detersivo , ,") == ["latte", "pane", "uova", "detersivo"]
    assert leggi_voci("") == []
    assert len(leggi_voci("x" * 200)[0]) == 80


def test_lista_completa():
    lista = Lista(ArchivioMemoria(), -5)
    nuove = lista.aggiungi(["latte", "pane"], da="Ale")
    assert [v.id for v in nuove] == [1, 2]
    assert lista.cambia(1) == Voce(1, "latte", True, "Ale")
    assert lista.cambia(1).fatta is False
    lista.cambia(2)
    assert lista.cambia(99) is None
    assert lista.togli_fatte() == 1
    assert [v.testo for v in lista.voci()] == ["latte"]
    assert lista.aggiungi(["uova"])[0].id == 2  # riparte dal più alto rimasto


def test_lista_ha_un_limite():
    lista = Lista(ArchivioMemoria(), 1)
    lista.aggiungi([f"voce {i}" for i in range(MASSIMO_VOCI + 10)])
    assert len(lista.voci()) == MASSIMO_VOCI
    assert lista.aggiungi(["ancora"]) == []


def test_testo_e_pulsanti_lista():
    assert "vuota" in messaggio_lista([])
    assert pulsanti_lista([]) is None
    voci = [Voce(1, "latte <bio>", False, "Giulia"), Voce(2, "pane", True)]
    testo = messaggio_lista(voci)
    assert "1 da fare su 2" in testo
    assert "⬜ latte &lt;bio&gt; <i>(Giulia)</i>" in testo
    assert "✅ <s>pane</s>" in testo
    righe = pulsanti_lista(voci)["inline_keyboard"]
    assert righe[0][0]["callback_data"] == "voce:1"
    assert righe[-1][0]["callback_data"] == "lista:pulisci"
    assert len(pulsanti_lista(voci[:1])["inline_keyboard"]) == 1  # niente da pulire


# --- Weekend ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("oggi", "sabato"),
    [
        (date(2026, 11, 5), date(2026, 11, 7)),  # giovedì
        (date(2026, 11, 7), date(2026, 11, 7)),  # sabato
        (date(2026, 11, 8), date(2026, 11, 7)),  # domenica: è ancora questo weekend
        (date(2026, 11, 9), date(2026, 11, 14)),  # lunedì
    ],
)
def test_prossimo_weekend(oggi, sabato):
    assert prossimo_weekend(oggi) == (sabato, date.fromordinal(sabato.toordinal() + 1))


def test_brutto_tempo():
    assert not brutto_tempo(BEL_TEMPO)
    assert brutto_tempo(PIOGGIA)
    assert brutto_tempo(replace(BEL_TEMPO, prob_pioggia=60))
    assert not brutto_tempo(replace(BEL_TEMPO, codice=61, prob_pioggia=10))
    assert not brutto_tempo(None)


def test_scegli_idee_ruota_e_rispetta_il_tipo():
    fuori = scegli_idee(False, 45)
    dentro = scegli_idee(True, 45)
    assert len(fuori) == 3 and not any(i.al_coperto for i in fuori)
    assert all(i.al_coperto for i in dentro)
    assert scegli_idee(False, 46) != fuori
    assert len({i.nome for i in IDEE}) == len(IDEE)


def test_messaggio_weekend_bello_con_eventi():
    eventi = [Notizia("Sagra della piadina", "https://a.it", "Altarimini")]
    testo = messaggio_weekend(BEL_TEMPO, BEL_TEMPO, SABATO, eventi)
    assert "Giri della domenica" in testo
    assert "Sabato: 🌤️" in testo
    assert "Sagre ed eventi" in testo and 'href="https://a.it"' in testo


def test_messaggio_weekend_bagnato_senza_eventi():
    testo = messaggio_weekend(PIOGGIA, PIOGGIA, SABATO, [])
    assert "idee al coperto" in testo
    assert "Sagre" not in testo


def test_messaggio_weekend_senza_previsioni():
    testo = messaggio_weekend(BEL_TEMPO, None, SABATO, [])
    assert "Domenica: previsioni non ancora disponibili" in testo


def test_filtri_eventi():
    sagra = Notizia("Sagra a Santarcangelo", "https://a", "Altarimini", categorie=("Eventi",))
    lontana = Notizia("Sagra del tartufo ad Alba", "https://b", "Gazzetta d'Alba")
    assert e_un_evento(sagra) and e_in_riviera(sagra)
    assert not e_in_riviera(lontana)
    assert not e_un_evento(Notizia("Cronaca", "https://c", "x", categorie=("Cronaca",)))
