"""Vento: da gradi e km/h a parole che si usano in Riviera.

Open-Meteo dà la direzione *da cui* soffia il vento (convenzione meteorologica):
0° = da nord, 90° = da est. I nomi seguono la rosa dei venti dell'Adriatico,
con il nome romagnolo "garbino" per il libeccio da sud-ovest.
"""

from __future__ import annotations

from dataclasses import dataclass

PUNTI_CARDINALI = ("N", "NE", "E", "SE", "S", "SO", "O", "NO")
NOMI_VENTI = (
    "Tramontana",
    "Bora",
    "Levante",
    "Scirocco",
    "Ostro",
    "Garbino",
    "Ponente",
    "Maestrale",
)
# Freccia verso dove VA il vento: la tramontana (da nord) spinge verso sud, quindi "↓".
FRECCE = ("↓", "↙", "←", "↖", "↑", "↗", "→", "↘")

KMH_PER_NODO = 1.852


def settore(gradi: float) -> int:
    """Indice 0-7 dello spicchio di 45° (centrato sul punto cardinale) che contiene ``gradi``."""
    return int(((gradi % 360) + 22.5) // 45) % 8


def punto_cardinale(gradi: float) -> str:
    return PUNTI_CARDINALI[settore(gradi)]


def nome_vento(gradi: float) -> str:
    return NOMI_VENTI[settore(gradi)]


def freccia(gradi: float) -> str:
    return FRECCE[settore(gradi)]


def kmh_in_nodi(kmh: float) -> float:
    return kmh / KMH_PER_NODO


@dataclass(frozen=True)
class Beaufort:
    forza: int
    descrizione: str


# Limite superiore (escluso) in km/h di ogni grado della scala Beaufort.
_SCALA_BEAUFORT: tuple[tuple[float, str], ...] = (
    (1, "calma"),
    (6, "bava di vento"),
    (12, "brezza leggera"),
    (20, "brezza tesa"),
    (29, "vento moderato"),
    (39, "vento teso"),
    (50, "vento fresco"),
    (62, "vento forte"),
    (75, "burrasca moderata"),
    (89, "burrasca forte"),
    (103, "tempesta"),
    (118, "fortunale"),
)


def beaufort(kmh: float) -> Beaufort:
    """Grado Beaufort (0-12) per una velocità media in km/h."""
    for forza, (limite, descrizione) in enumerate(_SCALA_BEAUFORT):
        if kmh < limite:
            return Beaufort(forza, descrizione)
    return Beaufort(12, "uragano")


def direzione_vento(gradi: float) -> str:
    """Esempio: ``"Garbino da SO ↗"``."""
    return f"{nome_vento(gradi)} da {punto_cardinale(gradi)} {freccia(gradi)}"


def descrivi_vento(velocita: float | None, gradi: float | None, raffiche: float | None) -> str:
    """Frase completa sul vento.

    Esempio: ``"Garbino da SO ↗ 18 km/h (10 nodi), raffiche 30 km/h · forza 3, brezza tesa"``.
    """
    if velocita is None:
        return "dati sul vento non disponibili"
    scala = beaufort(velocita)
    if scala.forza == 0:
        return "calma piatta, niente vento"
    parti = []
    if gradi is not None:
        parti.append(direzione_vento(gradi))
    parti.append(f"{velocita:.0f} km/h ({kmh_in_nodi(velocita):.0f} nodi)")
    testo = " ".join(parti)
    if raffiche is not None and raffiche > velocita:
        testo += f", raffiche {raffiche:.0f} km/h"
    return f"{testo} · forza {scala.forza}, {scala.descrizione}"
