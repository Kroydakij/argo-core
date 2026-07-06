"""
core.migrate — migrazioni additive, il solo tipo di migrazione ammesso.

Il migrate_db() di un modulo diventa una sequenza di queste chiamate:

    def migrate_db():
        con = db.owned(DB_PATH)
        migrate.ensure_table(con, '''CREATE TABLE IF NOT EXISTS eventi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT (datetime('now','localtime')),
            sorgente TEXT NOT NULL DEFAULT 'MANUALE')''')
        migrate.ensure_column(con, "eventi", "operatore", "TEXT")
        migrate.rebuild_views(con, VISTE)      # SEMPRE alla fine
        con.commit(); con.close()

Cosa NON esiste qui, per costruzione: drop di tabelle, ricreazione del
database, UPDATE di schema distruttivi.
"""
from __future__ import annotations

import sqlite3


def table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    """Nomi delle colonne esistenti di una tabella (vuoto se non esiste)."""
    return {r[1] for r in con.execute(f"PRAGMA table_info({_ident(table)})")}


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def ensure_table(con: sqlite3.Connection, ddl: str) -> None:
    """Esegue una CREATE TABLE, imponendo che sia IF NOT EXISTS.

    Il controllo e' volutamente pedante: una CREATE TABLE senza IF NOT EXISTS
    dentro una migrazione additiva e' quasi sempre un errore che esplode al
    secondo avvio.
    """
    if "if not exists" not in " ".join(ddl.lower().split()):
        raise ValueError("ensure_table richiede 'CREATE TABLE IF NOT EXISTS ...'")
    con.execute(ddl)


def ensure_column(con: sqlite3.Connection, table: str, column: str,
                  ddl_type: str) -> bool:
    """ALTER TABLE ADD COLUMN con guardia di esistenza. Idempotente.

    Ritorna True se la colonna e' stata aggiunta ora, False se c'era gia'.
    ddl_type e' il tipo + eventuali vincoli, es. "TEXT NOT NULL DEFAULT ''".
    """
    if column in table_columns(con, table):
        return False
    con.execute(f"ALTER TABLE {_ident(table)} ADD COLUMN {_ident(column)} {ddl_type}")
    return True


def rebuild_views(con: sqlite3.Connection, views: dict[str, str]) -> None:
    """DROP + CREATE di ogni vista. Da chiamare SEMPRE alla fine di migrate_db().

    views = {"v_stato": "CREATE VIEW v_stato AS SELECT ..."}.
    Motivo: SQLite riscrive le viste durante i rename di tabelle e puo'
    romperle in modo silenzioso; ricrearle da costante nel codice a ogni
    avvio rende il codice l'unica fonte di verita' della loro definizione.
    """
    for name, ddl in views.items():
        con.execute(f"DROP VIEW IF EXISTS {_ident(name)}")
        con.execute(ddl)


def _ident(name: str) -> str:
    """Valida un identificatore SQL (tabella/colonna/vista) e lo quota.

    Difesa contro l'interpolazione accidentale di input non fidato nei DDL.
    """
    if not name or not all(c.isalnum() or c == "_" for c in name):
        raise ValueError(f"identificatore SQL non valido: {name!r}")
    return f'"{name}"'
