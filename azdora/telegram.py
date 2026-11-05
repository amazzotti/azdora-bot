"""Client minimale per la Bot API di Telegram (https://core.telegram.org/bots/api).

Usa solo i metodi che servono al bot, chiamati con ``httpx``: niente framework,
così il codice gira uguale su Vercel, su GitHub Actions e in locale.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

import httpx

URL_API = "https://api.telegram.org"

# Telegram rifiuta i messaggi più lunghi di 4096 caratteri.
LUNGHEZZA_MASSIMA = 4096


class ErroreTelegram(RuntimeError):
    """La Bot API ha risposto con ``ok: false`` o non è raggiungibile."""


class Messaggero(Protocol):
    """Quello che serve al bot per parlare: Telegram vero, o la console nel simulatore."""

    def invia_messaggio(self, chat_id: int, testo: str) -> None: ...

    def esci_da_chat(self, chat_id: int) -> None: ...


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

    def invia_messaggio(self, chat_id: int, testo: str) -> None:
        self.chiama(
            "sendMessage",
            chat_id=chat_id,
            text=testo[:LUNGHEZZA_MASSIMA],
            parse_mode="HTML",
            link_preview_options={"is_disabled": True},
        )

    def esci_da_chat(self, chat_id: int) -> None:
        self.chiama("leaveChat", chat_id=chat_id)

    def imposta_webhook(self, url: str, segreto: str) -> None:
        self.chiama(
            "setWebhook",
            url=url,
            secret_token=segreto,
            allowed_updates=["message", "my_chat_member"],
            drop_pending_updates=True,
        )

    def info_webhook(self) -> dict[str, Any]:
        return self.chiama("getWebhookInfo")

    def imposta_comandi(self, comandi: Iterable[tuple[str, str]]) -> None:
        self.chiama(
            "setMyCommands",
            commands=[{"command": nome, "description": descr} for nome, descr in comandi],
        )

    def chi_sono(self) -> dict[str, Any]:
        return self.chiama("getMe")
