"""Adattatore tra le funzioni serverless di Vercel (``api/*.py``) e la logica in ``web.py``."""

from __future__ import annotations

from collections.abc import Callable
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qsl, urlsplit

from azdora.app import configura_log, crea_azdora, nuovo_client_http
from azdora.bot import Azdora
from azdora.config import Impostazioni
from azdora.web import Risposta


def rispondi(richiesta: BaseHTTPRequestHandler, risposta: Risposta) -> None:
    corpo = risposta.json()
    richiesta.send_response(risposta.stato)
    richiesta.send_header("Content-Type", "application/json; charset=utf-8")
    richiesta.send_header("Content-Length", str(len(corpo)))
    richiesta.end_headers()
    richiesta.wfile.write(corpo)


def esegui(
    richiesta: BaseHTTPRequestHandler,
    elabora: Callable[[Impostazioni, Callable[[], Azdora]], Risposta],
) -> None:
    """Prepara impostazioni e client HTTP, chiama ``elabora`` e scrive la risposta."""
    configura_log()
    impostazioni = Impostazioni.da_env()
    with nuovo_client_http() as http:
        risposta = elabora(impostazioni, lambda: crea_azdora(impostazioni, http))
    rispondi(richiesta, risposta)


def leggi_corpo(richiesta: BaseHTTPRequestHandler) -> bytes:
    lunghezza = int(richiesta.headers.get("Content-Length") or 0)
    return richiesta.rfile.read(lunghezza) if lunghezza > 0 else b""


def leggi_parametri(richiesta: BaseHTTPRequestHandler) -> dict[str, str]:
    return dict(parse_qsl(urlsplit(richiesta.path).query))
