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
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
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
    area: str = "Rimini"  # Cervia, Cesena, Rimini o Pesaro: serve a dare spazio a ogni zona
    # Per i giornali di una città più grande (es. Ravenna): si tengono solo le notizie che
    # nominano uno di questi posti.
    solo_su: tuple[str, ...] = ()


def url_google_news_sito(dominio: str) -> str:
    """Gli articoli di un giornale presi da Google News, con i link al sito originale.

    Si usa per i giornali che rifiutano i programmi automatici sul loro feed: il bot non
    finge di essere un browser, chiede a Google News che li pubblica legittimamente.
    """
    domanda = quote_plus(f"site:{dominio} when:1d")
    return f"https://news.google.com/rss/search?q={domanda}&hl=it&gl=IT&ceid=IT:it"


# I giornali da Cesena a Pesaro.
def url_google_news_posti(*posti: str) -> str:
    """Ricerca su Google News di più posti insieme, nell'ultimo giorno."""
    domanda = quote_plus(" OR ".join(f'"{p}"' for p in posti) + " when:1d")
    return f"https://news.google.com/rss/search?q={domanda}&hl=it&gl=IT&ceid=IT:it"


LITORALE_CERVIA = (
    "Cervia",
    "Milano Marittima",
    "Cesenatico",
    "Pinarella",
    "Tagliata",
    "Savio",
    "Gatteo",
)

FONTI_ZONA: tuple[Fonte, ...] = (
    # Cervia, Milano Marittima e Cesenatico: dai giornali di Ravenna solo le notizie su di loro
    Fonte(
        "il Resto del Carlino Ravenna",
        "https://www.ilrestodelcarlino.it/ravenna/rss",
        "Cervia",
        LITORALE_CERVIA,
    ),
    Fonte("Ravenna Today", "https://www.ravennatoday.it/rss", "Cervia", LITORALE_CERVIA),
    Fonte("Ravenna Notizie", "https://www.ravennanotizie.it/feed/", "Cervia", LITORALE_CERVIA),
    Fonte(
        "Google News",
        url_google_news_posti("Cervia", "Milano Marittima", "Cesenatico"),
        "Cervia",
        LITORALE_CERVIA,
    ),
    # Cesena
    Fonte("il Resto del Carlino Cesena", "https://www.ilrestodelcarlino.it/cesena/rss", "Cesena"),
    Fonte("Cesena Notizie", "https://www.cesenanotizie.net/feed/", "Cesena"),
    Fonte("Cesena Today", url_google_news_sito("cesenatoday.it"), "Cesena"),
    Fonte("Romagna Notizie", "https://www.romagnanotizie.net/feed/", "Cesena"),
    # Rimini, Riccione e dintorni
    Fonte("Rimini Today", url_google_news_sito("riminitoday.it"), "Rimini"),
    Fonte("Altarimini", "https://www.altarimini.it/feed/", "Rimini"),
    Fonte("Chiamami Città", "https://www.chiamamicitta.it/feed/", "Rimini"),
    Fonte("il Resto del Carlino Rimini", "https://www.ilrestodelcarlino.it/rimini/rss", "Rimini"),
    Fonte("NewsRimini", url_google_news_sito("newsrimini.it"), "Rimini"),
    # Pesaro, Gabicce e dintorni
    Fonte("il Resto del Carlino Pesaro", "https://www.ilrestodelcarlino.it/pesaro/rss", "Pesaro"),
    Fonte("Pesaro Notizie", "https://www.pesaronotizie.com/feed/", "Pesaro"),
    Fonte("Corriere Adriatico Pesaro", "https://www.corriereadriatico.it/rss/pesaro.xml", "Pesaro"),
)
FONTI_RIVIERA = FONTI_ZONA  # nome storico
AREA_DELLA_FONTE = {f.nome: f.area for f in FONTI_ZONA}

