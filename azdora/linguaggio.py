"""Messaggi scritti come si parla, senza comandi.

- Spese: "45 benzina", "benzina 45", "ho speso 12,50 per la pizza", "pagato 80€ di bolletta".
- Promemoria: "ricordami domani alle 18 di chiamare il meccanico" (vedi ``promemoria.py``).

Non c'è nessuna intelligenza artificiale: solo regole chiare, così è gratis, veloce e
prevedibile.
"""

from __future__ import annotations

import re

from azdora.soldi import leggi_importo

_FRASI_INIZIALI = re.compile(
    r"^(?:oggi\s+|ieri\s+)?(?:ho\s+)?(?:speso|spesi|pagato|pagata|comprato|preso|"
    r"fatto)\b\s*",
    re.IGNORECASE,
)
_PAROLE_DI_TROPPO = re.compile(
    r"^(?:euro|eur|€|di|per|il|lo|la|i|gli|le|un|uno|una|al|allo|alla|ai|dal|dalla|da|"
    r"in|nel|nella)\s+",
    re.IGNORECASE,
)
_SPAZIO_EURO = re.compile(r"(\d)\s+(€|euro|eur)\b", re.IGNORECASE)
MASSIMO_PAROLE = 10


def leggi_spesa_libera(testo: str) -> tuple[int, str] | None:
    """Riconosce una spesa in una frase: (centesimi, descrizione), oppure ``None``.

    Deve esserci **un solo** numero che sia un importo, e qualche parola: così "ci vediamo
    alle 8 e 30" non diventa una spesa.
    """
    frase = _SPAZIO_EURO.sub(r"\1\2", testo.strip())
    parole = frase.split()
    if not parole or len(parole) > MASSIMO_PAROLE:
        return None
    importi = [(i, leggi_importo(p)) for i, p in enumerate(parole)]
    trovati = [(i, v) for i, v in importi if v is not None and v > 0]
    if len(trovati) != 1:
        return None
    indice, centesimi = trovati[0]
    resto = " ".join(parole[:indice] + parole[indice + 1 :])
    resto = _FRASI_INIZIALI.sub("", resto).strip()
    while (pulito := _PAROLE_DI_TROPPO.sub("", resto)) != resto:
        resto = pulito
    resto = resto.strip(" ,.;:!")
    if not resto or not re.search(r"[a-zà-ù]", resto, re.IGNORECASE):
        return None
    return centesimi, resto
