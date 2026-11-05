"""Funzione Vercel: ``/api/cron?job=mattino`` viene chiamata da cron-job.org agli orari fissati.

Serve l'header ``Authorization: Bearer <CRON_SECRET>``.
"""

import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from azdora.vercel import esegui, leggi_parametri
from azdora.web import gestisci_cron


class handler(BaseHTTPRequestHandler):  # nome imposto da Vercel
    def _gestisci(self) -> None:
        parametri = leggi_parametri(self)
        esegui(
            self,
            lambda impostazioni, crea_bot: gestisci_cron(
                self.headers, parametri, impostazioni, crea_bot
            ),
        )

    def do_GET(self) -> None:
        self._gestisci()

    def do_POST(self) -> None:
        self._gestisci()