# Frazioni e località di mare → il comune di cui fanno parte: se della frazione non si
# parla, si cercano le notizie del comune.
FRAZIONI = {
    "milano marittima": "Cervia",
    "pinarella": "Cervia",
    "tagliata": "Cervia",
    "valverde": "Cesenatico",
    "villamarina": "Cesenatico",
    "zadina": "Cesenatico",
    "gatteo a mare": "Gatteo",
    "igea marina": "Bellaria-Igea Marina",
    "viserba": "Rimini",
    "viserbella": "Rimini",
    "torre pedrera": "Rimini",
    "rivabella": "Rimini",
    "miramare": "Rimini",
    "rivazzurra": "Rimini",
    "marebello": "Rimini",
    "portoverde": "Misano Adriatico",
    "fiorenzuola di focara": "Pesaro",
}

# I giornali locali si leggono se sei entro questo raggio da Cesena, Rimini o Pesaro.
CENTRI_ZONA = {
    "Cervia": (44.2630, 12.3490),
    "Cesena": (44.1391, 12.2431),
    "Rimini": (44.0594, 12.5683),
    "Pesaro": (43.9098, 12.9131),
}
RAGGIO_ZONA_KM = 30


def in_zona(lat: float, lon: float) -> bool:
    """Sei tra Cesena e Pesaro (o lì vicino)?"""
    return any(distanza_km(lat, lon, *centro) <= RAGGIO_ZONA_KM for centro in CENTRI_ZONA.values())


MASSIMO_ETA = timedelta(hours=36)
NOTIZIE_DEL_GIORNO = 10
MASSIMO_SUL_COMUNE = 4
MINIMO_NOTIZIE_GIORNO = 5
# Google News a volte pesca siti di scommesse e simili: non sono notizie.
_SPAZZATURA = re.compile(r"scommess|pronostic|betting|casin[oò]|zazoom|howl\.link", re.IGNORECASE)
_PAGINA_ELENCO = re.compile(
    r"^(notizie su|ultim['’]ora|ultime notizie|tutte le notizie|news su)(\s|$)", re.IGNORECASE
)
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
    area: str = ""  # la zona del giornale da cui arriva (Cervia, Cesena, Rimini, Pesaro)

    @property
    def impronta(self) -> str:
        """Identificativo stabile: lo stesso articolo ripreso da due feed ha la stessa impronta."""
        # Solo lettere e numeri: "l’offerta" e "l'offerta" sono lo stesso titolo.
        titolo = " ".join(re.findall(r"\w+", self.titolo.lower()))
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


def riguarda(notizia: Notizia, posto: str) -> bool:
    """Come ``parla_di``, ma vale anche il nome del giornale ("La Voce di Cesenatico")."""
    return parla_di(notizia, posto) or posto.lower() in notizia.fonte.lower()


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
    """Scommesse e pagine elenco ("Ultim'ora Cesena", "Notizie su …"): non sono notizie."""
    if _SPAZZATURA.search(notizia.fonte) or _SPAZZATURA.search(notizia.titolo):
        return True
    return bool(_PAGINA_ELENCO.match(notizia.titolo))


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


def formatta(notizie: Sequence[Notizia], titolo: str, evidenzia: str | None = None) -> str:
    """Elenco di notizie con link; quelle che parlano di ``evidenzia`` hanno 📍."""
    righe = [titolo]
    for n in notizie:
        quando = f" · {n.data.astimezone(FUSO_ORARIO).strftime('%H:%M')}" if n.data else ""
        icona = "📍" if evidenzia and parla_di(n, evidenzia) else "📰"
        righe.append(
            f'\n{icona} <a href="{escape(n.link, quote=True)}">{escape(n.titolo)}</a>\n'
            f"<i>{escape(n.fonte)}{quando}</i>"
        )
    return "\n".join(righe)


def del_giorno(notizie: Iterable[Notizia], adesso: datetime) -> list[Notizia]:
    """Le notizie di oggi (ora di Roma); se sono poche, quelle delle ultime 24 ore."""
    tutte = [n for n in notizie if n.data is not None]
    oggi = adesso.astimezone(FUSO_ORARIO).date()
    di_oggi = [n for n in tutte if n.data.astimezone(FUSO_ORARIO).date() == oggi]
    if len(di_oggi) >= MINIMO_NOTIZIE_GIORNO:
        return di_oggi
    return [n for n in tutte if adesso - n.data <= timedelta(hours=24)]


