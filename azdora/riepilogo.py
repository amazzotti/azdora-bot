"""Riepilogo della giornata (comandi /oggi e /domani, messaggio delle 7) con consigli pratici."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from html import escape

from azdora.messaggi import (
    CREDITI,
    CREDITI_OSM,
    data_lunga,
    gradi,
    mare_del_giorno,
    ora,
    pioggia,
    vento_del_giorno,
)
from azdora.meteo.codici_wmo import (
    CODICI_NEBBIA,
    CODICI_NEVE,
    CODICI_PIOGGIA,
    CODICI_TEMPORALE,
    condizione,
)
from azdora.meteo.openmeteo import MareGiorno, MeteoGiorno, Previsioni, PrevisioniMare
from azdora.meteo.vento import descrivi_vento

# Soglie dei consigli, raccolte qui per poterle ritoccare facilmente.
PIOGGIA_CERTA = 60
PIOGGIA_POSSIBILE = 30
PIOGGIA_IMPROBABILE = 10  # sotto questa soglia la pioggia prevista è solo "rumore" del modello
RAFFICHE_FORTI = 50
RAFFICHE_VENTOSO = 35
GELO = 3
CALDO = 33
UV_ALTO = 6
MARE_MOSSO = 1.25
SPIAGGIA_T_MIN = 24
SPIAGGIA_ONDE_MAX = 0.5


@dataclass(frozen=True)
class DatiLocalita:
    """Previsioni e mare di una località, pronti per il riepilogo."""

    previsioni: Previsioni
    mare: PrevisioniMare | None = None

    def giorno(self, data: date) -> MeteoGiorno | None:
        return next((g for g in self.previsioni.giorni if g.data == data), None)

    def giorno_mare(self, data: date) -> MareGiorno | None:
        if self.mare is None:
            return None
        return next((g for g in self.mare.giorni if g.data == data), None)


def saluto(adesso: datetime) -> str:
    if adesso.hour < 12:
        return "Buongiorno"
    if adesso.hour < 18:
        return "Buon pomeriggio"
    return "Buonasera"


def consigli(giorno: MeteoGiorno, mare: MareGiorno | None = None) -> list[str]:
    """Suggerimenti pratici per la giornata, dal più importante al meno importante."""
    lista: list[str] = []
    codice = giorno.codice
    prob = giorno.prob_pioggia or 0

    if codice in CODICI_TEMPORALE:
        lista.append("⛈️ Possibili temporali: occhio a fulmini e allagamenti nei sottopassi.")
    if codice in CODICI_NEVE:
        lista.append("❄️ Neve in arrivo: gomme invernali o catene a bordo.")
    if prob >= PIOGGIA_CERTA or (codice in CODICI_PIOGGIA and prob >= PIOGGIA_POSSIBILE):
        lista.append(f"☂️ Porta l'ombrello: probabilità di pioggia {prob:.0f}%.")
    elif prob >= PIOGGIA_POSSIBILE or (codice in CODICI_PIOGGIA and prob >= PIOGGIA_IMPROBABILE):
        lista.append(f"🌂 Possibile qualche goccia ({prob:.0f}%): ombrello in macchina.")
    if codice in CODICI_NEBBIA:
        lista.append("🌫️ Nebbia: prudenza in A14 e sulla Statale 16.")

    raffiche = giorno.raffiche_max or 0
    if raffiche >= RAFFICHE_FORTI:
        lista.append(
            f"💨 Raffiche fino a {raffiche:.0f} km/h: attenzione a vasi, ombrelloni e scooter."
        )
    elif raffiche >= RAFFICHE_VENTOSO:
        lista.append(f"🌬️ Giornata ventosa, raffiche fino a {raffiche:.0f} km/h.")

    if giorno.t_min is not None and giorno.t_min <= GELO:
        lista.append("🧊 Freddo al mattino: possibile ghiaccio sul parabrezza.")
    if giorno.t_max is not None and giorno.t_max >= CALDO:
        lista.append("🥵 Caldo forte: bevi tanto ed evita il sole nelle ore centrali.")
    if giorno.uv_max is not None and giorno.uv_max >= UV_ALTO:
        lista.append(f"🧴 UV alto ({giorno.uv_max:.0f}): crema solare e cappello.")

    onde_max = mare.altezza_max if mare else None
    if onde_max is not None and onde_max > MARE_MOSSO:
        lista.append(f"🌊 Mare molto mosso (onde fino a {onde_max:.1f} m): niente bagno.")
    elif (
        onde_max is not None
        and onde_max <= SPIAGGIA_ONDE_MAX
        and giorno.t_max is not None
        and giorno.t_max >= SPIAGGIA_T_MIN
        and prob < PIOGGIA_POSSIBILE
        and raffiche < RAFFICHE_VENTOSO
    ):
        lista.append("🏖️ Mare calmo e caldo: giornata da spiaggia!")

    if not lista:
        lista.append("✅ Nessuna sorpresa in vista: buona giornata!")
    return lista


def _blocco_localita(dati: DatiLocalita, data: date, con_attuale: bool) -> list[str]:
    giorno = dati.giorno(data)
    nome = escape(dati.previsioni.localita.nome)  # può arrivare da un servizio esterno
    if giorno is None:
        return [f"📍 <b>{nome}</b>", "Previsioni non disponibili."]
    righe = [
        f"📍 <b>{nome}</b>",
        f"{condizione(giorno.codice)} · {gradi(giorno.t_min)}/{gradi(giorno.t_max)}"
        f" · {pioggia(giorno)}",
    ]
    if con_attuale:
        a = dati.previsioni.attuale
        righe.append(f"💨 Adesso: {descrivi_vento(a.vento, a.direzione_vento, a.raffiche)}")
    righe.append(f"🌬️ In giornata: {vento_del_giorno(giorno)}")
    if dati.mare is not None:
        mare_testo = f"🌊 Mare: {mare_del_giorno(dati.giorno_mare(data))}"
        if con_attuale and dati.mare.attuale.temperatura_acqua is not None:
            mare_testo += f" · acqua {gradi(dati.mare.attuale.temperatura_acqua)}C"
        righe.append(mare_testo)
    return righe


def componi_riepilogo(
    localita: Sequence[DatiLocalita],
    data: date,
    *,
    titolo: str,
    con_attuale: bool,
    crediti: str = CREDITI,
) -> str:
    """Messaggio completo per una giornata. La prima località decide alba, tramonto e consigli."""
    righe = [titolo]
    for dati in localita:
        righe += ["", *_blocco_localita(dati, data, con_attuale)]

    principale = localita[0] if localita else None
    giorno = principale.giorno(data) if principale else None
    if giorno is not None:
        sole = f"🌅 Alba {ora(giorno.alba)} · 🌇 Tramonto {ora(giorno.tramonto)}"
        if giorno.uv_max is not None:
            sole += f" · UV max {giorno.uv_max:.0f}"
        righe += ["", sole, "", "<b>Consigli</b>"]
        righe += consigli(giorno, principale.giorno_mare(data) if principale else None)

    righe += ["", crediti]
    return "\n".join(righe)


def riepilogo_oggi(localita: Sequence[DatiLocalita], adesso: datetime, nome: str) -> str:
    oggi = adesso.date()
    titolo = (
        f"☀️ <b>{saluto(adesso)} {nome}!</b>\nOggi è {data_lunga(oggi)}."
        if nome
        else f"☀️ <b>Oggi, {data_lunga(oggi)}</b>"
    )
    return componi_riepilogo(localita, oggi, titolo=titolo, con_attuale=True)


def riepilogo_domani(localita: Sequence[DatiLocalita], domani: date) -> str:
    titolo = f"🔮 <b>Domani, {data_lunga(domani)}</b>"
    return componi_riepilogo(localita, domani, titolo=titolo, con_attuale=False)


def riepilogo_posizione(dati: DatiLocalita, adesso: datetime, nome_da_osm: bool) -> str:
    """Risposta a una posizione mandata su Telegram: com'è il tempo lì, adesso."""
    titolo = f"🧭 <b>Meteo dove sei</b>\n{data_lunga(adesso.date())}, ore {ora(adesso)}"
    crediti = f"{CREDITI} · {CREDITI_OSM}" if nome_da_osm else CREDITI
    return componi_riepilogo(
        [dati], adesso.date(), titolo=titolo, con_attuale=True, crediti=crediti
    )
