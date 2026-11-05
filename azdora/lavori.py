"""Lavori lanciabili da riga di comando, ad esempio da GitHub Actions.

Uso::

    python -m azdora.lavori mattino
"""

from __future__ import annotations

import argparse
import sys

from azdora.app import configura_log, crea_azdora, nuovo_client_http
from azdora.config import Impostazioni
from azdora.web import LAVORI


def main(argomenti: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Esegue un lavoro programmato dell'Azdora.")
    parser.add_argument("lavoro", choices=sorted(LAVORI), help="il lavoro da eseguire")
    scelte = parser.parse_args(argomenti)

    configura_log()
    impostazioni = Impostazioni.da_env()
    with nuovo_client_http() as http:
        LAVORI[scelte.lavoro](crea_azdora(impostazioni, http))
    print(f"Lavoro '{scelte.lavoro}' completato.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
