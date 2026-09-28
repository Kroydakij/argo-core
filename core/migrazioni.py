"""
core.migrazioni — migrazioni di schema numerate, con backup prima di ogni migrazione.

Kernel (ADR-004). Sostituisce la convenzione "migrate_db() idempotente e basta"
con una lista di PASSI numerati che un runner applica una sola volta, in
ordine, dopo aver fatto un backup del database. Gli helper additivi di
core.migrate (ensure_table, ensure_column, rebuild_views) restano l'API con
cui si scrive il DDL dentro i passi.

Uso in un modulo proprietario del proprio DB:

    from core import migrate, migrazioni

    def _p1(con):   # schema iniziale (= la vecchia migrate_db(), idempotente)
        migrate.ensure_table(con, '''CREATE TABLE IF NOT EXISTS fermate (...)''')

    def _p2(con):
        migrate.ensure_column(con, "fermate", "causale", "TEXT")

    PASSI = [
        migrazioni.Passo(1, "schema iniziale", _p1),
        migrazioni.Passo(2, "causale sulle fermate", _p2),
    ]
    VISTE = {"fermate_aperte": "CREATE VIEW fermate_aperte AS ..."}

    migrazioni.applica(DB_PATH, PASSI, viste=VISTE)

Cosa fa applica(), nell'ordine:
  1. legge la versione del DB dalla tabella di sistema _argo_schema;
  2. DB piu' nuovo del codice (es. ricopiato un rilascio vecchio) -> rifiuta;
  3. se ci sono passi da applicare su un DB che contiene gia' dati, fa un
     BACKUP con l'API di backup online di SQLite (consistente anche in WAL) e
     ne verifica l'integrita'; backup fallito -> nessun passo eseguito;
  4. applica ogni passo in una transazione propria (BEGIN IMMEDIATE) e lo
     registra in _argo_schema nella stessa transazione;
  5. passo fallito -> ROLLBACK, riga ERRORE nel registro, MigrazioneFallita
     con l'indicazione del backup da cui ripartire;
  6. ricrea SEMPRE le viste alla fine (regola 5), anche senza passi pendenti.

Regole per chi scrive un passo:
  - numeri consecutivi da 1, mai riusati ne' riordinati; un passo pubblicato
    non si modifica: se ne aggiunge un altro;
  - il passo NON fa commit: la transazione la gestisce il runner;
  - solo DDL additivo (helper di core.migrate) e riempimento di colonne nuove
    su tabelle NON di log; sui log append-only solo ADD COLUMN.

Adozione di un DB 0.x (senza _argo_schema): il runner parte dal passo 1.
Funziona perche' il passo 1 e' la vecchia migrate_db(), idempotente per
costruzione (IF NOT EXISTS, ensure_column).

Supporto:  python -m core.migrazioni stato <file.sqlite>
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import db as coredb
from . import migrate

#: tabella di sistema (append-only) con la storia delle migrazioni del DB.
TABELLA = "_argo_schema"

#: backup da tenere per DB se il chiamante non dice altro.
BACKUP_DA_TENERE_DEFAULT = 5

#: margine di spazio libero richiesto per il backup, rispetto alla dimensione del DB.
MARGINE_SPAZIO = 1.1

ESITO_OK = "OK"
ESITO_ERRORE = "ERRORE"

_DDL = f"""CREATE TABLE IF NOT EXISTS {TABELLA} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    numero INTEGER NOT NULL,
    descrizione TEXT NOT NULL,
    applicato_il_utc TEXT NOT NULL,
    versione_codice TEXT,
    esito TEXT NOT NULL CHECK (esito IN ('{ESITO_OK}','{ESITO_ERRORE}')),
    errore TEXT,
    backup TEXT
)"""


# --- errori ---------------------------------------------------------------

class MigrazioneError(Exception):
    """Base: la migrazione non e' andata a buon fine. Il modulo NON deve partire."""


