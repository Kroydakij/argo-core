"""
core.db — connessioni SQLite con impostazioni uniformi.

Due sole funzioni, due soli modi di aprire un database:

  owned(path)     -> il modulo e' il PROPRIETARIO del DB (unico scrittore).
  readonly(path)  -> il DB appartiene a un altro modulo: sola lettura garantita
                     dal motore SQLite (mode=ro), non dalla disciplina.

Regole che queste funzioni rendono automatiche:
  - journal_mode=WAL sul DB di proprieta' (lettori e scrittore convivono);
  - busy_timeout UNICO per tutti (fine delle divergenze 3000/5000);
  - row_factory = sqlite3.Row sempre (accesso per nome colonna);
  - foreign_keys ON di default sul DB di proprieta'.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

#: timeout unico, in millisecondi, per l'intera suite.
BUSY_TIMEOUT_MS = 5000


def owned(path: str | Path, *, wal: bool = True, fk: bool = True,
          timeout_ms: int = BUSY_TIMEOUT_MS) -> sqlite3.Connection:
    """Apre (creandolo se non esiste) il database DI PROPRIETA' del modulo.

    Da usare SOLO sul database di cui il modulo e' l'unico scrittore.
    """
    con = sqlite3.connect(str(path))
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
    p = Path(path)
    uri = f"file:{p.as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    con.execute(f"PRAGMA busy_timeout={int(timeout_ms)}")
    return con
