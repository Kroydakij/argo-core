"""
core.auth — identita' centrale, sessione condivisa e permessi (kernel, ADR-001).

Un solo archivio di utenti per tutta la suite: `comune/auth.sqlite`.
Proprietario il KERNEL: ci scrivono solo la shell e la CLI del kernel
(`python -m core.auth ...`). I moduli lo leggono SOLO con db.readonly(): a
ogni richiesta risolvono la sessione e i permessi dell'utente.

Cosa c'e' dentro:
  - `auth_eventi`: log append-only (busta di ADR-005) di tutto cio' che
    cambia: utenti, gruppi, appartenenze, ruoli, assegnazioni, sessioni.
    Utenti, gruppi, ruoli e permessi effettivi sono VISTE su questo log:
    chi ha dato cosa a chi, e quando, resta per sempre (audit trail).
  - `credenziali`: hash delle password, aggiornabile. E' l'eccezione motivata
    all'append-only: il log registra il FATTO (core.password_impostata), non
    il segreto; tenere per sempre gli hash vecchi esporrebbe le password
    riusate.

Modello (RBAC piatto): un RUOLO e' un insieme di permessi "<modulo>.<azione>"
(dichiarati nei manifest, ADR-003); un GRUPPO e' un insieme di utenti; un
ruolo si assegna a un utente o a un gruppo. Permessi effettivi = unione.
Niente negazioni, gruppi annidati, gerarchie di ruoli.

Sessione: la shell apre la sessione al login e imposta il cookie
`argo_sessione` (token casuale; nel DB solo il suo SHA-256). I cookie non
distinguono le porte, quindi arriva a tutti i moduli sullo stesso host.
Scadenza fissata all'apertura e verificata a tempo di lettura; logout,
utente disattivato e permesso revocato hanno effetto alla richiesta
successiva, in ogni modulo.

Hash delle password: stdlib (hashlib), nello STESSO formato di Werkzeug
("scrypt:N:r:p$sale$hex" / "pbkdf2:sha256:N$sale$hex"): gli hash degli utenti
0.x si importano senza reset e non serve Werkzeug fuori dal web.

Nel modulo (Flask, import lazy):

    from core import auth, manifest
    M = manifest.carica(QUI)

    def create_app():
        app = Flask(__name__)

        @app.get("/")
        @auth.richiede_permesso("andon.vedi")
        def home(): ...                  # utente in flask.g.utente

        @app.get("/api/health")
        @auth.pubblica
        def health(): ...

        auth.inizializza(app, manifest=M, auth_db=COMUNE / "auth.sqlite")  # DOPO le route
        return app

Nella shell / CLI (scrittura, connessione owned su auth.sqlite):

    auth.prepara_db(path)                               # schema + backup (ADR-004)
    uid = auth.crea_utente(con, "mrossi", password="...", attore=admin_id)
    token = auth.login(con, "mrossi", "...", durata_ore=12)
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from . import db as coredb
from . import migrate, migrazioni

# --- costanti ---------------------------------------------------------------

#: attore riservato per le azioni del kernel stesso (bootstrap, import, CLI).
SISTEMA = "sistema"

#: nome del cookie di sessione della suite (riservato: i moduli non lo usano).
COOKIE = "argo_sessione"

#: porta della shell, per il redirect al login.
PORTA_SHELL = 4700

TIPI_UTENTE = ("persona", "servizio")

#: permessi del kernel (i permessi di anagrafica per tipo arrivano con ADR-002).
PERMESSI_KERNEL = {
    "core.admin": "Amministrazione della suite (registro moduli, browser DB)",
    "core.utenti": "Gestione di utenti, gruppi e ruoli",
    "core.link_esterni": "Vedere i moduli legacy (senza manifest) nel menu",
}

RUOLO_ADMIN = "Amministratore"

# tipi di evento (busta ADR-005, namespace core.*)
E_UTENTE_CREATO = "core.utente_creato"
E_UTENTE_RINOMINATO = "core.utente_rinominato"
E_UTENTE_DISATTIVATO = "core.utente_disattivato"
E_UTENTE_RIATTIVATO = "core.utente_riattivato"
E_PASSWORD = "core.password_impostata"
E_GRUPPO_CREATO = "core.gruppo_creato"
E_MEMBRO_AGGIUNTO = "core.membro_aggiunto"
E_MEMBRO_RIMOSSO = "core.membro_rimosso"
E_RUOLO_DEFINITO = "core.ruolo_definito"
E_RUOLO_ASSEGNATO = "core.ruolo_assegnato"
E_RUOLO_REVOCATO = "core.ruolo_revocato"
E_SESSIONE_APERTA = "core.sessione_aperta"
E_SESSIONE_CHIUSA = "core.sessione_chiusa"

_RE_PERMESSO = re.compile(r"^\w+(\.\w+)+$")

#: algoritmo di hash per le password NUOVE (formato Werkzeug).
_METODO = "scrypt:32768:8:1" if hasattr(hashlib, "scrypt") else "pbkdf2:sha256:600000"
_SALT_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


class AuthError(Exception):
    """Operazione di auth non valida (utente inesistente, nome duplicato, ...)."""


# --- schema (core.migrazioni) ------------------------------------------------

def _p1_schema(con: sqlite3.Connection) -> None:
    migrate.ensure_table(con, """CREATE TABLE IF NOT EXISTS auth_eventi (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid TEXT NOT NULL UNIQUE,
        tipo TEXT NOT NULL,
        versione INTEGER NOT NULL,
        ts_utc TEXT NOT NULL,
        offset_min INTEGER NOT NULL,
        attore_id TEXT NOT NULL,
        entita_id TEXT,
        sorgente TEXT NOT NULL DEFAULT 'MANUALE'
            CHECK (sorgente IN ('MANUALE','SENSORE')),
        utente_id TEXT,
        gruppo_id TEXT,
        ruolo_id TEXT,
        nome TEXT,
        nome_esteso TEXT,
        tipo_utente TEXT,
        backend TEXT,
        id_esterno TEXT,
        permessi TEXT,
        token_hash TEXT,
        scade_il_utc TEXT
    )""")
    migrate.ensure_table(con, """CREATE TABLE IF NOT EXISTS credenziali (
        utente_id TEXT PRIMARY KEY,
        password_hash TEXT NOT NULL,
        aggiornato_il_utc TEXT NOT NULL
    )""")
    for nome, colonne in (("ix_auth_utente", "utente_id, tipo"),
                          ("ix_auth_gruppo", "gruppo_id, tipo"),
                          ("ix_auth_ruolo", "ruolo_id, tipo"),
                          ("ix_auth_token", "token_hash")):
        con.execute(f"CREATE INDEX IF NOT EXISTS {nome} ON auth_eventi ({colonne})")


PASSI_AUTH = [migrazioni.Passo(1, "log eventi auth + credenziali", _p1_schema)]

VISTE_AUTH = {
    # utenti: dati dalla creazione + ultimo nome + ultimo stato attivo
    "auth_utenti": f"""CREATE VIEW auth_utenti AS
        SELECT c.utente_id AS id,
          (SELECT e.nome FROM auth_eventi e WHERE e.utente_id = c.utente_id
             AND e.tipo IN ('{E_UTENTE_CREATO}','{E_UTENTE_RINOMINATO}')
           ORDER BY e.id DESC LIMIT 1) AS username,
          (SELECT e.nome_esteso FROM auth_eventi e WHERE e.utente_id = c.utente_id
             AND e.tipo IN ('{E_UTENTE_CREATO}','{E_UTENTE_RINOMINATO}')
           ORDER BY e.id DESC LIMIT 1) AS nome,
          c.tipo_utente AS tipo, c.backend, c.id_esterno,
          (SELECT CASE e.tipo WHEN '{E_UTENTE_DISATTIVATO}' THEN 0 ELSE 1 END
             FROM auth_eventi e WHERE e.utente_id = c.utente_id
             AND e.tipo IN ('{E_UTENTE_CREATO}','{E_UTENTE_DISATTIVATO}',
                            '{E_UTENTE_RIATTIVATO}')
           ORDER BY e.id DESC LIMIT 1) AS attivo,
          c.ts_utc AS creato_il_utc
        FROM auth_eventi c WHERE c.tipo = '{E_UTENTE_CREATO}'""",
    "auth_gruppi": f"""CREATE VIEW auth_gruppi AS
        SELECT gruppo_id AS id, nome, ts_utc AS creato_il_utc
        FROM auth_eventi WHERE tipo = '{E_GRUPPO_CREATO}'""",
    # appartenenza = ultimo evento aggiunto/rimosso per (gruppo, utente)
    "auth_membri": f"""CREATE VIEW auth_membri AS
        SELECT m.gruppo_id, m.utente_id FROM auth_eventi m
        WHERE m.tipo = '{E_MEMBRO_AGGIUNTO}' AND m.id = (
          SELECT MAX(x.id) FROM auth_eventi x
          WHERE x.gruppo_id = m.gruppo_id AND x.utente_id = m.utente_id
            AND x.tipo IN ('{E_MEMBRO_AGGIUNTO}','{E_MEMBRO_RIMOSSO}'))""",
    # ruolo = ultima definizione (nome + insieme completo dei permessi)
    "auth_ruoli": f"""CREATE VIEW auth_ruoli AS
        SELECT r.ruolo_id AS id, r.nome, r.permessi FROM auth_eventi r
        WHERE r.tipo = '{E_RUOLO_DEFINITO}' AND r.id = (
          SELECT MAX(x.id) FROM auth_eventi x
          WHERE x.ruolo_id = r.ruolo_id AND x.tipo = '{E_RUOLO_DEFINITO}')""",
    # assegnazione = ultimo evento assegnato/revocato per (ruolo, utente|gruppo)
    "auth_assegnazioni": f"""CREATE VIEW auth_assegnazioni AS
        SELECT a.ruolo_id, a.utente_id, a.gruppo_id FROM auth_eventi a
        WHERE a.tipo = '{E_RUOLO_ASSEGNATO}' AND a.id = (
          SELECT MAX(x.id) FROM auth_eventi x
          WHERE x.ruolo_id = a.ruolo_id AND x.utente_id IS a.utente_id
            AND x.gruppo_id IS a.gruppo_id
            AND x.tipo IN ('{E_RUOLO_ASSEGNATO}','{E_RUOLO_REVOCATO}'))""",
    # permessi effettivi: ruoli diretti + ruoli dei gruppi, solo utenti attivi
    "auth_permessi_utente": """CREATE VIEW auth_permessi_utente AS
        SELECT DISTINCT u.id AS utente_id, j.value AS permesso
        FROM auth_utenti u
        JOIN auth_assegnazioni a
          ON a.utente_id = u.id
          OR a.gruppo_id IN (SELECT m.gruppo_id FROM auth_membri m
                             WHERE m.utente_id = u.id)
        JOIN auth_ruoli r ON r.id = a.ruolo_id
        JOIN json_each(r.permessi) j
        WHERE u.attivo = 1""",
    # sessioni non chiuse (la scadenza si confronta a tempo di lettura)
    "auth_sessioni": f"""CREATE VIEW auth_sessioni AS
        SELECT s.token_hash, s.utente_id, s.ts_utc AS aperta_il_utc,
               s.scade_il_utc
        FROM auth_eventi s WHERE s.tipo = '{E_SESSIONE_APERTA}'
        AND NOT EXISTS (SELECT 1 FROM auth_eventi c
                        WHERE c.tipo = '{E_SESSIONE_CHIUSA}'
                          AND c.token_hash = s.token_hash)""",
}


def percorso_db(comune: str | Path | None = None) -> Path:
    """comune/auth.sqlite. Cartella dati: argomento, poi ARGO_COMUNE, poi
    <cartella della suite>/comune (come il portale)."""
    if comune is None:
        comune = os.environ.get("ARGO_COMUNE",
                                Path(__file__).resolve().parents[1] / "comune")
    return Path(comune) / "auth.sqlite"


def prepara_db(path: str | Path) -> migrazioni.Esito:
    """Porta auth.sqlite all'ultimo schema, con backup (ADR-004). Solo kernel."""
    return migrazioni.applica(path, PASSI_AUTH, viste=VISTE_AUTH)


