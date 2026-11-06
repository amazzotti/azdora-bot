"""Testi dei messaggi del bot, in HTML di Telegram (<b>, <i>, <code>).

Qui c'è solo formattazione: nessuna chiamata di rete, quindi è tutto testabile.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from html import escape
from typing import Any

from azdora.meteo.codici_wmo import condizione
from azdora.meteo.mare import stato_mare
from azdora.meteo.openmeteo import MareGiorno, MeteoGiorno, Previsioni, PrevisioniMare
from azdora.meteo.vento import descrivi_vento, direzione_vento, punto_cardinale
from azdora.posti import a_posto
from azdora.soldi import euro

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
GIORNI_MOSTRATI = 3  # Open-Meteo ne dà 7, ma nei messaggi ne bastano 3
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
        *(riga_giorno(g) for g in previsioni.giorni[:GIORNI_MOSTRATI]),
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
        righe += [
            f"{data_breve(g.data)}: {vento_del_giorno(g)}"
            for g in previsioni.giorni[:GIORNI_MOSTRATI]
        ]
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
            for g in mare.giorni[:GIORNI_MOSTRATI]
        ]
    righe += ["", "<i>Valori al largo, indicativi per la costa.</i>", CREDITI]
    return "\n".join(righe)


def mare_del_giorno(giorno: MareGiorno | None) -> str:
    if giorno is None:
        return ND
    return f"onde fino a {onde(giorno.altezza_max, giorno.direzione_dominante)}"


AIUTO = """👋 <b>Ciao, sono l'Azdora!</b> Mando avanti la casa. Scrivimi come parli:
• <i>45 benzina</i> o <i>ho speso 12,50 per la pizza</i> → segno la spesa
• <i>ricordami domani alle 18 di chiamare il meccanico</i> → te lo ricordo
• <i>ricordami ogni lunedì alle 20 di portare fuori la plastica</i> → ogni settimana
• <i>ricordami di comprare il pane quando arrivo a Rimini</i> → quando ci arrivi
• <i>ricordami il compleanno della mamma il 14/3</i> → ogni anno, anche una settimana prima
• 📍 mandami la posizione → meteo, notizie e allerte di dove sei

🌤️ <b>Meteo</b>
/oggi · /domani – riepilogo con consigli
/meteo · /vento · /mare – dove sei o dove vuoi: <code>/vento cesenatico</code>
/costa – tempo, vento e mare da Cervia a Pesaro (anche <code>/costa domani</code>)
/aria – UV, qualità dell'aria e pollini · /qui – manda la posizione

🗞️ <b>Intorno a te</b>
/notizie – le notizie di oggi divise per città, più Italia e mondo
/italia · /mondo – le principali notizie nazionali e dal mondo
/allerte – allerte meteo · /benzina – i distributori più economici
/dove · /dimentica – la posizione che ricordo

💶 <b>Soldi</b>
/mese · /spese · /esporta – riepiloghi e file per Excel
/budget – quanto vuoi spendere al mese (anche per categoria)
/scadenza · /scadenze – bollo, assicurazione… con ✅ Pagato

🏠 <b>Casa e gruppo</b>
/aggiungi latte, pane · /lista – la lista condivisa
/lista fissa – la lista in alto nella chat, sempre aggiornata
/conti · /pareggio – nel gruppo: chi deve quanto a chi
/rifiuti – raccolta differenziata, avviso la sera prima
/promemoria – i tuoi promemoria · /sera – riepilogo della sera
/compleanni – i compleanni segnati, dal più vicino
/posto lavoro – salva dove sei, per <i>«ricordami… quando arrivo al lavoro»</i>
/orari – quando ti scrivo (mattino, sera, notizie, silenzio)
/telefono – posizione automatica dal telefono · /stato – come sto
/tv – stasera in TV · /tv film · /tv adesso · /tv rai1
/weekend – meteo, gite e sagre · /sagre – feste da Cervia a Pesaro

Ogni mattina alle 7 ti mando il riepilogo ☕. /id – l'id di questa chat"""

CREDITI_OSM = '<i>Luogo: © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a></i>'

# Tastiera con il pulsante che chiede la posizione (funziona solo nelle chat private).
TASTIERA_POSIZIONE = {
    "keyboard": [[{"text": "📍 Manda la mia posizione", "request_location": True}]],
    "resize_keyboard": True,
    "one_time_keyboard": True,
    "input_field_placeholder": "Tocca il pulsante qui sotto",
}
TOGLI_TASTIERA = {"remove_keyboard": True}

QUI_PRIVATA = "📍 Tocca il pulsante qui sotto e ti dico che tempo fa dove sei."
QUI_GRUPPO = (
    "📍 Nei gruppi il pulsante non funziona: manda la posizione con 📎 → <b>Posizione</b> "
    "e vi dico che tempo fa lì."
)
NOME_POSIZIONE_SCONOSCIUTO = "La tua posizione"


