"""La costa da Cervia a Pesaro in un colpo d'occhio: tempo, vento e mare di ogni località."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from html import escape

from azdora.config import RICCIONE, RIMINI, Localita
from azdora.meteo.codici_wmo import condizione
from azdora.meteo.mare import stato_mare
from azdora.meteo.openmeteo import MareGiorno, MeteoGiorno, Previsioni
from azdora.meteo.vento import nome_vento

# Da nord a sud. I punti "mare" sono un po' al largo, dove il modello marino ha dati.
COSTA: tuple[Localita, ...] = (
    Localita("cervia", "Cervia · Milano Marittima", 44.2630, 12.3490, 44.27, 12.40),
    Localita("cesenatico", "Cesenatico", 44.1990, 12.3960, 44.21, 12.43),
    Localita("bellaria", "Bellaria · Igea Marina", 44.1460, 12.4710, 44.16, 12.50),
    RIMINI,
    RICCIONE,
    Localita("cattolica", "Cattolica · Gabicce", 43.9630, 12.7380, 43.98, 12.76),
    Localita("pesaro", "Pesaro", 43.9098, 12.9131, 43.93, 12.93),
)


@dataclass(frozen=True)
class Spiaggia:
    nome: str
    previsioni: Previsioni
    giorno: MeteoGiorno | None
    mare: MareGiorno | None
    adesso: bool = True  # oggi: vento di adesso; domani: vento previsto in giornata


def riga(spiaggia: Spiaggia) -> str:
    a = spiaggia.previsioni.attuale
    parti = [f"<b>{escape(spiaggia.nome)}</b>"]
    g = spiaggia.giorno
    if g is not None and g.t_min is not None and g.t_max is not None:
        parti.append(f"{condizione(g.codice).emoji} {g.t_min:.0f}°/{g.t_max:.0f}°")
    if spiaggia.adesso:
        vento, direzione = a.vento, a.direzione_vento
    else:
        vento = g.vento_max if g else None
        direzione = g.direzione_dominante if g else None
    if vento is not None:
        verso = f"{nome_vento(direzione)} " if direzione is not None else ""
        parti.append(f"💨 {verso}{vento:.0f} km/h")
    if spiaggia.mare is not None and spiaggia.mare.altezza_max is not None:
        altezza = spiaggia.mare.altezza_max
        parti.append(
            f"🌊 {altezza:.1f}".replace(".", ",") + f" m {stato_mare(altezza).descrizione}"
        )
    return " · ".join(parti)


def messaggio_costa(spiagge: Sequence[Spiaggia], titolo: str) -> str:
    vento = "vento adesso" if all(s.adesso for s in spiagge) else "vento massimo"
    righe = [f"🏖️ <b>{escape(titolo)}</b>", f"<i>da nord a sud: tempo, {vento}, mare</i>", ""]
    righe += [riga(s) for s in spiagge]
    righe += ["", '<i>Dati: <a href="https://open-meteo.com/">Open-Meteo.com</a></i>']
    return "\n".join(righe)