def alternate(
    notizie: Sequence[Notizia],
    quante: int,
    gruppo: Callable[[Notizia], str] = lambda n: n.fonte,
) -> list[Notizia]:
    """Sceglie le più recenti alternando i gruppi (giornali o zone); poi le ordina."""
    per_fonte: dict[str, list[Notizia]] = {}
    for n in ordina(notizie):
        per_fonte.setdefault(gruppo(n), []).append(n)
    scelte: list[Notizia] = []
    while len(scelte) < quante and any(per_fonte.values()):
        for code in per_fonte.values():
            if code and len(scelte) < quante:
                scelte.append(code.pop(0))
    return ordina(scelte)


def prima_quelle_di(notizie: Sequence[Notizia], comune: str) -> list[Notizia]:
    """Le notizie sul comune in cima, poi le altre, ognuna dalla più recente."""
    vicine = [n for n in notizie if parla_di(n, comune)]
    altre = [n for n in notizie if not parla_di(n, comune)]
    return [*ordina(vicine), *ordina(altre)]


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

PAESI_EVENTI = (
    "Cervia",
    "Milano Marittima",
    "Cesenatico",
    "Cesena",
    "Bellaria",
    "Rimini",
    "Riccione",
    "Santarcangelo",
    "Misano",
    "Cattolica",
    "Gabicce",
    "Gradara",
    "Pesaro",
    "San Marino",
    "Verucchio",
    "San Leo",
)
URL_EVENTI_GOOGLE = (
    "https://news.google.com/rss/search?q="
    + quote_plus(
        '(sagra OR fiera OR festa OR mercatino OR "fine settimana") ('
        + " OR ".join(f'"{p}"' for p in PAESI_EVENTI)
        + ") when:14d"
    )
    + "&hl=it&gl=IT&ceid=IT:it"
)
GIORNI_EVENTI = 14
URL_ALTARIMINI = "https://www.altarimini.it/feed/"
_RIVIERA = re.compile(
    r"rimini|riccione|romagn|cattolica|misano|bellaria|santarcangelo|verucchio|san leo|"
    r"gradara|gabicce|san marino|cesena|forl[iì]|ravenna|cervia|milano marittima|"
    r"valmarecchia|valconca|"
    r"pesaro|fano|urbino",
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

    def principali(self, sezione: str, adesso: datetime) -> list[Notizia]:
        """Le notizie più importanti di "italia" o "mondo", in ordine di importanza."""
        ...

    def del_giorno_in_riviera(self, comune: str, adesso: datetime) -> list[Notizia]:
        """Le ultime notizie di oggi da tutti i giornali della Riviera, il comune in cima."""
        ...

    def tutte_di_oggi(self, adesso: datetime) -> list[Notizia]:
        """Tutte le notizie di oggi dei giornali locali, pulite e senza doppioni."""
        ...


# --- Italia e mondo -------------------------------------------------------------------------

# Google News mette in ordine di importanza i titoli dei principali giornali; se non
# risponde, si ripiega su RaiNews (Italia) e ANSA (mondo).
FONTI_PRINCIPALI = {
    "italia": (
        Fonte(
            "Google News",
            "https://news.google.com/rss/headlines/section/topic/NATION?hl=it&gl=IT&ceid=IT:it",
            "Italia",
        ),
        Fonte("RaiNews", "https://www.rainews.it/rss/ultimora", "Italia"),
    ),
    "mondo": (
        Fonte(
            "Google News",
            "https://news.google.com/rss/headlines/section/topic/WORLD?hl=it&gl=IT&ceid=IT:it",
            "Mondo",
        ),
        Fonte("ANSA", "https://www.ansa.it/sito/notizie/mondo/mondo_rss.xml", "Mondo"),
    ),
}
ETA_PRINCIPALI = timedelta(hours=24)


class LettoreNotizie:
    """Scarica i feed veri."""

    def __init__(self, http: httpx.Client, fonti_riviera: Sequence[Fonte] = FONTI_RIVIERA):
        self._http = http
        self._fonti_riviera = fonti_riviera
        # Durante un giro di controlli per più persone ogni feed si scarica una volta sola.
        self._gia_scaricati: dict[str, list[Notizia]] = {}

    def _scarica(self, fonte: Fonte) -> list[Notizia]:
        if fonte.url not in self._gia_scaricati:
            self._gia_scaricati[fonte.url] = self._scarica_davvero(fonte)
        return self._gia_scaricati[fonte.url]

    def _scarica_davvero(self, fonte: Fonte) -> list[Notizia]:
        notizie = self._leggi(fonte.url, fonte.nome) or []
        if fonte.url.startswith("https://news.google.com/") and fonte.nome != "Google News":
            # Da Google arrivano con il nome scritto dal giornale ("RiminiToday"): uniformiamo.
            notizie = [replace(n, fonte=fonte.nome) for n in notizie]
        if fonte.solo_su:
            notizie = [n for n in notizie if any(riguarda(n, posto) for posto in fonte.solo_su)]
        return [replace(n, area=fonte.area) for n in notizie]

    def _leggi(self, url: str, nome: str) -> list[Notizia] | None:
        """Scarica e legge un feed; ``None`` se il sito non risponde o rifiuta la richiesta."""
        try:
            risposta = self._http.get(url, follow_redirects=True)
            risposta.raise_for_status()
        except httpx.HTTPError as errore:
            # Un giornale giù non deve fermare gli altri.
            log.warning("Feed %s non disponibile: %s", nome, errore)
            return None
        return leggi_feed(risposta.content, nome)

    def del_giorno_in_riviera(self, comune: str, adesso: datetime) -> list[Notizia]:
        tutte = [n for fonte in self._fonti_riviera for n in self._scarica(fonte)]
        pulite = senza_doppioni(n for n in del_giorno(tutte, adesso) if not e_spazzatura(n))
        # Al massimo 6 sul tuo comune: le altre 4 raccontano il resto della Riviera.
        vicine = ordina(n for n in pulite if parla_di(n, comune))[:MASSIMO_SUL_COMUNE]
        # Le altre si dividono tra Cesena, Rimini e Pesaro, poi tra i giornali.
        altre = alternate(
            [n for n in pulite if not parla_di(n, comune)],
            NOTIZIE_DEL_GIORNO - len(vicine),
            gruppo=lambda n: n.area or n.fonte,
        )
        return prima_quelle_di([*vicine, *altre], comune)

    def notizie_per(self, comune: str, lat: float, lon: float, adesso: datetime) -> list[Notizia]:
        trovate: list[Notizia] = []
        if in_zona(lat, lon):
            for fonte in self._fonti_riviera:
                trovate += [n for n in self._scarica(fonte) if parla_di(n, comune)]
        # Google cerca anche dentro gli articoli e pesca roba lontana: si tiene solo ciò
        # che nomina il posto nel titolo o arriva da un giornale con quel nome.
        da_google = self._scarica(Fonte("Google News", url_google_news(comune)))
        trovate += [n for n in da_google if riguarda(n, comune)]
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
            n
            for n in [*locali, *google]
            if n.data is None or adesso - n.data <= timedelta(days=GIORNI_EVENTI)
        ]
        return ordina(senza_doppioni(settimana))

    def principali(self, sezione: str, adesso: datetime) -> list[Notizia]:
        for fonte in FONTI_PRINCIPALI.get(sezione, ()):
            # Qui conta l'ordine del giornale (dalla più importante), non l'ora.
            notizie = [
                n
                for n in self._scarica(fonte)
                if not e_spazzatura(n) and (n.data is None or adesso - n.data <= ETA_PRINCIPALI)
            ]
            if notizie:
                return senza_doppioni(notizie)
        return []


