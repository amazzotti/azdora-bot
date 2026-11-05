"""Il cervello del bot: riceve un update di Telegram e decide cosa rispondere.

Non sa nulla di HTTP né di Vercel: riceve il messaggero (chi spedisce i messaggi) e la
fonte meteo dall'esterno. Così nei test si sostituiscono con dei finti e nel
simulatore con la console.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from typing import Any

from azdora import messaggi
from azdora.config import FUSO_ORARIO, LOCALITA, Impostazioni, Localita, trova_localita
from azdora.meteo.openmeteo import ErroreMeteo, FonteMeteo
from azdora.riepilogo import DatiLocalita, riepilogo_domani, riepilogo_oggi
from azdora.telegram import Messaggero

log = logging.getLogger(__name__)

# Comandi mostrati nel menu di Telegram (impostati con setMyCommands).
COMANDI: tuple[tuple[str, str], ...] = (
    ("oggi", "Riepilogo di oggi: meteo, vento, mare e consigli"),
    ("domani", "Com'è domani"),
    ("meteo", "Meteo adesso e prossimi giorni (es. /meteo rimini)"),
    ("vento", "Vento adesso e nelle prossime ore"),
    ("mare", "Onde e temperatura dell'acqua"),
    ("id", "Mostra l'id di questa chat"),
    ("aiuto", "Cosa so fare"),
)

TIPI_GRUPPO = frozenset({"group", "supergroup"})


@dataclass(frozen=True)
class Comando:
    nome: str
    argomenti: tuple[str, ...]
    destinatario: str | None = None

    @property
    def testo_argomenti(self) -> str:
        return " ".join(self.argomenti)


def leggi_comando(testo: str | None) -> Comando | None:
    """``"/vento@azdora_bot rimini"`` → ``Comando("vento", ("rimini",), "azdora_bot")``."""
    if not testo or not testo.startswith("/"):
        return None
    parti = testo.split()
    nome, _, destinatario = parti[0][1:].partition("@")
    if not nome:
        return None
    return Comando(nome.lower(), tuple(parti[1:]), destinatario or None)


def adesso_a_roma() -> datetime:
    return datetime.now(FUSO_ORARIO)


class Azdora:
    def __init__(
        self,
        impostazioni: Impostazioni,
        messaggero: Messaggero,
        meteo: FonteMeteo,
        orologio: Callable[[], datetime] = adesso_a_roma,
    ) -> None:
        self.impostazioni = impostazioni
        self.messaggero = messaggero
        self.meteo = meteo
        self.orologio = orologio
        self._azioni: dict[str, Callable[[int, Comando], None]] = {
            "start": self._aiuto,
            "aiuto": self._aiuto,
            "help": self._aiuto,
            "oggi": self._oggi,
            "domani": self._domani,
            "meteo": self._meteo,
            "vento": self._vento,
            "mare": self._mare,
            "id": self._id,
        }

    # --- Ingresso ---------------------------------------------------------------------

    def gestisci_update(self, update: dict[str, Any]) -> None:
        """Punto di ingresso per ogni update arrivato dal webhook."""
        if "my_chat_member" in update:
            self._aggiunto_a_chat(update["my_chat_member"])
        elif "message" in update:
            self._messaggio(update["message"])

    def _e_autorizzata(self, chat_id: int) -> bool:
        return chat_id in self.impostazioni.chat_autorizzate

    def _e_il_padrone(self, utente_id: int | None) -> bool:
        """Il padrone è chi possiede la chat privata autorizzata (in privato chat id = user id)."""
        padrone = self.impostazioni.chat_personale
        return padrone is not None and utente_id == padrone

    def _aggiunto_a_chat(self, evento: dict[str, Any]) -> None:
        chat = evento.get("chat", {})
        chat_id = chat.get("id")
        nuovo_stato = evento.get("new_chat_member", {}).get("status")
        if chat.get("type") not in TIPI_GRUPPO or nuovo_stato not in {"member", "administrator"}:
            return
        if self._e_autorizzata(chat_id):
            return
        if self._e_il_padrone(evento.get("from", {}).get("id")):
            # L'ha aggiunto Alessandro: resto, così può leggere l'id e autorizzare il gruppo.
            self.messaggero.invia_messaggio(chat_id, messaggi.messaggio_id(chat_id, False))
        else:
            log.warning("Aggiunto a un gruppo non autorizzato (%s): esco", chat_id)
            self.messaggero.esci_da_chat(chat_id)

    def _messaggio(self, messaggio: dict[str, Any]) -> None:
        chat = messaggio.get("chat", {})
        chat_id = chat.get("id")
        if chat_id is None:
            return
        comando = leggi_comando(messaggio.get("text"))
        if comando is None:
            return  # Per ora l'Azdora risponde solo ai comandi.
        if self._non_per_me(comando):
            return

        if not self._e_autorizzata(chat_id):
            self._chat_sconosciuta(chat_id, chat.get("type"), messaggio, comando)
            return

        azione = self._azioni.get(comando.nome)
        if azione is None:
            self.messaggero.invia_messaggio(chat_id, messaggi.COMANDO_SCONOSCIUTO)
            return
        try:
            azione(chat_id, comando)
        except ErroreMeteo as errore:
            log.warning("Meteo non disponibile: %s", errore)
            self.messaggero.invia_messaggio(chat_id, messaggi.METEO_NON_DISPONIBILE)

    def _non_per_me(self, comando: Comando) -> bool:
        """Nei gruppi ``/meteo@altro_bot`` è rivolto a un altro bot."""
        mio = self.impostazioni.username_bot.lower()
        return bool(comando.destinatario and mio and comando.destinatario.lower() != mio)

    def _chat_sconosciuta(
        self, chat_id: int, tipo: str | None, messaggio: dict[str, Any], comando: Comando
    ) -> None:
        """Chat non autorizzata: solo /start e /id rispondono, per aiutare la configurazione."""
        mittente = messaggio.get("from", {}).get("id")
        if tipo in TIPI_GRUPPO and not self._e_il_padrone(mittente):
            log.warning("Messaggio da un gruppo non autorizzato (%s): esco", chat_id)
            self.messaggero.esci_da_chat(chat_id)
            return
        if comando.nome in {"start", "id"}:
            self.messaggero.invia_messaggio(chat_id, messaggi.messaggio_id(chat_id, False))

    # --- Comandi ----------------------------------------------------------------------

    def _localita_richiesta(self, chat_id: int, comando: Comando) -> Localita | None:
        localita = trova_localita(comando.testo_argomenti)
        if localita is None:
            elenco = ", ".join(loc.nome for loc in LOCALITA)
            self.messaggero.invia_messaggio(
                chat_id,
                messaggi.LOCALITA_SCONOSCIUTA.format(
                    nome=escape(comando.testo_argomenti), elenco=elenco
                ),
            )
        return localita

    def _aiuto(self, chat_id: int, comando: Comando) -> None:
        self.messaggero.invia_messaggio(chat_id, messaggi.AIUTO)

    def _id(self, chat_id: int, comando: Comando) -> None:
        self.messaggero.invia_messaggio(chat_id, messaggi.messaggio_id(chat_id, True))

    def _meteo(self, chat_id: int, comando: Comando) -> None:
        if localita := self._localita_richiesta(chat_id, comando):
            testo = messaggi.messaggio_meteo(self.meteo.previsioni(localita))
            self.messaggero.invia_messaggio(chat_id, testo)

    def _vento(self, chat_id: int, comando: Comando) -> None:
        if localita := self._localita_richiesta(chat_id, comando):
            testo = messaggi.messaggio_vento(self.meteo.previsioni(localita))
            self.messaggero.invia_messaggio(chat_id, testo)

    def _mare(self, chat_id: int, comando: Comando) -> None:
        if localita := self._localita_richiesta(chat_id, comando):
            self.messaggero.invia_messaggio(
                chat_id, messaggi.messaggio_mare(self.meteo.mare(localita))
            )

    def _oggi(self, chat_id: int, comando: Comando) -> None:
        self.messaggero.invia_messaggio(chat_id, self.testo_oggi(saluta=False))

    def _domani(self, chat_id: int, comando: Comando) -> None:
        domani = self.orologio().date() + timedelta(days=1)
        self.messaggero.invia_messaggio(chat_id, riepilogo_domani(self._dati_localita(), domani))

    # --- Riepiloghi -------------------------------------------------------------------

    def _dati_localita(self) -> list[DatiLocalita]:
        dati = []
        for localita in LOCALITA:
            previsioni = self.meteo.previsioni(localita)
            try:
                mare = self.meteo.mare(localita)
            except ErroreMeteo as errore:
                # Senza il mare il riepilogo resta utile: lo mandiamo lo stesso.
                log.warning("Mare non disponibile per %s: %s", localita.nome, errore)
                mare = None
            dati.append(DatiLocalita(previsioni, mare))
        return dati

    def testo_oggi(self, saluta: bool) -> str:
        nome = self.impostazioni.nome_utente if saluta else ""
        return riepilogo_oggi(self._dati_localita(), self.orologio(), nome)

    def invia_riepilogo_mattutino(self) -> None:
        """Il messaggio delle 7, spedito alla chat personale."""
        chat_id = self.impostazioni.chat_personale
        if chat_id is None:
            raise RuntimeError("Manca TELEGRAM_CHAT_ID: non so a chi mandare il riepilogo")
        try:
            testo = self.testo_oggi(saluta=True)
        except ErroreMeteo as errore:
            log.error("Riepilogo mattutino senza meteo: %s", errore)
            testo = messaggi.METEO_NON_DISPONIBILE
        self.messaggero.invia_messaggio(chat_id, testo)
