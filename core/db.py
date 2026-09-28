"""
core.db — connessioni SQLite con impostazioni uniformi.

Due sole funzioni, due soli modi di aprire un database:

  owned(path)     -> il modulo e' il PROPRIETARIO del DB (unico scrittore).
  readonly(path)  -> il DB appartiene a un altro modulo: sola lettura garantita
                     dal motore SQLite (mode=ro), non dalla disciplina.

Piu' attach_readonly(con, path, alias) per i JOIN con un DB altrui (es.
l'anagrafica del kernel, ADR-002), sempre in sola lettura.

Regole che queste funzioni rendono automatiche:
  - journal_mode=WAL sul DB di proprieta' (lettori e scrittore convivono);
  - busy_timeout UNICO per tutti (fine delle divergenze 3000/5000);
  - row_factory = sqlite3.Row sempre (accesso per nome colonna);
  - foreign_keys ON di default sul DB di proprieta'.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import quote

#: timeout unico, in millisecondi, per l'intera suite.
BUSY_TIMEOUT_MS = 5000


def owned(path: str | Path, *, wal: bool = True, fk: bool = True,
          timeout_ms: int = BUSY_TIMEOUT_MS) -> sqlite3.Connection:
    """Apre (creandolo se non esiste) il database DI PROPRIETA' del modulo.

    Da usare SOLO sul database di cui il modulo e' l'unico scrittore.
    """
    # URI: serve perche' attach_readonly() possa aprire altri DB in mode=ro
    con = sqlite3.connect(_uri(path, "rwc"), uri=True) if str(path) != ":memory:" \
        else sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    if fk:
        con.execute("PRAGMA foreign_keys=ON")
    con.execute(f"PRAGMA busy_timeout={int(timeout_ms)}")
    return con


def readonly(path: str | Path, *, timeout_ms: int = BUSY_TIMEOUT_MS) -> sqlite3.Connection:
    """Apre in SOLA LETTURA un database di proprieta' di un altro modulo.

    Usa l'URI mode=ro: qualunque tentativo di scrittura fallisce nel motore,
    non per convenzione. Solleva sqlite3.OperationalError se il file non esiste
    (un DB read-only che non c'e' e' un errore di configurazione, non va creato).
    """
    con = sqlite3.connect(_uri(path, "ro"), uri=True)
    con.row_factory = sqlite3.Row
    con.execute(f"PRAGMA busy_timeout={int(timeout_ms)}")
    return con


def attach_readonly(con: sqlite3.Connection, path: str | Path, alias: str) -> None:
    """Collega a `con` un DB altrui in SOLA LETTURA (mode=ro), come schema
    `alias`: permette i JOIN (es. log del modulo x anagrafica del kernel).
    Il file deve esistere. Scrivere su `alias.*` fallisce nel motore."""
    from .migrate import ident
    p = Path(path)
    if not p.exists():
        raise sqlite3.OperationalError(f"database da collegare inesistente: {p}")
    con.execute(f"ATTACH DATABASE ? AS {ident(alias)}", (_uri(p, "ro"),))


def _uri(path: str | Path, mode: str) -> str:
    """URI SQLite di un file (caratteri speciali del percorso protetti)."""
    return f"file:{quote(Path(path).as_posix(), safe='/:')}?mode={mode}"
