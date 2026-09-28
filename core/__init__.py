"""
argo-core — fondamenta per suite gestionali Flask + SQLite su un PC qualunque.

Nessuna dipendenza obbligatoria oltre la libreria standard: Flask (e werkzeug,
che arriva con esso) serve solo dove si serve web ed e' importato lazy. Import
di `core` resta stdlib-only.

Moduli (Fase 0-1):
    db        connessioni owned / readonly con impostazioni uniformi
    migrate   migrazioni additive (ensure_table, ensure_column, rebuild_views)
    codes     registro delle normalizzazioni codici (unico punto)
    notify    email SMTP con log append-only opzionale
    schedule  scheduler a tempo di lettura (funzione pura)
    export    CSV per Excel locale italiano
    adminbrowser  blueprint browser DB read-only (richiede Flask: import esplicito)
    portal    alias deprecato di core.shell (ADR-001)

Moduli (Fase 2):
    config       configurazione TOML fail-fast
    events       layer event-sourced (log append-only + latest_state_per_entity)
    statemachine macchina a stati dichiarativa (pura)
    shifts       turni parametrici a tempo di lettura (pura)
    forms        form-engine dichiarativo (validazione + render)
    auth         identita' centrale, sessione condivisa, permessi (ADR-001)
                 (import esplicito: from core import auth; ha una CLI)
    board        board (kanban) config-driven
    inventory    inventario generico event-sourced (anagrafica + movimenti + giacenze)
    scaffold     generatore di scheletri di moduli (python -m core.scaffold)

Kernel 1.0 (in costruzione, vedi docs/adr/):
    migrazioni   passi di migrazione numerati + backup prima di migrare (ADR-004)
                 (import esplicito: from core import migrazioni; ha una CLI)
    manifest     manifest.toml dei moduli: validazione + scansione (ADR-003)
    registro     registro dei moduli e menu per permessi (core.sqlite, stdlib)
    shell        la shell: login unico, cornice, menu, admin (python -m core.shell)

Uso da un modulo della suite (nessuna installazione richiesta):

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from core import db, migrate, export
"""
__version__ = "0.4.0"

from . import board, codes, config, db, events, export, forms, inventory, manifest, migrate, notify, schedule, shifts, statemachine  # noqa: F401,E402
