"""Client per le API gratuite di Open-Meteo (previsioni e mare), senza chiave.

Le funzioni ``leggi_*`` trasformano il JSON in dataclass e sono pure (facili da testare);
la classe ``OpenMeteo`` fa le chiamate HTTP.

Dati meteo: Open-Meteo.com, licenza CC BY 4.0.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Protocol

import httpx

from azdora.config import FUSO_ORARIO, Localita

URL_PREVISIONI = "https://api.open-meteo.com/v1/forecast"
URL_MARE = "https://marine-api.open-meteo.com/v1/marine"

VARIABILI_ATTUALI = (
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "weather_code",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
)
VARIABILI_ORARIE = (
    "temperature_2m",
    "precipitation_probability",
    "weather_code",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
)
VARIABILI_GIORNALIERE = (
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_probability_max",
    "precipitation_sum",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "wind_direction_10m_dominant",
    "sunrise",
    "sunset",
    "uv_index_max",
)
VARIABILI_MARE_ATTUALI = (
    "wave_height",
    "wave_direction",
    "wave_period",
    "sea_surface_temperature",
)
VARIABILI_MARE_GIORNALIERE = ("wave_height_max", "wave_direction_dominant")


class ErroreMeteo(RuntimeError):
    """Open-Meteo non ha risposto o ha risposto qualcosa di inatteso."""


# --- Modello dei dati -------------------------------------------------------------------


@dataclass(frozen=True)
class MeteoAttuale:
    ora: datetime
    temperatura: float | None
    percepita: float | None
    umidita: float | None
    codice: int | None
    vento: float | None
    direzione_vento: float | None
    raffiche: float | None


@dataclass(frozen=True)
class MeteoOra:
    ora: datetime
    temperatura: float | None
    prob_pioggia: float | None
    codice: int | None
    vento: float | None
    direzione_vento: float | None
    raffiche: float | None


@dataclass(frozen=True)
class MeteoGiorno:
    data: date
    codice: int | None
    t_min: float | None
    t_max: float | None
    prob_pioggia: float | None
    pioggia_mm: float | None
    vento_max: float | None
    raffiche_max: float | None
    direzione_dominante: float | None
    alba: datetime | None
    tramonto: datetime | None
    uv_max: float | None


@dataclass(frozen=True)
class Previsioni:
    localita: Localita
    attuale: MeteoAttuale
    ore: tuple[MeteoOra, ...]
    giorni: tuple[MeteoGiorno, ...]

    def prossime_ore(self, quante: int = 12, passo: int = 3) -> tuple[MeteoOra, ...]:
        """Le ore successive a quella attuale, una ogni ``passo`` ore, per ``quante`` ore."""
        future = [o for o in self.ore if o.ora > self.attuale.ora]
        return tuple(future[passo - 1 : quante : passo])


@dataclass(frozen=True)
class MareAttuale:
    ora: datetime
    altezza_onde: float | None
    direzione_onde: float | None
    periodo_onde: float | None
    temperatura_acqua: float | None


@dataclass(frozen=True)
class MareGiorno:
    data: date
    altezza_max: float | None
    direzione_dominante: float | None


@dataclass(frozen=True)
class PrevisioniMare:
    localita: Localita
    attuale: MareAttuale
    giorni: tuple[MareGiorno, ...]
    # Punto della griglia marina scelto da Open-Meteo (può essere a qualche km dalla costa).
    punto: tuple[float, float] | None = None

    @property
    def ha_dati(self) -> bool:
        """Nell'entroterra Open-Meteo risponde, ma con tutti i valori a ``null``."""
        return self.attuale.altezza_onde is not None or any(
            g.altezza_max is not None for g in self.giorni
        )


# --- Lettura del JSON -------------------------------------------------------------------


def _ora_locale(testo: str | None) -> datetime | None:
    """Open-Meteo con ``timezone=Europe/Rome`` restituisce orari locali senza fuso."""
    if not testo:
        return None
    return datetime.fromisoformat(testo).replace(tzinfo=FUSO_ORARIO)


def _numero(valore: Any) -> float | None:
    return None if valore is None else float(valore)


def _intero(valore: Any) -> int | None:
    return None if valore is None else int(valore)


def _colonna(blocco: dict[str, Any], nome: str, lunghezza: int) -> Sequence[Any]:
    valori = blocco.get(nome)
    if valori is None:
        return [None] * lunghezza
    return valori


def _sezione(dati: dict[str, Any], nome: str) -> dict[str, Any]:
    sezione = dati.get(nome)
    if not isinstance(sezione, dict):
        raise ErroreMeteo(f"Nella risposta di Open-Meteo manca '{nome}'")
    return sezione