class PassiNonValidi(MigrazioneError):
    """La lista dei passi e' malformata (errore del programmatore)."""


class DBPiuNuovoDelCodice(MigrazioneError):
    """Il DB e' a una versione che questo codice non conosce."""


class BackupFallito(MigrazioneError):
    """Backup non riuscito o non integro: nessun passo e' stato eseguito."""


class MigrazioneFallita(MigrazioneError):
    """Un passo e' fallito. I passi precedenti restano applicati (atomici)."""

    def __init__(self, messaggio: str, *, numero: int, backup: Path | None):
        super().__init__(messaggio)
        self.numero = numero
        self.backup = backup


# --- modello --------------------------------------------------------------

@dataclass(frozen=True)
class Passo:
    """Un passo di migrazione: numero progressivo, descrizione, funzione(con)."""
    numero: int
    descrizione: str
    funzione: Callable[[sqlite3.Connection], None]


@dataclass(frozen=True)
class Esito:
    """Risultato di applica()."""
    da: int                      # versione prima
    a: int                       # versione dopo
    applicati: tuple[int, ...]   # numeri dei passi applicati ora
    backup: Path | None          # file di backup creato (None se non serviva)


# --- API ------------------------------------------------------------------

def applica(db_path: str | Path, passi: list[Passo], *,
            viste: dict[str, str] | None = None,
            backup_dir: str | Path | None = None,
            backup_da_tenere: int = BACKUP_DA_TENERE_DEFAULT,
            versione_codice: str | None = None) -> Esito:
    """Porta il DB all'ultimo passo noto, con backup prima. Fail-fast.

    backup_dir: cartella dei backup; default <cartella del DB>/_backup
                (nella cartella dati: chi copia `comune` si porta dietro anche
                questi). Dentro, una sottocartella per DB.
    backup_da_tenere: backup conservati per questo DB (minimo 1). Il backup
                appena fatto non viene mai cancellato.
    versione_codice: finisce nel registro; default la versione di core.
    """
    _valida_passi(passi)
    if backup_da_tenere < 1:
        raise ValueError("backup_da_tenere deve essere almeno 1")
    db_path = Path(db_path)
    backup_dir = Path(backup_dir) if backup_dir else db_path.parent / "_backup"
    if versione_codice is None:
        from . import __version__ as versione_codice
    ultimo = passi[-1].numero if passi else 0

    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = coredb.owned(db_path)
    livello = con.isolation_level
    con.isolation_level = None          # transazioni esplicite: le governa il runner
    try:
        con.execute(_DDL)
        da = _versione(con)
        if da > ultimo:
            raise DBPiuNuovoDelCodice(
                f"{db_path.name}: DB allo schema {da}, questo codice conosce fino "
                f"al passo {ultimo}. Stai avviando un rilascio piu' vecchio?")

        pendenti = [p for p in passi if p.numero > da]
        backup = None
        if pendenti and _ha_dati(con):
            backup = _fai_backup(con, db_path, backup_dir, da)
            _pulisci_backup(backup_dir / db_path.stem, db_path.stem,
                            backup_da_tenere, tieni=backup)

        applicati = []
        for p in pendenti:
            if _applica_passo(con, p, versione_codice, backup):
                applicati.append(p.numero)

        if viste:                        # SEMPRE alla fine (regola 5)
            con.execute("BEGIN IMMEDIATE")
            try:
                migrate.rebuild_views(con, viste)
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
        return Esito(da=da, a=_versione(con), applicati=tuple(applicati),
                     backup=backup)
    finally:
        con.isolation_level = livello
        con.close()


def versione(db_path: str | Path) -> int:
    """Versione di schema di un DB, letta in SOLA LETTURA (PRAGMA user_version).

    Per i moduli che leggono DB altrui (es. i DB del kernel) e vogliono
    rifiutarsi di partire se lo schema e' piu' vecchio di quello atteso.
    """
    con = coredb.readonly(db_path)
    try:
        return int(con.execute("PRAGMA user_version").fetchone()[0])
    finally:
        con.close()


