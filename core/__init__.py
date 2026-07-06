"""
argo-core — fondamenta per suite gestionali Flask + SQLite su un PC qualunque.

Fase 0: solo funzioni pure e helper, nessun processo, nessuna dipendenza
obbligatoria oltre la libreria standard (Flask serve solo a export.csv_response
ed e' importato lazy).

Moduli:
    db        connessioni owned / readonly con impostazioni uniformi
    migrate   migrazioni additive (ensure_table, ensure_column, rebuild_views)
    codes     registro delle normalizzazioni codici (unico punto)
    notify    email SMTP con log append-only opzionale
    schedule  scheduler a tempo di lettura (funzione pura)
    export    CSV per Excel locale italiano
    adminbrowser  blueprint browser DB read-only (richiede Flask: import esplicito)
    portal    il portale della suite, porta 4700 (richiede Flask: python -m core.portal)

Uso da un modulo della suite (nessuna installazione richiesta):

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from core import db, migrate, export
"""
__version__ = "0.2.0"

from . import codes, config, db, events, export, migrate, notify, schedule, statemachine  # noqa: F401,E402
