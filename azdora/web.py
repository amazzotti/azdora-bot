"""Logica degli endpoint HTTP, separata da Vercel per poterla testare.

- ``/api/webhook``: Telegram ci manda ogni messaggio ricevuto dal bot.
- ``/api/cron``: cron-job.org ci chiama agli orari stabiliti (es. le 7 per il riepilogo).

Entrambi controllano un segreto prima di fare qualsiasi cosa.
"""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from azdora.archivio import ErroreArchivio
from azdora.bot import Azdora
from azdora.config import Impostazioni

log = logging.getLogger(__name__)

INTESTAZIONE_SEGRETO_TELEGRAM = "x-telegram-bot-api-secret-token"


@dataclass(frozen=True)
class Risposta:
    stato: int
    corpo: dict[str, Any] = field(default_factory=dict)

    def json(self) -> bytes:
        return json.dumps(self.corpo, ensure_ascii=False).encode("utf-8")


def _minuscole(intestazioni: Mapping[str, str] | Any) -> dict[str, str]:
    return {chiave.lower(): valore for chiave, valore in intestazioni.items()}


def _segreto_valido(atteso: str, ricevuto: str | None) -> bool:
    """Confronto a tempo costante; un segreto non configurato non è mai valido."""
    return bool(atteso) and ricevuto is not None and hmac.compare_digest(atteso, ricevuto)


def gia_ricevuto(bot: Azdora, update_id: Any) -> bool:
    """Questo update di Telegram è già arrivato una volta?"""
    if not isinstance(update_id, int) or not bot.archivio.persistente:
        return False
    try:
        (nuovo,) = bot.archivio.segna_nuovi([f"update:{update_id}"], 24 * 3600)
    except ErroreArchivio:
        return False
    return not nuovo


def gestisci_webhook(
    intestazioni: Mapping[str, str] | Any,
    corpo: bytes,
    impostazioni: Impostazioni,
    crea_bot: Callable[[], Azdora],
) -> Risposta:
    ricevuto = _minuscole(intestazioni).get(INTESTAZIONE_SEGRETO_TELEGRAM)
    if not _segreto_valido(impostazioni.segreto_webhook, ricevuto):
        return Risposta(401, {"ok": False, "errore": "segreto non valido"})
    try:
        update = json.loads(corpo or b"{}")
    except ValueError:
        return Risposta(400, {"ok": False, "errore": "JSON non valido"})
    if not isinstance(update, dict):
        return Risposta(400, {"ok": False, "errore": "update non valido"})

    try:
        bot = crea_bot()
        if gia_ricevuto(bot, update.get("update_id")):
            # Telegram lo rimanda perché la volta prima ci abbiamo messo troppo: lo saltiamo,
            # altrimenti resterebbe in coda all'infinito e bloccherebbe tutti i messaggi dopo.
            log.warning("Update %s già ricevuto: saltato", update.get("update_id"))
            return Risposta(200, {"ok": True, "gia_ricevuto": True})
        bot.gestisci_update(update)
    except Exception:
        # Rispondiamo comunque 200: con un errore Telegram rimanderebbe lo stesso update
        # all'infinito. L'errore resta nei log di Vercel.
        log.exception("Errore nella gestione dell'update %s", update.get("update_id"))
    return Risposta(200, {"ok": True})


LAVORI: dict[str, Callable[[Azdora], object]] = {
    # Rispetta l'orario scelto da ognuno con /orari (di default le 7).
    "mattino": lambda bot: bot.controlla_mattino(anche_senza_memoria=True),
    # Subito e a tutti: per provare (workflow "Prova riepilogo").
    "mattino-subito": Azdora.invia_riepilogo_mattutino,
    "controlli": Azdora.controlli,  # promemoria, allerte, notizie, salute… ogni 15 minuti
    "notizie": Azdora.controlla_notizie,
    "allerte": Azdora.controlla_allerte,
}


def gestisci_cron(
    intestazioni: Mapping[str, str] | Any,
    parametri: Mapping[str, str],
    impostazioni: Impostazioni,
    crea_bot: Callable[[], Azdora],
) -> Risposta:
    autorizzazione = _minuscole(intestazioni).get("authorization", "")
    prefisso = "Bearer "
    ricevuto = autorizzazione[len(prefisso) :] if autorizzazione.startswith(prefisso) else None
    if not _segreto_valido(impostazioni.segreto_cron, ricevuto):
        return Risposta(401, {"ok": False, "errore": "non autorizzato"})

    nome = parametri.get("job", "")
    lavoro = LAVORI.get(nome)
    if lavoro is None:
        return Risposta(404, {"ok": False, "errore": f"lavoro sconosciuto: {nome!r}"})
    try:
        lavoro(crea_bot())
    except Exception:
        log.exception("Errore nel lavoro %s", nome)
        return Risposta(500, {"ok": False, "job": nome})
    return Risposta(200, {"ok": True, "job": nome})


def gestisci_posizione(
    intestazioni: Mapping[str, str] | Any,
    corpo: bytes,
    crea_bot: Callable[[], Azdora],
) -> Risposta:
    """``POST /api/posizione``: il telefono manda {"lat": …, "lon": …} con la sua chiave."""
    from azdora.telefono import CollegamentiTelefono

    autorizzazione = _minuscole(intestazioni).get("authorization", "")
    if not autorizzazione.startswith("Bearer "):
        return Risposta(401, {"ok": False, "errore": "manca la chiave (Authorization: Bearer …)"})
    chiave = autorizzazione.removeprefix("Bearer ").strip()
    if not chiave:
        return Risposta(401, {"ok": False, "errore": "manca la chiave (Authorization: Bearer …)"})
    try:
        dati = json.loads(corpo or b"{}")
        lat = float(dati.get("lat", dati.get("latitude")))
        lon = float(dati.get("lon", dati.get("longitude")))
    except (ValueError, TypeError, AttributeError):
        return Risposta(
            400, {"ok": False, "errore": 'serve un JSON come {"lat": 44.0, "lon": 12.6}'}
        )
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return Risposta(400, {"ok": False, "errore": "coordinate fuori scala"})
    bot = crea_bot()
    chat_id = CollegamentiTelefono(bot.archivio).chat_di(chiave)
    if chat_id is None or chat_id not in bot.impostazioni.chat_autorizzate:
        return Risposta(401, {"ok": False, "errore": "chiave non valida"})
    try:
        posizione = bot.salva_posizione(chat_id, lat, lon)
    except Exception:
        log.exception("Posizione dal telefono non salvata")
        return Risposta(500, {"ok": False})
    return Risposta(200, {"ok": True, "luogo": posizione.etichetta})