def migra(con: sqlite3.Connection) -> None:
    """Schema su una connessione gia' aperta, senza backup ne' versione
    (test in memoria). La suite vera usa prepara_db()."""
    _p1_schema(con)
    migrate.rebuild_views(con, VISTE_AUTH)
    con.commit()


# --- scrittura: single write-point -------------------------------------------

def _ora_utc(ora: datetime | None = None) -> datetime:
    return (ora or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _registra(con, tipo: str, *, attore: str, **campi) -> None:
    """SINGLE WRITE-POINT del log auth (solo INSERT). Busta ADR-005 +
    colonne di dominio. Non fa commit: lo fa la funzione pubblica chiamante."""
    _verifica_attore(con, attore)
    locale = datetime.now().astimezone()
    offset = int(locale.utcoffset().total_seconds() // 60)
    cols = ["uid", "tipo", "versione", "ts_utc", "offset_min", "attore_id"]
    vals = [str(uuid.uuid4()), tipo, 1, _iso(_ora_utc()), offset, attore]
    for k, v in campi.items():
        cols.append(migrate.ident(k))
        vals.append(v)
    con.execute(f"INSERT INTO auth_eventi ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(vals))})", vals)


def _verifica_attore(con, attore: str) -> None:
    if not isinstance(attore, str) or not attore:
        raise AuthError("ogni modifica richiede un attore (ID utente o 'sistema')")
    if attore != SISTEMA and utente(con, attore) is None:
        raise AuthError(f"attore sconosciuto: {attore!r}")


def _nuovo_id() -> str:
    return str(uuid.uuid4())


def crea_utente(con, username: str, *, attore: str, password: str | None = None,
                nome: str = "", tipo: str = "persona", backend: str = "locale",
                id_esterno: str | None = None) -> str:
    """Crea un utente e ne ritorna l'ID stabile. username unico (anche fra
    disattivati). tipo: 'persona' (solo sessione) o 'servizio' (Basic Auth)."""
    username = (username or "").strip()
    if not username:
        raise AuthError("username vuoto")
    if tipo not in TIPI_UTENTE:
        raise AuthError(f"tipo utente non valido: {tipo!r} (ammessi {TIPI_UTENTE})")
    if utente_per_nome(con, username) is not None:
        raise AuthError(f"username gia' in uso: {username!r}")
    uid = _nuovo_id()
    _registra(con, E_UTENTE_CREATO, attore=attore, utente_id=uid, nome=username,
              nome_esteso=nome.strip(), tipo_utente=tipo, backend=backend,
              id_esterno=id_esterno)
    if password is not None:
        _salva_hash(con, uid, hash_password(password))
        _registra(con, E_PASSWORD, attore=attore, utente_id=uid)
    con.commit()
    return uid


def rinomina_utente(con, utente_id: str, username: str, *, attore: str,
                    nome: str | None = None) -> None:
    u = _utente_o_errore(con, utente_id)
    username = (username or "").strip()
    if not username:
        raise AuthError("username vuoto")
    altro = utente_per_nome(con, username)
    if altro is not None and altro["id"] != utente_id:
        raise AuthError(f"username gia' in uso: {username!r}")
    _registra(con, E_UTENTE_RINOMINATO, attore=attore, utente_id=utente_id,
              nome=username, nome_esteso=u["nome"] if nome is None else nome.strip())
    con.commit()


def disattiva_utente(con, utente_id: str, *, attore: str) -> None:
    """Effetto immediato: sessioni e permessi dell'utente smettono di valere."""
    _utente_o_errore(con, utente_id)
    _registra(con, E_UTENTE_DISATTIVATO, attore=attore, utente_id=utente_id)
    con.commit()


def riattiva_utente(con, utente_id: str, *, attore: str) -> None:
    _utente_o_errore(con, utente_id)
    _registra(con, E_UTENTE_RIATTIVATO, attore=attore, utente_id=utente_id)
    con.commit()


def imposta_password(con, utente_id: str, password: str, *, attore: str) -> None:
    """Aggiorna l'hash in `credenziali`; nel log va solo il fatto."""
    _utente_o_errore(con, utente_id)
    _salva_hash(con, utente_id, hash_password(password))
    _registra(con, E_PASSWORD, attore=attore, utente_id=utente_id)
    con.commit()


def crea_gruppo(con, nome: str, *, attore: str) -> str:
    nome = (nome or "").strip()
    if not nome:
        raise AuthError("nome gruppo vuoto")
    if con.execute("SELECT 1 FROM auth_gruppi WHERE nome=?", (nome,)).fetchone():
        raise AuthError(f"gruppo gia' esistente: {nome!r}")
    gid = _nuovo_id()
    _registra(con, E_GRUPPO_CREATO, attore=attore, gruppo_id=gid, nome=nome)
    con.commit()
    return gid


def aggiungi_membro(con, gruppo_id: str, utente_id: str, *, attore: str) -> None:
    _gruppo_o_errore(con, gruppo_id)
    _utente_o_errore(con, utente_id)
    _registra(con, E_MEMBRO_AGGIUNTO, attore=attore, gruppo_id=gruppo_id,
              utente_id=utente_id)
    con.commit()


def rimuovi_membro(con, gruppo_id: str, utente_id: str, *, attore: str) -> None:
    _gruppo_o_errore(con, gruppo_id)
    _utente_o_errore(con, utente_id)
    _registra(con, E_MEMBRO_RIMOSSO, attore=attore, gruppo_id=gruppo_id,
              utente_id=utente_id)
    con.commit()


def definisci_ruolo(con, nome: str, permessi, *, attore: str,
                    ruolo_id: str | None = None) -> str:
    """Crea (ruolo_id=None) o ridefinisce un ruolo con l'insieme COMPLETO dei
    permessi. Il catalogo dei permessi validi e' compito della shell
    (manifest + PERMESSI_KERNEL); qui si verifica solo la forma."""
    nome = (nome or "").strip()
    if not nome:
        raise AuthError("nome ruolo vuoto")
    perm = sorted(set(permessi))
    for p in perm:
        if not isinstance(p, str) or not _RE_PERMESSO.match(p):
            raise AuthError(f"permesso non valido: {p!r} (forma '<modulo>.<azione>')")
    omonimo = con.execute("SELECT id FROM auth_ruoli WHERE nome=?", (nome,)).fetchone()
    if ruolo_id is None:
        if omonimo:
            raise AuthError(f"ruolo gia' esistente: {nome!r}")
        ruolo_id = _nuovo_id()
    else:
        _ruolo_o_errore(con, ruolo_id)
        if omonimo and omonimo["id"] != ruolo_id:
            raise AuthError(f"ruolo gia' esistente: {nome!r}")
    _registra(con, E_RUOLO_DEFINITO, attore=attore, ruolo_id=ruolo_id, nome=nome,
              permessi=json.dumps(perm))
    con.commit()
    return ruolo_id


def assegna_ruolo(con, ruolo_id: str, *, attore: str, utente_id: str | None = None,
                  gruppo_id: str | None = None) -> None:
    _assegnazione(con, E_RUOLO_ASSEGNATO, ruolo_id, attore, utente_id, gruppo_id)


def revoca_ruolo(con, ruolo_id: str, *, attore: str, utente_id: str | None = None,
                 gruppo_id: str | None = None) -> None:
    _assegnazione(con, E_RUOLO_REVOCATO, ruolo_id, attore, utente_id, gruppo_id)


def _assegnazione(con, tipo, ruolo_id, attore, utente_id, gruppo_id) -> None:
    if (utente_id is None) == (gruppo_id is None):
        raise AuthError("indicare esattamente uno fra utente_id e gruppo_id")
    _ruolo_o_errore(con, ruolo_id)
    if utente_id is not None:
        _utente_o_errore(con, utente_id)
    else:
        _gruppo_o_errore(con, gruppo_id)
    _registra(con, tipo, attore=attore, ruolo_id=ruolo_id, utente_id=utente_id,
              gruppo_id=gruppo_id)
    con.commit()


# --- sessioni ------------------------------------------------------------------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def apri_sessione(con, utente_id: str, *, durata_ore: float,
                  ora: datetime | None = None) -> str:
    """Apre una sessione e ritorna il TOKEN da mettere nel cookie. Nel DB va
    solo il suo SHA-256. Attore = l'utente stesso (e' lui che entra)."""
    u = _utente_o_errore(con, utente_id)
    if not u["attivo"]:
        raise AuthError("utente disattivato")
    if durata_ore <= 0:
        raise AuthError("durata_ore deve essere positiva")
    token = secrets.token_urlsafe(32)
    scade = _ora_utc(ora) + timedelta(hours=durata_ore)
    _registra(con, E_SESSIONE_APERTA, attore=utente_id, utente_id=utente_id,
              token_hash=_hash_token(token), scade_il_utc=_iso(scade))
    con.commit()
    return token


def chiudi_sessione(con, token: str) -> bool:
    """Logout. Effetto immediato in tutti i moduli. False se il token non
    corrisponde a una sessione aperta."""
    th = _hash_token(token)
    r = con.execute("SELECT utente_id FROM auth_sessioni WHERE token_hash=?",
                    (th,)).fetchone()
    if r is None:
        return False
    _registra(con, E_SESSIONE_CHIUSA, attore=r["utente_id"],
              utente_id=r["utente_id"], token_hash=th)
    con.commit()
    return True


def sessione(con, token: str | None, *, ora: datetime | None = None) -> dict | None:
    """Utente della sessione (con permessi) se il token e' valido, altrimenti
    None. Valida = aperta, non chiusa, non scaduta, utente attivo. Funziona
    su connessione read-only: e' quello che fanno i moduli a ogni richiesta."""
    if not token:
        return None
    r = con.execute("SELECT utente_id FROM auth_sessioni "
                    "WHERE token_hash=? AND scade_il_utc > ?",
                    (_hash_token(token), _iso(_ora_utc(ora)))).fetchone()
    if r is None:
        return None
    u = utente(con, r["utente_id"])
    if u is None or not u["attivo"]:
        return None
    return _con_permessi(con, u)


# --- backend (seam per LDAP/AD) --------------------------------------------------

class Backend(Protocol):
    """Risponde SOLO a "queste credenziali sono valide, e per chi?".
    Gruppi, ruoli e permessi restano sempre locali al kernel."""
    nome: str

    def autentica(self, con, username: str, password: str) -> dict | None: ...


class BackendLocale:
    """Password verificate contro `credenziali` (hash formato Werkzeug)."""
    nome = "locale"

    def autentica(self, con, username: str, password: str) -> dict | None:
        u = utente_per_nome(con, username or "")
        if u is None or not u["attivo"] or u["backend"] != self.nome:
            _verifica_hash(_HASH_FINTO, password or "")   # tempo costante
            return None
        r = con.execute("SELECT password_hash FROM credenziali WHERE utente_id=?",
                        (u["id"],)).fetchone()
        if r is None or not _verifica_hash(r["password_hash"], password or ""):
            return None
        return u


def login(con, username: str, password: str, *, durata_ore: float,
          backend: Backend | None = None) -> str | None:
    """Verifica le credenziali e, se valide, apre la sessione (token) o None.
    Solo per utenti 'persona': i servizi usano Basic Auth, non sessioni."""
    u = (backend or BackendLocale()).autentica(con, username, password)
    if u is None or u["tipo"] != "persona":
        return None
    return apri_sessione(con, u["id"], durata_ore=durata_ore)


# --- lettura -------------------------------------------------------------------

def utente(con, utente_id: str) -> dict | None:
    r = con.execute("SELECT * FROM auth_utenti WHERE id=?", (utente_id,)).fetchone()
    return dict(r) if r else None


def utente_per_nome(con, username: str) -> dict | None:
    r = con.execute("SELECT * FROM auth_utenti WHERE username=?",
                    (username.strip(),)).fetchone()
    return dict(r) if r else None


def utenti(con) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT * FROM auth_utenti ORDER BY username")]


def gruppi(con) -> list[dict]:
    out = []
    for r in con.execute("SELECT * FROM auth_gruppi ORDER BY nome"):
        g = dict(r)
        g["membri"] = [m[0] for m in con.execute(
            "SELECT utente_id FROM auth_membri WHERE gruppo_id=?", (g["id"],))]
        out.append(g)
    return out


def ruoli(con) -> list[dict]:
    out = []
    for r in con.execute("SELECT * FROM auth_ruoli ORDER BY nome"):
        d = dict(r)
        d["permessi"] = json.loads(d["permessi"])
        out.append(d)
    return out


def permessi_effettivi(con, utente_id: str) -> frozenset[str]:
    return frozenset(r[0] for r in con.execute(
        "SELECT permesso FROM auth_permessi_utente WHERE utente_id=?", (utente_id,)))


def storico(con, *, utente_id: str | None = None) -> list[dict]:
    """Audit trail: eventi del log (di un utente, o tutti), in ordine."""
    if utente_id is None:
        q, par = "SELECT * FROM auth_eventi ORDER BY id", ()
    else:
        q, par = "SELECT * FROM auth_eventi WHERE utente_id=? ORDER BY id", (utente_id,)
    return [dict(r) for r in con.execute(q, par)]


def ha_permesso(utente: dict | None, permesso: str) -> bool:
    """True se `utente` (come in flask.g.utente) ha il permesso."""
    return bool(utente) and permesso in utente.get("permessi", ())


def _con_permessi(con, u: dict) -> dict:
    return {"id": u["id"], "username": u["username"], "nome": u["nome"] or "",
            "tipo": u["tipo"], "permessi": permessi_effettivi(con, u["id"])}


def _utente_o_errore(con, utente_id):
    u = utente(con, utente_id)
    if u is None:
        raise AuthError(f"utente sconosciuto: {utente_id!r}")
    return u


def _gruppo_o_errore(con, gruppo_id):
    if not con.execute("SELECT 1 FROM auth_gruppi WHERE id=?", (gruppo_id,)).fetchone():
        raise AuthError(f"gruppo sconosciuto: {gruppo_id!r}")


def _ruolo_o_errore(con, ruolo_id):
    if not con.execute("SELECT 1 FROM auth_ruoli WHERE id=?", (ruolo_id,)).fetchone():
        raise AuthError(f"ruolo sconosciuto: {ruolo_id!r}")


# --- hash delle password (stdlib, formato Werkzeug) -----------------------------

def hash_password(password: str, *, metodo: str | None = None) -> str:
    """Hash nel formato di werkzeug.security.generate_password_hash."""
    if not isinstance(password, str) or not password:
        raise AuthError("password vuota")
    sale = "".join(secrets.choice(_SALT_CHARS) for _ in range(16))
    h, m = _hash_interno(metodo or _METODO, sale, password)
    return f"{m}${sale}${h}"


def _verifica_hash(password_hash: str, password: str) -> bool:
    """Come werkzeug.security.check_password_hash (scrypt e pbkdf2)."""
    try:
        metodo, sale, atteso = password_hash.split("$", 2)
        calcolato = _hash_interno(metodo, sale, password)[0]
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calcolato, atteso)


