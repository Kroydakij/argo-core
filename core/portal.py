"""
core.portal — DEPRECATO dalla 1.0: il portale e' diventato la shell (ADR-001).

Usa `python -m core.shell` e `from core import shell`. Questo alias resta per
una minor e poi sparisce. Cosa e' cambiato rispetto alla 0.x:
  - login unico con sessione (niente piu' Basic Auth ARGO_PORTAL_USER/PASS);
  - config in comune/argo.toml (portal.json non e' piu' letto);
  - registro moduli in core.registro (lista_moduli, upsert_modulo, ...).
"""
import warnings

from .registro import (lista_moduli, prossima_porta,  # noqa: F401
                       registra_scansione, toggle_modulo, upsert_modulo)
from .shell import (PORTA_DEFAULT, PRIMA_PORTA_MODULI,  # noqa: F401
                    check_salute, create_app, main)

warnings.warn("core.portal e' deprecato: usa core.shell", DeprecationWarning,
              stacklevel=2)

if __name__ == "__main__":
    raise SystemExit(main())