def richiedi_versione(db_path: str | Path, minima: int, *,
                      suggerimento: str = "") -> int:
    """Come versione(), ma solleva MigrazioneError se < minima. Fail-fast."""
    v = versione(db_path)
    if v < minima:
        extra = f" {suggerimento}" if suggerimento else ""
        raise MigrazioneError(
            f"{Path(db_path).name}: schema {v}, serve almeno {minima}.{extra}")
    return v


def stato(db_path: str | Path, *, backup_dir: str | Path | None = None) -> dict:
    """Riepilogo per il supporto: versione, storia delle migrazioni, backup presenti.

    Solo lettura: non crea nulla e non tocca il DB.
    """
    db_path = Path(db_path)
    backup_dir = Path(backup_dir) if backup_dir else db_path.parent / "_backup"
    con = coredb.readonly(db_path)
    try:
        user_version = int(con.execute("PRAGMA user_version").fetchone()[0])
        if migrate.table_exists(con, TABELLA):
            storia = [dict(r) for r in con.execute(
                f"SELECT * FROM {TABELLA} ORDER BY id")]
        else:
            storia = []
    finally:
        con.close()
    ok = [r["numero"] for r in storia if r["esito"] == ESITO_OK]
    cartella = backup_dir / db_path.stem
    backups = sorted(cartella.glob(f"{db_path.stem}.v*.sqlite")) if cartella.exists() else []
    return {"db": str(db_path), "versione": max(ok, default=0),
            "user_version": user_version, "storia": storia,
            "backup": [str(b) for b in backups]}


# --- interni --------------------------------------------------------------

def _valida_passi(passi: list[Passo]) -> None:
    for atteso, p in enumerate(passi, start=1):
        if not isinstance(p, Passo):
            raise PassiNonValidi(f"elemento {atteso} non e' un Passo: {p!r}")
        if p.numero != atteso:
            raise PassiNonValidi(
                f"passi non consecutivi: atteso {atteso}, trovato {p.numero} "
                f"({p.descrizione!r}). I numeri partono da 1 e non si saltano.")
        if not callable(p.funzione):
            raise PassiNonValidi(f"passo {p.numero}: funzione non chiamabile")
        if not p.descrizione.strip():
            raise PassiNonValidi(f"passo {p.numero}: descrizione vuota")


def _versione(con: sqlite3.Connection) -> int:
    r = con.execute(
        f"SELECT MAX(numero) FROM {TABELLA} WHERE esito=?", (ESITO_OK,)).fetchone()
    return r[0] or 0


def _ha_dati(con: sqlite3.Connection) -> bool:
    """True se il DB contiene oggetti oltre alla tabella di sistema.
    Su un DB appena creato il backup sarebbe un file vuoto: si salta."""
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
        "AND name <> ? LIMIT 1", (TABELLA,)).fetchone() is not None


def _ts_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _fai_backup(con: sqlite3.Connection, db_path: Path, backup_dir: Path,
                da: int) -> Path:
    cartella = backup_dir / db_path.stem
    try:
        cartella.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise BackupFallito(f"cartella backup non creabile ({cartella}): {e}") from e

    dimensione = sum(p.stat().st_size for p in
                     (db_path, Path(f"{db_path}-wal")) if p.exists())
    libero = shutil.disk_usage(cartella).free
    if libero < dimensione * MARGINE_SPAZIO:
        raise BackupFallito(
            f"spazio insufficiente per il backup di {db_path.name}: servono "
            f"~{int(dimensione * MARGINE_SPAZIO)} byte, liberi {libero}")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = cartella / f"{db_path.stem}.v{da}-{ts}.sqlite"
    try:
        out = sqlite3.connect(str(dest))
        try:
            con.backup(out)              # API di backup online: consistente in WAL
            integro = out.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            out.close()
    except sqlite3.Error as e:
        dest.unlink(missing_ok=True)
        raise BackupFallito(f"backup di {db_path.name} fallito: {e}") from e
    if integro != "ok":
        raise BackupFallito(f"backup {dest} non integro: {integro}")
    return dest


