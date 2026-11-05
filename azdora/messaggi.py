"""Testi dei messaggi del bot, in HTML di Telegram (<b>, <i>, <code>).

Qui c'è solo formattazione: nessuna chiamata di rete, quindi è tutto testabile.
"""

from __future__ import annotations

from datetime import date, datetime
from html import escape

from azdora.meteo.codici_wmo import condizione
from azdora.meteo.mare import stato_mare
from azdora.meteo.openmeteo import MareGiorno, MeteoGiorno, Previsioni, PrevisioniMare
from azdora.meteo.vento import descrivi_vento, direzione_vento, punto_cardinale

GIORNI_BREVI = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")
GIORNI = ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica")
MESI = (
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
)

ND = "n.d."
CREDITI = '<i>Dati: <a href="https://open-meteo.com/">Open-Meteo.com</a></i>'


# --- Piccoli formattatori ---------------------------------------------------------------


def gradi(valore: float | None) -> str:
    return ND if valore is None else f"{valore:.0f}°"


def kmh(valore: float | None) -> str:
    return ND if valore is None else f"{valore:.0f} km/h"


def percentuale(valore: float | None) -> str:
    return ND if valore is None else f"{valore:.0f}%"


def ora(valore: datetime | None) -> str:
    return ND if valore is None else valore.strftime("%H:%M")


def data_breve(giorno: date) -> str:
    """Esempio: ``"gio 5/11"``."""
    return f"{GIORNI_BREVI[giorno.weekday()]} {giorno.day}/{giorno.month}"


def data_lunga(giorno: date) -> str:
    """Esempio: ``"giovedì 5 novembre"``."""
    return f"{GIORNI[giorno.weekday()]} {giorno.day} {MESI[giorno.month - 1]}"


def onde(altezza: float | None, direzione: float | None = None) -> str:
    """Esempio: ``"0.4 m da NE (poco mosso)"``."""
    if altezza is None:
        return ND
    testo = f"{altezza:.1f} m"
    if direzione is not None:
        testo += f" da {punto_cardinale(direzione)}"
    return f"{testo} ({stato_mare(altezza).descrizione})"


def pioggia(giorno: MeteoGiorno) -> str:
    testo = f"pioggia {percentuale(giorno.prob_pioggia)}"
    if giorno.pioggia_mm:
        testo += f" ({giorno.pioggia_mm:.1f} mm)"
    return testo


def riga_giorno(giorno: MeteoGiorno) -> str:
    """Esempio: ``"ven 6/11 🌧️ Pioggia debole · 10°/13° · pioggia 88% (8.5 mm)"``."""
    return (
        f"{data_breve(giorno.data)} {condizione(giorno.codice)} · "
        f"{gradi(giorno.t_min)}/{gradi(giorno.t_max)} · {pioggia(giorno)}"
    )


def vento_del_giorno(giorno: MeteoGiorno) -> str:
    """Esempio: ``"Maestrale da NO ↘, fino a 11 km/h, raffiche 21 km/h"``."""
    parti = []
    if giorno.direzione_dominante is not None:
        parti.append(direzione_vento(giorno.direzione_dominante))
    parti.append(f"fino a {kmh(giorno.vento_max)}")
    if giorno.raffiche_max is not None:
        parti.append(f"raffiche {kmh(giorno.raffiche_max)}")
    return ", ".join(parti)


def _titolo(emoji: str, testo: str, previsioni: Previsioni | PrevisioniMare) -> str:
    return f"{emoji} <b>{escape(testo)} a {escape(previsioni.localita.nome)}</b>"


# --- Comandi ----------------------------------------------------------------------------


def messaggio_meteo(previsioni: Previsioni) -> str:
    a = previsioni.attuale
    righe = [
        _titolo("🌤️", "Meteo", previsioni),
        "",
        f"<b>Adesso</b> ({ora(a.ora)}): {condizione(a.codice)}, {gradi(a.temperatura)}"
        f" (percepiti {gradi(a.percepita)}), umidità {percentuale(a.umidita)}",
        f"💨 {descrivi_vento(a.vento, a.direzione_vento, a.raffiche)}",
        "",
        "<b>Prossimi giorni</b>",
        *(riga_giorno(g) for g in previsioni.giorni),
    ]
    if previsioni.giorni and previsioni.giorni[0].alba:
        oggi = previsioni.giorni[0]
        righe += ["", f"🌅 Alba {ora(oggi.alba)} · 🌇 Tramonto {ora(oggi.tramonto)}"]
    righe += ["", CREDITI]
    return "\n".join(righe)