def mare_lontano(nome: str) -> str:
    return f"🏞️ {escape(nome)} è lontano dal mare: niente onde da mostrare."


def messaggio_id(chat_id: int, autorizzata: bool) -> str:
    righe = [f"🆔 L'id di questa chat è <code>{chat_id}</code>"]
    if not autorizzata:
        righe.append(
            "Questa chat non è ancora autorizzata: manda questo numero a chi gestisce il bot.\n"
            "Per chi lo gestisce, su Vercel: <code>TELEGRAM_CHAT_ID</code> (la tua chat), "
            "<code>TELEGRAM_CHAT_ID_FAMIGLIA</code> (chat di un familiare, numeri separati da "
            "virgola), <code>TELEGRAM_CHAT_ID_COPPIA</code> (gruppo di coppia) o "
            "<code>TELEGRAM_CHAT_ID_GRUPPI</code> (altri gruppi, separati da virgola)."
        )
    return "\n".join(righe)


LOCALITA_SCONOSCIUTA = "🤔 Non trovo «{nome}». Prova a scrivere il nome del comune per intero."
COMANDO_SCONOSCIUTO = "🤔 Non conosco questo comando. Scrivi /aiuto per vedere cosa so fare."
METEO_NON_DISPONIBILE = "😓 Open-Meteo non risponde in questo momento. Riprova tra qualche minuto."


# --- Tappa 2: posizione salvata, notizie e allerte ----------------------------------------

POSIZIONE_RICORDATA = (
    "📌 Mi ricordo che sei qui: notizie e allerte saranno su questa zona per 3 giorni. "
    "/dimentica per cancellare la posizione."
)
POSIZIONE_LIVE = (
    "📡 Posizione in tempo reale: aggiorno la tua zona mentre ti sposti, finché la condividi."
)
POSIZIONE_DIMENTICATA = "🧹 Fatto: ho dimenticato la tua posizione. Torno a guardare Riccione."
NESSUNA_POSIZIONE = "🏠 Non so dove sei: uso Riccione. Mandami la posizione con /qui."
ARCHIVIO_NON_DISPONIBILE = "😓 Non riesco a leggere la mia memoria (Upstash). Riprova tra poco."


def messaggio_dove(etichetta: str, quando: datetime, in_tempo_reale: bool, valida: bool) -> str:
    righe = [
        f"📍 L'ultima posizione che mi hai mandato è <b>{escape(etichetta)}</b>",
        f"({data_breve(quando.date())} alle {ora(quando)}"
        + (", in tempo reale)" if in_tempo_reale else ")"),
    ]
    if not valida:
        righe.append("È passato troppo tempo: per notizie e allerte uso Riccione.")
    righe.append("/dimentica per cancellarla.")
    return "\n".join(righe)


def titolo_notizie(etichetta: str, nuove: bool) -> str:
    if nuove:
        return f"🗞️ <b>Novità da {escape(etichetta)}</b>"
    return f"🗞️ <b>Ultime notizie da {escape(etichetta)}</b>"


def titolo_notizie_riviera(comune: str) -> str:
    return f"🗞️ <b>Le notizie di oggi, da Cervia a Pesaro</b>\n<i>📍 = parla di {escape(comune)}</i>"


ERRORE_INATTESO = (
    "😓 Qualcosa è andato storto mentre ci lavoravo. Riprova tra un attimo; "
    "se continua, scrivi /stato."
)
TITOLO_NOTIZIE_OGGI = "🗞️ <b>Le notizie di oggi</b>"


def titolo_riassunto(momento) -> str:
    saluto = (
        "del mattino"
        if momento.hour < 12
        else "di mezzogiorno"
        if momento.hour < 15
        else ("del pomeriggio" if momento.hour < 18 else "della sera")
    )
    return f"🗞️ <b>Le notizie {saluto}</b>"


TITOLI_PRINCIPALI = {"italia": "🇮🇹 <b>Italia</b>", "mondo": "🌍 <b>Mondo</b>"}
TITOLI_SEZIONE = {
    "italia": "🇮🇹 <b>Le principali notizie dall'Italia</b>",
    "mondo": "🌍 <b>Le principali notizie dal mondo</b>",
}
NESSUNA_PRINCIPALE = (
    "🗞️ In questo momento non riesco a leggere le notizie nazionali. Riprova tra poco."
)


def nessuna_notizia(etichetta: str) -> str:
    return f"🗞️ Nessuna notizia nelle ultime ore su {escape(etichetta)}."


def titolo_allerte(regione: str) -> str:
    return f"⚠️ <b>Allerte meteo · {escape(regione)}</b>"


def nessuna_allerta(regione: str) -> str:
    return f"🟢 Nessuna allerta meteo in {escape(regione)}."


ALLERTE_NON_DISPONIBILI = "🤷 Per questa zona non ho dati sulle allerte meteo."