def _hash_interno(metodo: str, sale: str, password: str) -> tuple[str, str]:
    nome, *arg = metodo.split(":")
    pw, s = password.encode(), sale.encode()
    if nome == "scrypt":
        n, r, p = map(int, arg) if arg else (2**15, 8, 1)
        return (hashlib.scrypt(pw, salt=s, n=n, r=r, p=p, maxmem=132 * n * r * p).hex(),
                f"scrypt:{n}:{r}:{p}")
    if nome == "pbkdf2":
        algo = arg[0] if arg else "sha256"
        iterazioni = int(arg[1]) if len(arg) > 1 else 600_000
        return (hashlib.pbkdf2_hmac(algo, pw, s, iterazioni).hex(),
                f"pbkdf2:{algo}:{iterazioni}")
    raise ValueError(f"metodo di hash non supportato: {metodo!r}")


_HASH_FINTO = "pbkdf2:sha256:1000$xxxxxxxxxxxxxxxx$" + "0" * 64


def _salva_hash(con, utente_id: str, password_hash: str) -> None:
    con.execute(
        "INSERT INTO credenziali (utente_id, password_hash, aggiornato_il_utc) "
        "VALUES (?,?,?) ON CONFLICT(utente_id) DO UPDATE SET "
        "password_hash=excluded.password_hash, "
        "aggiornato_il_utc=excluded.aggiornato_il_utc",
        (utente_id, password_hash, _iso(_ora_utc())))