def messaggio_vento(previsioni: Previsioni) -> str:
    a = previsioni.attuale
    righe = [
        _titolo("💨", "Vento", previsioni),
        "",
        f"<b>Adesso</b> ({ora(a.ora)}): {descrivi_vento(a.vento, a.direzione_vento, a.raffiche)}",
    ]
    prossime = previsioni.prossime_ore()
    if prossime:
        righe += ["", "<b>Prossime ore</b>"]
        for o in prossime:
            direzione = (
                "" if o.direzione_vento is None else f"{direzione_vento(o.direzione_vento)} "
            )
            raffiche = "" if o.raffiche is None else f", raffiche {kmh(o.raffiche)}"
            righe.append(f"<code>{ora(o.ora)}</code> {direzione}{kmh(o.vento)}{raffiche}")
    if previsioni.giorni:
        righe += ["", "<b>Giorni</b>"]
        righe += [f"{data_breve(g.data)}: {vento_del_giorno(g)}" for g in previsioni.giorni]
    righe += ["", CREDITI]
    return "\n".join(righe)


def messaggio_mare(mare: PrevisioniMare) -> str:
    a = mare.attuale
    righe = [
        _titolo("🌊", "Mare", mare),
        "",
        f"<b>Adesso</b>: onde {onde(a.altezza_onde, a.direzione_onde)}"
        + ("" if a.periodo_onde is None else f", periodo {a.periodo_onde:.1f} s"),
        f"🌡️ Acqua: {gradi(a.temperatura_acqua)}C"
        if a.temperatura_acqua is not None
        else "🌡️ Acqua: n.d.",
    ]
    if mare.giorni:
        righe += ["", "<b>Prossimi giorni</b>"]
        righe += [
            f"{data_breve(g.data)}: onde fino a {onde(g.altezza_max, g.direzione_dominante)}"
            for g in mare.giorni
        ]
    righe += ["", "<i>Valori al largo, indicativi per la costa.</i>", CREDITI]
    return "\n".join(righe)


def mare_del_giorno(giorno: MareGiorno | None) -> str:
    if giorno is None:
        return ND
    return f"onde fino a {onde(giorno.altezza_max, giorno.direzione_dominante)}"


AIUTO = """👋 <b>Ciao, sono l'Azdora!</b>
Tengo d'occhio il tempo tra Riccione e Rimini.

/oggi – riepilogo di oggi (meteo, vento, mare, consigli)
/domani – com'è domani
/meteo – meteo adesso e prossimi giorni
/vento – vento adesso e nelle prossime ore
/mare – onde e temperatura dell'acqua
/id – l'id di questa chat
/aiuto – questo messaggio

Puoi aggiungere la località: <code>/vento rimini</code>, <code>/meteo rn</code>.
Senza località uso Riccione.
Ogni mattina alle 7 ti mando il riepilogo. ☕"""


def messaggio_id(chat_id: int, autorizzata: bool) -> str:
    righe = [f"🆔 L'id di questa chat è <code>{chat_id}</code>"]
    if not autorizzata:
        righe.append(
            "Questa chat non è ancora autorizzata: metti questo numero in "
            "<code>TELEGRAM_CHAT_ID</code> (chat privata) o "
            "<code>TELEGRAM_CHAT_ID_COPPIA</code> (gruppo) su Vercel."
        )
    return "\n".join(righe)


LOCALITA_SCONOSCIUTA = "🤔 Non conosco «{nome}». Per ora so dire di: {elenco}."
COMANDO_SCONOSCIUTO = "🤔 Non conosco questo comando. Scrivi /aiuto per vedere cosa so fare."
METEO_NON_DISPONIBILE = "😓 Open-Meteo non risponde in questo momento. Riprova tra qualche minuto."
