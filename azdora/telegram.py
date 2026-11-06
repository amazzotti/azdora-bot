"""Client minimale per la Bot API di Telegram (https://core.telegram.org/bots/api).

Usa solo i metodi che servono al bot, chiamati con ``httpx``: niente framework,
così il codice gira uguale su Vercel, su GitHub Actions e in locale.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from html import escape, unescape
from typing import Any, Protocol

import httpx

URL_API = "https://api.telegram.org"

# Telegram rifiuta i messaggi più lunghi di 4096 caratteri.
LUNGHEZZA_MASSIMA = 4096
# Si resta un po' sotto il limite, per sicurezza.
LUNGHEZZA_PARTE = 3800
_TAG = re.compile(r"<[^>]+>")


def testo_visibile(testo: str) -> str:
    """Il testo come lo vede chi legge: senza tag HTML e senza gli indirizzi dei link.

    Telegram conta questo per il limite di 4096 caratteri, non l'HTML: i link di Google
    News sono lunghissimi ma non contano.
    """
    return unescape(_TAG.sub("", testo))


def dividi(testo: str, limite: int = LUNGHEZZA_PARTE) -> list[str]:
    """Divide un messaggio troppo lungo in più parti, sempre a fine riga (i tag dei
    messaggi dell'Azdora non vanno mai a capo, quindi nessun tag resta spezzato)."""
    if len(testo_visibile(testo)) <= limite:
        return [testo]
    parti: list[str] = []
    attuale: list[str] = []
    lunghezza = 0
    for riga in _righe_corte(testo.split("\n"), limite):
        quanto = len(testo_visibile(riga)) + 1
        if attuale and lunghezza + quanto > limite:
            parti.append("\n".join(attuale).strip("\n"))
            attuale, lunghezza = [], 0
        attuale.append(riga)
        lunghezza += quanto
    if attuale:
        parti.append("\n".join(attuale).strip("\n"))
    return [p for p in parti if p.strip()]


def _righe_corte(righe: list[str], limite: int) -> list[str]:
    """Una riga da sola più lunga del limite (succede solo con testo scritto da qualcuno,
    es. una nota lunghissima) viene tagliata a pezzi come testo semplice, senza tag."""
    risultato: list[str] = []
    for riga in righe:
        visibile = testo_visibile(riga)
        if len(visibile) <= limite:
            risultato.append(riga)
            continue
        risultato += [
            escape(visibile[i : i + limite], quote=False) for i in range(0, len(visibile), limite)
        ]
    return risultato


class ErroreTelegram(RuntimeError):
    """La Bot API ha risposto con ``ok: false`` o non è raggiungibile."""


class Messaggero(Protocol):
    """Quello che serve al bot per parlare: Telegram vero, o la console nel simulatore."""

    def invia_messaggio(
        self, chat_id: int, testo: str, tastiera: dict[str, Any] | None = None
    ) -> None: ...

    def esci_da_chat(self, chat_id: int) -> None: ...

    def rispondi_pulsante(self, id_pulsante: str, testo: str | None = None) -> None:
        """Conferma il tocco su un pulsante (altrimenti Telegram mostra la clessidra)."""
        ...

    def modifica_messaggio(
        self, chat_id: int, id_messaggio: int, testo: str, tastiera: dict[str, Any] | None = None
    ) -> None: ...

    def invia_documento(
        self, chat_id: int, nome_file: str, contenuto: bytes, didascalia: str = ""
    ) -> None: ...


# Tipi di update che chiediamo a Telegram: messaggi, posizione in tempo reale (che arriva
# come messaggio modificato), tocchi sui pulsanti e ingresso/uscita dai gruppi.
UPDATE_CONSENTITI = ["message", "edited_message", "callback_query", "my_chat_member"]

# Il menu dei comandi si imposta per tutti gli "ambiti" e anche per l'italiano: Telegram
# mostra la lista più specifica che trova (es. "chat private in italiano" vince su quella
# generica), quindi una lista vecchia rimasta lì nasconderebbe quella nuova.
AMBITI_COMANDI: tuple[dict[str, str], ...] = (
    {"type": "default"},
    {"type": "all_private_chats"},
    {"type": "all_group_chats"},
)
LINGUE_COMANDI: tuple[str | None, ...] = (None, "it")


class Telegram:
    def __init__(self, token: str, http: httpx.Client) -> None:
        if not token:
            raise ValueError("Serve il token del bot")
        self._url_base = f"{URL_API}/bot{token}"
        self._http = http

    def chiama(self, metodo: str, **parametri: Any) -> Any:
        """Chiama un metodo della Bot API e restituisce il campo ``result``."""
        try:
            risposta = self._http.post(f"{self._url_base}/{metodo}", json=parametri)
            dati = risposta.json()
        except (httpx.HTTPError, ValueError):
            # Il messaggio di httpx contiene l'URL, e quindi il token: non lo propaghiamo.
            raise ErroreTelegram(f"{metodo}: Telegram non raggiungibile") from None
        if not isinstance(dati, dict) or not dati.get("ok"):
            descrizione = dati.get("description") if isinstance(dati, dict) else None
            raise ErroreTelegram(f"{metodo}: {descrizione or 'risposta non valida'}")
        return dati.get("result")

    def invia_messaggio(
        self, chat_id: int, testo: str, tastiera: dict[str, Any] | None = None
    ) -> None:
        """Manda un messaggio HTML; ``tastiera`` è un ``reply_markup`` della Bot API.

        Se è troppo lungo lo divide in più messaggi (la tastiera va sull'ultimo). Se Telegram
        rifiuta l'HTML, lo rimanda come testo semplice: meglio brutto che niente.
        """
        parti = dividi(testo)
        for numero, parte in enumerate(parti):
            parametri: dict[str, Any] = {
                "chat_id": chat_id,
                "text": parte,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            }
            if tastiera is not None and numero == len(parti) - 1:
                parametri["reply_markup"] = tastiera
            try:
                self.chiama("sendMessage", **parametri)
            except ErroreTelegram as errore:
                if "can't parse entities" not in str(errore).lower():
                    raise
                del parametri["parse_mode"]
                parametri["text"] = testo_visibile(parte)[:LUNGHEZZA_MASSIMA]
                self.chiama("sendMessage", **parametri)

    def esci_da_chat(self, chat_id: int) -> None:
        self.chiama("leaveChat", chat_id=chat_id)

    def rispondi_pulsante(self, id_pulsante: str, testo: str | None = None) -> None:
        parametri: dict[str, Any] = {"callback_query_id": id_pulsante}
        if testo:
            parametri["text"] = testo
        self.chiama("answerCallbackQuery", **parametri)

    def modifica_messaggio(
        self, chat_id: int, id_messaggio: int, testo: str, tastiera: dict[str, Any] | None = None
    ) -> None:
        parametri: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": id_messaggio,
            "text": dividi(testo)[0],
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if tastiera is not None:
            parametri["reply_markup"] = tastiera
        try:
            self.chiama("editMessageText", **parametri)
        except ErroreTelegram as errore:
            # Toccare due volte lo stesso pulsante non cambia il testo: non è un vero errore.
            if "message is not modified" not in str(errore):
                raise

    def invia_documento(
        self, chat_id: int, nome_file: str, contenuto: bytes, didascalia: str = ""
    ) -> None:
        """Manda un file (es. il CSV delle spese). Qui serve multipart, non JSON."""
        try:
            risposta = self._http.post(
                f"{self._url_base}/sendDocument",
                data={"chat_id": str(chat_id), "caption": didascalia, "parse_mode": "HTML"},
                files={"document": (nome_file, contenuto, "text/csv")},
            )
            dati = risposta.json()
        except (httpx.HTTPError, ValueError):
            raise ErroreTelegram("sendDocument: Telegram non raggiungibile") from None
        if not isinstance(dati, dict) or not dati.get("ok"):
            descrizione = dati.get("description") if isinstance(dati, dict) else None
            raise ErroreTelegram(f"sendDocument: {descrizione or 'risposta non valida'}")

    def imposta_webhook(self, url: str, segreto: str) -> None:
        self.chiama(
            "setWebhook",
            url=url,
            secret_token=segreto,
            allowed_updates=UPDATE_CONSENTITI,
            drop_pending_updates=True,
        )

    def info_webhook(self) -> dict[str, Any]:
        return self.chiama("getWebhookInfo")

    def imposta_comandi(self, comandi: Iterable[tuple[str, str]]) -> None:
        lista = [{"command": nome, "description": descr} for nome, descr in comandi]
        for ambito in AMBITI_COMANDI:
            for lingua in LINGUE_COMANDI:
                parametri: dict[str, Any] = {"commands": lista, "scope": ambito}
                if lingua:
                    parametri["language_code"] = lingua
                self.chiama("setMyCommands", **parametri)

    def leggi_comandi(self) -> list[str]:
        """I comandi che vede adesso chi scrive al bot in privato, con Telegram in italiano."""
        risultato = self.chiama(
            "getMyCommands", scope={"type": "all_private_chats"}, language_code="it"
        )
        return [c.get("command", "") for c in risultato or []]

    def chi_sono(self) -> dict[str, Any]:
        return self.chiama("getMe")
