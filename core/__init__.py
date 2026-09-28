"""
argo-core — fondamenta per suite gestionali Flask + SQLite su un PC qualunque.

Nessuna dipendenza obbligatoria oltre la libreria standard: Flask (e werkzeug,
che arriva con esso) serve solo dove si serve web ed e' importato lazy.
`import core` resta stdlib-only.

Due livelli (ADR-000). Criterio: nel kernel solo cio' che deve dare la stessa
risposta a tutti i moduli.

KERNEL — obbligatorio per un modulo conforme:
    config       configurazione TOML fail-fast (+ config di suite argo.toml)
    db           connessioni owned / readonly / attach_readonly
    migrate      helper di migrazione additivi (ensure_table, ensure_column, ...)
    codes        normalizzazione dei codici (registro + regole dichiarative)
    manifest     manifest.toml dei moduli: validazione + scansione (ADR-003)
    migrazioni   passi numerati + backup prima di migrare (ADR-004, CLI)
    busta        la busta standard di ogni log (ADR-005)
    events       log di stati sulla busta + proiezione
    auth         identita' centrale, sessione condivisa, permessi (ADR-001, CLI)
    anagrafica   entita' condivise con ID stabile, alias, fusioni (ADR-002, CLI)
    registro     registro dei moduli e menu per permessi (core.sqlite)

UTILITY — opt-in, si importano per nome (`from core import board`):
    statemachine, shifts, schedule, forms, board, inventory, export, notify,
    adminbrowser (richiede Flask), scaffold (python -m core.scaffold)

APPLICAZIONI — processi costruiti sul kernel:
    shell        login unico, cornice, menu, amministrazione (python -m core.shell)
    portal       alias deprecato di shell

`import core` carica solo il kernel: config, db, migrate, codes, manifest,
busta, events subito; migrazioni, auth, anagrafica, registro al primo accesso
(`core.auth`), perche' hanno una CLI (`python -m core.auth`) e caricarli qui
li farebbe importare due volte. Le librerie del kernel non importano mai le
utility (tests/test_architettura.py lo verifica).

Uso da un modulo della suite (nessuna installazione richiesta):

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from core import auth, db, events, manifest, migrazioni
"""
__version__ = "1.0.0"

KERNEL = ("config", "db", "migrate", "codes", "manifest", "migrazioni",
          "auth", "anagrafica", "busta", "events", "registro")
UTILITY = ("statemachine", "shifts", "schedule", "forms", "board",
           "inventory", "export", "notify", "adminbrowser", "scaffold")
APPLICAZIONI = ("shell", "portal")

from . import busta, codes, config, db, events, manifest, migrate  # noqa: F401,E402

_KERNEL_LAZY = ("migrazioni", "auth", "anagrafica", "registro")


def __getattr__(nome: str):
    """Kernel con CLI caricato al primo accesso; le utility non si caricano
    da sole (ADR-000): vanno importate per nome."""
    if nome in _KERNEL_LAZY:
        import importlib
        return importlib.import_module(f".{nome}", __name__)
    if nome in UTILITY or nome in APPLICAZIONI:
        raise AttributeError(f"core.{nome} non e' caricato da `import core` "
                             f"(utility, ADR-000): usa `from core import {nome}`")
    raise AttributeError(f"module 'core' has no attribute {nome!r}")
