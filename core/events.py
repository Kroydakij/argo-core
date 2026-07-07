"""
core.events — layer event-sourced: lo stato e' una proiezione dello storico.

Il pattern in una riga: non si aggiorna MAI lo stato di un'entita', si
APPENDE un evento che ne dichiara il nuovo stato. Lo stato corrente non e'
un dato memorizzato ma il risultato di una query sullo storico (la proiezione
`latest_state_per_entity`: per ogni entita', l'evento piu' recente).

Perche':
  - lo storico e' la verita' e non si perde nulla (audit, rollback logico,
    ricostruzione a posteriori);
  - niente scritture distruttive => niente KPI corrotti da un UPDATE sbagliato;
  - `sorgente` (MANUALE / SENSORE) distingue il dato inserito a mano da quello
    letto da un sensore/automazione, sullo stesso binario.

Regole rese automatiche:
  - append-only: l'unica scrittura e' registra() (single write-point), che fa
    solo INSERT. Nessuna funzione qui aggiorna o cancella.
  - migrazioni additive: migra() compone gli helper di core.migrate e ricrea
    la vista di proiezione SEMPRE alla fine.
  - identificatori (tabella/vista/colonne extra) validati prima di ogni
    interpolazione DDL/DML, riusando core.migrate._ident.

Uso in un modulo proprietario del proprio DB:

    from core import db, events

    def migrate_db():
        con = db.owned(DB_PATH)
        events.migra(con, "eventi", extra_colonne={"valore": "REAL"})
        con.commit(); con.close()

    events.registra(con, "PRESSA-01", "IN_USO", operatore="rossi")
    events.stato_corrente(con, "PRESSA-01")   # -> {..., 'stato': 'IN_USO', ...}
"""
from __future__ import annotations

from typing import Any

from .migrate import _ident

#: le sole origini ammesse per un evento. Enum di dominio generico:
#: MANUALE = inserito da una persona; SENSORE = letto da automazione/sensore.
SORGENTI = ("MANUALE", "SENSORE")

TABELLA_DEFAULT = "eventi"
VISTA_DEFAULT = "latest_state_per_entity"


def ddl_log(table: str = TABELLA_DEFAULT) -> str:
    """DDL del log eventi append-only (colonne standard della suite)."""
    check = ",".join(f"'{s}'" for s in SORGENTI)      # SORGENTI = costanti sicure
    return f"""CREATE TABLE IF NOT EXISTS {_ident(table)} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        entita TEXT NOT NULL,
        stato TEXT NOT NULL,
        sorgente TEXT NOT NULL DEFAULT 'MANUALE' CHECK (sorgente IN ({check})),
        operatore TEXT,
        note TEXT
    )"""


def ddl_proiezione(table: str = TABELLA_DEFAULT, vista: str = VISTA_DEFAULT) -> str:
    """DDL della vista di proiezione: l'evento con id massimo per ogni entita'.

    id e' AUTOINCREMENT monotono, quindi 'id massimo' = 'evento piu' recente'
    senza dipendere dalla risoluzione del timestamp.
    """
    t, v = _ident(table), _ident(vista)
    return (f"CREATE VIEW {v} AS SELECT e.* FROM {t} e "
            f"JOIN (SELECT entita, MAX(id) AS _mid FROM {t} GROUP BY entita) u "
            f"ON e.id = u._mid")


def migra(con, table: str = TABELLA_DEFAULT, vista: str = VISTA_DEFAULT,
          *, extra_colonne: dict[str, str] | None = None) -> None:
    """Crea/aggiorna log + proiezione. Additiva; ricrea la vista alla fine.

    extra_colonne: {nome: tipo_ddl} colonne aggiuntive del modulo, aggiunte in
    modo idempotente (es. {"valore": "REAL", "commessa": "TEXT"}).
    """
    from . import migrate
    migrate.ensure_table(con, ddl_log(table))
    for nome, tipo in (extra_colonne or {}).items():
        migrate.ensure_column(con, table, nome, tipo)
    migrate.rebuild_views(con, {vista: ddl_proiezione(table, vista)})


def registra(con, entita: str, stato: str, *, table: str = TABELLA_DEFAULT,
             sorgente: str = "MANUALE", operatore: str | None = None,
             note: str | None = None, extra: dict[str, Any] | None = None) -> int:
    """SINGLE WRITE-POINT del log: appende un evento. Ritorna il suo id.

    L'unico modo per scrivere nel log. Fa solo INSERT (append-only). Valida
    la sorgente contro SORGENTI e i nomi delle colonne extra prima di comporre
    l'SQL. `extra` alimenta le colonne aggiunte via migra(extra_colonne=...).
    """
    if sorgente not in SORGENTI:
        raise ValueError(f"sorgente non valida: {sorgente!r} (ammesse: {SORGENTI})")
    cols = ["entita", "stato", "sorgente", "operatore", "note"]
    vals: list[Any] = [entita, stato, sorgente, operatore, note]
    for nome, valore in (extra or {}).items():
        cols.append(nome)
        vals.append(valore)
    collist = ",".join(_ident(c) for c in cols)       # valida ogni identificatore
    segnaposti = ",".join("?" * len(vals))
    cur = con.execute(
        f"INSERT INTO {_ident(table)} ({collist}) VALUES ({segnaposti})", vals)
    con.commit()
    return cur.lastrowid


def stato_corrente(con, entita: str | None = None, *, vista: str = VISTA_DEFAULT):
    """Stato corrente via proiezione.

    entita=None -> lista di tutti gli stati correnti (una riga per entita').
    entita="X"  -> dict dello stato corrente di X, oppure None se mai vista.
    """
    if entita is None:
        return [dict(r) for r in con.execute(
            f"SELECT * FROM {_ident(vista)} ORDER BY entita")]
    r = con.execute(
        f"SELECT * FROM {_ident(vista)} WHERE entita=?", (entita,)).fetchone()
    return dict(r) if r else None


def storico(con, entita: str, *, table: str = TABELLA_DEFAULT) -> list[dict]:
    """Storico completo append-only di un'entita', in ordine cronologico."""
    return [dict(r) for r in con.execute(
        f"SELECT * FROM {_ident(table)} WHERE entita=? ORDER BY id", (entita,))]