def leggi_previsioni(dati: dict[str, Any], localita: Localita) -> Previsioni:
    attuale = _sezione(dati, "current")
    ora_attuale = _ora_locale(attuale.get("time"))
    if ora_attuale is None:
        raise ErroreMeteo("Nella risposta di Open-Meteo manca l'ora attuale")

    orario = _sezione(dati, "hourly")
    tempi_orari = orario.get("time") or []
    n = len(tempi_orari)
    colonne_orarie = {nome: _colonna(orario, nome, n) for nome in VARIABILI_ORARIE}
    ore = tuple(
        MeteoOra(
            ora=_ora_locale(tempi_orari[i]),  # type: ignore[arg-type]
            temperatura=_numero(colonne_orarie["temperature_2m"][i]),
            prob_pioggia=_numero(colonne_orarie["precipitation_probability"][i]),
            codice=_intero(colonne_orarie["weather_code"][i]),
            vento=_numero(colonne_orarie["wind_speed_10m"][i]),
            direzione_vento=_numero(colonne_orarie["wind_direction_10m"][i]),
            raffiche=_numero(colonne_orarie["wind_gusts_10m"][i]),
        )
        for i in range(n)
    )

    giornaliero = _sezione(dati, "daily")
    date_giorni = giornaliero.get("time") or []
    g = len(date_giorni)
    col = {nome: _colonna(giornaliero, nome, g) for nome in VARIABILI_GIORNALIERE}
    giorni = tuple(
        MeteoGiorno(
            data=date.fromisoformat(date_giorni[i]),
            codice=_intero(col["weather_code"][i]),
            t_min=_numero(col["temperature_2m_min"][i]),
            t_max=_numero(col["temperature_2m_max"][i]),
            prob_pioggia=_numero(col["precipitation_probability_max"][i]),
            pioggia_mm=_numero(col["precipitation_sum"][i]),
            vento_max=_numero(col["wind_speed_10m_max"][i]),
            raffiche_max=_numero(col["wind_gusts_10m_max"][i]),
            direzione_dominante=_numero(col["wind_direction_10m_dominant"][i]),
            alba=_ora_locale(col["sunrise"][i]),
            tramonto=_ora_locale(col["sunset"][i]),
            uv_max=_numero(col["uv_index_max"][i]),
        )
        for i in range(g)
    )

    return Previsioni(
        localita=localita,
        attuale=MeteoAttuale(
            ora=ora_attuale,
            temperatura=_numero(attuale.get("temperature_2m")),
            percepita=_numero(attuale.get("apparent_temperature")),
            umidita=_numero(attuale.get("relative_humidity_2m")),
            codice=_intero(attuale.get("weather_code")),
            vento=_numero(attuale.get("wind_speed_10m")),
            direzione_vento=_numero(attuale.get("wind_direction_10m")),
            raffiche=_numero(attuale.get("wind_gusts_10m")),
        ),
        ore=ore,
        giorni=giorni,
    )


def leggi_mare(dati: dict[str, Any], localita: Localita) -> PrevisioniMare:
    attuale = _sezione(dati, "current")
    ora_attuale = _ora_locale(attuale.get("time"))
    if ora_attuale is None:
        raise ErroreMeteo("Nella risposta del mare manca l'ora attuale")

    giornaliero = _sezione(dati, "daily")
    date_giorni = giornaliero.get("time") or []
    g = len(date_giorni)
    col = {nome: _colonna(giornaliero, nome, g) for nome in VARIABILI_MARE_GIORNALIERE}
    giorni = tuple(
        MareGiorno(
            data=date.fromisoformat(date_giorni[i]),
            altezza_max=_numero(col["wave_height_max"][i]),
            direzione_dominante=_numero(col["wave_direction_dominant"][i]),
        )
        for i in range(g)
    )
    punto = None
    if dati.get("latitude") is not None and dati.get("longitude") is not None:
        punto = (float(dati["latitude"]), float(dati["longitude"]))
    return PrevisioniMare(
        localita=localita,
        punto=punto,
        attuale=MareAttuale(
            ora=ora_attuale,
            altezza_onde=_numero(attuale.get("wave_height")),
            direzione_onde=_numero(attuale.get("wave_direction")),
            periodo_onde=_numero(attuale.get("wave_period")),
            temperatura_acqua=_numero(attuale.get("sea_surface_temperature")),
        ),
        giorni=giorni,
    )


# --- Chiamate HTTP ----------------------------------------------------------------------


class FonteMeteo(Protocol):
    """Qualunque cosa sappia dare previsioni e mare: Open-Meteo vero o un finto nei test."""

    def previsioni(self, localita: Localita) -> Previsioni: ...

    def mare(self, localita: Localita) -> PrevisioniMare: ...


class OpenMeteo:
    """Fonte meteo reale basata su Open-Meteo."""

    def __init__(self, http: httpx.Client, giorni: int = 7) -> None:
        self._http = http
        self._giorni = giorni

    def _scarica(self, url: str, parametri: dict[str, Any]) -> dict[str, Any]:
        try:
            risposta = self._http.get(url, params=parametri)
            risposta.raise_for_status()
            dati = risposta.json()
        except (httpx.HTTPError, ValueError) as errore:
            raise ErroreMeteo(f"Open-Meteo non disponibile: {errore}") from errore
        if not isinstance(dati, dict):
            raise ErroreMeteo("Risposta di Open-Meteo non valida")
        if dati.get("error"):
            raise ErroreMeteo(f"Open-Meteo: {dati.get('reason', 'errore sconosciuto')}")
        return dati

    def previsioni(self, localita: Localita) -> Previsioni:
        dati = self._scarica(
            URL_PREVISIONI,
            {
                "latitude": localita.lat,
                "longitude": localita.lon,
                "current": ",".join(VARIABILI_ATTUALI),
                "hourly": ",".join(VARIABILI_ORARIE),
                "daily": ",".join(VARIABILI_GIORNALIERE),
                "timezone": "Europe/Rome",
                "forecast_days": self._giorni,
            },
        )
        return leggi_previsioni(dati, localita)

    def mare(self, localita: Localita) -> PrevisioniMare:
        dati = self._scarica(
            URL_MARE,
            {
                "latitude": localita.mare_lat,
                "longitude": localita.mare_lon,
                "current": ",".join(VARIABILI_MARE_ATTUALI),
                "daily": ",".join(VARIABILI_MARE_GIORNALIERE),
                "cell_selection": "sea",
                "timezone": "Europe/Rome",
                "forecast_days": self._giorni,
            },
        )
        return leggi_mare(dati, localita)


def con_localita[T: (Previsioni, PrevisioniMare)](dato: T, localita: Localita) -> T:
    """Copia di previsioni/mare attribuita a un'altra località (utile nei test e nelle demo)."""
    return replace(dato, localita=localita)
