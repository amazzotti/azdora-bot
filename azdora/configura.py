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
from azdora.telegram import ErroreTelegram, Telegram


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

    if not scelte.solo_info and not scelte.url:
        parser.error("serve --url (oppure --solo-info)")

    configura_log()
    try:
        _configura(Impostazioni.da_env(), scelte.url, scelte.solo_info)
    except (ConfigurazioneErrata, ErroreTelegram) as errore:
        print(f"❌ {errore}")
        print(consiglio(str(errore)))
        return 1
    return 0


def consiglio(errore: str) -> str:
    """Cosa controllare, in italiano, per gli errori più comuni."""
    if "TELEGRAM_BOT_TOKEN" in errore:
        return (
            "👉 Su GitHub manca il secret TELEGRAM_BOT_TOKEN: Settings → Secrets and variables "
            "→ Actions → New repository secret (nome tutto maiuscolo, con i trattini bassi)."
        )
    if "TELEGRAM_WEBHOOK_SECRET" in errore:
        return "👉 Su GitHub manca il secret TELEGRAM_WEBHOOK_SECRET (lo stesso messo su Vercel)."
    if "Unauthorized" in errore or "Not Found" in errore:
        return (
            "👉 Telegram non riconosce il token: ricopialo da @BotFather (/mybots → il bot → "
            "API Token) senza spazi né virgolette e aggiorna il secret TELEGRAM_BOT_TOKEN."
        )
    if "secret token" in errore.lower():
        return (
            "👉 Il segreto del webhook può contenere solo lettere, numeri, _ e -: "
            "generane uno nuovo e aggiornalo sia su GitHub sia su Vercel."
        )
    if "https" in errore:
        return "👉 Scrivi l'indirizzo completo, es. https://azdora-bot.vercel.app"
    return "👉 Controlla i secret su GitHub e l'indirizzo di Vercel, poi rilancia il workflow."


def _configura(impostazioni: Impostazioni, url_base: str | None, solo_info: bool) -> None:
    token = impostazioni.richiedi_token()
    if not solo_info and not impostazioni.segreto_webhook:
        raise ConfigurazioneErrata("Manca TELEGRAM_WEBHOOK_SECRET")
    url = indirizzo_webhook(url_base) if not solo_info and url_base else None
    with nuovo_client_http() as http:
        telegram = Telegram(token, http)
        bot = telegram.chi_sono()
        print(f"Bot: @{bot.get('username')} ({bot.get('first_name')})")

        if url is not None:
            telegram.imposta_webhook(url, impostazioni.segreto_webhook)
            telegram.imposta_comandi(COMANDI)
            print(f"✅ Webhook impostato su {url} e menu comandi aggiornato.")

        info = telegram.info_webhook()
        print(f"Webhook attuale: {info.get('url') or '(nessuno)'}")
        print(f"Update in attesa: {info.get('pending_update_count', 0)}")
        if info.get("last_error_message"):
            print(f"Ultimo errore: {info['last_error_message']}")


if __name__ == "__main__":
    sys.exit(main())
