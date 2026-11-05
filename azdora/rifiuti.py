"""Raccolta differenziata: ``/rifiuti martedì plastica, carta``, con avviso la sera prima.

Ogni comune (e ogni zona) ha il suo calendario: lo scrivi tu una volta e poi ci pensa l'Azdora.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from html import escape

from azdora.archivio import Archivio
from azdora.promemoria import GIORNI_SETTIMANA

EMOJI = {
    "plastica": "🟡",
    "carta": "🔵",
    "vetro": "🟢",
    "organico": "🟤",
    "umido": "🟤",
    "indifferenziata": "⚫",
    "secco": "⚫",
    "sfalci": "🌿",
    "verde": "🌿",
    "lattine": "🟡",
}


def giorno_della_settimana(parola: str) -> int | None:
    pulita = parola.strip().lower().replace("ì", "i")
    for i, nome in enumerate(GIORNI_SETTIMANA):
        if nome.replace("ì", "i").startswith(pulita) and len(pulita) >= 3:
            return i
    return None


def con_emoji(rifiuto: str) -> str:
    emoji = next((e for chiave, e in EMOJI.items() if rifiuto.lower().startswith(chiave)), "♻️")
    return f"{emoji} {rifiuto}"


class Calendario:
    def __init__(self, archivio: Archivio, chat_id: int) -> None:
        self._archivio = archivio
        self._chiave = f"rifiuti:{chat_id}"

    def tutto(self) -> dict[int, list[str]]:
        return {int(k): v for k, v in (self._archivio.leggi(self._chiave) or {}).items()}

    def imposta(self, giorno: int, rifiuti: Sequence[str]) -> None:
        calendario = self.tutto()
        if rifiuti:
            calendario[giorno] = list(rifiuti)
        else:
            calendario.pop(giorno, None)
        self._archivio.scrivi(self._chiave, {str(k): v for k, v in calendario.items()})

    def del_giorno(self, giorno: int) -> list[str]:
        return self.tutto().get(giorno, [])


def leggi_rifiuti(argomenti: Sequence[str]) -> tuple[int, list[str]] | None:
    """``("martedì", "plastica,", "carta")`` → (1, ["plastica", "carta"]); ``"-"`` svuota."""
    if not argomenti:
        return None
    giorno = giorno_della_settimana(argomenti[0])
    if giorno is None:
        return None
    resto = " ".join(argomenti[1:]).strip()
    if resto in {"-", "niente", "nulla"}:
        return giorno, []
    voci = [v.strip().lower() for v in re.split(r"[,;]|\se\s", resto) if v.strip()]
    return (giorno, voci) if voci else None


def messaggio_calendario(calendario: dict[int, list[str]]) -> str:
    if not calendario:
        return USO
    righe = ["♻️ <b>Raccolta differenziata</b>", "<i>Ti avviso la sera prima, verso le 20.</i>", ""]
    for giorno in sorted(calendario):
        voci = ", ".join(con_emoji(escape(v)) for v in calendario[giorno])
        righe.append(f"<b>{GIORNI_SETTIMANA[giorno].capitalize()}</b>: {voci}")
    return "\n".join(righe)


def avviso_sera(rifiuti: Sequence[str]) -> str:
    return "🗑️ <b>Stasera porta fuori</b>: " + ", ".join(con_emoji(escape(r)) for r in rifiuti)


USO = (
    "♻️ Dimmi cosa passa e quando, un giorno alla volta:\n"
    "<code>/rifiuti lunedì organico</code>\n"
    "<code>/rifiuti martedì plastica, lattine</code>\n"
    "<code>/rifiuti giovedì carta</code>\n"
    "<code>/rifiuti martedì -</code> per togliere un giorno\n"
    "La sera prima, verso le 20, ti ricordo cosa portare fuori."
)
