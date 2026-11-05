"""Il cervello del bot: riceve un update di Telegram e decide cosa rispondere.

Non sa nulla di HTTP né di Vercel: riceve dall'esterno il messaggero (chi spedisce i
messaggi), la fonte meteo e il servizio dei luoghi. Così nei test si sostituiscono con
dei finti e nel simulatore con la console.
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
from azdora.luoghi import CercaLuoghi, distanza_km, localita_qui
from azdora.meteo.openmeteo import ErroreMeteo, FonteMeteo, PrevisioniMare
from azdora.riepilogo import (
    DatiLocalita,
    riepilogo_domani,
    riepilogo_oggi,
    riepilogo_posizione,
)
from azdora.telegram import Messaggero

log = logging.getLogger(__name__)

# Comandi mostrati nel menu di Telegram (impostati con setMyCommands).
COMANDI: tuple[tuple[str, str], ...] = (
    ("oggi", "Riepilogo di oggi: meteo, vento, mare e consigli"),
    ("domani", "Com'è domani"),
    ("meteo", "Meteo adesso e prossimi giorni (es. /meteo cesenatico)"),
    ("vento", "Vento adesso e nelle prossime ore"),
    ("mare", "Onde e temperatura dell'acqua"),
    ("qui", "Meteo dove ti trovi adesso"),
    ("id", "Mostra l'id di questa chat"),
    ("aiuto", "Cosa so fare"),
)

TIPI_GRUPPO = frozenset({"group", "supergroup"})

# Oltre questa distanza il punto marino di Open-Meteo non descrive più "il mare lì davanti".
DISTANZA_MASSIMA_MARE_KM = 30


@dataclass(frozen=True)
class Chat:
    id: int
    tipo: str = "private"

    @property
    def e_un_gruppo(self) -> bool:
        return self.tipo in TIPI_GRUPPO


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
        luoghi: CercaLuoghi | None = None,
        orologio: Callable[[], datetime] = adesso_a_roma,
    ) -> None:
        self.impostazioni = impostazioni
        self.messaggero = messaggero
        self.meteo = meteo
        self.luoghi = luoghi
        self.orologio = orologio
        self._azioni: dict[str, Callable[[Chat, Comando], None]] = {
            "start": self._aiuto,
            "aiuto": self._aiuto,
            "help": self._aiuto,
            "oggi": self._oggi,
            "domani": self._domani,
            "meteo": self._meteo,
            "vento": self._vento,
            "mare": self._mare,
            "qui": self._qui,
            "id": self._id,
        }

    # --- Ingresso ---------------------------------------------------------------------

    def gestisci_update(self, update: dict[str, Any]) -> None:
        """Punto di ingresso per ogni update arrivato dal webhook.

        Gli aggiornamenti della posizione in tempo reale arrivano come ``edited_message``
        e per ora vengono ignorati: si risponde solo alla posizione iniziale.
        """
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
        dati_chat = messaggio.get("chat", {})
        if dati_chat.get("id") is None:
            return
        chat = Chat(dati_chat["id"], dati_chat.get("type", "private"))
        comando = leggi_comando(messaggio.get("text"))
        posizione = messaggio.get("location")
        if comando is None and posizione is None:
            return  # Testo libero: per ora l'Azdora risponde solo a comandi e posizioni.
        if comando is not None and self._non_per_me(comando):
            return

        if not self._e_autorizzata(chat.id):
            self._chat_sconosciuta(chat, messaggio, comando)
            return

        try:
            if posizione is not None:
                self._posizione(chat, posizione)
            elif comando is not None:
                azione = self._azioni.get(comando.nome)
                if azione is None:
                    self.messaggero.invia_messaggio(chat.id, messaggi.COMANDO_SCONOSCIUTO)
                else:
                    azione(chat, comando)
        except ErroreMeteo as errore:
            log.warning("Meteo non disponibile: %s", errore)
            self.messaggero.invia_messaggio(chat.id, messaggi.METEO_NON_DISPONIBILE)

    def _non_per_me(self, comando: Comando) -> bool:
        """Nei gruppi ``/meteo@altro_bot`` è rivolto a un altro bot."""
        mio = self.impostazioni.username_bot.lower()
        return bool(comando.destinatario and mio and comando.destinatario.lower() != mio)

    def _chat_sconosciuta(
        self, chat: Chat, messaggio: dict[str, Any], comando: Comando | None
    ) -> None:
        """Chat non autorizzata: solo /start e /id rispondono, per aiutare la configurazione."""
        mittente = messaggio.get("from", {}).get("id")
        if chat.e_un_gruppo and not self._e_il_padrone(mittente):
            log.warning("Messaggio da un gruppo non autorizzato (%s): esco", chat.id)
            self.messaggero.esci_da_chat(chat.id)
            return
        if comando is not None and comando.nome in {"start", "id"}:
            self.messaggero.invia_messaggio(chat.id, messaggi.messaggio_id(chat.id, False))

    # --- Località e mare --------------------------------------------------------------

    def _localita_richiesta(self, chat: Chat, comando: Comando) -> Localita | None:
        """Riccione/Rimini (anche abbreviate) o qualsiasi comune, cercato per nome."""
        testo = comando.testo_argomenti
        localita = trova_localita(testo)
        if localita is None and self.luoghi is not None:
            localita = self.luoghi.cerca(testo)
        if localita is None:
            self.messaggero.invia_messaggio(
                chat.id, messaggi.LOCALITA_SCONOSCIUTA.format(nome=escape(testo))
            )
        return localita

    def _mare_vicino(self, localita: Localita) -> PrevisioniMare | None:
        """Il mare davanti alla località, o ``None`` se è nell'entroterra o non disponibile."""
        try:
            mare = self.meteo.mare(localita)
        except ErroreMeteo as errore:
            # Senza il mare il messaggio resta utile: lo mandiamo lo stesso.
            log.warning("Mare non disponibile per %s: %s", localita.nome, errore)
            return None
        if not mare.ha_dati:
            return None
        if mare.punto is not None:
            distanza = distanza_km(localita.lat, localita.lon, *mare.punto)
            if distanza > DISTANZA_MASSIMA_MARE_KM:
                return None
        return mare

    # --- Comandi ----------------------------------------------------------------------

    def _aiuto(self, chat: Chat, comando: Comando) -> None:
        self.messaggero.invia_messaggio(chat.id, messaggi.AIUTO)

    def _id(self, chat: Chat, comando: Comando) -> None:
        self.messaggero.invia_messaggio(chat.id, messaggi.messaggio_id(chat.id, True))

    def _meteo(self, chat: Chat, comando: Comando) -> None:
        if localita := self._localita_richiesta(chat, comando):
            testo = messaggi.messaggio_meteo(self.meteo.previsioni(localita))
            self.messaggero.invia_messaggio(chat.id, testo)

    def _vento(self, chat: Chat, comando: Comando) -> None:
        if localita := self._localita_richiesta(chat, comando):
            testo = messaggi.messaggio_vento(self.meteo.previsioni(localita))
            self.messaggero.invia_messaggio(chat.id, testo)

    def _mare(self, chat: Chat, comando: Comando) -> None:
        if localita := self._localita_richiesta(chat, comando):
            mare = self._mare_vicino(localita)
            testo = (
                messaggi.messaggio_mare(mare)
                if mare is not None
                else messaggi.mare_lontano(localita.nome)
            )
            self.messaggero.invia_messaggio(chat.id, testo)

    def _qui(self, chat: Chat, comando: Comando) -> None:
        if chat.e_un_gruppo:
            # Il pulsante "request_location" Telegram lo permette solo nelle chat private.
            self.messaggero.invia_messaggio(chat.id, messaggi.QUI_GRUPPO)
        else:
            self.messaggero.invia_messaggio(
                chat.id, messaggi.QUI_PRIVATA, messaggi.TASTIERA_POSIZIONE
            )

    def _posizione(self, chat: Chat, posizione: dict[str, Any]) -> None:
        """Hai mandato la posizione: meteo, vento, mare (se vicino) e consigli per quel punto."""
        lat, lon = float(posizione["latitude"]), float(posizione["longitude"])
        nome = self.luoghi.nome_di(lat, lon) if self.luoghi is not None else None
        localita = localita_qui(lat, lon, nome or messaggi.NOME_POSIZIONE_SCONOSCIUTO)
        dati = DatiLocalita(self.meteo.previsioni(localita), self._mare_vicino(localita))
        testo = riepilogo_posizione(dati, self.orologio(), nome_da_osm=nome is not None)
        # Nelle chat private toglie il pulsante "Manda la mia posizione" dopo l'uso.
        tastiera = None if chat.e_un_gruppo else messaggi.TOGLI_TASTIERA
        self.messaggero.invia_messaggio(chat.id, testo, tastiera)

    def _oggi(self, chat: Chat, comando: Comando) -> None:
        self.messaggero.invia_messaggio(chat.id, self.testo_oggi(saluta=False))

    def _domani(self, chat: Chat, comando: Comando) -> None:
        domani = self.orologio().date() + timedelta(days=1)
        self.messaggero.invia_messaggio(chat.id, riepilogo_domani(self._dati_localita(), domani))

    # --- Riepiloghi -------------------------------------------------------------------

    def _dati_localita(self) -> list[DatiLocalita]:
        return [
            DatiLocalita(self.meteo.previsioni(localita), self._mare_vicino(localita))
            for localita in LOCALITA
        ]

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