# --- integrazione Flask nei moduli (import lazy) ----------------------------------

def richiede_permesso(permesso: str):
    """Decoratore: la route richiede `permesso`, che DEVE essere dichiarato
    nel manifest del modulo (verificato da inizializza(), all'avvio)."""
    def deco(f):
        f._argo_permesso = permesso
        return f
    return deco


def pubblica(f):
    """Decoratore: route accessibile senza sessione (es. /api/health)."""
    f._argo_pubblica = True
    return f


def inizializza(app, *, manifest, auth_db: str | Path, url_login: str | None = None):
    """Collega il modulo all'identita' della suite. Va chiamata DOPO aver
    definito le route. Fail-fast:
      - ogni @richiede_permesso deve usare un permesso dichiarato nel manifest;
      - auth.sqlite deve esistere ed essere allo schema atteso (avvia la shell).
    Da qui in poi OGNI route richiede una sessione valida, salvo @pubblica;
    l'utente e' in flask.g.utente = {id, username, nome, tipo, permessi}.
    """
    from flask import Response, g, redirect, request
    from urllib.parse import quote, urlsplit

    from .manifest import ManifestError

    non_dichiarati = sorted({
        f"{ep}: {v._argo_permesso}" for ep, v in app.view_functions.items()
        if getattr(v, "_argo_permesso", None)
        and not manifest.dichiara_permesso(v._argo_permesso)})
    if non_dichiarati:
        raise ManifestError(f"{manifest.nome}: permessi usati ma non dichiarati "
                            f"nel manifest: {', '.join(non_dichiarati)}")
    auth_db = Path(auth_db)
    if not auth_db.exists():
        raise AuthError(f"{auth_db} non trovato: avvia prima la shell "
                        f"(o python -m core.auth crea-admin)")
    migrazioni.richiedi_versione(auth_db, len(PASSI_AUTH),
                                 suggerimento="Avvia prima la shell.")

    # la sessione Flask del modulo non deve pestare quella di altri moduli
    # sullo stesso host (i cookie non distinguono le porte)
    app.config["SESSION_COOKIE_NAME"] = f"argo_{manifest.nome}"
    app.extensions["argo_auth"] = {"manifest": manifest, "auth_db": auth_db}

    @app.before_request
    def _argo_auth():
        g.utente = None
        vista = app.view_functions.get(request.endpoint)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origine = request.headers.get("Origin")
            if origine and urlsplit(origine).hostname != request.host.split(":")[0]:
                return Response("Origine non ammessa", 403)
        if request.endpoint == "static" or getattr(vista, "_argo_pubblica", False):
            return None
        u = utente_da_richiesta(auth_db, request)
        if u is None:
            if request.method == "GET" and request.accept_mimetypes.accept_html:
                host = request.host.split(":")[0]
                base = url_login or f"http://{host}:{PORTA_SHELL}/login"
                return redirect(f"{base}?next={quote(request.url, safe='')}")
            return Response("Autenticazione richiesta", 401,
                            {"WWW-Authenticate": 'Basic realm="ARGO"'})
        g.utente = u
        permesso = getattr(vista, "_argo_permesso", None)
        if permesso and not ha_permesso(u, permesso):
            return Response(f"Permesso richiesto: {permesso}", 403)
        return None

    return app


