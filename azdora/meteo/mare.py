"""Stato del mare secondo la scala Douglas, a partire dall'altezza d'onda significativa."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StatoMare:
    grado: int
    descrizione: str


# Limite superiore (incluso) in metri di ogni grado della scala Douglas.
_SCALA_DOUGLAS: tuple[tuple[float, str], ...] = (
    (0.0, "calmo"),
    (0.10, "quasi calmo"),
    (0.50, "poco mosso"),
    (1.25, "mosso"),
    (2.5, "molto mosso"),
    (4.0, "agitato"),
    (6.0, "molto agitato"),
    (9.0, "grosso"),
    (14.0, "molto grosso"),
)


def stato_mare(altezza_m: float) -> StatoMare:
    """Esempio: ``stato_mare(0.4)`` → grado 2, ``"poco mosso"``."""
    for grado, (limite, descrizione) in enumerate(_SCALA_DOUGLAS):
        if altezza_m <= limite:
            return StatoMare(grado, descrizione)
    return StatoMare(9, "tempestoso")
