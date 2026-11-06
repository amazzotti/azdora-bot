"""Guida TV: cosa c'è stasera, i film, cosa c'è adesso, la giornata di un canale.

La programmazione arriva da guide XMLTV gratuite (lo standard delle guide TV aperte), con
più fonti in fila: se la prima non risponde si usa la seguente. Il bot la scarica una
volta al giorno e la tiene in memoria per i giorni successivi (le guide coprono 4-5
giorni): così ``/tv`` funziona anche se per qualche giorno nessuna fonte risponde.
"""

from __future__ import annotations

import gzip
import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from html import escape
from typing import Any, Protocol

import httpx
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from azdora.config import FUSO_ORARIO

log = logging.getLogger(__name__)

# In ordine: la prima ha 195 canali e i generi (Film, Serie…); le altre sono di riserva.
FONTI_GUIDA: tuple[tuple[str, str], ...] = (
    ("epgshare01", "https://epgshare01.online/epgshare01/epg_ripper_IT1.xml.gz"),
    ("Open-EPG", "https://www.open-epg.com/files/italy3.xml"),
    ("Open-EPG", "https://www.open-epg.com/files/italy2.xml"),
)
GIORNI_IN_MEMORIA = 6
LUNGHEZZA_TRAMA = 110

# Canali: (nome mostrato, gruppo, nomi con cui compaiono nelle guide, già "normalizzati").
GRATIS = "gratis"
SKY = "sky"
CANALI: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Rai 1", GRATIS, ("rai1",)),
    ("Rai 2", GRATIS, ("rai2",)),
    ("Rai 3", GRATIS, ("rai3",)),
    ("Rete 4", GRATIS, ("rete4",)),
    ("Canale 5", GRATIS, ("canale5",)),
    ("Italia 1", GRATIS, ("italia1",)),
    ("La7", GRATIS, ("la7",)),
    ("TV8", GRATIS, ("tv8",)),
    ("Nove", GRATIS, ("nove",)),
    ("20", GRATIS, ("20", "20mediaset")),
    ("Rai 4", GRATIS, ("rai4",)),
    ("Iris", GRATIS, ("iris",)),
    ("Rai 5", GRATIS, ("rai5",)),
    ("Rai Movie", GRATIS, ("raimovie",)),
    ("Rai Premium", GRATIS, ("raipremium",)),
    ("Cielo", GRATIS, ("cielo",)),
    ("27 Twentyseven", GRATIS, ("27twentyseven", "twentyseven")),
    ("La5", GRATIS, ("la5",)),
    ("Real Time", GRATIS, ("realtime",)),
    ("Cine34", GRATIS, ("cine34",)),
    ("Focus", GRATIS, ("focus",)),
    ("Giallo", GRATIS, ("giallo", "giallotv")),
    ("Top Crime", GRATIS, ("topcrime",)),
    ("DMAX", GRATIS, ("dmax",)),
    ("La7 Cinema", GRATIS, ("la7cinema", "la7d")),
    ("Italia 2", GRATIS, ("italia2",)),
    ("Warner TV", GRATIS, ("warnertv",)),
    ("Sky Uno", SKY, ("skyuno",)),
    ("Sky Atlantic", SKY, ("skyatlantic",)),
    ("Sky Serie", SKY, ("skyserie",)),
    ("Sky Investigation", SKY, ("skyinvestigation",)),
    ("Sky Cinema Uno", SKY, ("skycinemauno",)),
    ("Sky Cinema Due", SKY, ("skycinemadue",)),
    ("Sky Cinema Collection", SKY, ("skycinemacollection",)),
    ("Sky Cinema Family", SKY, ("skycinemafamily",)),
    ("Sky Cinema Action", SKY, ("skycinemaaction",)),
    ("Sky Cinema Suspense", SKY, ("skycinemasuspense",)),
    ("Sky Cinema Romance", SKY, ("skycinemaromance",)),
    ("Sky Cinema Drama", SKY, ("skycinemadrama",)),
    ("Sky Cinema Comedy", SKY, ("skycinemacomedy",)),
    ("Sky Documentaries", SKY, ("skydocumentaries",)),
    ("Sky Nature", SKY, ("skynature",)),
    ("Sky Sport Uno", SKY, ("skysportuno",)),
    ("Sky Sport Calcio", SKY, ("skysportcalcio",)),
    ("Sky Sport F1", SKY, ("skysportf1",)),
    ("Sky Sport MotoGP", SKY, ("skysportmotogp",)),
)
_ALIAS = {alias: nome for nome, _, alias_canale in CANALI for alias in alias_canale}
GRUPPO_DEL_CANALE = {nome: gruppo for nome, gruppo, _ in CANALI}
ORDINE = {nome: i for i, (nome, _, _) in enumerate(CANALI)}
# Canali dove un programma lungo in prima serata è quasi sempre un film (per le fonti
# di riserva, che non dicono il genere).
GENERI_DA_FILM = {
    "commedia",
    "western",
    "drammatico",
    "thriller",
    "azione",
    "avventura",
    "fantascienza",
    "horror",
    "animazione",
    "romantico",
    "sentimentale",
    "giallo",
    "poliziesco",
    "fantasy",
    "biografico",
    "storico",
    "guerra",
    "musicale",
    "noir",
    "catastrofico",
    "cinema",
}
_EPISODIO = re.compile(r"\bEp\.?\s*\d|\bSt\d+\s*Ep\d|\bStag\.\s*\d+\s*Ep", re.IGNORECASE)
CANALI_CINEMA = {"Iris", "Rai Movie", "Cine34", "La7 Cinema", "Rai 4", "20", "Cielo"}