def utente_da_richiesta(auth_db: str | Path, request) -> dict | None:
    """Risolve l'utente di una richiesta, in SOLA LETTURA su auth.sqlite:
    cookie di sessione (persone) oppure Basic Auth (solo utenti 'servizio')."""
    con = coredb.readonly(auth_db)
    try:
        token = request.cookies.get(COOKIE)
        if token:
            return sessione(con, token)
        cred = request.authorization
        if cred and cred.username:
            u = BackendLocale().autentica(con, cred.username, cred.password or "")
            if u is not None and u["tipo"] == "servizio":
                return _con_permessi(con, u)
        return None
    finally:
        con.close()


# --- CLI ------------------------------------------------------------------------

def crea_admin(path: str | Path, username: str, password: str) -> str:
    """Bootstrap: crea (se manca) il ruolo Amministratore con i permessi del
    kernel, l'utente e l'assegnazione. Attore: sistema. Ritorna l'ID utente."""
    prepara_db(path)
    con = coredb.owned(path)
    try:
        r = con.execute("SELECT id FROM auth_ruoli WHERE nome=?", (RUOLO_ADMIN,)).fetchone()
        ruolo_id = r["id"] if r else definisci_ruolo(
            con, RUOLO_ADMIN, PERMESSI_KERNEL, attore=SISTEMA)
        uid = crea_utente(con, username, password=password, attore=SISTEMA,
                          nome="Amministratore")
        assegna_ruolo(con, ruolo_id, utente_id=uid, attore=SISTEMA)
        return uid
    finally:
        con.close()


