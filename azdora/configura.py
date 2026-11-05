"""Collega il bot al suo indirizzo su Vercel e imposta il menu dei comandi.

Si lancia una volta sola (dal workflow "Configura bot" su GitHub Actions, o in locale)::

    python -m azdora.configura --url https://azdora-bot.vercel.app

Legge ``TELEGRAM_BOT_TOKEN`` e ``TELEGRAM_WEBHOOK_SECRET`` dall'ambiente.
"""

from __future__ import annotations

import argparse
import sys

from azdora.app import configura_log, nuovo_client_http
from azdora.bot import COMANDI
from azdora.config import ConfigurazioneErrata, Impostazioni
from azdora.telegram import Telegram


def indirizzo_webhook(url_base: str) -> str:
    url_base = url_base.strip().rstrip("/")
    if not url_base.startswith("https://"):
        raise ConfigurazioneErrata("Telegram accetta solo indirizzi https://")
    if url_base.endswith("/api/webhook"):
        return url_base
    return f"{url_base}/api/webhook"


def main(argomenti: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configura webhook e comandi del bot.")
    parser.add_argument(
        "--url", help="indirizzo del progetto Vercel, es. https://azdora-bot.vercel.app"
    )
    parser.add_argument("--solo-info", action="store_true", help="mostra lo stato senza cambiarlo")
    scelte = parser.parse_args(argomenti)

    configura_log()
    impostazioni = Impostazioni.da_env()
    with nuovo_client_http() as http:
        telegram = Telegram(impostazioni.richiedi_token(), http)
        bot = telegram.chi_sono()
        print(f"Bot: @{bot.get('username')} ({bot.get('first_name')})")

        if not scelte.solo_info:
            if not scelte.url:
                parser.error("serve --url (oppure --solo-info)")
            if not impostazioni.segreto_webhook:
                raise ConfigurazioneErrata("Manca TELEGRAM_WEBHOOK_SECRET")
            url = indirizzo_webhook(scelte.url)
            telegram.imposta_webhook(url, impostazioni.segreto_webhook)
            telegram.imposta_comandi(COMANDI)
            print(f"Webhook impostato su {url} e menu comandi aggiornato.")

        info = telegram.info_webhook()
        print(f"Webhook attuale: {info.get('url') or '(nessuno)'}")
        print(f"Update in attesa: {info.get('pending_update_count', 0)}")
        if info.get("last_error_message"):
            print(f"Ultimo errore: {info['last_error_message']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