# --- Notizie divise per città -----------------------------------------------------------------

# Da nord a sud: (città, emoji, parole che la fanno riconoscere).
CITTA: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Cervia", "🧂", ("Cervia", "Milano Marittima", "Pinarella", "Tagliata")),
    ("Cesenatico", "⚓", ("Cesenatico", "Gatteo", "Valverde", "Villamarina")),
    ("Cesena", "🏛️", ("Cesena",)),
    ("Bellaria", "🏖️", ("Bellaria", "Igea Marina")),
    ("Rimini", "🎡", ("Rimini", "Viserba", "Torre Pedrera", "Miramare", "Rivazzurra")),
    ("Santarcangelo", "🍷", ("Santarcangelo",)),
    ("Riccione", "🌴", ("Riccione",)),
    ("Misano", "🏍️", ("Misano",)),
    ("Cattolica", "⛵", ("Cattolica", "Gabicce", "Gradara")),
    ("Pesaro", "🎼", ("Pesaro", "Fano")),
)
ALTRE = "Dintorni"
_EMOJI_CITTA = {nome: emoji for nome, emoji, _ in CITTA}
_SCHEMI_CITTA = [
    (nome, re.compile(r"\b(?:" + "|".join(re.escape(p) for p in parole) + r")\b", re.IGNORECASE))
    for nome, _, parole in CITTA
]


