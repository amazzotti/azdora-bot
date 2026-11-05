"""Budget mensile: totale e per categoria, con avvisi quando ti avvicini o lo superi.

``/budget 800`` → 800 € al mese in tutto; ``/budget auto 150`` → 150 € al mese per l'auto;
``/budget auto 0`` → toglie quel budget.
"""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

from azdora.archivio import Archivio
from azdora.soldi import euro, leggi_importo
from azdora.spese import Spesa, barra, totale, trova_categoria

TOTALE = "totale"
SOGLIA_ATTENZIONE = 80  # percento


class Budget:
    def __init__(self, archivio: Archivio, chat_id: int) -> None:
        self._archivio = archivio
        self._chiave = f"budget:{chat_id}"

    def tutti(self) -> dict[str, int]:
        return self._archivio.leggi(self._chiave) or {}

    def imposta(self, voce: str, centesimi: int) -> None:
        budget = self.tutti()
        if centesimi > 0:
            budget[voce] = centesimi
        else:
            budget.pop(voce, None)
        self._archivio.scrivi(self._chiave, budget)


def leggi_budget(argomenti: Sequence[str]) -> tuple[str, int] | None:
    """``("800",)`` → (totale, 80000); ``("auto", "150")`` → ("🚗 Auto", 15000)."""
    if not argomenti:
        return None
    importo = leggi_importo(argomenti[-1])
    if importo is None:
        return None
    if len(argomenti) == 1:
        return TOTALE, importo
    categoria = trova_categoria(" ".join(argomenti[:-1]))
    return (categoria, importo) if categoria else None


def _riga(nome: str, speso: int, limite: int) -> str:
    percento = round(100 * speso / limite) if limite else 0
    segnale = "🚨" if percento >= 100 else "⚠️" if percento >= SOGLIA_ATTENZIONE else "✅"
    return (
        f"{segnale} {escape(nome)}: {euro(speso)} di {euro(limite)} ({percento}%)\n"
        f"<code>{barra(min(speso, limite), limite)}</code>"
    )


def stato(budget: dict[str, int], spese: Sequence[Spesa]) -> str:
    if not budget:
        return USO
    righe = ["📊 <b>Budget del mese</b>", ""]
    if TOTALE in budget:
        righe.append(_riga("Totale", totale(spese), budget[TOTALE]))
    for voce, limite in sorted(budget.items()):
        if voce != TOTALE:
            speso = totale(s for s in spese if s.categoria == voce)
            righe.append(_riga(voce, speso, limite))
    return "\n".join(righe)


def avviso_dopo_spesa(budget: dict[str, int], spese: Sequence[Spesa], spesa: Spesa) -> str:
    """Una riga sotto la spesa appena segnata, solo se c'è un budget che la riguarda."""
    righe = []
    for voce in (spesa.categoria, TOTALE):
        limite = budget.get(voce)
        if not limite:
            continue
        speso = totale(spese if voce == TOTALE else (s for s in spese if s.categoria == voce))
        percento = round(100 * speso / limite)
        nome = "Totale" if voce == TOTALE else voce
        if percento >= 100:
            righe.append(f"🚨 Budget {escape(nome)} superato: {euro(speso)} su {euro(limite)}")
        elif percento >= SOGLIA_ATTENZIONE:
            righe.append(f"⚠️ Budget {escape(nome)} al {percento}%: restano {euro(limite - speso)}")
        else:
            righe.append(f"📊 {escape(nome)}: {euro(speso)} di {euro(limite)}")
    return "\n".join(righe)


USO = (
    "📊 Nessun budget impostato. Esempi:\n"
    "<code>/budget 800</code> – 800 € al mese in tutto\n"
    "<code>/budget auto 150</code> – 150 € al mese per l'auto\n"
    "<code>/budget fuori 120</code> – ristoranti, bar, pizze…\n"
    "<code>/budget auto 0</code> – toglie quel budget\n"
    "Categorie: auto, spesa, bollette, casa, fuori, salute, svago, abbonamenti, vestiti, altro."
)
