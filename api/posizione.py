"""Funzione Vercel: ``POST /api/posizione`` riceve la posizione dal telefono (vedi /telefono)."""

import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from azdora.vercel import esegui, leggi_corpo
from azdora.web import gestisci_posizione


class handler(BaseHTTPRequestHandler):  # nome imposto da Vercel
    def do_POST(self) -> None:
        corpo = leggi_corpo(self)
        esegui(
            self,
            lambda impostazioni, crea_bot: gestisci_posizione(self.headers, corpo, crea_bot),
        )
