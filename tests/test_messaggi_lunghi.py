"""Messaggi lunghi e HTML valido: Telegram rifiuta i testi oltre 4096 caratteri visibili e
l'HTML con tag non chiusi. Se succede il messaggio non arriva proprio (es. il riepilogo
del mattino che si fermava a metà, o /mondo senza risposta)."""

from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser

import pytest

from azdora.bot import COMANDI, Azdora
from azdora.config import FUSO_ORARIO
from azdora.notizie import Notizia
from azdora.telegram import LUNGHEZZA_MASSIMA, dividi
from azdora.telegram import testo_visibile as visibile
from tests.conftest import FintoMessaggero, messaggio

ADESSO = datetime(2026, 11, 5, 7, 5, tzinfo=FUSO_ORARIO)
TAG_DI_TELEGRAM = {"b", "i", "u", "s", "a", "code", "pre", "blockquote", "tg-spoiler"}
# I link di Google News sono lunghissimi: è qui che il vecchio taglio a 4096 rompeva l'HTML.
LINK = "https://news.google.com/rss/articles/" + "CBMi" * 150


class ControlloHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.aperti: list[str] = []
        self.errori: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in TAG_DI_TELEGRAM:
            self.errori.append(f"tag non ammesso <{tag}>")
        self.aperti.append(tag)

    def handle_endtag(self, tag):
        if not self.aperti or self.aperti.pop() != tag:
            self.errori.append(f"</{tag}> fuori posto")


def valido(testo: str) -> list[str]:
    controllo = ControlloHTML()
    controllo.feed(testo)
    controllo.close()
    return controllo.errori + [f"<{t}> non chiuso" for t in controllo.aperti]


def tante(prefisso: str, quante: int = 40) -> list[Notizia]:
    return [
        Notizia(
            f'{prefisso}: titolo piuttosto lungo numero {i} con dettagli & virgolette "vere"',
            f"{LINK}{prefisso}{i}",
            "Il Resto del Carlino",
            ADESSO,
        )
        for i in range(quante)
    ]


@dataclass
class FonteAbbondante:
    """Una giornata piena di notizie, come un lunedì d'estate in riviera."""

    def notizie_per(self, comune, lat, lon, adesso):
        return tante(comune)

    def tutte_di_oggi(self, adesso):
        return tante("Riccione") + tante("Cesena") + tante("Pesaro") + tante("Cervia")

    def del_giorno_in_riviera(self, comune, adesso):
        return self.tutte_di_oggi(adesso)

    def eventi(self, adesso):
        return tante("Sagra")

    def principali(self, sezione, adesso):
        return tante(sezione)


@pytest.fixture
def pieno(impostazioni, messaggero, meteo, archivio):
    return Azdora(
        impostazioni,
        messaggero,
        meteo,
        archivio=archivio,
        notizie=FonteAbbondante(),
        orologio=lambda: ADESSO,
    )


COMANDI_DA_PROVARE = [
    "/oggi",
    "/domani",
    "/meteo",
    "/vento",
    "/mare",
    "/notizie",
    "/italia",
    "/mondo",
    "/sagre",
    "/weekend",
    "/aiuto",
    "/orari",
    "/scadenze",
    "/spese",
    "/lista",
    "/promemoria",
    "/compleanni",
    "/compleanno mamma 14/3/1966",
    "/posto",
    "/posto lavoro",
    "/lista fissa",
    "/stato",
    "ricordami di comprare il pane quando arrivo a casa",
    "ricordami il compleanno di <b>Giulia</b> il 2 maggio",
]


@pytest.mark.parametrize("comando", COMANDI_DA_PROVARE)
def test_ogni_comando_risponde_con_html_valido(pieno, messaggero: FintoMessaggero, comando):
    pieno.gestisci_update(messaggio(comando))
    assert messaggero.inviati, f"{comando} non ha risposto"
    for _, testo in messaggero.inviati:
        assert valido(testo) == [], comando
        for parte in dividi(testo):
            assert len(visibile(parte)) <= LUNGHEZZA_MASSIMA
            assert valido(parte) == [], f"{comando}: una parte ha l'HTML rotto"


