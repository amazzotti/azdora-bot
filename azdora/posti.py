"""Promemoria legati a un posto e posti con un nome (casa, lavoro, la mamma…).

    ricordami di comprare il pane quando sono a Rimini
    quando torno a casa ricordami di portare fuori la plastica
    /posto lavoro        → salva la posizione attuale come "lavoro"

Funzionano con la posizione che arriva da sola: dal telefono (``/telefono``) o dalla
posizione in tempo reale di Telegram. Il promemoria scatta quando *entri* nel posto:
se ci sei già quando lo scrivi, aspetta che tu esca e rientri.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from html import escape
from typing import Any

from azdora.archivio import Archivio
from azdora.luoghi import distanza_km

MASSIMO_PROMEMORIA = 20
MASSIMO_POSTI = 15
# Quanto vicino devi essere: un posto salvato col GPS è preciso, un paese è grande.
RAGGIO_POSTO_KM = 0.3
RAGGIO_PAESE_KM = 3.0
RAGGIO_CASA_STIMATA_KM = 2.0
CONSEGNATI_S = 7 * 24 * 3600

_RICORDA = re.compile(r"\bricord(?:a|ami|amelo|armi)\b", re.IGNORECASE)
_PREFISSO = re.compile(r"^\s*/?ricord(?:a|ami|amelo)\b[\s,:]*", re.IGNORECASE)
_QUANDO = re.compile(
    r"\bquando\s+(?:sono|sarò|saro|arrivo|arriverò|arrivero|torno|tornerò|tornero|rientro|"
    r"passo|vado|entro)\s+"
    r"(?:(?:vicino\s+alla|vicino\s+al|vicino\s+a|allo|alla|agli|alle|al|ai|ad|a|"
    r"nello|nella|nel|nei|in|dallo|dalla|dalle|dal|dai|da)\s+|all')?"
    r"(?P<posto>.+?)"
    # "di" chiude il posto, tranne che nei nomi come "Santarcangelo di Romagna".
    r"(?=\s+(?:di\s+(?!(?-i:[A-Z]))|[Cc]he\b|[Rr]icord)|\s*[,.;!?]|\s*$)",
    re.IGNORECASE,
)
_RIEMPITIVI = re.compile(r"^(?:di|che|a|ad|per|:|,)\s+", re.IGNORECASE)
_NOME_POSTO = re.compile(r"^[\wàèéìòù' -]{1,30}$", re.IGNORECASE)


@dataclass(frozen=True)
class RichiestaLuogo:
    cosa: str
    posto: str


def leggi_richiesta(testo: str) -> RichiestaLuogo | None:
    """``"ricordami di comprare il pane quando sono a Rimini"`` → (comprare il pane, Rimini)."""
    if not _RICORDA.search(testo):
        return None
    trovato = _QUANDO.search(testo)
    if trovato is None:
        return None
    posto = trovato["posto"].strip(" '\"")
    resto = (testo[: trovato.start()] + " " + testo[trovato.end() :]).strip()
    resto = _RICORDA.sub(" ", _PREFISSO.sub("", resto), count=1)
    resto = re.sub(r"\s+", " ", resto).strip(" ,.;:")
    while (pulito := _RIEMPITIVI.sub("", resto)) != resto:
        resto = pulito
    if not posto or not resto:
        return None
    return RichiestaLuogo(cosa=resto[:200], posto=posto[:40])


def normalizza(nome: str) -> str:
    nome = nome.strip().lower()
    nome = re.sub(r"^(?:(?:il|lo|la|i|gli|le|mio|mia|dal|dalla|da)\s+|l')", "", nome)
    return re.sub(r"\s+", " ", nome)


@dataclass(frozen=True)
class Posto:
    nome: str
    lat: float
    lon: float


class PostiSalvati:
    """I posti con un nome di una persona: ``/posto lavoro`` salva dove sei adesso."""

    def __init__(self, archivio: Archivio, persona: int) -> None:
        self._archivio = archivio
        self._chiave = f"posti:{persona}"

    def tutti(self) -> list[Posto]:
        return [Posto(**d) for d in self._archivio.leggi(self._chiave) or []]

    def cerca(self, nome: str) -> Posto | None:
        chiave = normalizza(nome)
        return next((p for p in self.tutti() if p.nome == chiave), None)

    def salva(self, nome: str, lat: float, lon: float) -> Posto:
        chiave = normalizza(nome)
        if not _NOME_POSTO.match(chiave):
            raise ValueError("Usa un nome corto, ad esempio: casa, lavoro, mamma")
        altri = [p for p in self.tutti() if p.nome != chiave]
        if len(altri) >= MASSIMO_POSTI:
            raise ValueError(f"Hai già {MASSIMO_POSTI} posti: togline qualcuno")
        nuovo = Posto(chiave, round(lat, 6), round(lon, 6))
        self._archivio.scrivi(self._chiave, [asdict(p) for p in [*altri, nuovo]])
        return nuovo

    def togli(self, nome: str) -> bool:
        chiave = normalizza(nome)
        tutti = self.tutti()
        rimasti = [p for p in tutti if p.nome != chiave]
        self._archivio.scrivi(self._chiave, [asdict(p) for p in rimasti])
        return len(rimasti) != len(tutti)


@dataclass(frozen=True)
class PromemoriaLuogo:
    id: int
    testo: str
    posto: str  # il nome come lo mostriamo: "Rimini", "casa"
    lat: float
    lon: float
    raggio_km: float
    chat: int  # dove mandarlo (la chat privata o il gruppo in cui l'hai scritto)
    dentro: bool = False  # eri già lì? Allora scatta solo dopo che esci e rientri

    def contiene(self, lat: float, lon: float) -> bool:
        return distanza_km(self.lat, self.lon, lat, lon) <= self.raggio_km


class RegistroLuoghi:
    """I promemoria legati a un posto di una persona (seguono la *sua* posizione)."""

    def __init__(self, archivio: Archivio, persona: int) -> None:
        self._archivio = archivio
        self._chiave = f"promemoria-luogo:{persona}"

    def tutti(self) -> list[PromemoriaLuogo]:
        return [PromemoriaLuogo(**d) for d in self._archivio.leggi(self._chiave) or []]

    def _salva(self, voci: Sequence[PromemoriaLuogo]) -> None:
        self._archivio.scrivi(self._chiave, [asdict(v) for v in voci])

    def aggiungi(self, voce: PromemoriaLuogo) -> PromemoriaLuogo:
        voci = self.tutti()
        if len(voci) >= MASSIMO_PROMEMORIA:
            raise ValueError(f"Hai già {MASSIMO_PROMEMORIA} promemoria legati a un posto")
        ultimo = int(self._archivio.leggi(f"{self._chiave}:ultimo-id") or 0)
        prossimo = max([v.id for v in voci] + [ultimo], default=0) + 1
        nuova = replace(voce, id=prossimo)
        self._archivio.scrivi(f"{self._chiave}:ultimo-id", prossimo)
        self._salva([*voci, nuova])
        return nuova

    def elimina(self, id_voce: int) -> PromemoriaLuogo | None:
        voci = self.tutti()
        trovata = next((v for v in voci if v.id == id_voce), None)
        if trovata is not None:
            self._salva([v for v in voci if v.id != id_voce])
        return trovata

    def arrivato(self, lat: float, lon: float) -> list[PromemoriaLuogo]:
        """Nuova posizione: restituisce (e toglie) i promemoria dei posti in cui sei appena
        entrato; ricorda anche da quali posti sei uscito."""
        voci = self.tutti()
        if not voci:
            return []
        scattati: list[PromemoriaLuogo] = []
        rimasti: list[PromemoriaLuogo] = []
        cambiato = False
        for v in voci:
            dentro = v.contiene(lat, lon)
            if dentro and not v.dentro:
                scattati.append(v)
                cambiato = True
                continue
            if dentro != v.dentro:
                v = replace(v, dentro=dentro)
                cambiato = True
            rimasti.append(v)
        if cambiato:
            self._salva(rimasti)
        if scattati:
            consegnati = self._archivio.leggi(f"{self._chiave}:consegnati") or {}
            consegnati.update({str(v.id): asdict(v) for v in scattati})
            self._archivio.scrivi(f"{self._chiave}:consegnati", consegnati, CONSEGNATI_S)
        return scattati

    def rimetti(self, id_voce: int) -> PromemoriaLuogo | None:
        """ "Anche la prossima volta": torna attivo, e scatta al prossimo arrivo."""
        consegnati = self._archivio.leggi(f"{self._chiave}:consegnati") or {}
        dati = consegnati.get(str(id_voce))
        if dati is None or any(v.id == id_voce for v in self.tutti()):
            return None
        voce = replace(PromemoriaLuogo(**dati), dentro=True)
        self._salva([*self.tutti(), voce])
        return voce


# --- Testi ------------------------------------------------------------------------------


def avviso(voce: PromemoriaLuogo) -> str:
    return f"📍 <b>Sei {a_posto(voce.posto)}</b>\nRicordati: {escape(voce.testo)}"


def pulsanti_avviso(persona: int, voce: PromemoriaLuogo) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "🔁 Anche la prossima volta", "callback_data": f"plrip:{persona}:{voce.id}"}]
        ]
    }


def conferma(voce: PromemoriaLuogo, posizione_automatica: bool) -> str:
    righe = [f"📍 Va bene: quando arrivi {a_posto(voce.posto)} ti ricordo di {escape(voce.testo)}."]
    if voce.dentro:
        righe.append("Adesso ci sei già: scatta la prossima volta che ci arrivi.")
    if not posizione_automatica:
        righe.append(
            "\n⚠️ Per accorgermi di quando arrivi mi serve la tua posizione in automatico: "
            "scrivi /telefono, oppure condividi la <b>posizione in tempo reale</b> "
            "(📎 → Posizione → Condividi posizione in tempo reale)."
        )
    return "\n".join(righe)


def righe_elenco(voci: Sequence[PromemoriaLuogo]) -> list[str]:
    if not voci:
        return []
    return [
        "",
        "📍 <b>Quando arrivi in un posto</b>",
        *(f"• {escape(v.posto)}: {escape(v.testo)}" for v in voci),
    ]


def pulsanti_elenco(persona: int, voci: Sequence[PromemoriaLuogo]) -> list[list[dict[str, str]]]:
    return [
        [{"text": f"🗑 📍 {v.testo[:26]}", "callback_data": f"pldel:{persona}:{v.id}"}] for v in voci
    ]


def messaggio_posti(posti: Sequence[Posto]) -> str:
    righe = ["📌 <b>I tuoi posti</b>", ""]
    if posti:
        righe += [f"• {escape(p.nome)}" for p in posti]
    else:
        righe.append("Non hai ancora salvato nessun posto.")
    righe += [
        "",
        "Per salvare dove sei adesso: <code>/posto lavoro</code> (o casa, mamma, palestra…).",
        "Per toglierne uno: <code>/posto lavoro off</code>.",
        "Poi scrivi ad esempio: <i>ricordami di passare in farmacia quando arrivo al lavoro</i>.",
    ]
    return "\n".join(righe)


def a_posto(posto: str) -> str:
    """ "a Rimini", "a casa", "al lavoro"."""
    nome = escape(posto)
    if posto.lower() in {"lavoro", "mare", "supermercato", "bar", "parco", "centro"}:
        return f"al {nome}"
    if posto.lower() in {"palestra", "piscina", "stazione", "posta", "farmacia", "spiaggia"}:
        return f"alla {nome}"
    if posto.lower() in {"mamma", "babbo", "nonna", "nonno", "papà", "suoceri"}:
        return f"da {nome}"
    return f"a {nome}"


USO_POSTO = (
    "📌 Prima mandami la posizione (📎 → Posizione), poi scrivi "
    "<code>/posto nome</code>, ad esempio <code>/posto lavoro</code>."
)
NON_TROVATO = (
    "🤔 Non so dov'è «{posto}». Scrivi il nome di un paese (es. <i>quando arrivo a Cesena</i>), "
    "oppure salva prima il posto: vai lì e scrivi <code>/posto {posto}</code>."
)