# --- Tappe 3-5: scadenze, spese, lista ----------------------------------------------------

USO_SPESA = (
    "💶 Scrivi importo e cosa: <code>/spesa 45 benzina</code>, <code>/spesa 12,50 pizza</code>.\n"
    "La categoria la indovino io; /mese per il riepilogo, /annulla se sbagli."
)
USO_MESE = (
    "📆 Scrivi il mese così: <code>/mese 10</code>, <code>/mese 10/2026</code> "
    "o <code>/mese ottobre</code>."
)
NIENTE_DA_ANNULLARE = "🤷 Questo mese non ci sono spese da annullare."
USO_AGGIUNGI = "📝 Scrivi cosa aggiungere, separato da virgole: <code>/aggiungi latte, pane</code>"


def spesa_segnata(importo: int, categoria: str, descrizione: str, totale_mese: int) -> str:
    return (
        f"✅ Segnata: <b>{euro(importo)}</b> · {escape(categoria)} · {escape(descrizione)}\n"
        f"Questo mese: {euro(totale_mese)}"
    )


def aggiunte_alla_lista(testi: Sequence[str]) -> str:
    cose = ", ".join(escape(t) for t in testi)
    return f"✅ Aggiunto: {cose}. La lista è in alto 📌"


FISSA_SENZA_PERMESSO = (
    "📌 Ho mandato la lista ma non ho il permesso di fissarla in alto.\n"
    "Fissala tu: tieni premuto il messaggio → <b>Fissa</b>. Si aggiornerà lo stesso.\n"
    "Oppure rendimi amministratore del gruppo con il permesso <i>Fissare messaggi</i> "
    "e riscrivi /lista fissa."
)
FISSA_NON_DISPONIBILE = "📌 Qui non posso fissare messaggi."
LISTA_SGANCIATA = "📌 Ok, la lista non è più fissata. La trovi sempre con /lista."


def posto_salvato(nome: str, posizione: Any) -> str:
    dove = f" ({escape(posizione.etichetta)})" if getattr(posizione, "comune", None) else ""
    return (
        f"📌 Salvato «{escape(nome)}» qui{dove}.\n"
        f"Ora puoi scrivere ad esempio: <i>ricordami di … quando arrivo {_articolo(nome)}</i>"
    )


def _articolo(nome: str) -> str:
    return a_posto(nome)


def lista_svuotata(quante: int) -> str:
    if quante == 0:
        return "📝 Non c'era niente di spuntato da togliere."
    return f"🧹 Tolte {quante} cose fatte dalla lista."


# --- Migliorie: spese libere, conti, sera, aria, carburante --------------------------------

NON_HO_CAPITO = (
    "🤔 Non ho capito. Puoi scrivermi come parli:\n"
    "• <i>45 benzina</i> oppure <i>ho speso 12,50 per la pizza</i> → segno una spesa\n"
    "• <i>ricordami domani alle 18 di chiamare il meccanico</i> → promemoria\n"
    "Oppure usa un comando: /aiuto li elenca tutti."
)
CONTI_SOLO_GRUPPO = (
    "🤝 I conti si fanno nel gruppo: lì ogni spesa ricorda chi l'ha pagata. "
    "Scrivete <code>/spesa 60 spesa conad</code> e poi /conti."
)
PAREGGIO = "🤝 Fatto: siete pari! Da domani i conti ripartono da zero."
ARIA_NON_DISPONIBILE = "😓 Il servizio della qualità dell'aria non risponde. Riprova tra poco."
CARBURANTE_NON_DISPONIBILE = (
    "😓 Il sito dei prezzi dei carburanti del Ministero non risponde. Riprova tra poco."
)


def spesa_annullata(importo: int, descrizione: str) -> str:
    return f"↩️ Annullata: <s>{euro(importo)} · {escape(descrizione)}</s>"


def scegli_categoria(descrizione: str) -> str:
    return f"🏷️ In che categoria metto <b>{escape(descrizione)}</b>?"


def categoria_cambiata(importo: int, categoria: str, descrizione: str) -> str:
    return (
        f"✅ {euro(importo)} · {escape(descrizione)} → <b>{escape(categoria)}</b>\n"
        "La prossima volta lo metto lì da solo."
    )


def sera_impostata(attiva: bool) -> str:
    if attiva:
        return (
            "🌙 Riepilogo della sera <b>attivo</b>: alle 20 ti mando il meteo di domani, "
            "i promemoria e le scadenze del giorno dopo. /sera per spegnerlo."
        )
    return "🌙 Riepilogo della sera <b>spento</b>. I rifiuti te li ricordo comunque."


def riparato(in_coda: int) -> str:
    return (
        f"🔧 Mi ero inceppata: Telegram aveva {in_coda} messaggi bloccati. Ho svuotato la "
        "coda e adesso funziono di nuovo. Se mi avevi scritto da poco, riscrivimi pure!"
    )
