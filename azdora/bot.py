"""Il cervello del bot: riceve un update di Telegram e decide cosa rispondere.

Non sa nulla di HTTP né di Vercel: riceve dall'esterno il messaggero (chi spedisce i
messaggi), la fonte meteo, i luoghi, l'archivio, le notizie e le allerte. Così nei test
si sostituiscono con dei finti e nel simulatore con la console.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from html import escape
from typing import Any

import httpx

from azdora import allerte as modulo_allerte
from azdora import budget as modulo_budget
from azdora import carburante as modulo_carburante
from azdora import conti as modulo_conti
from azdora import costa as modulo_costa
from azdora import feste as modulo_feste
from azdora import lista as modulo_lista
from azdora import messaggi
from azdora import notizie as modulo_notizie
from azdora import orari as modulo_orari
from azdora import promemoria as modulo_promemoria
from azdora import rifiuti as modulo_rifiuti
from azdora import scadenze as modulo_scadenze
from azdora import spese as modulo_spese
from azdora import telefono as modulo_telefono
from azdora import weekend as modulo_weekend
from azdora.allerte import FonteAllerte
from azdora.archivio import Archivio, ArchivioMemoria, ErroreArchivio
from azdora.budget import Budget, leggi_budget
from azdora.carburante import CARBURANTI, FonteCarburante
from azdora.config import (
    FUSO_ORARIO,
    LOCALITA,
    RICCIONE,
    Impostazioni,
    Localita,
    trova_localita,
)
from azdora.conti import MembriGruppo, Pareggio, spese_da
from azdora.linguaggio import leggi_spesa_libera
from azdora.lista import Lista, leggi_voci
from azdora.luoghi import CercaLuoghi, Luogo, distanza_km, localita_qui
from azdora.meteo.aria import FonteAria, avvisi_aria, messaggio_aria
from azdora.meteo.imminente import FonteImminente, valuta
from azdora.meteo.openmeteo import ErroreMeteo, FonteMeteo, PrevisioniMare
from azdora.notizie import FonteNotizie, Notizia
from azdora.orari import (
    FINESTRA,
    ErroreOrari,
    Orari,
    PreferenzeOrari,
    in_orario,
    in_silenzio,
)
from azdora.posizione import (
    SPOSTAMENTO_MINIMO_KM,
    VALIDITA_ZONA,
    ZONA_CASA,
    MemoriaPosizione,
    Posizione,
    Zona,
)
from azdora.promemoria import (
    ErrorePromemoria,
    RegistroPromemoria,
    e_un_promemoria,
    leggi_ricorrente,
)
from azdora.riepilogo import (
    DatiLocalita,
    riepilogo_domani,
    riepilogo_oggi,
    riepilogo_posizione,
)
from azdora.rifiuti import Calendario, leggi_rifiuti
from azdora.scadenze import RegistroScadenze, leggi_scadenza
from azdora.soldi import aggiungi_mesi, euro, leggi_mese
from azdora.spese import RegistroSpese, leggi_spesa
from azdora.telefono import CollegamentiTelefono
from azdora.telegram import ErroreTelegram, Messaggero

log = logging.getLogger(__name__)

# Comandi mostrati nel menu di Telegram (impostati con setMyCommands).
COMANDI: tuple[tuple[str, str], ...] = (
    ("oggi", "Riepilogo di oggi: meteo, vento, mare e consigli"),
    ("domani", "Com'è domani"),
    ("meteo", "Meteo adesso e prossimi giorni (es. /meteo cesenatico)"),
    ("vento", "Vento adesso e nelle prossime ore"),
    ("mare", "Onde e temperatura dell'acqua"),
    ("qui", "Meteo dove ti trovi adesso"),
    ("notizie", "Ultime notizie della tua zona"),
    ("allerte", "Allerte meteo della tua zona"),
    ("dove", "Dove credo che tu sia"),
    ("scadenze", "Le tue scadenze (bollo, assicurazione…)"),
    ("scadenza", "Segna una scadenza: /scadenza 31/01 bollo 180€ annuale"),
    ("spesa", "Segna una spesa: /spesa 45 benzina"),
    ("mese", "Riepilogo delle spese del mese"),
    ("spese", "Ultime spese segnate"),
    ("annulla", "Cancella l'ultima spesa segnata"),
    ("esporta", "Scarica le spese dell'anno in CSV"),
    ("lista", "La lista condivisa delle cose da fare"),
    ("aggiungi", "Aggiungi alla lista: /aggiungi latte, pane"),
    ("weekend", "Meteo del weekend, gite e sagre"),
    ("sagre", "Sagre, fiere e feste da Cervia a Pesaro"),
    ("costa", "Tempo, vento e mare da Cervia a Pesaro"),
    ("italia", "Le principali notizie dall'Italia"),
    ("mondo", "Le principali notizie dal mondo"),
    ("orari", "Quando ti scrivo: mattino, sera, notizie, silenzio"),
    ("telefono", "Posizione automatica dal telefono"),
    ("stato", "Come sto: versione e salute del bot"),
    ("budget", "Budget mensile: /budget 800 o /budget auto 150"),
    ("conti", "Nel gruppo: chi deve quanto a chi"),
    ("pareggio", "Nel gruppo: siamo pari, si riparte da zero"),
    ("promemoria", "I tuoi promemoria"),
    ("ricordami", "Es. /ricordami domani alle 18 di chiamare"),
    ("rifiuti", "Raccolta differenziata, avviso la sera prima"),
    ("sera", "Riepilogo della sera alle 20: attiva o spegni"),
    ("aria", "UV, qualità dell'aria e pollini"),
    ("benzina", "Distributori più economici: /benzina gasolio"),
    ("dimentica", "Cancella la posizione che ricordo"),
    ("id", "Mostra l'id di questa chat"),
    ("aiuto", "Cosa so fare"),
)

TIPI_GRUPPO = frozenset({"group", "supergroup"})

# Oltre questa distanza il punto marino di Open-Meteo non descrive più "il mare lì davanti".
DISTANZA_MASSIMA_MARE_KM = 30
# Se sei più lontano di così da casa (Riccione e Rimini), il riepilogo aggiunge dove sei.
DISTANZA_DA_CASA_KM = 15

# Notizie: quante al massimo per messaggio e per quanto ricordarle come già mandate.
MASSIMO_NOTIZIE = 5
MASSIMO_NOTIZIE_ZONA_NUOVA = 3
NOTIZIE_A_RICHIESTA = 10  # con /notizie se ne vedono di più che negli avvisi automatici
RICORDO_NOTIZIE_S = 3 * 24 * 3600
RICORDO_ZONA_S = 30 * 24 * 3600
NOTIZIE_NEL_MATTINO = 3
NOTIZIE_NEL_RIASSUNTO = 10
# Italia e mondo: quante in coda a /notizie, ai riassunti e al riepilogo del mattino.
PRINCIPALI_IN_CODA = 3
PRINCIPALI_AL_MATTINO = 2
PRINCIPALI_A_RICHIESTA = 8
TEMPO_MASSIMO_CONTROLLI_S = 45
ORA_SCADENZE = dt_time(8, 0)
VERIFICA_MENU_S = 6 * 3600  # ogni quanto ricontrollare il menu dei comandi
ERRORE_RECENTE_S = 3600  # errori di Telegram dell'ultima ora: c'è da riparare
PAUSA_AVVISI_S = 4 * 3600  # lo stesso avviso meteo non prima di 4 ore
# Come li chiamano tutti in Romagna → il nome vero, per cercarli.
SOPRANNOMI = {
    "mima": "Milano Marittima",
    "mi.ma": "Milano Marittima",
    "pmm": "Milano Marittima",
    "rimini nord": "Viserba",
    "sammarino": "San Marino",
}


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
    mittente: str = ""  # nome di chi scrive, utile nel gruppo
    mittente_id: int | None = None

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
        *,
        archivio: Archivio | None = None,
        notizie: FonteNotizie | None = None,
        allerte: FonteAllerte | None = None,
        aria: FonteAria | None = None,
        carburante: FonteCarburante | None = None,
        imminente: FonteImminente | None = None,
        orologio: Callable[[], datetime] = adesso_a_roma,
    ) -> None:
        self.impostazioni = impostazioni
        self.messaggero = messaggero
        self.meteo = meteo
        self.luoghi = luoghi
        self.archivio: Archivio = archivio if archivio is not None else ArchivioMemoria()
        self.posizioni = MemoriaPosizione(self.archivio)
        self.notizie = notizie
        self.allerte = allerte
        self.aria = aria
        self.carburante = carburante
        self.imminente = imminente
        self.orari = PreferenzeOrari(self.archivio)
        self.orologio = orologio
        # Previsioni già scaricate in questa richiesta: con più persone non si riscarica.
        self._cache_previsioni: dict[tuple[float, float], Any] = {}
        self._cache_mare: dict[tuple[float, float], PrevisioniMare | None] = {}
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
            "notizie": self._notizie,
            "allerte": self._allerte,
            "dove": self._dove,
            "dimentica": self._dimentica,
            "id": self._id,
            "scadenza": self._scadenza,
            "scadenze": self._scadenze,
            "spesa": self._spesa,
            "spese": self._spese,
            "mese": self._mese,
            "annulla": self._annulla,
            "esporta": self._esporta,
            "lista": self._lista,
            "aggiungi": self._aggiungi,
            "svuota": self._svuota,
            "weekend": self._weekend,
            "sagre": self._sagre,
            "costa": self._costa,
            "italia": self._italia,
            "mondo": self._mondo,
            "orari": self._orari,
            "telefono": self._telefono,
            "stato": self._stato,
            "budget": self._budget,
            "conti": self._conti,
            "pareggio": self._pareggio,
            "promemoria": self._promemoria,
            "ricordami": self._ricordami,
            "rifiuti": self._rifiuti,
            "sera": self._sera,
            "aria": self._aria,
            "benzina": self._benzina,
        }

    # --- Ingresso ---------------------------------------------------------------------

    def gestisci_update(self, update: dict[str, Any]) -> None:
        """Punto di ingresso per ogni update arrivato dal webhook."""
        if "my_chat_member" in update:
            self._aggiunto_a_chat(update["my_chat_member"])
        elif "message" in update:
            self._messaggio(update["message"])
        elif "callback_query" in update:
            self._pulsante(update["callback_query"])
        elif "edited_message" in update:
            # La posizione in tempo reale arriva come messaggio modificato, più volte.
            self._posizione_aggiornata(update["edited_message"])

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
        testo = (messaggio.get("text") or "").strip()
        da = messaggio.get("from") or {}
        mittente, mittente_id = da.get("first_name", ""), da.get("id")
        comando = leggi_comando(testo)
        if comando is not None:
            comando = Comando(
                comando.nome, comando.argomenti, comando.destinatario, mittente, mittente_id
            )
        posizione = messaggio.get("location")
        if comando is None and posizione is None and not testo:
            return
        if comando is not None and self._non_per_me(comando):
            return

        if not self._e_autorizzata(chat.id):
            self._chat_sconosciuta(chat, messaggio, comando)
            return
        if not chat.e_un_gruppo:
            self._ricorda_nome(chat.id, mittente)
        elif mittente_id is not None:
            self._ricorda_membro(chat.id, mittente_id, mittente)

        try:
            if posizione is not None:
                self._posizione(chat, posizione)
            elif comando is not None:
                azione = self._azioni.get(comando.nome)
                if azione is None:
                    self.messaggero.invia_messaggio(chat.id, messaggi.COMANDO_SCONOSCIUTO)
                else:
                    azione(chat, comando)
            else:
                self._testo_libero(chat, testo, mittente, mittente_id)
        except ErroreMeteo as errore:
            log.warning("Meteo non disponibile: %s", errore)
            self.messaggero.invia_messaggio(chat.id, messaggi.METEO_NON_DISPONIBILE)
        except ErroreArchivio as errore:
            log.warning("Archivio non disponibile: %s", errore)
            self.messaggero.invia_messaggio(chat.id, messaggi.ARCHIVIO_NON_DISPONIBILE)
        except ErroreTelegram:
            raise
        except Exception:
            # Meglio una risposta di scuse che il silenzio.
            log.exception("Errore inatteso su %r", testo[:40])
            self.messaggero.invia_messaggio(chat.id, messaggi.ERRORE_INATTESO)

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

    def _zona_della_chat(self, chat: Chat) -> Zona:
        """Dove si trova chi scrive: la posizione della chat, poi quella del padrone, poi casa."""
        try:
            zona = self.posizioni.zona(chat.id, self.orologio())
            if not zona.da_posizione and chat.e_un_gruppo:
                zona = self.zona()
        except ErroreArchivio as errore:
            log.warning("Zona non disponibile, uso casa: %s", errore)
            return ZONA_CASA
        return zona

    def _localita_richiesta(self, chat: Chat, comando: Comando) -> Localita | None:
        """Il posto scritto (Riccione, "rn", qualsiasi comune) o, se manca, dove sei adesso."""
        testo = SOPRANNOMI.get(comando.testo_argomenti.strip().lower(), comando.testo_argomenti)
        if not testo.strip():
            zona = self._zona_della_chat(chat)
            if zona.da_posizione:
                return localita_qui(zona.lat, zona.lon, zona.etichetta)
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
        chiave = (localita.mare_lat, localita.mare_lon)
        if chiave not in self._cache_mare:
            self._cache_mare[chiave] = self._mare_da_scaricare(localita)
        return self._cache_mare[chiave]

    def _mare_da_scaricare(self, localita: Localita) -> PrevisioniMare | None:
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

    # --- Posizione --------------------------------------------------------------------

    def _luogo(self, lat: float, lon: float) -> Luogo | None:
        return self.luoghi.luogo_di(lat, lon) if self.luoghi is not None else None

    def _posizione(self, chat: Chat, posizione: dict[str, Any]) -> None:
        """Hai mandato la posizione: meteo per quel punto, e la ricordo per notizie e allerte."""
        lat, lon = float(posizione["latitude"]), float(posizione["longitude"])
        luogo = self._luogo(lat, lon)
        etichetta = luogo.etichetta if luogo else messaggi.NOME_POSIZIONE_SCONOSCIUTO
        localita = localita_qui(lat, lon, etichetta)
        dati = DatiLocalita(self.meteo.previsioni(localita), self._mare_vicino(localita))
        testo = riepilogo_posizione(dati, self.orologio(), nome_da_osm=luogo is not None)

        in_tempo_reale = "live_period" in posizione
        self.posizioni.salva(chat.id, self._nuova_posizione(lat, lon, luogo, in_tempo_reale))
        nota = messaggi.POSIZIONE_LIVE if in_tempo_reale else messaggi.POSIZIONE_RICORDATA
        testo = f"{testo}\n\n{nota}"

        # Nelle chat private toglie il pulsante "Manda la mia posizione" dopo l'uso.
        tastiera = None if chat.e_un_gruppo else messaggi.TOGLI_TASTIERA
        self.messaggero.invia_messaggio(chat.id, testo, tastiera)

    def _nuova_posizione(
        self, lat: float, lon: float, luogo: Luogo | None, in_tempo_reale: bool
    ) -> Posizione:
        return Posizione(
            lat=lat,
            lon=lon,
            quando=self.orologio(),
            comune=luogo.comune if luogo else None,
            provincia=luogo.provincia if luogo else None,
            regione=luogo.regione if luogo else None,
            paese=luogo.paese if luogo else None,
            in_tempo_reale=in_tempo_reale,
        )

    def _posizione_aggiornata(self, messaggio: dict[str, Any]) -> None:
        """Aggiornamento della posizione in tempo reale: si salva in silenzio, senza rispondere."""
        chat_id = messaggio.get("chat", {}).get("id")
        posizione = messaggio.get("location")
        if chat_id is None or posizione is None or not self._e_autorizzata(chat_id):
            return
        lat, lon = float(posizione["latitude"]), float(posizione["longitude"])
        try:
            self.salva_posizione(chat_id, lat, lon)
        except ErroreArchivio as errore:
            log.warning("Posizione in tempo reale non salvata: %s", errore)

    def salva_posizione(self, chat_id: int, lat: float, lon: float) -> Posizione:
        """Salva in silenzio una posizione (in tempo reale o dal telefono)."""
        precedente = self.posizioni.leggi(chat_id)
        spostato = precedente is None or (
            distanza_km(precedente.lat, precedente.lon, lat, lon) >= SPOSTAMENTO_MINIMO_KM
        )
        if spostato or not precedente.comune:
            luogo = self._luogo(lat, lon)
        else:
            # Ancora nello stesso posto: niente richiesta a Nominatim, si tiene il nome.
            luogo = Luogo(
                precedente.comune, precedente.provincia, precedente.regione, precedente.paese
            )
        nuova = self._nuova_posizione(lat, lon, luogo, True)
        self.posizioni.salva(chat_id, nuova)
        return nuova

    def zona(self, chat_id: int | None = None) -> Zona:
        """La zona attuale di una persona (di default il padrone): dove si trova, o casa."""
        chi = chat_id if chat_id is not None else self.impostazioni.chat_personale
        return self.posizioni.zona(chi, self.orologio())

    def _ricorda_nome(self, chat_id: int, nome: str) -> None:
        """Il nome di Telegram dei familiari, per il "Buongiorno" delle 7."""
        if not nome or chat_id == self.impostazioni.chat_personale:
            return
        try:
            if self.archivio.leggi(f"nome:{chat_id}") != nome:
                self.archivio.scrivi(f"nome:{chat_id}", nome)
        except ErroreArchivio as errore:
            log.warning("Nome non salvato: %s", errore)

    def nome_di(self, chat_id: int) -> str:
        if chat_id == self.impostazioni.chat_personale:
            return self.impostazioni.nome_utente
        try:
            return self.archivio.leggi(f"nome:{chat_id}") or ""
        except ErroreArchivio:
            return ""

    # --- Comandi ----------------------------------------------------------------------

    def _aiuto(self, chat: Chat, comando: Comando) -> None:
        self.messaggero.invia_messaggio(chat.id, messaggi.AIUTO)
        # Chi chiede aiuto deve trovare anche il menu giusto.
        with contextlib.suppress(ErroreTelegram, ErroreArchivio):
            self.controlla_menu()

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

    def _dove(self, chat: Chat, comando: Comando) -> None:
        posizione = self.posizioni.leggi(chat.id)
        if posizione is None:
            self.messaggero.invia_messaggio(chat.id, messaggi.NESSUNA_POSIZIONE)
            return
        valida = self.orologio() - posizione.quando <= VALIDITA_ZONA
        self.messaggero.invia_messaggio(
            chat.id,
            messaggi.messaggio_dove(
                posizione.etichetta, posizione.quando, posizione.in_tempo_reale, valida
            ),
        )

    def _dimentica(self, chat: Chat, comando: Comando) -> None:
        self.posizioni.dimentica(chat.id)
        self.messaggero.invia_messaggio(chat.id, messaggi.POSIZIONE_DIMENTICATA)

    def _zona_richiesta(self, chat: Chat, comando: Comando) -> tuple[str, float, float] | None:
        """Comune su cui cercare notizie: quello scritto, o la zona attuale."""
        if comando.argomenti:
            localita = self._localita_richiesta(chat, comando)
            if localita is None:
                return None
            return localita.nome.split(" (")[0], localita.lat, localita.lon
        zona = self._zona_della_chat(chat)
        return zona.comune, zona.lat, zona.lon

    def _notizie(self, chat: Chat, comando: Comando) -> None:
        if self.notizie is None:
            self.messaggero.invia_messaggio(chat.id, messaggi.nessuna_notizia("questa zona"))
            return
        zona = self._zona_richiesta(chat, comando)
        if zona is None:
            return
        comune, lat, lon = zona
        adesso = self.orologio()
        if not comando.argomenti and modulo_notizie.in_zona(lat, lon):
            # In zona: le notizie di oggi divise per città, la tua per prima.
            gruppi = modulo_notizie.raggruppa(
                self._tutte_di_oggi(comune, adesso), modulo_notizie.citta_del_comune(comune)
            )
            if gruppi:
                testo = modulo_notizie.formatta_per_citta(
                    gruppi, messaggi.TITOLO_NOTIZIE_OGGI, modulo_notizie.citta_del_comune(comune)
                )
                self.messaggero.invia_messaggio(chat.id, testo + self._coda_principali(adesso))
                return
        trovate = self.notizie.notizie_per(comune, lat, lon, adesso)
        titolo_comune = comune
        if not trovate and (padre := modulo_notizie.FRAZIONI.get(comune.lower())):
            # Di Milano Marittima oggi non si parla? Allora le notizie di Cervia.
            trovate = self.notizie.notizie_per(padre, lat, lon, adesso)
            titolo_comune = f"{padre} (per {comune})"
        if not trovate:
            self.messaggero.invia_messaggio(chat.id, messaggi.nessuna_notizia(comune))
            return
        testo = modulo_notizie.formatta(
            trovate[:NOTIZIE_A_RICHIESTA], messaggi.titolo_notizie(titolo_comune, nuove=False)
        )
        self.messaggero.invia_messaggio(chat.id, testo)

    def _allerte(self, chat: Chat, comando: Comando) -> None:
        zona = self._zona_della_chat(chat)
        self.messaggero.invia_messaggio(chat.id, self._testo_allerte(zona, solo_se_ci_sono=False))

    def _testo_allerte(self, zona: Zona, solo_se_ci_sono: bool) -> str:
        if self.allerte is None or not zona.regione or not zona.paese:
            return "" if solo_se_ci_sono else messaggi.ALLERTE_NON_DISPONIBILI
        attive = modulo_allerte.per_regione(
            self.allerte.allerte(zona.paese), zona.regione, self.orologio()
        )
        if not attive:
            return "" if solo_se_ci_sono else messaggi.nessuna_allerta(zona.regione)
        return modulo_allerte.formatta(attive, zona.regione, messaggi.titolo_allerte(zona.regione))

    def _oggi(self, chat: Chat, comando: Comando) -> None:
        zona = self._zona_della_chat(chat)
        self.messaggero.invia_messaggio(chat.id, self.testo_oggi(saluta=False, zona=zona))

    def _domani(self, chat: Chat, comando: Comando) -> None:
        domani = self.orologio().date() + timedelta(days=1)
        dati = self._dati_localita(self._zona_della_chat(chat))
        self.messaggero.invia_messaggio(chat.id, riepilogo_domani(dati, domani))

    # --- Riepiloghi -------------------------------------------------------------------

    def _dati_localita(self, zona: Zona | None = None) -> list[DatiLocalita]:
        """Casa (Riccione e Rimini); se sei lontano, prima il posto in cui ti trovi."""
        luoghi = list(LOCALITA)
        if zona is not None and zona.da_posizione and self._lontano_da_casa(zona):
            luoghi.insert(0, localita_qui(zona.lat, zona.lon, zona.etichetta))
        return [
            DatiLocalita(self._previsioni(localita), self._mare_vicino(localita))
            for localita in luoghi
        ]

    def _previsioni(self, localita: Localita) -> Any:
        chiave = (localita.lat, localita.lon)
        if chiave not in self._cache_previsioni:
            self._cache_previsioni[chiave] = self.meteo.previsioni(localita)
        return self._cache_previsioni[chiave]

    @staticmethod
    def _lontano_da_casa(zona: Zona) -> bool:
        return all(
            distanza_km(zona.lat, zona.lon, casa.lat, casa.lon) > DISTANZA_DA_CASA_KM
            for casa in LOCALITA
        )

    def testo_oggi(self, saluta: bool, zona: Zona | None = None, nome: str | None = None) -> str:
        if nome is None:
            nome = self.impostazioni.nome_utente if saluta else ""
        zona = zona if zona is not None else self._zona_sicura()
        avvisi = []
        if zona is not None and (testo := self._testo_allerte(zona, solo_se_ci_sono=True)):
            avvisi.append(testo)
        aria = None
        if zona is not None and self.aria is not None:
            aria = self.aria.aria(localita_qui(zona.lat, zona.lon, zona.etichetta))
        if aria is not None:
            avvisi += avvisi_aria(aria)
        return riepilogo_oggi(self._dati_localita(zona), self.orologio(), nome, avvisi)

    def _zona_sicura(self, chat_id: int | None = None) -> Zona | None:
        """La zona, ma senza far fallire il riepilogo se l'archivio non risponde."""
        try:
            return self.zona(chat_id)
        except ErroreArchivio as errore:
            log.warning("Zona non disponibile: %s", errore)
            return None

    def invia_riepilogo_mattutino(self) -> None:
        """Il messaggio delle 7, a ogni persona con il suo nome e la sua zona;
        poi promemoria delle scadenze e (il 1°) il resoconto delle spese."""
        for chat_id in self._persone():
            # Es. un familiare ha bloccato il bot: gli altri lo ricevono lo stesso.
            self._riepilogo_per(chat_id)
            with contextlib.suppress(ErroreArchivio):
                self.archivio.segna_nuovi([f"mattino:{chat_id}:{self._oggi_data()}"], 2 * 24 * 3600)
        self._promemoria_scadenze_e_resoconti()

    def _promemoria_scadenze_e_resoconti(self) -> None:
        """Scadenze in arrivo e (il 1°) il resoconto del mese: una volta al giorno per chat."""
        oggi = self._oggi_data()
        for chat in sorted(self.impostazioni.chat_autorizzate, reverse=True):
            chiave = f"scadenze:{chat}:{oggi}"
            try:
                (nuovo,) = self.archivio.segna_nuovi([chiave], 2 * 24 * 3600)
                if not nuovo:
                    continue
                self.invia_promemoria_scadenze(chat)
                if oggi.day == 1:
                    self.invia_resoconto_mese_scorso(chat)
            except (ErroreArchivio, ErroreTelegram) as errore:
                log.warning("Promemoria e resoconto saltati per %s: %s", chat, errore)

    def controlla_scadenze(self) -> None:
        """Le scadenze arrivano anche a chi ha spento il riepilogo del mattino (dalle 8)."""
        if not self.archivio.persistente:
            return
        if self.orologio().time().replace(tzinfo=None) >= ORA_SCADENZE:
            self._promemoria_scadenze_e_resoconti()

    # --- Scadenze ---------------------------------------------------------------------

    def _oggi_data(self) -> date:
        return self.orologio().date()

    def _scadenza(self, chat: Chat, comando: Comando) -> None:
        nuova = leggi_scadenza(comando.argomenti, self._oggi_data())
        if nuova is None:
            self.messaggero.invia_messaggio(chat.id, modulo_scadenze.USO)
            return
        scadenza = RegistroScadenze(self.archivio, chat.id).aggiungi(nuova)
        testo = (
            f"📅 Segnata: {modulo_scadenze.riga(scadenza, self._oggi_data())}\n"
            "Ti avviso 30, 7 e 1 giorno prima, e il giorno stesso. /scadenze per vederle tutte."
        )
        self.messaggero.invia_messaggio(chat.id, testo)

    def _testo_scadenze(self, chat_id: int) -> tuple[str, dict[str, Any] | None]:
        tutte = RegistroScadenze(self.archivio, chat_id).tutte()
        return (
            modulo_scadenze.elenco(tutte, self._oggi_data()),
            modulo_scadenze.pulsanti(tutte, con_elimina=True),
        )

    def _scadenze(self, chat: Chat, comando: Comando) -> None:
        self.messaggero.invia_messaggio(chat.id, *self._testo_scadenze(chat.id))

    def invia_promemoria_scadenze(self, chat_id: int) -> int:
        oggi = self._oggi_data()
        da_dire = modulo_scadenze.da_ricordare(
            RegistroScadenze(self.archivio, chat_id).tutte(), oggi
        )
        if da_dire:
            self.messaggero.invia_messaggio(
                chat_id,
                modulo_scadenze.promemoria(da_dire, oggi),
                modulo_scadenze.pulsanti([s for s, _ in da_dire], con_elimina=False),
            )
        return len(da_dire)

    def _paga(self, chat_id: int, id_scadenza: int) -> str:
        risultato = RegistroScadenze(self.archivio, chat_id).paga(id_scadenza)
        if risultato is None:
            return "Questa scadenza non c'è più"
        pagata, prossima = risultato
        if pagata.importo:
            RegistroSpese(self.archivio, chat_id).aggiungi(
                self._oggi_data(), pagata.importo, pagata.descrizione
            )
        avviso = f"✅ {pagata.descrizione}: pagato"
        if prossima:
            avviso += f". Prossima: {prossima.data.day}/{prossima.data.month}/{prossima.data.year}"
        return avviso

    # --- Spese ------------------------------------------------------------------------

    def _spesa(self, chat: Chat, comando: Comando) -> None:
        letta = leggi_spesa(comando.argomenti)
        if letta is None:
            self.messaggero.invia_messaggio(chat.id, messaggi.USO_SPESA)
            return
        importo, descrizione = letta
        self._registra_spesa(chat, importo, descrizione, comando.mittente, comando.mittente_id)

    def _registra_spesa(
        self, chat: Chat, importo: int, descrizione: str, chi: str, chi_id: int | None
    ) -> None:
        """Segna la spesa (nel gruppo anche chi l'ha pagata) e mostra i pulsanti."""
        registro = RegistroSpese(self.archivio, chat.id)
        oggi = self._oggi_data()
        nel_gruppo = chat.e_un_gruppo
        spesa = registro.aggiungi(
            oggi,
            importo,
            descrizione,
            chi=chi if nel_gruppo else "",
            chi_id=chi_id if nel_gruppo else None,
        )
        spese_mese = registro.del_mese(oggi)
        testo = messaggi.spesa_segnata(
            spesa.importo, spesa.categoria, descrizione, modulo_spese.totale(spese_mese)
        )
        if nel_gruppo and chi:
            testo += f"\n💳 Pagata da {escape(chi)} · /conti per i conti di casa"
        budget = Budget(self.archivio, chat.id).tutti()
        if righe := modulo_budget.avviso_dopo_spesa(budget, spese_mese, spesa):
            testo += f"\n{righe}"
        self.messaggero.invia_messaggio(chat.id, testo, modulo_spese.pulsanti_spesa(spesa))

    def _spese(self, chat: Chat, comando: Comando) -> None:
        spese = RegistroSpese(self.archivio, chat.id).del_mese(self._oggi_data())
        self.messaggero.invia_messaggio(chat.id, modulo_spese.elenco(spese))

    def _testo_mese(self, chat_id: int, mese: date) -> str:
        registro = RegistroSpese(self.archivio, chat_id)
        precedente = modulo_spese.totale(registro.del_mese(aggiungi_mesi(mese, -1)))
        return modulo_spese.resoconto(registro.del_mese(mese), mese, precedente or None)

    def _mese(self, chat: Chat, comando: Comando) -> None:
        oggi = self._oggi_data()
        mese = leggi_mese(comando.testo_argomenti, oggi) if comando.argomenti else None
        if comando.argomenti and mese is None:
            self.messaggero.invia_messaggio(chat.id, messaggi.USO_MESE)
            return
        self.messaggero.invia_messaggio(
            chat.id, self._testo_mese(chat.id, mese or oggi.replace(day=1))
        )

    def _annulla(self, chat: Chat, comando: Comando) -> None:
        tolta = RegistroSpese(self.archivio, chat.id).annulla_ultima(self._oggi_data())
        testo = (
            messaggi.NIENTE_DA_ANNULLARE
            if tolta is None
            else f"↩️ Tolta: {euro(tolta.importo)} · {escape(tolta.descrizione)}"
        )
        self.messaggero.invia_messaggio(chat.id, testo)

    def _esporta(self, chat: Chat, comando: Comando) -> None:
        anno = self._oggi_data().year
        if comando.argomenti and comando.argomenti[0].isdigit():
            anno = int(comando.argomenti[0])
        spese = RegistroSpese(self.archivio, chat.id).dell_anno(anno)
        if not spese:
            self.messaggero.invia_messaggio(chat.id, f"💶 Nessuna spesa segnata nel {anno}.")
            return
        self.messaggero.invia_documento(
            chat.id,
            f"spese-{anno}.csv",
            modulo_spese.csv_spese(spese),
            f"💶 Spese del {anno}: {modulo_spese.quante(len(spese))}, "
            f"{euro(modulo_spese.totale(spese))}",
        )

    def invia_resoconto_mese_scorso(self, chat_id: int) -> bool:
        mese = aggiungi_mesi(self._oggi_data().replace(day=1), -1)
        if not RegistroSpese(self.archivio, chat_id).del_mese(mese):
            return False
        self.messaggero.invia_messaggio(chat_id, self._testo_mese(chat_id, mese))
        return True

    # --- Lista condivisa --------------------------------------------------------------

    def _lista(self, chat: Chat, comando: Comando) -> None:
        voci = Lista(self.archivio, chat.id).voci()
        self.messaggero.invia_messaggio(
            chat.id, modulo_lista.messaggio_lista(voci), modulo_lista.pulsanti_lista(voci)
        )

    def _aggiungi(self, chat: Chat, comando: Comando) -> None:
        testi = leggi_voci(comando.testo_argomenti)
        if not testi:
            self.messaggero.invia_messaggio(chat.id, messaggi.USO_AGGIUNGI)
            return
        lista = Lista(self.archivio, chat.id)
        lista.aggiungi(testi, comando.mittente if chat.e_un_gruppo else "")
        voci = lista.voci()
        self.messaggero.invia_messaggio(
            chat.id, modulo_lista.messaggio_lista(voci), modulo_lista.pulsanti_lista(voci)
        )

    def _svuota(self, chat: Chat, comando: Comando) -> None:
        tolte = Lista(self.archivio, chat.id).togli_fatte()
        self.messaggero.invia_messaggio(chat.id, messaggi.lista_svuotata(tolte))

    # --- Weekend ----------------------------------------------------------------------

    def _weekend(self, chat: Chat, comando: Comando) -> None:
        sabato, domenica = modulo_weekend.prossimo_weekend(self._oggi_data())
        previsioni = self.meteo.previsioni(RICCIONE)
        giorni = {g.data: g for g in previsioni.giorni}
        eventi = []
        if self.notizie is not None:
            eventi = self.notizie.eventi(self.orologio())
        testo = modulo_weekend.messaggio_weekend(
            giorni.get(sabato),
            giorni.get(domenica),
            sabato,
            eventi,
            modulo_feste.nel_periodo(sabato, domenica),
        )
        self.messaggero.invia_messaggio(chat.id, testo)

    # --- Pulsanti ---------------------------------------------------------------------

    def _pulsante(self, tocco: dict[str, Any]) -> None:
        """Tocco su un pulsante inline: pagato, elimina, spunta della lista."""
        id_tocco = str(tocco.get("id", ""))
        messaggio = tocco.get("message") or {}
        chat_id = (messaggio.get("chat") or {}).get("id")
        id_messaggio = messaggio.get("message_id")
        azione, _, valore = str(tocco.get("data", "")).partition(":")
        if chat_id is None or not self._e_autorizzata(chat_id):
            self.messaggero.rispondi_pulsante(id_tocco)
            return
        try:
            avviso = self._esegui_pulsante(chat_id, id_messaggio, azione, valore)
        except (ErroreArchivio, ValueError) as errore:
            log.warning("Pulsante %s non riuscito: %s", azione, errore)
            avviso = "😓 Non ci sono riuscita, riprova"
        except Exception:
            # Es. messaggio troppo vecchio da modificare: la clessidra va tolta comunque.
            log.exception("Pulsante %s non riuscito", azione)
            avviso = "😓 Non ci sono riuscita, riprova"
        self.messaggero.rispondi_pulsante(id_tocco, avviso)

    def _esegui_pulsante(
        self, chat_id: int, id_messaggio: int | None, azione: str, valore: str
    ) -> str | None:
        if azione in {"pagato", "elimina"}:
            if azione == "pagato":
                avviso = self._paga(chat_id, int(valore))
            else:
                tolta = RegistroScadenze(self.archivio, chat_id).elimina(int(valore))
                avviso = f"🗑 Tolta: {tolta.descrizione}" if tolta else None
            if id_messaggio is not None:
                self.messaggero.modifica_messaggio(
                    chat_id, id_messaggio, *self._testo_scadenze(chat_id)
                )
            return avviso
        if azione in {"spcat", "spset", "spann"}:
            return self._pulsante_spesa(chat_id, id_messaggio, azione, valore)
        if azione in {"prpost", "prok"}:
            return self._pulsante_promemoria(chat_id, id_messaggio, azione, valore)
        if azione == "prdel":
            RegistroPromemoria(self.archivio, chat_id).elimina(int(valore))
            if id_messaggio is not None:
                voci = RegistroPromemoria(self.archivio, chat_id).tutti()
                self.messaggero.modifica_messaggio(
                    chat_id,
                    id_messaggio,
                    modulo_promemoria.elenco(voci, self.orologio()),
                    modulo_promemoria.pulsanti(voci) or {"inline_keyboard": []},
                )
            return "🗑 Cancellato"
        if azione in {"voce", "lista"}:
            lista = Lista(self.archivio, chat_id)
            if azione == "voce":
                lista.cambia(int(valore))
            elif valore == "pulisci":
                lista.togli_fatte()
            voci = lista.voci()
            if id_messaggio is not None:
                self.messaggero.modifica_messaggio(
                    chat_id,
                    id_messaggio,
                    modulo_lista.messaggio_lista(voci),
                    modulo_lista.pulsanti_lista(voci) or {"inline_keyboard": []},
                )
            return None
        return None

    def _persone(self) -> tuple[int, ...]:
        persone = self.impostazioni.persone
        if not persone:
            raise RuntimeError("Manca TELEGRAM_CHAT_ID: non so a chi scrivere")
        return persone

    # --- Controlli automatici (ogni 15 minuti da cron-job.org) --------------------------

    def _serve_memoria(self, lavoro: str) -> bool:
        if not self.archivio.persistente:
            # Senza memoria ogni controllo rimanderebbe tutto da capo: meglio non mandare nulla.
            log.warning(
                "%s: serve l'archivio Upstash per ricordare cosa è già stato mandato", lavoro
            )
            return False
        return True

    def _per_ogni_persona(self, controllo: Callable[[int], int]) -> int:
        totale = 0
        for chat_id in self._persone():
            try:
                totale += controllo(chat_id)
            except ErroreTelegram as errore:
                log.warning("%s non consegnato a %s: %s", controllo.__name__, chat_id, errore)
        return totale

    def controlla_notizie(self) -> int:
        """Manda a ognuno le notizie nuove della sua zona. Restituisce quante ne ha mandate."""
        persone = self._persone()
        if self.notizie is None or not self._serve_memoria("Notizie") or not persone:
            return 0
        return self._per_ogni_persona(self._notizie_per)

    def _notizie_per(self, chat_id: int) -> int:
        assert self.notizie is not None
        orari = self.orari.di(chat_id)
        adesso = self.orologio()
        if in_silenzio(orari, adesso):
            return 0
        if not orari.notizie_subito:
            return self._riassunto_notizie(chat_id, orari, adesso)
        zona = self.zona(chat_id)
        trovate = self.notizie.notizie_per(zona.comune, zona.lat, zona.lon, self.orologio())
        # La prima volta in una zona non si manda tutto l'arretrato: solo le ultime.
        (zona_nuova,) = self.archivio.segna_nuovi(
            [f"zona-notizie:{chat_id}:{zona.comune.lower()}"], RICORDO_ZONA_S
        )
        if not trovate:
            return 0
        nuove = [
            n
            for n, nuova in zip(
                trovate,
                self.archivio.segna_nuovi(
                    [f"notizia:{chat_id}:{n.impronta}" for n in trovate], RICORDO_NOTIZIE_S
                ),
                strict=True,
            )
            if nuova
        ]
        massimo = MASSIMO_NOTIZIE_ZONA_NUOVA if zona_nuova else MASSIMO_NOTIZIE
        da_mandare: list[Notizia] = nuove[:massimo]
        if da_mandare:
            titolo = messaggi.titolo_notizie(zona.etichetta, nuove=True)
            self.messaggero.invia_messaggio(chat_id, modulo_notizie.formatta(da_mandare, titolo))
        return len(da_mandare)

    def controlla_allerte(self) -> int:
        """Manda a ognuno le allerte nuove (o salite di livello) della sua zona."""
        persone = self._persone()
        if self.allerte is None or not self._serve_memoria("Allerte") or not persone:
            return 0
        return self._per_ogni_persona(self._allerte_per)

    def _allerte_per(self, chat_id: int) -> int:
        assert self.allerte is not None
        zona = self.zona(chat_id)
        if not zona.regione or not zona.paese:
            return 0
        adesso = self.orologio()
        attive = modulo_allerte.per_regione(self.allerte.allerte(zona.paese), zona.regione, adesso)
        if in_silenzio(self.orari.di(chat_id), adesso):
            # Di notte si sveglia qualcuno solo per allerte arancioni o rosse.
            attive = [a for a in attive if a.livello >= 3]
        nuove = []
        for allerta in attive:
            durata = (allerta.fine - adesso).total_seconds() if allerta.fine else 3 * 24 * 3600
            (nuova,) = self.archivio.segna_nuovi(
                [f"{allerta.chiave}:{chat_id}"], max(int(durata), 3600)
            )
            if nuova:
                nuove.append(allerta)
        if nuove:
            titolo = messaggi.titolo_allerte(zona.regione)
            self.messaggero.invia_messaggio(
                chat_id, modulo_allerte.formatta(nuove, zona.regione, titolo)
            )
        return len(nuove)

    def controlli(self) -> None:
        """Il giro di controlli periodico: promemoria, allerte, notizie e (alle 20) la sera."""
        inizio = time.monotonic()
        for controllo in (
            self.controlla_salute,
            self.controlla_promemoria,
            self.controlla_mattino,
            self.controlla_allerte,
            self.controlla_imminente,
            self.controlla_notizie,
            self.controlla_sera,
            self.controlla_scadenze,
            self.controlla_menu,
        ):
            if time.monotonic() - inizio > TEMPO_MASSIMO_CONTROLLI_S:
                # Vercel ferma tutto a 60 secondi: il resto lo fa il giro dopo.
                log.warning("Controllo %s rimandato: giro troppo lungo", controllo.__name__)
                continue
            try:
                controllo()
            except (ErroreArchivio, ErroreMeteo) as errore:
                log.warning("Controllo %s saltato: %s", controllo.__name__, errore)
            except Exception:
                # Un controllo che si rompe non deve fermare gli altri (notizie, allerte…).
                log.exception("Controllo %s fallito", controllo.__name__)
        with contextlib.suppress(ErroreArchivio):
            self.archivio.scrivi("ultimo-giro", self.orologio().isoformat(), 7 * 24 * 3600)

    # --- Messaggi liberi ---------------------------------------------------------------

    def _testo_libero(self, chat: Chat, testo: str, chi: str, chi_id: int | None) -> None:
        """Frasi senza comando: "45 benzina", "ricordami domani alle 18 di…".

        Nei gruppi la gente chiacchiera: lì si capiscono solo le frasi che iniziano con
        "spesa" o "ricordami", per non scambiare un messaggio qualsiasi per una spesa.
        """
        if e_un_promemoria(testo):
            self._crea_promemoria(chat, testo)
            return
        frase = testo
        if chat.e_un_gruppo:
            minuscolo = testo.lower()
            if not minuscolo.startswith(("spesa ", "+")):
                return
            frase = testo[1:] if testo.startswith("+") else testo[len("spesa ") :]
        if (letta := leggi_spesa_libera(frase)) is not None:
            self._registra_spesa(chat, *letta, chi, chi_id)
        elif not chat.e_un_gruppo:
            self.messaggero.invia_messaggio(chat.id, messaggi.NON_HO_CAPITO)

    def _ricorda_membro(self, chat_id: int, utente_id: int, nome: str) -> None:
        try:
            MembriGruppo(self.archivio, chat_id).ricorda(utente_id, nome)
        except ErroreArchivio as errore:
            log.warning("Membro del gruppo non salvato: %s", errore)

    # --- Spese: pulsanti, budget, conti -------------------------------------------------

    def _pulsante_spesa(
        self, chat_id: int, id_messaggio: int | None, azione: str, valore: str
    ) -> str | None:
        mese, id_spesa, indice = modulo_spese.leggi_riferimento(valore)
        registro = RegistroSpese(self.archivio, chat_id)
        spesa = registro.trova(mese, id_spesa)
        if spesa is None:
            return "Questa spesa non c'è più"
        if azione == "spann":
            registro.elimina(mese, id_spesa)
            testo = messaggi.spesa_annullata(spesa.importo, spesa.descrizione)
            tastiera: dict[str, Any] = {"inline_keyboard": []}
            avviso = "↩️ Annullata"
        elif azione == "spcat":
            testo = messaggi.scegli_categoria(spesa.descrizione)
            tastiera = modulo_spese.pulsanti_categorie(spesa)
            avviso = None
        else:
            categorie = modulo_spese.TUTTE_LE_CATEGORIE
            if indice is None or not 0 <= indice < len(categorie):
                return None
            cambiata = registro.cambia_categoria(mese, id_spesa, categorie[indice])
            assert cambiata is not None
            testo = messaggi.categoria_cambiata(
                cambiata.importo, cambiata.categoria, cambiata.descrizione
            )
            tastiera = modulo_spese.pulsanti_spesa(cambiata)
            avviso = f"🏷️ {cambiata.categoria}"
        if id_messaggio is not None:
            self.messaggero.modifica_messaggio(chat_id, id_messaggio, testo, tastiera)
        return avviso

    def _budget(self, chat: Chat, comando: Comando) -> None:
        budget = Budget(self.archivio, chat.id)
        if comando.argomenti:
            letto = leggi_budget(comando.argomenti)
            if letto is None:
                self.messaggero.invia_messaggio(chat.id, modulo_budget.USO)
                return
            budget.imposta(*letto)
        spese = RegistroSpese(self.archivio, chat.id).del_mese(self._oggi_data())
        self.messaggero.invia_messaggio(chat.id, modulo_budget.stato(budget.tutti(), spese))

    def _conti(self, chat: Chat, comando: Comando) -> None:
        if not chat.e_un_gruppo:
            self.messaggero.invia_messaggio(chat.id, messaggi.CONTI_SOLO_GRUPPO)
            return
        dal = Pareggio(self.archivio, chat.id).dal()
        spese = spese_da(RegistroSpese(self.archivio, chat.id), dal, self._oggi_data())
        membri = MembriGruppo(self.archivio, chat.id).tutti()
        self.messaggero.invia_messaggio(chat.id, modulo_conti.resoconto(spese, membri, dal))

    def _pareggio(self, chat: Chat, comando: Comando) -> None:
        if not chat.e_un_gruppo:
            self.messaggero.invia_messaggio(chat.id, messaggi.CONTI_SOLO_GRUPPO)
            return
        oggi = self._oggi_data()
        Pareggio(self.archivio, chat.id).segna(oggi + timedelta(days=1))
        self.messaggero.invia_messaggio(chat.id, messaggi.PAREGGIO)

    # --- Promemoria, rifiuti, sera ---------------------------------------------------------

    def _crea_promemoria(self, chat: Chat, frase: str) -> None:
        adesso = self.orologio()
        try:
            quando, cosa, ripeti = leggi_ricorrente(frase, adesso)
            nuovo = RegistroPromemoria(self.archivio, chat.id).aggiungi(quando, cosa, ripeti)
        except ErrorePromemoria as errore:
            self.messaggero.invia_messaggio(chat.id, f"🤔 {errore}.\n\n{modulo_promemoria.USO}")
            return
        self.messaggero.invia_messaggio(chat.id, modulo_promemoria.conferma(nuovo, adesso))

    def _ricordami(self, chat: Chat, comando: Comando) -> None:
        if not comando.argomenti:
            self.messaggero.invia_messaggio(chat.id, modulo_promemoria.USO)
            return
        self._crea_promemoria(chat, comando.testo_argomenti)

    def _promemoria(self, chat: Chat, comando: Comando) -> None:
        voci = RegistroPromemoria(self.archivio, chat.id).tutti()
        self.messaggero.invia_messaggio(
            chat.id,
            modulo_promemoria.elenco(voci, self.orologio()),
            modulo_promemoria.pulsanti(voci),
        )

    def _rifiuti(self, chat: Chat, comando: Comando) -> None:
        calendario = Calendario(self.archivio, chat.id)
        if comando.argomenti:
            letto = leggi_rifiuti(comando.argomenti)
            if letto is None:
                self.messaggero.invia_messaggio(chat.id, modulo_rifiuti.USO)
                return
            calendario.imposta(*letto)
        self.messaggero.invia_messaggio(
            chat.id, modulo_rifiuti.messaggio_calendario(calendario.tutto())
        )

    def _sera_attiva(self, chat_id: int) -> bool:
        return bool(self.archivio.leggi(f"sera-attiva:{chat_id}"))

    def _sera(self, chat: Chat, comando: Comando) -> None:
        scelta = comando.testo_argomenti.lower()
        if scelta in {"on", "si", "sì", "attiva"}:
            attiva = True
        elif scelta in {"off", "no", "spegni", "basta"}:
            attiva = False
        else:
            attiva = not self._sera_attiva(chat.id)
        self.archivio.scrivi(f"sera-attiva:{chat.id}", attiva)
        self.messaggero.invia_messaggio(chat.id, messaggi.sera_impostata(attiva))

    def controlla_promemoria(self) -> int:
        """Consegna i promemoria arrivati all'ora giusta, in tutte le chat."""
        if not self._serve_memoria("Promemoria"):
            return 0
        adesso = self.orologio()
        mandati = 0
        for chat_id in sorted(self.impostazioni.chat_autorizzate):
            registro = RegistroPromemoria(self.archivio, chat_id)
            da_riprovare = []
            for voce in registro.scaduti(adesso):
                try:
                    self.messaggero.invia_messaggio(
                        chat_id,
                        modulo_promemoria.avviso(voce),
                        modulo_promemoria.pulsanti_avviso(voce),
                    )
                    mandati += 1
                except ErroreTelegram as errore:
                    log.warning("Promemoria non consegnato a %s: %s", chat_id, errore)
                    if not _errore_definitivo(errore):
                        da_riprovare.append(voce)
            if da_riprovare:
                registro.rimetti(da_riprovare)  # al prossimo giro, fra 15 minuti
        return mandati

    def controlla_sera(self) -> int:
        """Alle 20: rifiuti da portare fuori e, se attivo, il riepilogo di domani."""
        adesso = self.orologio()
        if not self._serve_memoria("Sera"):
            return 0
        domani = adesso.date() + timedelta(days=1)
        mandati = 0
        for chat_id in sorted(self.impostazioni.chat_autorizzate):
            if not in_orario(adesso, self.orari.di(chat_id).sera):
                continue
            (nuova,) = self.archivio.segna_nuovi([f"sera:{chat_id}:{adesso.date()}"], 2 * 24 * 3600)
            if not nuova:
                continue
            parti = []
            if rifiuti := Calendario(self.archivio, chat_id).del_giorno(domani.weekday()):
                parti.append(modulo_rifiuti.avviso_sera(rifiuti))
            if self._sera_attiva(chat_id):
                parti += self._parti_della_sera(chat_id, domani)
            if not parti:
                continue
            try:
                self.messaggero.invia_messaggio(chat_id, "\n\n".join(parti))
                mandati += 1
            except ErroreTelegram as errore:
                log.warning("Sera non consegnata a %s: %s", chat_id, errore)
        return mandati

    def _parti_della_sera(self, chat_id: int, domani: date) -> list[str]:
        parti = [f"🌙 <b>Buonasera{', ' + escape(n) if (n := self.nome_di(chat_id)) else ''}!</b>"]
        zona = self._zona_sicura(chat_id if chat_id in self.impostazioni.persone else None)
        try:
            parti.append(riepilogo_domani(self._dati_localita(zona), domani))
        except ErroreMeteo as errore:
            log.warning("Meteo di domani non disponibile: %s", errore)
        if voci := RegistroPromemoria(self.archivio, chat_id).del_giorno(domani):
            parti.append(
                "⏰ <b>Domani</b>\n"
                + "\n".join(f"• {p.quando:%H:%M} {escape(p.testo)}" for p in voci)
            )
        scadenze = [s for s in RegistroScadenze(self.archivio, chat_id).tutte() if s.data == domani]
        if scadenze:
            parti.append(
                "📅 <b>Scade domani</b>\n"
                + "\n".join(f"• {escape(s.descrizione)}" for s in scadenze)
            )
        return parti

    # --- Aria e carburante ----------------------------------------------------------------

    def _aria(self, chat: Chat, comando: Comando) -> None:
        if self.aria is None:
            self.messaggero.invia_messaggio(chat.id, messaggi.ARIA_NON_DISPONIBILE)
            return
        if (localita := self._localita_richiesta(chat, comando)) is None:
            return
        aria = self.aria.aria(localita)
        testo = messaggio_aria(aria, localita.nome) if aria else messaggi.ARIA_NON_DISPONIBILE
        self.messaggero.invia_messaggio(chat.id, testo)

    def _benzina(self, chat: Chat, comando: Comando) -> None:
        if self.carburante is None:
            self.messaggero.invia_messaggio(chat.id, messaggi.CARBURANTE_NON_DISPONIBILE)
            return
        argomenti = list(comando.argomenti)
        tipo = "benzina"
        if argomenti and argomenti[0].lower() in CARBURANTI:
            tipo = argomenti.pop(0).lower()
        richiesta = replace(comando, argomenti=tuple(argomenti))
        if (localita := self._localita_richiesta(chat, richiesta)) is None:
            return
        carburante = CARBURANTI[tipo]
        try:
            offerte = self.carburante.offerte(
                carburante, localita.lat, localita.lon, self.orologio()
            )
        except httpx.HTTPError as errore:
            log.warning("Osservaprezzi non disponibile: %s", errore)
            self.messaggero.invia_messaggio(chat.id, messaggi.CARBURANTE_NON_DISPONIBILE)
            return
        self.messaggero.invia_messaggio(
            chat.id, modulo_carburante.messaggio(offerte, carburante, localita.nome)
        )

    def _pulsante_promemoria(
        self, chat_id: int, id_messaggio: int | None, azione: str, valore: str
    ) -> str | None:
        """Sotto un promemoria arrivato: ⏰ +1 ora, 📅 domani, ✅ fatto."""
        id_testo, _, quando = valore.partition(":")
        registro = RegistroPromemoria(self.archivio, chat_id)
        testo = registro.testo_consegnato(int(id_testo))
        if testo is None:
            return "Questo promemoria è troppo vecchio"
        adesso = self.orologio()
        if azione == "prok":
            nuovo_testo = f"✅ <s>{escape(testo)}</s>"
            avviso = "✅ Fatto"
        else:
            nuovo_quando = (
                adesso + timedelta(days=1) if quando == "d" else adesso + timedelta(minutes=60)
            )
            nuovo = registro.aggiungi(nuovo_quando, testo)
            leggibile = modulo_promemoria.quando_leggibile(nuovo.quando, adesso)
            nuovo_testo = f"⏰ <b>{escape(testo)}</b>: te lo ricordo {leggibile}"
            avviso = f"⏰ Rimandato a {leggibile}"
        if id_messaggio is not None:
            self.messaggero.modifica_messaggio(
                chat_id, id_messaggio, nuovo_testo, {"inline_keyboard": []}
            )
        return avviso

    # --- Sagre e costa ----------------------------------------------------------------------

    def _sagre(self, chat: Chat, comando: Comando) -> None:
        eventi = self.notizie.eventi(self.orologio()) if self.notizie is not None else []
        self.messaggero.invia_messaggio(
            chat.id, modulo_feste.messaggio_sagre(self._oggi_data(), eventi)
        )

    def _costa(self, chat: Chat, comando: Comando) -> None:
        """Da Cervia a Pesaro: oggi, oppure domani con ``/costa domani``."""
        domani = comando.testo_argomenti.lower().startswith("doman")
        giorno = self._oggi_data() + timedelta(days=1 if domani else 0)
        spiagge = []
        for localita in modulo_costa.COSTA:
            previsioni = self._previsioni(localita)
            mare = self._mare_vicino(localita)
            spiagge.append(
                modulo_costa.Spiaggia(
                    localita.nome,
                    previsioni,
                    next((g for g in previsioni.giorni if g.data == giorno), None),
                    next((g for g in mare.giorni if g.data == giorno), None) if mare else None,
                    adesso=not domani,
                )
            )
        titolo = "La costa domani" if domani else "La costa oggi"
        self.messaggero.invia_messaggio(chat.id, modulo_costa.messaggio_costa(spiagge, titolo))

    # --- Orari, riassunti, avvisi imminenti, telefono ---------------------------------------

    def _orari(self, chat: Chat, comando: Comando) -> None:
        orari = self.orari.di(chat.id)
        if comando.argomenti:
            try:
                orari = modulo_orari.modifica(orari, comando.argomenti)
            except ErroreOrari as errore:
                self.messaggero.invia_messaggio(
                    chat.id, f"🤔 {escape(str(errore))}.\n\n{modulo_orari.USO}"
                )
                return
            self.orari.salva(chat.id, orari)
        self.messaggero.invia_messaggio(
            chat.id, modulo_orari.messaggio(orari, per_gruppo=chat.e_un_gruppo)
        )

    def controlla_mattino(self, anche_senza_memoria: bool = False) -> int:
        """Il riepilogo del mattino, a ognuno all'ora che ha scelto (una volta al giorno)."""
        if not self.archivio.persistente and not anche_senza_memoria:
            return 0
        adesso = self.orologio()
        mandati = 0
        for chat_id in self._persone():
            mattino = self.orari.di(chat_id).mattino
            if mattino is None or not in_orario(adesso, mattino):
                continue
            chiave = f"mattino:{chat_id}:{adesso.date()}"
            if not self.archivio.sono_nuovi([chiave])[0]:
                continue
            # Se il meteo non risponde, si riprova al giro dopo (finché c'è tempo).
            ultimo_giro = not in_orario(adesso, mattino, FINESTRA - timedelta(minutes=20))
            testo = self._testo_mattino(chat_id, riprova=not ultimo_giro)
            if testo is None:
                continue
            (nuovo,) = self.archivio.segna_nuovi([chiave], 2 * 24 * 3600)
            if not nuovo:
                continue  # l'ha già mandato un altro giro nel frattempo
            if self._consegna(chat_id, testo, "Riepilogo"):
                mandati += 1
            elif not ultimo_giro:
                self.archivio.cancella(chiave)  # Telegram non ha risposto: si riprova
        if mandati:
            self._promemoria_scadenze_e_resoconti()
        return mandati

    def _testo_mattino(self, chat_id: int, riprova: bool = False) -> str | None:
        """Il testo del riepilogo; ``None`` se il meteo non c'è e conviene riprovare dopo."""
        try:
            testo = self.testo_oggi(
                saluta=True, zona=self._zona_sicura(chat_id), nome=self.nome_di(chat_id)
            )
        except ErroreMeteo as errore:
            log.error("Riepilogo mattutino senza meteo: %s", errore)
            return None if riprova else messaggi.METEO_NON_DISPONIBILE
        return self._aggiungi_extra_mattino(chat_id, testo)

    def _consegna(self, chat_id: int, testo: str, cosa: str) -> bool:
        try:
            self.messaggero.invia_messaggio(chat_id, testo)
        except ErroreTelegram as errore:
            log.warning("%s non consegnato a %s: %s", cosa, chat_id, errore)
            return False
        return True

    def _riepilogo_per(self, chat_id: int) -> None:
        if testo := self._testo_mattino(chat_id):
            self._consegna(chat_id, testo, "Riepilogo")

    def _aggiungi_extra_mattino(self, chat_id: int, testo: str) -> str:
        """In fondo al riepilogo: i promemoria di oggi e le prime notizie."""
        parti = [testo]
        try:
            oggi = self._oggi_data()
            if voci := RegistroPromemoria(self.archivio, chat_id).del_giorno(oggi):
                parti.append(
                    "⏰ <b>Oggi</b>\n"
                    + "\n".join(f"• {p.quando:%H:%M} {escape(p.testo)}" for p in voci)
                )
            if (
                self.notizie is not None
                and self.archivio.persistente
                and (nuove := self._notizie_nuove(chat_id, NOTIZIE_NEL_MATTINO))
            ):
                parti.append(modulo_notizie.formatta_breve(nuove, "🗞️ <b>Le prime notizie</b>"))
            if self.notizie is not None and self.archivio.persistente:
                coda, _ = self._principali_nuove(chat_id, PRINCIPALI_AL_MATTINO)
                if coda:
                    parti.append(coda.strip())
        except ErroreArchivio as errore:
            log.warning("Extra del mattino saltati: %s", errore)
        except Exception:
            # Un giornale con dati strani non deve far saltare il riepilogo.
            log.exception("Extra del mattino saltati")
        return "\n\n".join(p for p in parti if p)

    def _notizie_nuove(self, chat_id: int, quante: int) -> list[Notizia]:
        """Le notizie non ancora mandate a questa persona: il suo comune, poi la zona."""
        assert self.notizie is not None
        zona = self.zona(chat_id)
        adesso = self.orologio()
        trovate = list(self.notizie.notizie_per(zona.comune, zona.lat, zona.lon, adesso))
        if modulo_notizie.in_zona(zona.lat, zona.lon):
            trovate += self.notizie.del_giorno_in_riviera(zona.comune, adesso)
        nuove = self._mai_viste(chat_id, modulo_notizie.senza_doppioni(trovate))
        scelte = modulo_notizie.prima_quelle_di(nuove, zona.comune)[:quante]
        self._segna_viste(chat_id, scelte)
        return scelte

    def _riassunto_notizie(self, chat_id: int, orari: Orari, adesso: datetime) -> int:
        """Agli orari scelti: un solo messaggio con le notizie nuove (non una per volta)."""
        momento = next(
            (t for t in orari.notizie if in_orario(adesso, t, modulo_orari.FINESTRA_NOTIZIE)),
            None,
        )
        if momento is None:
            return 0
        (nuovo,) = self.archivio.segna_nuovi(
            [f"riassunto:{chat_id}:{adesso.date()}:{momento:%H%M}"], 2 * 24 * 3600
        )
        if not nuovo:
            return 0
        zona = self.zona(chat_id)
        citta = modulo_notizie.citta_del_comune(zona.comune)
        if modulo_notizie.in_zona(zona.lat, zona.lon):
            candidate = self._mai_viste(chat_id, self._tutte_di_oggi(zona.comune, adesso))
            gruppi = modulo_notizie.raggruppa(
                candidate, citta, per_citta=3, nella_prima=4, massimo=NOTIZIE_NEL_RIASSUNTO
            )
        else:
            fuori = self._notizie_nuove_senza_segnare(chat_id, zona, adesso)
            gruppi = [(zona.comune, fuori[:NOTIZIE_NEL_RIASSUNTO])] if fuori else []
            citta = zona.comune
        scelte = [n for _, voci in gruppi for n in voci]
        self._segna_viste(chat_id, scelte)
        coda, principali = self._principali_nuove(chat_id, PRINCIPALI_IN_CODA)
        if not scelte and not principali:
            return 0
        titolo = messaggi.titolo_riassunto(momento)
        testo = modulo_notizie.formatta_per_citta(gruppi, titolo, citta) if gruppi else titolo
        self.messaggero.invia_messaggio(chat_id, testo + coda)
        return len(scelte) + principali

    def controlla_imminente(self) -> int:
        """Pioggia, temporali, neve, raffiche nelle prossime 2 ore, per dove sei."""
        if self.imminente is None or not self._serve_memoria("Avvisi meteo"):
            return 0
        adesso = self.orologio()
        mandati = 0
        for chat_id in self._persone():
            orari = self.orari.di(chat_id)
            if not orari.avvisi_meteo or in_silenzio(orari, adesso):
                continue
            zona = self.zona(chat_id)
            avvisi = valuta(self.imminente.quarti(zona.lat, zona.lon), zona.etichetta, adesso)
            if not avvisi:
                continue
            nuovi_flag = self.archivio.segna_nuovi(
                [f"imminente:{chat_id}:{a.tipo}" for a in avvisi], PAUSA_AVVISI_S
            )
            nuovi = [a for a, nuovo in zip(avvisi, nuovi_flag, strict=True) if nuovo]
            if nuovi:
                try:
                    self.messaggero.invia_messaggio(chat_id, "\n\n".join(a.testo for a in nuovi))
                    mandati += len(nuovi)
                except ErroreTelegram as errore:
                    log.warning("Avviso meteo non consegnato a %s: %s", chat_id, errore)
        return mandati

    def _telefono(self, chat: Chat, comando: Comando) -> None:
        if chat.e_un_gruppo:
            self.messaggero.invia_messaggio(chat.id, modulo_telefono.USO_SOLO_PRIVATO)
            return
        collegamenti = CollegamentiTelefono(self.archivio)
        if comando.testo_argomenti.lower() in {"off", "no", "spegni", "stop"}:
            testo = (
                modulo_telefono.SPENTO if collegamenti.revoca(chat.id) else modulo_telefono.NESSUNO
            )
            self.messaggero.invia_messaggio(chat.id, testo)
            return
        chiave = collegamenti.crea(chat.id)
        indirizzo = self.impostazioni.indirizzo or "https://il-tuo-progetto.vercel.app"
        self.messaggero.invia_messaggio(chat.id, modulo_telefono.istruzioni(indirizzo, chiave))

    # --- Italia e mondo -------------------------------------------------------------------

    def _principali(self, sezione: str, adesso: datetime) -> list[Notizia]:
        """Le notizie nazionali o dal mondo; vuoto se la fonte non le offre (es. nei test)."""
        leggi = getattr(self.notizie, "principali", None)
        return list(leggi(sezione, adesso)) if leggi is not None else []

    def _coda_principali(self, adesso: datetime, quante: int = PRINCIPALI_IN_CODA) -> str:
        """In fondo a /notizie: le prime di Italia e mondo, in forma compatta."""
        parti = []
        for sezione in ("italia", "mondo"):
            if voci := self._principali(sezione, adesso)[:quante]:
                parti.append(
                    modulo_notizie.formatta_breve(voci, messaggi.TITOLI_PRINCIPALI[sezione])
                )
        return "".join(f"\n\n{p}" for p in parti)

    def _principali_nuove(self, chat_id: int, quante: int) -> tuple[str, int]:
        """Italia e mondo non ancora mandate a questa persona (per riassunti e mattino)."""
        adesso = self.orologio()
        parti, totale = [], 0
        for sezione in ("italia", "mondo"):
            voci = self._principali(sezione, adesso)
            if not voci:
                continue
            nuove = self._mai_viste(chat_id, voci)[:quante]
            self._segna_viste(chat_id, nuove)
            if nuove:
                parti.append(
                    modulo_notizie.formatta_breve(nuove, messaggi.TITOLI_PRINCIPALI[sezione])
                )
                totale += len(nuove)
        return "".join(f"\n\n{p}" for p in parti), totale

    def _sezione(self, chat: Chat, sezione: str) -> None:
        voci = self._principali(sezione, self.orologio())[:PRINCIPALI_A_RICHIESTA]
        if not voci:
            self.messaggero.invia_messaggio(chat.id, messaggi.NESSUNA_PRINCIPALE)
            return
        self.messaggero.invia_messaggio(
            chat.id, modulo_notizie.formatta(voci, messaggi.TITOLI_SEZIONE[sezione])
        )

    def _italia(self, chat: Chat, comando: Comando) -> None:
        self._sezione(chat, "italia")

    def _mondo(self, chat: Chat, comando: Comando) -> None:
        self._sezione(chat, "mondo")

    # --- Notizie già viste ------------------------------------------------------------------

    def _mai_viste(self, chat_id: int, notizie: list[Notizia]) -> list[Notizia]:
        """Le notizie mai mandate a questa persona (senza segnarle: si segnano solo quelle
        mostrate davvero, così le altre restano per il riassunto successivo)."""
        if not notizie:
            return []
        flag = self.archivio.sono_nuovi([f"notizia:{chat_id}:{n.impronta}" for n in notizie])
        return [n for n, nuova in zip(notizie, flag, strict=True) if nuova]

    def _segna_viste(self, chat_id: int, notizie: list[Notizia]) -> None:
        if notizie:
            self.archivio.segna_nuovi(
                [f"notizia:{chat_id}:{n.impronta}" for n in notizie], RICORDO_NOTIZIE_S
            )

    def _tutte_di_oggi(self, comune: str, adesso: datetime) -> list[Notizia]:
        """Tutte le notizie locali di oggi (o, per fonti più semplici, quelle del giorno)."""
        assert self.notizie is not None
        tutte = getattr(self.notizie, "tutte_di_oggi", None)
        if tutte is not None:
            return list(tutte(adesso))
        return list(self.notizie.del_giorno_in_riviera(comune, adesso))

    def _notizie_nuove_senza_segnare(
        self, chat_id: int, zona: Zona, adesso: datetime
    ) -> list[Notizia]:
        assert self.notizie is not None
        trovate = self.notizie.notizie_per(zona.comune, zona.lat, zona.lon, adesso)
        return self._mai_viste(chat_id, modulo_notizie.senza_doppioni(trovate))

    # --- Salute: si ripara da solo --------------------------------------------------------

    def _info_webhook(self) -> dict[str, Any] | None:
        info = getattr(self.messaggero, "info_webhook", None)
        if info is None:
            return None
        try:
            return info()
        except ErroreTelegram as errore:
            log.warning("Stato del webhook non disponibile: %s", errore)
            return None

    def controlla_salute(self) -> bool:
        """Se Telegram ha messaggi bloccati per errori recenti, svuota la coda e avvisa.

        Succede se un messaggio ci ha messo troppo: Telegram lo rimanderebbe all'infinito.
        Restituisce ``True`` se ha dovuto riparare qualcosa.
        """
        info = self._info_webhook()
        if not info or not info.get("url") or not self.impostazioni.segreto_webhook:
            return False
        in_coda = int(info.get("pending_update_count") or 0)
        errore_ts = info.get("last_error_date")
        adesso = self.orologio()
        recente = errore_ts and adesso.timestamp() - float(errore_ts) < ERRORE_RECENTE_S
        bloccata_prima = bool(self.archivio.leggi("coda-bloccata"))
        if in_coda == 0 or not recente:
            if bloccata_prima:
                self.archivio.cancella("coda-bloccata")
            return False
        if not bloccata_prima:
            # Al primo giro si aspetta: magari Telegram la sta già smaltendo.
            self.archivio.scrivi("coda-bloccata", in_coda, 3600)
            return False
        self.archivio.cancella("coda-bloccata")
        imposta = getattr(self.messaggero, "imposta_webhook", None)
        if imposta is None:
            return False
        imposta(info["url"], self.impostazioni.segreto_webhook)  # svuota la coda
        log.warning("Coda di Telegram svuotata: %s update bloccati", in_coda)
        padrone = self.impostazioni.chat_personale
        if padrone is not None:
            with contextlib.suppress(ErroreTelegram):
                self.messaggero.invia_messaggio(padrone, messaggi.riparato(in_coda))
        return True

    def _stato(self, chat: Chat, comando: Comando) -> None:
        info = self._info_webhook() or {}
        righe = ["🩺 <b>Come sto</b>", ""]
        versione = os.environ.get("VERCEL_GIT_COMMIT_SHA", "")[:7] or "locale"
        righe.append(f"🏷️ Versione: <code>{versione}</code>")
        righe.append(
            "🧠 Memoria (Upstash): " + ("✅ collegata" if self.archivio.persistente else "⚠️ spenta")
        )
        if info:
            in_coda = int(info.get("pending_update_count") or 0)
            righe.append(f"📨 Messaggi in coda su Telegram: {in_coda}")
            if errore := info.get("last_error_message"):
                quando = datetime.fromtimestamp(
                    float(info.get("last_error_date") or 0), FUSO_ORARIO
                )
                righe.append(f"⚠️ Ultimo errore: {escape(errore)} ({quando:%d/%m %H:%M})")
            else:
                righe.append("✅ Nessun errore recente")
        righe.append(self._stato_sveglia())
        if (menu := self._stato_menu()) is not None:
            righe.append(menu)
        self.messaggero.invia_messaggio(chat.id, "\n".join(righe))

    def _stato_sveglia(self) -> str:
        try:
            ultimo = self.archivio.leggi("ultimo-giro")
        except ErroreArchivio:
            ultimo = None
        if not ultimo:
            return "⏰ Sveglia (cron-job.org): ⚠️ nessun giro registrato"
        quando = datetime.fromisoformat(ultimo).astimezone(FUSO_ORARIO)
        minuti = int((self.orologio() - quando).total_seconds() // 60)
        segno = "✅" if minuti <= 30 else "⚠️"
        return f"⏰ Sveglia: {segno} ultimo giro alle {quando:%H:%M} ({minuti} min fa)"

    def _stato_menu(self) -> str | None:
        leggi = getattr(self.messaggero, "leggi_comandi", None)
        if leggi is None:
            return None
        attesi = [nome for nome, _ in COMANDI]
        try:
            if leggi() == attesi:
                return f"📋 Menu comandi: ✅ aggiornato ({len(attesi)} comandi)"
            self.controlla_menu(forza=True)
        except (ErroreTelegram, ErroreArchivio) as errore:
            return f"📋 Menu comandi: ⚠️ non riesco ad aggiornarlo ({escape(str(errore))})"
        return f"📋 Menu comandi: 🔧 era vecchio, l'ho appena aggiornato ({len(attesi)} comandi)"

    def controlla_menu(self, forza: bool = False) -> bool:
        """Tiene aggiornato il menu dei comandi di Telegram (quello che appare scrivendo /).

        Ogni 6 ore (o subito dopo una nuova versione) chiede a Telegram che menu mostra e,
        se non è quello giusto, lo reimposta. Restituisce ``True`` se l'ha cambiato.
        """
        imposta = getattr(self.messaggero, "imposta_comandi", None)
        if imposta is None:
            return False
        impronta = hashlib.sha256(repr(COMANDI).encode("utf-8")).hexdigest()[:16]
        if not forza and self.archivio.leggi("menu-comandi") == impronta:
            return False
        leggi = getattr(self.messaggero, "leggi_comandi", None)
        if not forza and leggi is not None:
            try:
                giusto = leggi() == [nome for nome, _ in COMANDI]
            except ErroreTelegram:
                giusto = False
            if giusto:
                self.archivio.scrivi("menu-comandi", impronta, VERIFICA_MENU_S)
                return False
        imposta(COMANDI)
        self.archivio.scrivi("menu-comandi", impronta, VERIFICA_MENU_S)
        log.info("Menu dei comandi aggiornato (%s comandi)", len(COMANDI))
        return True


def _errore_definitivo(errore: ErroreTelegram) -> bool:
    """Bot bloccato o chat sparita: inutile riprovare."""
    testo = str(errore).lower()
    return any(s in testo for s in ("forbidden", "chat not found", "blocked", "deactivated"))
