"""Lista condivisa (cose da fare, spesa…): pensata per il gruppo di coppia.

``/aggiungi latte, pane, prenotare il ristorante`` aggiunge più cose in una volta;
``/lista`` mostra la lista con un pulsante per spuntare ogni voce.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from html import escape
from typing import Any

from azdora.archivio import Archivio

MASSIMO_VOCI = 60  # Telegram accetta al massimo 100 pulsanti per messaggio
LUNGHEZZA_VOCE = 80


@dataclass(frozen=True)
class Voce:
    id: int
    testo: str
    fatta: bool = False
    da: str = ""


def leggi_voci(testo: str) -> list[str]:
    """``"latte, pane\\nuova"`` → ``["latte", "pane", "uova"]``."""
    voci = (v.strip(" -•\t") for v in re.split(r"[,;\n]", testo))
    return [v[:LUNGHEZZA_VOCE] for v in voci if v]


class Lista:
    def __init__(self, archivio: Archivio, chat_id: int) -> None:
        self._archivio = archivio
        self._chiave = f"lista:{chat_id}"

    def voci(self) -> list[Voce]:
        return [Voce(**d) for d in self._archivio.leggi(self._chiave) or []]

    def _salva(self, voci: Sequence[Voce]) -> None:
        self._archivio.scrivi(self._chiave, [asdict(v) for v in voci])

    def aggiungi(self, testi: Sequence[str], da: str = "") -> list[Voce]:
        voci = self.voci()
        prossimo = max((v.id for v in voci), default=0) + 1
        spazio = max(MASSIMO_VOCI - len(voci), 0)
        nuove = [Voce(prossimo + i, t, False, da) for i, t in enumerate(testi[:spazio])]
        self._salva([*voci, *nuove])
        return nuove

    def cambia(self, id_voce: int) -> Voce | None:
        """Spunta o toglie la spunta."""
        voci = self.voci()
        trovata = next((v for v in voci if v.id == id_voce), None)
        if trovata is None:
            return None
        cambiata = replace(trovata, fatta=not trovata.fatta)
        self._salva([cambiata if v.id == id_voce else v for v in voci])
        return cambiata

    def togli_fatte(self) -> int:
        voci = self.voci()
        rimaste = [v for v in voci if not v.fatta]
        self._salva(rimaste)
        return len(voci) - len(rimaste)


def messaggio_lista(voci: Sequence[Voce]) -> str:
    if not voci:
        return (
            "📝 La lista è vuota.\n"
            "Aggiungi qualcosa: <code>/aggiungi latte, pane, prenotare il ristorante</code>"
        )
    da_fare = sum(not v.fatta for v in voci)
    righe = [f"📝 <b>Lista</b> · {da_fare} da fare su {len(voci)}", ""]
    for v in voci:
        segno = "✅" if v.fatta else "⬜"
        testo = f"<s>{escape(v.testo)}</s>" if v.fatta else escape(v.testo)
        chi = f" <i>({escape(v.da)})</i>" if v.da else ""
        righe.append(f"{segno} {testo}{chi}")
    righe += ["", "Tocca una voce per spuntarla."]
    return "\n".join(righe)


def pulsanti_lista(voci: Sequence[Voce]) -> dict[str, Any] | None:
    if not voci:
        return None
    righe = [
        [{"text": f"{'✅' if v.fatta else '⬜'} {v.testo[:30]}", "callback_data": f"voce:{v.id}"}]
        for v in voci
    ]
    if any(v.fatta for v in voci):
        righe.append([{"text": "🧹 Togli le cose fatte", "callback_data": "lista:pulisci"}])
    return {"inline_keyboard": righe}
