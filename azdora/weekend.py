"""Cosa facciamo nel weekend? Meteo di sabato e domenica, giri della domenica e sagre.

- Le idee sono un piccolo catalogo di posti a meno di un'ora da Riccione, divisi tra
  "all'aperto" e "al coperto": se il weekend è brutto, il bot propone quelle al coperto.
- Ogni settimana la scelta cambia (dipende dal numero della settimana), così non
  propone sempre le stesse.
- Sagre ed eventi arrivano da Altarimini (articoli con categoria "Eventi") e da una
  ricerca su Google News.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from html import escape

from azdora.meteo.codici_wmo import CODICI_NEVE, CODICI_PIOGGIA, CODICI_TEMPORALE, condizione
from azdora.meteo.openmeteo import MeteoGiorno
from azdora.notizie import Notizia, formatta_breve

PIOGGIA_PROBABILE = 50


@dataclass(frozen=True)
class Idea:
    nome: str
    descrizione: str
    al_coperto: bool
    km: int  # da Riccione, più o meno


IDEE: tuple[Idea, ...] = (
    Idea("San Leo", "la fortezza di Cagliostro sulla rupe, e una piadina in piazza", False, 40),
    Idea("Gradara", "il castello di Paolo e Francesca e il giro delle mura", False, 15),
    Idea("San Marino", "le Tre Torri e il passo delle Streghe, con vista fino al mare", False, 25),
    Idea(
        "Santarcangelo", "centro storico, grotte tufacee e un caffè in piazza Ganganelli", False, 20
    ),
    Idea("Verucchio", "la rocca malatestiana e i belvedere sulla Valmarecchia", False, 25),
    Idea("Montefiore Conca", "rocca, borgo e passeggiata nei boschi della Valconca", False, 20),
    Idea(
        "Mondaino e Saludecio", "due borghi della Valconca, perfetti per un giro lento", False, 25
    ),
    Idea("Parco del San Bartolo", "la panoramica Pesaro–Gabicce, a piedi o in bici", False, 20),
    Idea("Cesenatico", "il porto canale leonardesco e il museo della marineria", False, 40),
    Idea("Saline di Cervia", "passeggiata tra fenicotteri e saline al tramonto", False, 55),
    Idea("Brisighella", "la Via degli Asini e i tre colli, con olio e Sangiovese", False, 75),
    Idea("Pennabilli", "il borgo di Tonino Guerra e i suoi luoghi dell'anima", False, 50),
    Idea("Fellini Museum", "a Rimini, tra Castel Sismondo e il Fulgor", True, 10),
    Idea("Domus del Chirurgo", "la casa romana del medico di Ariminum, a Rimini", True, 10),
    Idea("Terme di Riccione", "una giornata di relax alle terme, a due passi da casa", True, 2),
    Idea("Urbino", "il Palazzo Ducale e la Galleria Nazionale delle Marche", True, 60),
    Idea(
        "Museo della Marineria", "a Cesenatico, con le barche storiche nel porto canale", True, 40
    ),
    Idea("Teatro Galli", "uno spettacolo nel teatro di Rimini: guarda il cartellone", True, 10),
)


def prossimo_weekend(oggi: date) -> tuple[date, date]:
    """Sabato e domenica di questa settimana (se è già domenica, oggi e ieri)."""
    if oggi.weekday() == 6:
        return oggi - timedelta(days=1), oggi
    sabato = oggi + timedelta(days=5 - oggi.weekday())
    return sabato, sabato + timedelta(days=1)


def brutto_tempo(giorno: MeteoGiorno | None) -> bool:
    if giorno is None:
        return False
    codice = giorno.codice
    piove = codice in CODICI_PIOGGIA | CODICI_NEVE | CODICI_TEMPORALE
    return (giorno.prob_pioggia or 0) >= PIOGGIA_PROBABILE or (
        piove and (giorno.prob_pioggia or 0) >= 30
    )


def scegli_idee(al_coperto: bool, settimana: int, quante: int = 3) -> list[Idea]:
    """Idee del tipo giusto, ruotate in base alla settimana dell'anno."""
    adatte = [i for i in IDEE if i.al_coperto == al_coperto]
    inizio = (settimana * quante) % len(adatte)
    return [adatte[(inizio + k) % len(adatte)] for k in range(min(quante, len(adatte)))]


def riga_meteo(nome: str, giorno: MeteoGiorno | None) -> str:
    if giorno is None:
        return f"{nome}: previsioni non ancora disponibili"
    pioggia = f", pioggia {giorno.prob_pioggia:.0f}%" if giorno.prob_pioggia is not None else ""
    t_min = "?" if giorno.t_min is None else f"{giorno.t_min:.0f}°"
    t_max = "?" if giorno.t_max is None else f"{giorno.t_max:.0f}°"
    return f"{nome}: {condizione(giorno.codice)} · {t_min}/{t_max}{pioggia}"


def messaggio_weekend(
    sabato: MeteoGiorno | None,
    domenica: MeteoGiorno | None,
    giorno_sabato: date,
    eventi: Sequence[Notizia],
) -> str:
    al_coperto = brutto_tempo(sabato) and brutto_tempo(domenica)
    settimana = giorno_sabato.isocalendar().week
    idee = scegli_idee(al_coperto, settimana)
    righe = [
        "🧺 <b>Cosa facciamo nel weekend?</b>",
        "",
        riga_meteo("Sabato", sabato),
        riga_meteo("Domenica", domenica),
        "",
        "☔ Weekend bagnato: idee al coperto" if al_coperto else "🚗 <b>Giri della domenica</b>",
    ]
    for idea in idee:
        righe.append(f"• <b>{escape(idea.nome)}</b> ({idea.km} km): {escape(idea.descrizione)}")
    if eventi:
        righe += ["", formatta_breve(eventi[:5], "🎪 <b>Sagre ed eventi in zona</b>")]
    return "\n".join(righe)