def _pulisci_backup(cartella: Path, stem: str, da_tenere: int, *, tieni: Path) -> None:
    """Tiene gli ultimi `da_tenere` backup (per nome: il timestamp e' nel nome).
    Unica cancellazione automatica di file della suite; mai il backup appena fatto."""
    tutti = sorted(cartella.glob(f"{stem}.v*.sqlite"),
                   key=lambda p: p.name.rsplit("-", 1)[-1])
    for vecchio in tutti[:-da_tenere]:
        if vecchio != tieni:
            vecchio.unlink(missing_ok=True)


def _applica_passo(con: sqlite3.Connection, p: Passo, versione_codice: str,
                   backup: Path | None) -> bool:
    """Applica un passo nella sua transazione. False se un altro processo
    l'aveva gia' applicato (ricontrollo dentro il lock)."""
    con.execute("BEGIN IMMEDIATE")
    if _versione(con) >= p.numero:
        con.execute("ROLLBACK")
        return False
    try:
        p.funzione(con)
        if not con.in_transaction:
            raise MigrazioneError(
                f"il passo {p.numero} ha chiuso la transazione (commit nel "
                f"passo?): i passi non devono fare commit")
        con.execute(
            f"INSERT INTO {TABELLA} (numero, descrizione, applicato_il_utc, "
            f"versione_codice, esito, backup) VALUES (?,?,?,?,?,?)",
            (p.numero, p.descrizione, _ts_utc(), versione_codice, ESITO_OK,
             str(backup) if backup else None))
        con.execute(f"PRAGMA user_version = {int(p.numero)}")
        con.execute("COMMIT")
        return True
    except Exception as e:
        if con.in_transaction:
            con.execute("ROLLBACK")
        con.execute(
            f"INSERT INTO {TABELLA} (numero, descrizione, applicato_il_utc, "
            f"versione_codice, esito, errore, backup) VALUES (?,?,?,?,?,?,?)",
            (p.numero, p.descrizione, _ts_utc(), versione_codice, ESITO_ERRORE,
             f"{type(e).__name__}: {e}", str(backup) if backup else None))
        dove = (f"Backup prima della migrazione: {backup}" if backup
                else "Nessun backup: il DB era vuoto.")
        raise MigrazioneFallita(
            f"passo {p.numero} ({p.descrizione}) fallito: {e}. {dove}",
            numero=p.numero, backup=backup) from e


# --- CLI ------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.migrazioni",
                                 description="Stato delle migrazioni di un DB ARGO.")
    sub = ap.add_subparsers(dest="comando", required=True)
    s = sub.add_parser("stato", help="versione, storia e backup di un DB")
    s.add_argument("db", help="file .sqlite")
    s.add_argument("--backup-dir", default=None,
                   help="cartella backup (default: <cartella del DB>/_backup)")
    a = ap.parse_args(argv)
    try:
        st = stato(a.db, backup_dir=a.backup_dir)
    except sqlite3.Error as e:
        print(f"[migrazioni] {a.db}: {e}", file=sys.stderr)
        return 1
    print(f"DB:            {st['db']}")
    print(f"versione:      {st['versione']} (user_version {st['user_version']})")
    print("storia:")
    for r in st["storia"] or []:
        riga = f"  #{r['numero']:<3} {r['esito']:<6} {r['applicato_il_utc']}  {r['descrizione']}"
        if r["errore"]:
            riga += f"  [{r['errore']}]"
        print(riga)
    if not st["storia"]:
        print("  (nessuna migrazione registrata: DB 0.x o mai migrato)")
    print("backup:")
    for b in st["backup"] or []:
        print(f"  {b}")
    if not st["backup"]:
        print("  (nessuno)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