def test_riepilogo_del_mattino_completo_anche_con_tante_notizie(pieno, messaggero):
    assert pieno.controlla_mattino()
    testo = "\n".join(t for _, t in messaggero.inviati)
    assert valido(testo) == []
    # Arriva fino in fondo: dopo le notizie locali ci sono Italia e Mondo.
    assert "Italia" in testo and "Mondo" in testo
    for _, inviato in messaggero.inviati:
        for parte in dividi(inviato):
            assert len(visibile(parte)) <= LUNGHEZZA_MASSIMA and valido(parte) == []


def test_riassunto_delle_notizie_valido(pieno, messaggero):
    pieno.controlla_notizie()
    for _, testo in messaggero.inviati:
        assert valido(testo) == []


class MessaggeroConMenu(FintoMessaggero):
    """Come Telegram: ricorda il menu impostato e lo restituisce a chi lo chiede."""

    def __init__(self, attuale=("oggi", "meteo")):
        super().__init__()
        self.menu: list[tuple[tuple[str, str], ...]] = []
        self.attuale = list(attuale)
        self.letture = 0

    def imposta_comandi(self, comandi):
        self.menu.append(tuple(comandi))
        self.attuale = [nome for nome, _ in comandi]

    def leggi_comandi(self):
        self.letture += 1
        return list(self.attuale)


def test_menu_vecchio_aggiornato_da_solo(impostazioni, meteo, archivio):
    telegram = MessaggeroConMenu()  # Telegram mostra ancora un menu vecchio
    bot = Azdora(impostazioni, telegram, meteo, archivio=archivio, orologio=lambda: ADESSO)
    assert bot.controlla_menu()
    assert telegram.menu == [COMANDI]
    assert {"italia", "mondo"} <= set(telegram.attuale)
    assert not bot.controlla_menu()  # già verificato: non chiede niente a ogni giro
    assert telegram.letture == 1
    archivio.cancella("menu-comandi")  # passate 6 ore: ricontrolla, ma è già giusto
    assert not bot.controlla_menu() and len(telegram.menu) == 1 and telegram.letture == 2


def test_menu_ricontrollato_anche_senza_memoria(impostazioni, meteo):
    from azdora.archivio import ArchivioMemoria

    telegram = MessaggeroConMenu()
    bot = Azdora(impostazioni, telegram, meteo, archivio=ArchivioMemoria(), orologio=lambda: ADESSO)
    assert bot.controlla_menu() and telegram.menu == [COMANDI]


def test_aiuto_sistema_il_menu(impostazioni, meteo, archivio):
    telegram = MessaggeroConMenu()
    bot = Azdora(impostazioni, telegram, meteo, archivio=archivio, orologio=lambda: ADESSO)
    bot.gestisci_update(messaggio("/aiuto"))
    assert "Azdora" in telegram.ultimo and telegram.menu == [COMANDI]


def test_stato_dice_se_menu_e_sveglia_funzionano(impostazioni, meteo, archivio):
    telegram = MessaggeroConMenu()
    bot = Azdora(impostazioni, telegram, meteo, archivio=archivio, orologio=lambda: ADESSO)
    bot.gestisci_update(messaggio("/stato"))
    assert "nessun giro registrato" in telegram.ultimo
    assert "l'ho appena aggiornato" in telegram.ultimo and telegram.menu == [COMANDI]
    bot.controlli()
    bot.gestisci_update(messaggio("/stato"))
    assert "✅ ultimo giro alle 07:05" in telegram.ultimo
    assert f"✅ aggiornato ({len(COMANDI)} comandi)" in telegram.ultimo


def test_un_controllo_rotto_non_ferma_gli_altri(bot, monkeypatch):
    chiamati = []

    def rotto():
        raise KeyError("dato inatteso")

    monkeypatch.setattr(bot, "controlla_promemoria", rotto)
    monkeypatch.setattr(bot, "controlla_notizie", lambda: chiamati.append("notizie"))
    bot.controlli()
    assert chiamati == ["notizie"]
