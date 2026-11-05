"""Notizie locali dai feed RSS, filtrate sulla zona in cui ti trovi.

Fonti:
- giornali della provincia di Rimini (Rimini Today, Altarimini, Chiamami Città, Carlino);
- Google News, con una ricerca sul nome del comune: funziona per qualsiasi posto.

L'XML arriva da siti esterni, quindi si legge con ``defusedxml`` (protetto da XML malevoli).
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html import escape, unescape
from typing import Protocol
from urllib.parse import quote_plus

import httpx
from defusedxml import DefusedXmlException, ElementTree

from azdora.config import FUSO_ORARIO
from azdora.luoghi import distanza_km

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Fonte:
    nome: str
    url: str


FONTI_RIVIERA: tuple[Fonte, ...] = (
    Fonte("Rimini Today", "https://www.riminitoday.it/rss"),
    Fonte("Altarimini", "https://www.altarimini.it/feed/"),
    Fonte("Chiamami Città", "https://www.chiamamicitta.it/feed/"),
    Fonte("il Resto del Carlino", "https://www.ilrestodelcarlino.it/rimini/rss"),
)
# I giornali della Riviera si leggono solo se la zona è entro questa distanza da Rimini.
RIMINI = (44.0594, 12.5683)
RAGGIO_RIVIERA_KM = 40

MASSIMO_ETA = timedelta(hours=36)
# Google News a volte pesca siti di scommesse e simili: non sono notizie.
_SPAZZATURA = re.compile(r"scommess|pronostic|betting|casin[oò]", re.IGNORECASE)
LUNGHEZZA_SOMMARIO = 220
_TAG_HTML = re.compile(r"<[^>]+>")
_SPAZI = re.compile(r"\s+")


@dataclass(frozen=True)
class Notizia:
    titolo: str
    link: str
    fonte: str
    data: datetime | None = None
    sommario: str = ""
    categorie: tuple[str, ...] = ()  # es. ("Riccione", "Eventi") su Altarimini

    @property
    def impronta(self) -> str:
        """Identificativo stabile: lo stesso articolo ripreso da due feed ha la stessa impronta."""
        titolo = _SPAZI.sub(" ", self.titolo.lower()).strip()
        return hashlib.sha1(titolo.encode("utf-8")).hexdigest()[:16]


def url_google_news(comune: str) -> str:
    domanda = quote_plus(f'"{comune}" when:1d')
    return f"https://news.google.com/rss/search?q={domanda}&hl=it&gl=IT&ceid=IT:it"


def _testo_pulito(testo: str | None) -> str:
    if not testo:
        return ""
    return _SPAZI.sub(" ", unescape(_TAG_HTML.sub(" ", unescape(testo)))).strip()


def _data(testo: str | None) -> datetime | None:
    if not testo:
        return None
    try:
        return parsedate_to_datetime(testo.strip())
    except (TypeError, ValueError):
        return None


def leggi_feed(xml: bytes | str, fonte: str) -> list[Notizia]:
    """Legge un feed RSS 2.0. Le voci senza titolo o link vengono scartate."""
    try:
        radice = ElementTree.fromstring(xml)
    except (ElementTree.ParseError, DefusedXmlException) as errore:
        log.warning("Feed %s non leggibile: %s", fonte, errore)
        return []
    notizie = []
    for voce in radice.iter("item"):
        titolo = _testo_pulito(voce.findtext("title"))
        link = (voce.findtext("link") or "").strip()
        if not titolo or not link.startswith(("http://", "https://")):
            continue
        nome_fonte = fonte
        sorgente = voce.findtext("source")
        if sorgente:
            # Google News mette il giornale in <source> e in coda al titolo: "Titolo - Giornale".
            nome_fonte = sorgente.strip()
            titolo = titolo.removesuffix(f" - {nome_fonte}")
        sommario = "" if sorgente else _testo_pulito(voce.findtext("description"))
        if len(sommario) > LUNGHEZZA_SOMMARIO:
            sommario = sommario[: LUNGHEZZA_SOMMARIO - 1].rsplit(" ", 1)[0] + "…"
        categorie = tuple(c for c in (_testo_pulito(e.text) for e in voce.findall("category")) if c)
        notizie.append(
            Notizia(titolo, link, nome_fonte, _data(voce.findtext("pubDate")), sommario, categorie)
        )
    return notizie


def parla_di(notizia: Notizia, comune: str) -> bool:
    """La notizia cita il comune (parola intera, maiuscole indifferenti) o è etichettata così?"""
    schema = re.compile(rf"\b{re.escape(comune)}\b", re.IGNORECASE)
    if any(c.lower() == comune.lower() for c in notizia.categorie):
        return True
    return bool(schema.search(notizia.titolo) or schema.search(notizia.sommario))


def recenti(notizie: Iterable[Notizia], adesso: datetime) -> list[Notizia]:
    """Toglie le notizie troppo vecchie (quelle senza data si tengono)."""
    return [n for n in notizie if n.data is None or adesso - n.data <= MASSIMO_ETA]


def e_spazzatura(notizia: Notizia) -> bool:
    return bool(_SPAZZATURA.search(notizia.fonte) or _SPAZZATURA.search(notizia.titolo))


def senza_doppioni(notizie: Iterable[Notizia]) -> list[Notizia]:
    viste: set[str] = set()
    uniche = []
    for notizia in notizie:
        if notizia.impronta not in viste:
            viste.add(notizia.impronta)
            uniche.append(notizia)
    return uniche


def ordina(notizie: Iterable[Notizia]) -> list[Notizia]:
    """Dalla più recente; quelle senza data in fondo."""
    return sorted(notizie, key=lambda n: (n.data is not None, n.data), reverse=True)


def formatta(notizie: Sequence[Notizia], titolo: str) -> str:
    righe = [titolo]
    for n in notizie:
        quando = f" · {n.data.astimezone(FUSO_ORARIO).strftime('%H:%M')}" if n.data else ""
        righe.append(
            f'\n📰 <a href="{escape(n.link, quote=True)}">{escape(n.titolo)}</a>\n'
            f"<i>{escape(n.fonte)}{quando}</i>"
        )
    return "\n".join(righe)


def formatta_breve(notizie: Sequence[Notizia], titolo: str) -> str:
    """Elenco compatto: un link per riga, con il giornale tra parentesi."""
    righe = [titolo]
    righe += [
        f'• <a href="{escape(n.link, quote=True)}">{escape(n.titolo)}</a> '
        f"<i>({escape(n.fonte)})</i>"
        for n in notizie
    ]
    return "\n".join(righe)


# --- Eventi e sagre ------------------------------------------------------------------------

URL_EVENTI_GOOGLE = (
    "https://news.google.com/rss/search?q=(sagra+OR+festa+OR+eventi)+"
    "(Rimini+OR+Riccione+OR+Romagna)+weekend+when:7d&hl=it&gl=IT&ceid=IT:it"
)
URL_ALTARIMINI = "https://www.altarimini.it/feed/"
_RIVIERA = re.compile(
    r"rimini|riccione|romagn|cattolica|misano|bellaria|santarcangelo|verucchio|san leo|"
    r"gradara|san marino|cesena|forl[iì]|ravenna|cervia|valmarecchia|valconca|pesaro",
    re.IGNORECASE,
)


def e_in_riviera(notizia: Notizia) -> bool:
    testo = " ".join((notizia.titolo, notizia.fonte, notizia.sommario, *notizia.categorie))
    return bool(_RIVIERA.search(testo))


def e_un_evento(notizia: Notizia) -> bool:
    return any(c.lower() in {"eventi", "cultura", "spettacoli"} for c in notizia.categorie)


class FonteNotizie(Protocol):
    def notizie_per(self, comune: str, lat: float, lon: float, adesso: datetime) -> list[Notizia]:
        """Notizie recenti che parlano del comune, dalla più recente."""
        ...

    def eventi(self, adesso: datetime) -> list[Notizia]:
        """Sagre, feste ed eventi in Riviera della settimana."""
        ...


class LettoreNotizie:
    """Scarica i feed veri."""

    def __init__(self, http: httpx.Client, fonti_riviera: Sequence[Fonte] = FONTI_RIVIERA):
        self._http = http
        self._fonti_riviera = fonti_riviera

    def _scarica(self, fonte: Fonte) -> list[Notizia]:
        try:
            risposta = self._http.get(fonte.url, follow_redirects=True)
            risposta.raise_for_status()
        except httpx.HTTPError as errore:
            # Un giornale giù non deve fermare gli altri.
            log.warning("Feed %s non disponibile: %s", fonte.nome, errore)
            return []
        return leggi_feed(risposta.content, fonte.nome)

    def notizie_per(self, comune: str, lat: float, lon: float, adesso: datetime) -> list[Notizia]:
        trovate: list[Notizia] = []
        if distanza_km(lat, lon, *RIMINI) <= RAGGIO_RIVIERA_KM:
            for fonte in self._fonti_riviera:
                trovate += [n for n in self._scarica(fonte) if parla_di(n, comune)]
        trovate += self._scarica(Fonte("Google News", url_google_news(comune)))
        pulite = [n for n in recenti(trovate, adesso) if not e_spazzatura(n)]
        return ordina(senza_doppioni(pulite))

    def eventi(self, adesso: datetime) -> list[Notizia]:
        locali = [n for n in self._scarica(Fonte("Altarimini", URL_ALTARIMINI)) if e_un_evento(n)]
        google = [
            n
            for n in self._scarica(Fonte("Google News", URL_EVENTI_GOOGLE))
            if e_in_riviera(n) and not e_spazzatura(n)
        ]
        settimana = [
            n for n in [*locali, *google] if n.data is None or adesso - n.data <= timedelta(days=7)
        ]
        return ordina(senza_doppioni(settimana))
