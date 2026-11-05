"""Posizione mandata in automatico dal telefono (Comandi Rapidi su iPhone, HTTP Shortcuts o
Tasker su Android), senza aprire Telegram.

Con ``/telefono`` il bot crea una chiave personale: il telefono la mette nell'header
``Authorization`` quando chiama ``/api/posizione``. Nell'archivio si salva solo
l'impronta (hash) della chiave, mai la chiave in chiaro.
"""

from __future__ import annotations

import hashlib
import secrets
from html import escape

from azdora.archivio import Archivio


def _impronta(chiave: str) -> str:
    return hashlib.sha256(chiave.encode("utf-8")).hexdigest()


class CollegamentiTelefono:
    def __init__(self, archivio: Archivio) -> None:
        self._archivio = archivio

    def crea(self, chat_id: int) -> str:
        """Nuova chiave per la chat; quella vecchia smette subito di funzionare."""
        self.revoca(chat_id)
        chiave = secrets.token_urlsafe(24)
        self._archivio.scrivi(f"telefono-chiave:{_impronta(chiave)}", chat_id)
        self._archivio.scrivi(f"telefono:{chat_id}", _impronta(chiave))
        return chiave

    def revoca(self, chat_id: int) -> bool:
        impronta = self._archivio.leggi(f"telefono:{chat_id}")
        if not impronta:
            return False
        self._archivio.cancella(f"telefono-chiave:{impronta}")
        self._archivio.cancella(f"telefono:{chat_id}")
        return True

    def chat_di(self, chiave: str) -> int | None:
        if not chiave:
            return None
        valore = self._archivio.leggi(f"telefono-chiave:{_impronta(chiave)}")
        return int(valore) if valore is not None else None


def istruzioni(indirizzo: str, chiave: str) -> str:
    url = escape(f"{indirizzo.rstrip('/')}/api/posizione")
    righe = [
        "📱 <b>Posizione automatica dal telefono</b>",
        "",
        f"Indirizzo: <code>{url}</code>",
        f"Chiave: <code>{escape(chiave)}</code>",
        "⚠️ La chiave è personale: non condividerla. Con <code>/telefono off</code> la spegni.",
        "",
        "<b>iPhone – Comandi Rapidi</b>",
        "1. App <b>Comandi</b> → <b>Automazione</b> → <b>+</b> → ad esempio <i>Ora del giorno</i>"
        " (6:45, ogni giorno) oppure <i>Arrivo</i>/<i>Uscita</i> da un luogo →"
        " <b>Esegui immediatamente</b>.",
        "2. Aggiungi l'azione <b>Ottieni posizione attuale</b>.",
        "3. Aggiungi <b>Ottieni contenuto dell'URL</b>: incolla l'indirizzo, <b>Metodo</b> POST,"
        " <b>Intestazioni</b>: <code>Authorization</code> = <code>Bearer</code>, uno spazio e la"
        " chiave; <b>Corpo richiesta</b> JSON con <code>lat</code> = Latitudine e"
        " <code>lon</code> = Longitudine della posizione attuale.",
        "4. Fatto: ogni volta il bot aggiorna dove sei, in silenzio.",
        "",
        "<b>Android – app HTTP Shortcuts (gratis)</b>",
        "Nuova scorciatoia → POST all'indirizzo, header <code>Authorization: Bearer chiave</code>,"
        ' corpo JSON <code>{"lat": …, "lon": …}</code> con le variabili di posizione;'
        " poi programmala (es. ogni ora) o lanciala con Tasker.",
    ]
    return "\n".join(righe)


USO_SOLO_PRIVATO = "📱 Per sicurezza la chiave del telefono si crea solo nella chat privata con me."
SPENTO = "📱 Collegamento del telefono disattivato: la vecchia chiave non funziona più."
NESSUNO = "📱 Non c'era nessun telefono collegato."
