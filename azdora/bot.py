"""Il cervello del bot: riceve un update di Telegram e decide cosa rispondere.

Non sa nulla di HTTP né di Vercel: riceve dall'esterno il messaggero (chi spedisce i
messaggi), la fonte meteo, i luoghi, l'archivio, le notizie e le allerte. Così nei test
si sostituiscono con dei finti e nel simulatore con la console.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape
from typing import Any

from azdora import allerte as modulo_allerte
from azdora import lista as modulo_lista
from azdora import messaggi
from azdora import notizie as modulo_notizie
from azdora import scadenze as modulo_scadenze
from azdora import spese as modulo_spese
from azdora import weekend as modulo_weekend
from azdora.allerte import FonteAllerte
from azdora.archivio import Archivio, ArchivioMemoria, ErroreArchivio
from azdora.config import (
    FUSO_ORARIO,
    LOCALITA,
    RICCIONE,
    Impostazioni,
    Localita,
    trova_localita,
)
from azdora.lista import Lista, leggi_voci
from azdora.luoghi import CercaLuoghi, Luogo, distanza_km, localita_qui
from azdora.meteo.openmeteo import ErroreMeteo, FonteMeteo, PrevisioniMare
from azdora.notizie import FonteNotizie, Notizia
from azdora.posizione import (
    SPOSTAMENTO_MINIMO_KM,
    VALIDITA_ZONA,
    ZONA_CASA,
    MemoriaPosizione,
    Posizione,
    Zona,
)
from azdora.riepilogo import (
    DatiLocalita,
    riepilogo_domani,
    riepilogo_oggi,
    riepilogo_posizione,
)
from azdora.scadenze import RegistroScadenze, leggi_scadenza
from azdora.soldi import aggiungi_mesi, euro, leggi_mese
from azdora.spese import RegistroSpese, leggi_spesa
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
RICORDO_NOTIZIE_S = 3 * 24 * 3600
RICORDO_ZONA_S = 30 * 24 * 3600


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
        comando = leggi_comando(messaggio.get("text"))
        if comando is not None:
            mittente = (messaggio.get("from") or {}).get("first_name", "")
            comando = Comando(comando.nome, comando.argomenti, comando.destinatario, mittente)
        posizione = messaggio.get("location")
        if comando is None and posizione is None:
            return  # Testo libero: per ora l'Azdora risponde solo a comandi e posizioni.
        if comando is not None and self._non_per_me(comando):
            return

        if not self._e_autorizzata(chat.id):
            self._chat_sconosciuta(chat, messaggio, comando)
            return
        if not chat.e_un_gruppo:
            self._ricorda_nome(chat.id, (messaggio.get("from") or {}).get("first_name", ""))

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
        except ErroreArchivio as errore:
            log.warning("Archivio non disponibile: %s", errore)
            self.messaggero.invia_messaggio(chat.id, messaggi.ARCHIVIO_NON_DISPONIBILE)

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
        testo = comando.testo_argomenti
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
            self.posizioni.salva(chat_id, self._nuova_posizione(lat, lon, luogo, True))
        except ErroreArchivio as errore:
            log.warning("Posizione in tempo reale non salvata: %s", errore)

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
        trovate = self.notizie.notizie_per(comune, lat, lon, self.orologio())
        if not trovate:
            self.messaggero.invia_messaggio(chat.id, messaggi.nessuna_notizia(comune))
            return
        testo = modulo_notizie.formatta(
            trovate[:MASSIMO_NOTIZIE], messaggi.titolo_notizie(comune, nuove=False)
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
            try:
                testo = self.testo_oggi(
                    saluta=True,
                    zona=self._zona_sicura(chat_id),
                    nome=self.nome_di(chat_id),
                )
            except ErroreMeteo as errore:
                log.error("Riepilogo mattutino senza meteo: %s", errore)
                testo = messaggi.METEO_NON_DISPONIBILE
            try:
                self.messaggero.invia_messaggio(chat_id, testo)
            except ErroreTelegram as errore:
                # Es. un familiare ha bloccato il bot: gli altri lo ricevono lo stesso.
                log.warning("Riepilogo non consegnato a %s: %s", chat_id, errore)
        for chat in sorted(self.impostazioni.chat_autorizzate, reverse=True):
            try:
                self.invia_promemoria_scadenze(chat)
                if self.orologio().day == 1:
                    self.invia_resoconto_mese_scorso(chat)
            except (ErroreArchivio, ErroreTelegram) as errore:
                log.warning("Promemoria e resoconto saltati per %s: %s", chat, errore)

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
        registro = RegistroSpese(self.archivio, chat.id)
        oggi = self._oggi_data()
        spesa = registro.aggiungi(oggi, importo, descrizione)
        mese = modulo_spese.totale(registro.del_mese(oggi))
        self.messaggero.invia_messaggio(
            chat.id, messaggi.spesa_segnata(spesa.importo, spesa.categoria, descrizione, mese)
        )

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
            giorni.get(sabato), giorni.get(domenica), sabato, eventi
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
        """Il giro di controlli periodico: allerte prima, poi notizie."""
        for controllo in (self.controlla_allerte, self.controlla_notizie):
            try:
                controllo()
            except (ErroreArchivio, ErroreMeteo) as errore:
                log.warning("Controllo %s saltato: %s", controllo.__name__, errore)