def citta_di(notizia: Notizia) -> str:
    """La città di cui parla una notizia: quella nominata per prima nel titolo, poi nel
    sommario, nelle etichette o nel nome del giornale; altrimenti la zona del giornale."""
    for testo in (notizia.titolo, notizia.sommario, " ".join(notizia.categorie), notizia.fonte):
        trovate = [
            (m.start(), nome) for nome, schema in _SCHEMI_CITTA if (m := schema.search(testo))
        ]
        if trovate:
            return min(trovate)[1]
    return notizia.area if notizia.area in _EMOJI_CITTA else ALTRE


def citta_del_comune(comune: str) -> str | None:
    """``"Misano Adriatico"`` → ``"Misano"``; ``"Bologna"`` → ``None``."""
    for nome, schema in _SCHEMI_CITTA:
        if schema.search(comune):
            return nome
    return None


def raggruppa(
    notizie: Sequence[Notizia],
    prima: str | None,
    per_citta: int = 2,
    nella_prima: int = 4,
    massimo: int = 16,
) -> list[tuple[str, list[Notizia]]]:
    """Notizie divise per città: la tua per prima, poi da nord a sud.

    Si scelgono a giri (una per città, poi la seconda…) così nessuna città resta fuori.
    """
    per_nome: dict[str, list[Notizia]] = {}
    for n in ordina(notizie):
        per_nome.setdefault(citta_di(n), []).append(n)
    ordine = [c for c, _, _ in CITTA if c != prima] + [ALTRE]
    if prima:
        ordine.insert(0, prima)
    limiti = {c: (nella_prima if c == prima else per_citta) for c in ordine}
    scelte: dict[str, list[Notizia]] = {c: [] for c in ordine}
    totale, giro = 0, 0
    while totale < massimo and giro < max(limiti.values()):
        for citta in ordine:
            coda = per_nome.get(citta, [])
            if giro < limiti[citta] and giro < len(coda) and totale < massimo:
                scelte[citta].append(coda[giro])
                totale += 1
        giro += 1
    return [(c, voci) for c, voci in scelte.items() if voci]


def formatta_per_citta(
    gruppi: Sequence[tuple[str, Sequence[Notizia]]], titolo: str, prima: str | None
) -> str:
    righe = [titolo]
    for citta, voci in gruppi:
        emoji = "📍" if citta == prima else _EMOJI_CITTA.get(citta, "🗞️")
        righe += ["", f"{emoji} <b>{escape(citta)}</b>"]
        for n in voci:
            ora = f" · {n.data.astimezone(FUSO_ORARIO):%H:%M}" if n.data else ""
            righe.append(
                f'• <a href="{escape(n.link, quote=True)}">{escape(n.titolo)}</a> '
                f"<i>({escape(n.fonte)}{ora})</i>"
            )
    return "\n".join(righe)


def _tutte_di_oggi(lettore: LettoreNotizie, adesso: datetime) -> list[Notizia]:
    tutte = [n for fonte in lettore._fonti_riviera for n in lettore._scarica(fonte)]
    return ordina(senza_doppioni(n for n in del_giorno(tutte, adesso) if not e_spazzatura(n)))


LettoreNotizie.tutte_di_oggi = _tutte_di_oggi  # type: ignore[attr-defined]
