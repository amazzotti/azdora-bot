"""Configurazione: località seguite dal bot e impostazioni lette dalle variabili d'ambiente.

Nessun segreto è scritto nel codice: token e chiavi arrivano solo da ``os.environ``
(su Vercel dalle Environment Variables, su GitHub Actions dai Secrets).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from zoneinfo import ZoneInfo

FUSO_ORARIO = ZoneInfo("Europe/Rome")


@dataclass(frozen=True)
class Localita:
    """Un posto di cui il bot conosce meteo e mare.

    ``mare_lat``/``mare_lon`` sono un punto in acqua davanti alla costa: il modello
    marino di Open-Meteo lavora su una griglia e sulla terraferma non ha dati.
    """

    chiave: str
    nome: str
    lat: float
    lon: float
    mare_lat: float
    mare_lon: float
    alias: tuple[str, ...] = ()


RICCIONE = Localita(
    chiave="riccione",
    nome="Riccione",
    lat=43.9993,
    lon=12.6566,
    mare_lat=44.01,
    mare_lon=12.69,
    alias=("ric", "ricc"),
)
RIMINI = Localita(
    chiave="rimini",
    nome="Rimini",
    lat=44.0594,
    lon=12.5683,
    mare_lat=44.08,
    mare_lon=12.60,
    alias=("rn", "rim"),
)

# L'ordine conta: la prima è la località "di casa", usata quando non se ne indica una.
LOCALITA: tuple[Localita, ...] = (RICCIONE, RIMINI)


def trova_localita(testo: str | None) -> Localita | None:
    """Trova una località dal testo scritto dall'utente (``"Rimini"``, ``"rn"``, ...).

    Restituisce la località di casa se il testo è vuoto, ``None`` se non la riconosce.
    """
    if not testo or not testo.strip():
        return LOCALITA[0]
    cercato = testo.strip().lower()
    for localita in LOCALITA:
        if cercato == localita.chiave or cercato in localita.alias:
            return localita
    return None


class ConfigurazioneErrata(ValueError):
    """Una variabile d'ambiente ha un valore non valido."""


def _leggi_chat_id(env: Mapping[str, str], nome: str) -> int | None:
    valore = env.get(nome, "").strip()
    if not valore:
        return None
    try:
        return int(valore)
    except ValueError as errore:
        raise ConfigurazioneErrata(f"{nome} deve essere un numero, non {valore!r}") from errore


@dataclass(frozen=True)
class Impostazioni:
    """Tutto quello che il bot legge dall'ambiente."""

    token: str
    chat_personale: int | None = None
    chat_coppia: int | None = None
    segreto_webhook: str = ""
    segreto_cron: str = ""
    username_bot: str = ""
    nome_utente: str = "Ale"

    @classmethod
    def da_env(cls, env: Mapping[str, str] | None = None) -> Impostazioni:
        env = os.environ if env is None else env
        return cls(
            token=env.get("TELEGRAM_BOT_TOKEN", "").strip(),
            chat_personale=_leggi_chat_id(env, "TELEGRAM_CHAT_ID"),
            chat_coppia=_leggi_chat_id(env, "TELEGRAM_CHAT_ID_COPPIA"),
            segreto_webhook=env.get("TELEGRAM_WEBHOOK_SECRET", "").strip(),
            segreto_cron=env.get("CRON_SECRET", "").strip(),
            username_bot=env.get("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@"),
            nome_utente=env.get("AZDORA_NOME", "").strip() or "Ale",
        )

    @property
    def chat_autorizzate(self) -> frozenset[int]:
        """Le uniche chat a cui il bot risponde: la tua privata e il gruppo di coppia."""
        return frozenset(c for c in (self.chat_personale, self.chat_coppia) if c is not None)

    def richiedi_token(self) -> str:
        if not self.token:
            raise ConfigurazioneErrata("Manca TELEGRAM_BOT_TOKEN")
        return self.token
