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
    def __init__(self):
        super().__init__()
        self.menu: list[tuple[tuple[str, str], ...]] = []

    def imposta_comandi(self, comandi):
        self.menu.append(tuple(comandi))


def test_menu_dei_comandi_aggiornato_da_solo_una_volta(impostazioni, meteo, archivio):
    telegram = MessaggeroConMenu()
    bot = Azdora(impostazioni, telegram, meteo, archivio=archivio, orologio=lambda: ADESSO)
    assert bot.controlla_menu()
    assert telegram.menu == [COMANDI]
    assert {"italia", "mondo"} <= {nome for nome, _ in telegram.menu[0]}
    assert not bot.controlla_menu()  # già aggiornato: non lo rifà a ogni giro
    archivio.scrivi("menu-comandi", "versione-vecchia")
    assert bot.controlla_menu() and len(telegram.menu) == 2


def test_menu_senza_memoria_non_si_tocca(impostazioni, meteo):
    from azdora.archivio import ArchivioMemoria

    telegram = MessaggeroConMenu()
    bot = Azdora(impostazioni, telegram, meteo, archivio=ArchivioMemoria(), orologio=lambda: ADESSO)
    assert not bot.controlla_menu() and telegram.menu == []
