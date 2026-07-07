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
    portal    il portale della suite, porta 4700 (richiede Flask: python -m core.portal)

Moduli (Fase 2):
    config       configurazione TOML fail-fast
    events       layer event-sourced (log append-only + latest_state_per_entity)
    statemachine macchina a stati dichiarativa (pura)
    shifts       turni parametrici a tempo di lettura (pura)
    forms        form-engine dichiarativo (validazione + render)
    auth         utenti e ruoli con hashing Werkzeug (lazy)
    board        board (kanban) config-driven
    scaffold     generatore di scheletri di moduli (python -m core.scaffold)

Uso da un modulo della suite (nessuna installazione richiesta):

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from core import db, migrate, export
"""
__version__ = "0.3.0"

from . import auth, board, codes, config, db, events, export, forms, migrate, notify, schedule, shifts, statemachine  # noqa: F401,E402