def importa_0x(path: str | Path, db_vecchio: str | Path, *,
               tabella: str = "utenti") -> dict:
    """Importa gli utenti di una tabella 0.x (username, password_hash, ruolo,
    attivo) SENZA reset password: gli hash Werkzeug sono compatibili.
    I vecchi ruoli non diventano permessi: si ritornano da mappare a mano."""
    prepara_db(path)
    vecchio = coredb.readonly(db_vecchio)
    try:
        righe = [dict(r) for r in vecchio.execute(
            f"SELECT * FROM {migrate.ident(tabella)} ORDER BY username")]
    finally:
        vecchio.close()
    con = coredb.owned(path)
    esito = {"importati": [], "gia_presenti": [], "ruoli_da_mappare": {}}
    try:
        for r in righe:
            if utente_per_nome(con, r["username"]) is not None:
                esito["gia_presenti"].append(r["username"])
                continue
            uid = crea_utente(con, r["username"], attore=SISTEMA)
            _salva_hash(con, uid, r["password_hash"])
            _registra(con, E_PASSWORD, attore=SISTEMA, utente_id=uid)
            if not r.get("attivo", 1):
                _registra(con, E_UTENTE_DISATTIVATO, attore=SISTEMA, utente_id=uid)
            con.commit()
            esito["importati"].append(r["username"])
            esito["ruoli_da_mappare"].setdefault(r.get("ruolo") or "", []).append(
                r["username"])
    finally:
        con.close()
    return esito


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.auth",
                                 description="Identita' della suite ARGO (auth.sqlite).")
    ap.add_argument("--comune", default=None,
                    help="cartella dati (default: ARGO_COMUNE o ../comune)")
    sub = ap.add_subparsers(dest="comando", required=True)
    s = sub.add_parser("crea-admin", help="crea il primo amministratore")
    s.add_argument("username")
    s.add_argument("--password-stdin", action="store_true",
                   help="legge la password da stdin (script) invece di chiederla")
    s = sub.add_parser("importa", help="importa gli utenti di un DB di modulo 0.x")
    s.add_argument("--db", required=True, help="DB del modulo 0.x")
    s.add_argument("--tabella", default="utenti")
    a = ap.parse_args(argv)
    path = percorso_db(a.comune)
    path.parent.mkdir(parents=True, exist_ok=True)

    if a.comando == "crea-admin":
        if a.password_stdin:
            password = sys.stdin.readline().rstrip("\n")
        else:
            password = getpass.getpass("Password: ")
            if password != getpass.getpass("Ripeti password: "):
                print("[auth] le password non coincidono", file=sys.stderr)
                return 1
        try:
            uid = crea_admin(path, a.username, password)
        except AuthError as e:
            print(f"[auth] errore: {e}", file=sys.stderr)
            return 1
        print(f"[auth] amministratore {a.username!r} creato ({uid}) in {path}")
        return 0

    esito = importa_0x(path, a.db, tabella=a.tabella)
    print(f"[auth] importati: {len(esito['importati'])}, "
          f"gia' presenti: {len(esito['gia_presenti'])}")
    if esito["ruoli_da_mappare"]:
        print("[auth] vecchi ruoli da mappare a mano su ruoli della suite:")
        for ruolo, nomi in sorted(esito["ruoli_da_mappare"].items()):
            print(f"  {ruolo or '(vuoto)'}: {', '.join(nomi)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
