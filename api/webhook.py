"""Funzione Vercel: ``POST /api/webhook`` riceve i messaggi da Telegram."""

import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

# Su Vercel la cartella del progetto non è sempre nel path di import.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from azdora.vercel import esegui, leggi_corpo, rispondi
from azdora.web import Risposta, gestisci_webhook


class handler(BaseHTTPRequestHandler):  # nome imposto da Vercel
    def do_POST(self) -> None:
        corpo = leggi_corpo(self)
        esegui(
            self,
            lambda impostazioni, crea_bot: gestisci_webhook(
                self.headers, corpo, impostazioni, crea_bot
            ),
        )

    def do_GET(self) -> None:
        # Controllo veloce dal browser: risponde senza toccare Telegram.
        rispondi(self, Risposta(200, {"ok": True, "servizio": "azdora-bot"}))
