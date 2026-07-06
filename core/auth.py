"""
core.auth — utenti e ruoli, con password hashate (Werkzeug).

Pattern generico di autenticazione per i moduli che servono una UI: una
tabella utenti nel database DEL MODULO (username, hash password, ruolo), e un
controllo di ruolo sulle route. I ruoli sono stringhe libere decise dal
modulo (es. "admin", "operatore", "sola_lettura"): il core non ne impone
nessuno, fornisce solo il meccanismo.

Dipendenze e coerenza con le regole della suite:
  - l'hashing usa werkzeug.security, importato LAZY: werkzeug arriva insieme a
    Flask e l'autenticazione ha senso solo quando si serve web. Chi non serve
    web non paga la dipendenza (import di `core` resta stdlib-only).
  - il decoratore richiede() importa Flask lazy, come core.export.
  - le parti non crittografiche (DDL tabella, query, ha_ruolo) sono stdlib
    pure e testabili senza werkzeug ne' Flask.

Perche' hash e non password in chiaro: anche su una LAN "di fiducia" un DB che
gira in giro come file non deve contenere password leggibili. La protezione
resta "da colleghi", non da attaccanti (HTTP in chiaro), ma le password no.

Uso in un modulo:

    from core import db, auth

    def migrate_db():
        con = db.owned(DB_PATH); auth.migra(con); con.commit(); con.close()

    auth.crea_utente(con, "mario", "segreta", "operatore")

    def verifica(u, p):
        con = db.owned(DB_PATH)
        try: return auth.verifica(con, u, p)
        finally: con.close()

    @app.get("/admin")
    @auth.richiede("admin", verifica=verifica)
    def admin(): ...
"""
from __future__ import annotations

from functools import wraps

from .migrate import _ident


def ddl(table: str = "utenti") -> str:
    return f"""CREATE TABLE IF NOT EXISTS {_ident(table)} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        ruolo TEXT NOT NULL,
        attivo INTEGER NOT NULL DEFAULT 1,
        creato_il TEXT DEFAULT (datetime('now','localtime'))
    )"""


def migra(con, table: str = "utenti") -> None:
    """Crea/aggiorna la tabella utenti del modulo (additiva)."""
    from . import migrate
    migrate.ensure_table(con, ddl(table))
    migrate.rebuild_views(con, {})           # gancio viste: nessuna oggi


def crea_utente(con, username: str, password: str, ruolo: str,
                *, table: str = "utenti") -> None:
    """Crea o aggiorna un utente (upsert su username). Password hashata."""
    con.execute(
        f"INSERT INTO {_ident(table)} (username, password_hash, ruolo) "
        f"VALUES (?,?,?) ON CONFLICT(username) DO UPDATE SET "
        f"password_hash=excluded.password_hash, ruolo=excluded.ruolo",
        (username.strip(), _hash(password), ruolo.strip()))
    con.commit()


def imposta_password(con, username: str, password: str,
                     *, table: str = "utenti") -> None:
    con.execute(f"UPDATE {_ident(table)} SET password_hash=? WHERE username=?",
                (_hash(password), username.strip()))
    con.commit()


def disattiva(con, username: str, *, attivo: bool = False,
              table: str = "utenti") -> None:
    con.execute(f"UPDATE {_ident(table)} SET attivo=? WHERE username=?",
                (1 if attivo else 0, username.strip()))
    con.commit()


def lista_utenti(con, *, table: str = "utenti") -> list[dict]:
    """Utenti senza l'hash (per pannelli/UI)."""
    return [dict(r) for r in con.execute(
        f"SELECT id, username, ruolo, attivo, creato_il "
        f"FROM {_ident(table)} ORDER BY username")]


def verifica(con, username: str, password: str,
             *, table: str = "utenti") -> dict | None:
    """Verifica credenziali. Ritorna l'utente (senza hash) se valido e attivo,
    altrimenti None. Richiede row_factory = sqlite3.Row (come db.owned)."""
    r = con.execute(
        f"SELECT * FROM {_ident(table)} WHERE username=?",
        (username.strip() if username else "",)).fetchone()
    if not r or not r["attivo"]:
        return None
    if not _check(r["password_hash"], password or ""):
        return None
    d = dict(r)
    d.pop("password_hash", None)
    return d


def ha_ruolo(utente: dict | None, *ruoli: str) -> bool:
    """True se `utente` esiste e il suo ruolo e' tra quelli passati."""
    return bool(utente) and utente.get("ruolo") in ruoli


def richiede(*ruoli: str, verifica, realm: str = "ARGO"):
    """Decoratore Flask: Basic Auth + gate di ruolo.

    `verifica` e' una callable (username, password) -> utente|None (di norma
    una chiusura sul verifica() del modulo). Se `ruoli` e' vuoto basta essere
    autenticati; altrimenti il ruolo dell'utente deve essere tra quelli dati.
    L'utente autenticato finisce in flask.g.utente. Flask importato lazy.
    """
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            from flask import Response, g, request
            cred = request.authorization
            u = verifica(cred.username, cred.password) if cred else None
            if not u or (ruoli and not ha_ruolo(u, *ruoli)):
                return Response("Autenticazione richiesta", 401,
                                {"WWW-Authenticate": f'Basic realm="{realm}"'})
            g.utente = u
            return f(*a, **k)
        return w
    return deco


def _hash(password: str) -> str:
    from werkzeug.security import generate_password_hash   # lazy: vedi docstring
    return generate_password_hash(password)


def _check(password_hash: str, password: str) -> bool:
    from werkzeug.security import check_password_hash       # lazy
    return check_password_hash(password_hash, password)