# Prima serata: il programma che parte tra le 21 e le 22:15 (prima ci sono telegiornali e
# giochi dell'"access"), altrimenti quello in onda alle 21:45.
INIZIO_PRIMA_SERATA = time(21, 0)
FINE_PRIMA_SERATA = time(22, 15)
ORA_PRIMA_SERATA = time(21, 45)


def normalizza_canale(identificativo: str) -> str:
    """``"Rai.1.HD..101.it"``, ``"Rai1.it"``, ``"Rai 1.it"`` → ``"rai1"``."""
    testo = identificativo.lower().strip()
    testo = re.sub(r"\.it$", "", testo)
    testo = re.sub(r"\.\.+\d+\.?$", "", testo)  # numero del canale: "..101"
    testo = re.sub(r"\b(hd|fhd|uhd)\b", "", testo)
    return re.sub(r"[^a-z0-9+]", "", testo).replace("+", "piu")


def canale_di(identificativo: str) -> str | None:
    return _ALIAS.get(normalizza_canale(identificativo))


@dataclass(frozen=True)
class Programma:
    canale: str
    inizio: datetime
    fine: datetime
    titolo: str
    generi: tuple[str, ...] = ()
    trama: str = ""

    @property
    def durata_minuti(self) -> int:
        return int((self.fine - self.inizio).total_seconds() // 60)

    @property
    def e_un_film(self) -> bool:
        generi = {g.lower() for g in self.generi}
        if _EPISODIO.search(f"{self.titolo} {self.trama}"):
            return False  # "Stag. 1 Ep. 1": è una serie, anche se dura un'ora e mezza
        if any(g.startswith("film") for g in generi):
            return True
        # Molte guide scrivono solo il genere ("Commedia", "Western"): è un film se dura
        # quanto un film (una sitcom "Commedia" dura 25 minuti).
        if generi & GENERI_DA_FILM and self.durata_minuti >= 75:
            return True
        if re.match(r"^\s*(film|lungometraggio)\b", self.trama, re.IGNORECASE):
            return True
        # Fonti senza generi: un programma lungo su un canale di cinema.
        return not generi and self.canale in CANALI_CINEMA and self.durata_minuti >= 80

    @property
    def genere(self) -> str:
        """Il genere più preciso: "Commedia" invece di "Film"."""
        specifici = [g for g in self.generi if g.lower() not in {"film", "cinema", "serie tv"}]
        return (specifici or list(self.generi) or [""])[0]

    def in_onda(self, quando: datetime) -> bool:
        return self.inizio <= quando < self.fine

    def in_lista(self) -> list[Any]:
        return [
            self.canale,
            self.inizio.isoformat(timespec="minutes"),
            self.fine.isoformat(timespec="minutes"),
            self.titolo,
            "|".join(self.generi),
            self.trama,
        ]

    @classmethod
    def da_lista(cls, dati: Sequence[Any]) -> Programma:
        canale, inizio, fine, titolo, generi, trama = dati
        return cls(
            canale,
            datetime.fromisoformat(inizio).astimezone(FUSO_ORARIO),
            datetime.fromisoformat(fine).astimezone(FUSO_ORARIO),
            titolo,
            tuple(g for g in generi.split("|") if g),
            trama,
        )


def _data_xmltv(testo: str | None) -> datetime | None:
    """``"20261106200000 +0000"`` → datetime all'ora di Roma."""
    if not testo:
        return None
    try:
        return datetime.strptime(testo.strip(), "%Y%m%d%H%M%S %z").astimezone(FUSO_ORARIO)
    except ValueError:
        return None


def leggi_xmltv(contenuto: bytes) -> list[Programma]:
    """Legge una guida XMLTV (anche compressa .gz) e tiene solo i canali conosciuti."""
    if contenuto[:2] == b"\x1f\x8b":
        contenuto = gzip.decompress(contenuto)
    radice = ElementTree.fromstring(contenuto)
    per_identificativo: dict[str, list[Programma]] = {}
    for voce in radice.iter("programme"):
        identificativo = voce.get("channel") or ""
        canale = canale_di(identificativo)
        inizio, fine = _data_xmltv(voce.get("start")), _data_xmltv(voce.get("stop"))
        titolo = (voce.findtext("title") or "").strip()
        if canale is None or inizio is None or fine is None or not titolo or fine <= inizio:
            continue
        generi = tuple(dict.fromkeys(c.text.strip() for c in voce.findall("category") if c.text))
        trama = re.sub(r"\s+", " ", voce.findtext("desc") or "").strip()
        if len(trama) > LUNGHEZZA_TRAMA:
            trama = trama[: LUNGHEZZA_TRAMA - 1].rsplit(" ", 1)[0] + "…"
        per_identificativo.setdefault(identificativo, []).append(
            Programma(canale, inizio, fine, pulisci_titolo(titolo)[:120], generi[:3], trama)
        )
    # Alcune guide hanno lo stesso canale due volte ("Rai.1.HD..101" e "Rai1"): si tiene
    # l'elenco più completo (prima chi ha i generi, utili per trovare i film), altrimenti
    # ogni programma comparirebbe due volte.
    def completezza(lista: list[Programma]) -> tuple[int, int]:
        return sum(1 for p in lista if p.generi), len(lista)

    migliore: dict[str, list[Programma]] = {}
    for lista in per_identificativo.values():
        canale = lista[0].canale
        if canale not in migliore or completezza(lista) > completezza(migliore[canale]):
            migliore[canale] = lista
    return [p for lista in migliore.values() for p in lista]


def pulisci_titolo(titolo: str) -> str:
    """``"Stag. 2026 - Affari tuoi"`` → ``"Affari tuoi"``; ``"1^TV"`` → ``"(prima TV)"``."""
    titolo = re.sub(r"^\s*Stag\.\s*\d+\s*-\s*", "", titolo)
    titolo = re.sub(r"\s*-?\s*1\^\s*TV\b", " (prima TV)", titolo)
    return re.sub(r"\s+", " ", titolo).strip()


class ErroreGuida(RuntimeError):
    """Nessuna fonte della guida TV ha risposto."""


class FonteGuida(Protocol):
    def scarica(self) -> tuple[str, list[Programma]]: ...


class GuideXmltv:
    """Scarica la guida dalla prima fonte che risponde con dati validi."""

    def __init__(self, http: httpx.Client, fonti: Sequence[tuple[str, str]] = FONTI_GUIDA):
        self._http = http
        self._fonti = fonti

    def scarica(self) -> tuple[str, list[Programma]]:
        for nome, url in self._fonti:
            try:
                risposta = self._http.get(url, follow_redirects=True, timeout=25.0)
                risposta.raise_for_status()
                programmi = leggi_xmltv(risposta.content)
            except (httpx.HTTPError, ElementTree.ParseError, DefusedXmlException, OSError) as e:
                log.warning("Guida TV %s non disponibile: %s", nome, e)
                continue
            except Exception:
                log.exception("Guida TV %s illeggibile", nome)
                continue
            # Una guida quasi vuota (es. pagina di errore) non vale: si prova la seguente.
            if len({p.canale for p in programmi}) >= 10:
                return nome, programmi
            log.warning("Guida TV %s con troppi pochi canali", nome)
        raise ErroreGuida("Nessuna guida TV disponibile")


# --- Memoria: la guida salvata, divisa per giorno e per gruppo di canali ----------------


def _chiave(giorno: date, gruppo: str) -> str:
    return f"tv:{gruppo}:{giorno.isoformat()}"


def salva_guida(archivio: Any, programmi: Iterable[Programma], fonte: str, adesso: datetime) -> int:
    """Salva la guida giorno per giorno. Restituisce quanti giorni ha salvato."""
    per_giorno: dict[tuple[date, str], list[list[Any]]] = {}
    for p in programmi:
        # Un programma va nel giorno in cui comincia (la notte dopo mezzanotte: giorno dopo).
        giorno = p.inizio.date()
        if giorno < adesso.date() - timedelta(days=1):
            continue
        per_giorno.setdefault((giorno, GRUPPO_DEL_CANALE[p.canale]), []).append(p.in_lista())
    for (giorno, gruppo), lista in per_giorno.items():
        lista.sort(key=lambda d: (ORDINE[d[0]], d[1]))
        archivio.scrivi(_chiave(giorno, gruppo), lista, GIORNI_IN_MEMORIA * 24 * 3600)
    giorni = sorted({g for g, _ in per_giorno})
    archivio.scrivi(
        "tv-aggiornata",
        {
            "quando": adesso.isoformat(),
            "fonte": fonte,
            "fino": giorni[-1].isoformat() if giorni else "",
        },
        30 * 24 * 3600,
    )
    return len(giorni)


def leggi_guida(archivio: Any, giorni: Iterable[date], gruppo: str) -> list[Programma]:
    programmi = []
    for giorno in giorni:
        for dati in archivio.leggi(_chiave(giorno, gruppo)) or []:
            try:
                programmi.append(Programma.da_lista(dati))
            except (ValueError, TypeError, KeyError):
                continue
    return programmi


# --- Scelte: prima serata, film, adesso, un canale ------------------------------------------


def prima_serata(programmi: Iterable[Programma], giorno: date) -> list[Programma]:
    """Per ogni canale, il programma principale della sera."""
    riferimento = datetime.combine(giorno, ORA_PRIMA_SERATA, FUSO_ORARIO)
    da = datetime.combine(giorno, INIZIO_PRIMA_SERATA, FUSO_ORARIO)
    a = datetime.combine(giorno, FINE_PRIMA_SERATA, FUSO_ORARIO)
    che_partono: dict[str, Programma] = {}
    in_onda: dict[str, Programma] = {}
    for p in sorted(programmi, key=lambda p: p.inizio):
        if p.durata_minuti < 25:
            continue  # telegiornali brevi, meteo, pubblicità
        if da <= p.inizio <= a:
            che_partono.setdefault(p.canale, p)
        elif p.in_onda(riferimento):
            in_onda.setdefault(p.canale, p)
    scelti = {**in_onda, **che_partono}
    return sorted(scelti.values(), key=lambda p: ORDINE[p.canale])


def film_della_sera(programmi: Iterable[Programma], giorno: date) -> list[Programma]:
    """I film che iniziano dalle 20:30 alle 23:30, in ordine di orario."""
    da = datetime.combine(giorno, time(20, 30), FUSO_ORARIO)
    a = datetime.combine(giorno, time(23, 30), FUSO_ORARIO)
    film = [p for p in programmi if p.e_un_film and da <= p.inizio <= a]
    visti: set[tuple[str, str]] = set()
    unici = []
    for p in sorted(film, key=lambda p: (p.inizio, ORDINE[p.canale])):
        # Lo stesso film su due canali (es. La7 Cinema e La7d): una volta sola.
        chiave = (p.titolo.lower(), p.inizio.strftime("%H"))
        if chiave not in visti:
            visti.add(chiave)
            unici.append(p)
    return unici


def adesso_in_onda(
    programmi: Iterable[Programma], quando: datetime
) -> list[tuple[Programma, Programma | None]]:
    """Per ogni canale: cosa c'è adesso e cosa viene dopo."""
    per_canale: dict[str, list[Programma]] = {}
    for p in programmi:
        per_canale.setdefault(p.canale, []).append(p)
    risultato = []
    for lista in per_canale.values():
        lista.sort(key=lambda p: p.inizio)
        ora = next((p for p in lista if p.in_onda(quando)), None)
        if ora is None:
            continue
        dopo = next((p for p in lista if p.inizio >= ora.fine), None)
        risultato.append((ora, dopo))
    return sorted(risultato, key=lambda x: ORDINE[x[0].canale])


def giornata_del_canale(
    programmi: Iterable[Programma], canale: str, quando: datetime
) -> list[Programma]:
    """Da adesso a fine giornata (le 2 di notte) su un canale."""
    fine = datetime.combine(quando.date() + timedelta(days=1), time(2), FUSO_ORARIO)
    return sorted(
        (p for p in programmi if p.canale == canale and p.fine > quando and p.inizio < fine),
        key=lambda p: p.inizio,
    )


def cerca_canale(testo: str) -> str | None:
    """``"canale 5"``, ``"rai1"``, ``"la 7"`` → il nome del canale."""
    chiave = normalizza_canale(testo)
    if chiave in _ALIAS:
        return _ALIAS[chiave]
    for nome, _, _ in CANALI:
        if normalizza_canale(nome) == chiave:
            return nome
    return None


# --- Testi ----------------------------------------------------------------------------------


def _e(testo: str) -> str:
    return escape(testo, quote=False)


def _riga(p: Programma, con_trama: bool = False) -> str:
    genere = f" <i>({_e(p.genere)})</i>" if p.genere else ""
    riga = f"<b>{_e(p.canale)}</b> {p.inizio:%H:%M} · {_e(p.titolo)}{genere}"
    if con_trama and p.trama:
        riga += f"\n   <i>{_e(p.trama)}</i>"
    return riga


def _quando(giorno: date, oggi: date) -> str:
    return (
        "stasera"
        if giorno == oggi
        else "domani sera"
        if giorno == oggi + timedelta(1)
        else (f"la sera del {giorno:%d/%m}")
    )


def messaggio_prima_serata(scelti: Sequence[Programma], giorno: date, oggi: date, sky: bool) -> str:
    titolo = f"📺 <b>In TV {_quando(giorno, oggi)}</b>" + (" · Sky" if sky else "")
    if not scelti:
        return f"{titolo}\n\nNon trovo la programmazione: riprova più tardi."
    righe = [titolo, ""] + [_riga(p) for p in scelti]
    righe += ["", "🎬 Solo i film: /tv film · Adesso: /tv adesso · Un canale: /tv rai1"]
    return "\n".join(righe)


def messaggio_film(film: Sequence[Programma], giorno: date, oggi: date, sky: bool) -> str:
    titolo = f"🎬 <b>Film {_quando(giorno, oggi)}</b>" + (" · Sky" if sky else "")
    if not film:
        return f"{titolo}\n\nNessun film in prima serata."
    return "\n".join([titolo, ""] + [_riga(p, con_trama=True) for p in film])


def messaggio_adesso(
    in_onda: Sequence[tuple[Programma, Programma | None]], quando: datetime, sky: bool
) -> str:
    titolo = f"📺 <b>In onda adesso</b> ({quando:%H:%M})" + (" · Sky" if sky else "")
    if not in_onda:
        return f"{titolo}\n\nNon trovo la programmazione: riprova più tardi."
    righe = [titolo, ""]
    for ora, dopo in in_onda:
        seguito = f"\n   poi {dopo.inizio:%H:%M} {_e(dopo.titolo)}" if dopo else ""
        righe.append(f"<b>{_e(ora.canale)}</b> · {_e(ora.titolo)}{seguito}")
    return "\n".join(righe)


def messaggio_canale(canale: str, programmi: Sequence[Programma]) -> str:
    titolo = f"📺 <b>{_e(canale)}</b>, da adesso"
    if not programmi:
        return f"{titolo}\n\nNon trovo la programmazione di questo canale."
    return "\n".join(
        [titolo, ""]
        + [
            f"{p.inizio:%H:%M} · {_e(p.titolo)}" + (f" <i>({_e(p.genere)})</i>" if p.genere else "")
            for p in programmi
        ]
    )


USO = (
    "📺 <b>Guida TV</b>\n"
    "/tv – cosa c'è stasera in prima serata\n"
    "/tv film – solo i film di stasera\n"
    "/tv adesso – cosa c'è in onda adesso\n"
    "/tv domani – la prima serata di domani (anche <code>/tv film domani</code>)\n"
    "/tv rai1 – la giornata di un canale (<code>/tv canale5</code>, <code>/tv iris</code>…)\n"
    "Aggiungi <b>sky</b> per i canali Sky: <code>/tv sky</code>, <code>/tv film sky</code>"
)
NON_DISPONIBILE = (
    "📺 La guida TV in questo momento non è disponibile (le fonti non rispondono). "
    "Riprova tra un po'."
)
CANALE_SCONOSCIUTO = (
    "📺 Non conosco il canale «{nome}». Prova ad esempio: <code>/tv rai1</code>, "
    "<code>/tv canale5</code>, <code>/tv la7</code>, <code>/tv iris</code>."
)
