"""Quando l'Azdora ti scrive: riepilogo del mattino, della sera, riassunti delle notizie e
ore di silenzio. Ognuno se li sceglie con ``/orari``.

La "sveglia" (cron-job.org) passa ogni 15 minuti: a ogni giro il bot guarda gli orari di
ognuno e manda quello che è arrivato il momento di mandare, una volta sola.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from typing import Any

from azdora.archivio import Archivio

# Se il bot è rimasto fermo (es. sveglia saltata), recupera entro questo tempo, non oltre:
# meglio niente riepilogo del mattino che un "Buongiorno" alle tre del pomeriggio.
FINESTRA = timedelta(hours=3)
FINESTRA_NOTIZIE = timedelta(hours=1)
# Fino alla versione 1 il riassunto delle notizie era alle 12:30 e alle 19: chi non l'ha
# mai cambiato passa da solo a "ogni ora", chi l'ha scelto a mano lo tiene.
VERSIONE = 2
_VECCHIE_NOTIZIE = ["12:30", "19:00"]


@dataclass(frozen=True)
class Orari:
    mattino: time | None = time(7, 0)
    sera: time = time(20, 0)
    notizie: tuple[time, ...] = tuple(time(h) for h in range(8, 23))  # ogni ora, 8–22
    notizie_subito: bool = False
    silenzio_da: time | None = time(23, 0)
    silenzio_a: time | None = time(7, 0)
    avvisi_meteo: bool = True

    def in_dizionario(self) -> dict[str, Any]:
        def hhmm(t: time | None) -> str | None:
            return None if t is None else t.strftime("%H:%M")

        return {
            "mattino": hhmm(self.mattino),
            "sera": hhmm(self.sera),
            "notizie": [hhmm(t) for t in self.notizie],
            "notizie_subito": self.notizie_subito,
            "silenzio_da": hhmm(self.silenzio_da),
            "silenzio_a": hhmm(self.silenzio_a),
            "avvisi_meteo": self.avvisi_meteo,
            "versione": VERSIONE,
        }

    @classmethod
    def da_dizionario(cls, dati: dict[str, Any]) -> Orari:
        def ora(valore: str | None) -> time | None:
            return None if not valore else time.fromisoformat(valore)

        base = cls()
        notizie = dati.get("notizie")
        if notizie is None or (dati.get("versione", 1) < 2 and notizie == _VECCHIE_NOTIZIE):
            orari_notizie = base.notizie
        else:
            orari_notizie = tuple(t for v in notizie if (t := ora(v)))
        return cls(
            mattino=ora(dati["mattino"]) if "mattino" in dati else base.mattino,
            sera=ora(dati.get("sera")) or base.sera,
            notizie=orari_notizie,
            notizie_subito=bool(dati.get("notizie_subito", False)),
            silenzio_da=ora(dati.get("silenzio_da")),
            silenzio_a=ora(dati.get("silenzio_a")),
            avvisi_meteo=bool(dati.get("avvisi_meteo", True)),
        )


def in_orario(adesso: datetime, orario: time, finestra: timedelta = FINESTRA) -> bool:
    """È arrivato il momento (e non è passato troppo tempo)?"""
    inizio = datetime.combine(adesso.date(), orario, adesso.tzinfo)
    return inizio <= adesso < inizio + finestra


def in_silenzio(orari: Orari, adesso: datetime) -> bool:
    """Siamo nelle ore di silenzio? Funziona anche a cavallo della mezzanotte (23–7)."""
    if orari.silenzio_da is None or orari.silenzio_a is None:
        return False
    ora = adesso.time().replace(tzinfo=None)
    da, a = orari.silenzio_da, orari.silenzio_a
    if da <= a:
        return da <= ora < a
    return ora >= da or ora < a


class PreferenzeOrari:
    def __init__(self, archivio: Archivio) -> None:
        self._archivio = archivio

    def di(self, chat_id: int) -> Orari:
        dati = self._archivio.leggi(f"orari:{chat_id}")
        return Orari.da_dizionario(dati) if dati else Orari()

    def salva(self, chat_id: int, orari: Orari) -> None:
        self._archivio.scrivi(f"orari:{chat_id}", orari.in_dizionario())


class ErroreOrari(ValueError):
    pass


_ORA = re.compile(r"^(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))?$")
_SI = {"on", "si", "sì", "attiva", "attivi"}
_NO = {"off", "no", "spegni", "spento", "spenti", "nessuno", "mai"}


def leggi_ora(testo: str) -> time:
    corrispondenza = _ORA.match(testo.strip().lower().removesuffix("h"))
    if corrispondenza is None:
        raise ErroreOrari(f"«{testo}» non è un orario: scrivi ad esempio 6:30 o 21")
    ore, minuti = int(corrispondenza["h"]), int(corrispondenza["m"] or 0)
    if ore > 23 or minuti > 59:
        raise ErroreOrari(f"«{testo}» non è un orario valido")
    return time(ore, minuti)


def modifica(orari: Orari, argomenti: Sequence[str]) -> Orari:
    """Applica ``/orari mattino 6:30``, ``/orari notizie 8 13 19``, ``/orari silenzio 23-7``…"""
    if not argomenti:
        raise ErroreOrari("Dimmi cosa cambiare")
    voce = argomenti[0].lower()
    valori = [v for a in argomenti[1:] for v in re.split(r"[,\s]+", a) if v]
    testo = " ".join(valori).lower()
    if voce in {"mattino", "mattina", "buongiorno"}:
        return replace(orari, mattino=None if testo in _NO else leggi_ora(testo))
    if voce in {"sera", "serale"}:
        return replace(orari, sera=leggi_ora(testo))
    if voce == "notizie":
        if testo == "subito":
            return replace(orari, notizie_subito=True)
        if testo in {"ogni ora", "orario", "ore"}:
            return replace(orari, notizie=Orari().notizie, notizie_subito=False)
        if testo in _NO:
            return replace(orari, notizie=(), notizie_subito=False)
        if not valori:
            raise ErroreOrari("Dimmi a che ore: /orari notizie 8 13 19")
        return replace(
            orari, notizie=tuple(sorted({leggi_ora(v) for v in valori})), notizie_subito=False
        )
    if voce in {"silenzio", "notte", "nondisturbare"}:
        if testo in _NO:
            return replace(orari, silenzio_da=None, silenzio_a=None)
        parti = re.split(r"\s*(?:-|–|a|alle)\s*", testo)
        if len(parti) != 2:
            raise ErroreOrari("Scrivi le ore di silenzio così: /orari silenzio 23-7")
        return replace(orari, silenzio_da=leggi_ora(parti[0]), silenzio_a=leggi_ora(parti[1]))
    if voce in {"avvisi", "pioggia", "meteo"}:
        if testo in _SI:
            return replace(orari, avvisi_meteo=True)
        if testo in _NO:
            return replace(orari, avvisi_meteo=False)
        raise ErroreOrari("Scrivi /orari avvisi on oppure /orari avvisi off")
    raise ErroreOrari(f"Non conosco «{voce}»")


def _hhmm(t: time | None) -> str:
    return "spento" if t is None else t.strftime("%H:%M")


def messaggio(orari: Orari, per_gruppo: bool = False) -> str:
    righe = ["🕰️ <b>I tuoi orari</b>", ""]
    if not per_gruppo:
        righe.append(f"☀️ Riepilogo del mattino: <b>{_hhmm(orari.mattino)}</b>")
    righe.append(f"🌙 Sera (rifiuti e, se attivo, /sera): <b>{_hhmm(orari.sera)}</b>")
    if not per_gruppo:
        if orari.notizie_subito:
            notizie = "<b>subito</b>, appena escono (solo il tuo comune)"
        elif orari.notizie == Orari().notizie:
            notizie = "riassunto <b>ogni ora, dalle 8 alle 22</b>"
        elif orari.notizie:
            notizie = "riassunto alle <b>" + ", ".join(_hhmm(t) for t in orari.notizie) + "</b>"
        else:
            notizie = "<b>spente</b> (le trovi sempre con /notizie)"
        righe.append(f"🗞️ Notizie: {notizie}")
        silenzio = (
            f"<b>{_hhmm(orari.silenzio_da)}–{_hhmm(orari.silenzio_a)}</b>"
            if orari.silenzio_da
            else "<b>mai</b>"
        )
        righe.append(f"🤫 Silenzio: {silenzio} (i promemoria arrivano lo stesso)")
        righe.append(
            "☔ Avvisi pioggia, temporali, raffiche: "
            + ("<b>attivi</b>" if orari.avvisi_meteo else "<b>spenti</b>")
        )
    righe += ["", USO_GRUPPO if per_gruppo else USO]
    return "\n".join(righe)


USO = (
    "Per cambiarli:\n"
    "<code>/orari mattino 6:30</code> · <code>/orari mattino off</code>\n"
    "<code>/orari sera 21</code>\n"
    "<code>/orari notizie ogni ora</code> · <code>/orari notizie 8 13 19</code> · "
    "<code>/orari notizie subito</code> · "
    "<code>/orari notizie off</code>\n"
    "<code>/orari silenzio 23-7</code> · <code>/orari silenzio off</code>\n"
    "<code>/orari avvisi off</code>"
)
USO_GRUPPO = "Per cambiarlo: <code>/orari sera 21</code>"
