"""
core.busta — la busta standard degli eventi della suite (kernel, ADR-005).

Ogni log append-only della suite (dei moduli, delle utility, del kernel) ha
queste colonne, con questi nomi e questo significato:

    id          INTEGER PK  ordine locale nel log (monotono)
    uid         TEXT        UUID dell'evento: identita' globale tra DB
    tipo        TEXT        "<modulo>.<evento>", dichiarato nel manifest
    versione    INTEGER     versione dello schema di quel tipo (0 = riga 0.x)
    ts_utc      TEXT        istante UTC, ISO 8601 al millisecondo con 'Z'
    offset_min  INTEGER     scarto dell'ora locale del PC in quel momento
    attore_id   TEXT        chi: ID utente (core.auth) o "sistema"
    entita_id   TEXT        su cosa: ID dell'entita' (anagrafica, ADR-002) o NULL
    sorgente    TEXT        MANUALE | SENSORE

Le colonne DI DOMINIO restano colonne tipizzate accanto alla busta (lo
`stato` di core.events, la `quantita` di un movimento...): niente payload
JSON, i dati restano interrogabili in SQL e leggibili nel browser DB.

Perche' UTC + offset e non UTC + fuso orario: turni e report ragionano in ora
locale; ricavarla da un istante UTC richiederebbe il database dei fusi
(`tzdata`, non in stdlib su Windows). L'offset salvato ricostruisce l'ora
"dell'orologio a muro" senza dipendenze e senza ambiguita' al cambio
dell'ora legale: ora_locale(riga).

Uso in un modulo:

    from core import busta, manifest
    M = manifest.carica(QUI)

    def _p1(con):                       # passo di migrazione
        busta.crea_log(con, "fermate", {"causale": "TEXT", "durata_min": "REAL"})

    busta.scrivi(con, "fermate", tipo="andon.fermata_chiusa", manifest=M,
                 entita_id=eid, dati={"causale": "GUASTO"})   # attore dalla sessione
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from . import migrate

#: le sole origini ammesse per un evento (MANUALE = persona, SENSORE = automazione).
SORGENTI = ("MANUALE", "SENSORE")

#: attore riservato alle azioni del kernel stesso (uguale a core.auth.SISTEMA).
SISTEMA = "sistema"

#: soglia oltre la quale la shell segnala un orologio sfasato (ADR-005).
SOGLIA_OROLOGIO_S = 120

COLONNE = ("id", "uid", "tipo", "versione", "ts_utc", "offset_min",
           "attore_id", "entita_id", "sorgente")


class BustaError(ValueError):
    """Evento non scrivibile: tipo non dichiarato, attore mancante, ecc."""


# --- schema ------------------------------------------------------------------

def ddl_busta() -> str:
    """Le colonne della busta, per una CREATE TABLE nuova (NOT NULL dove serve)."""
    check = ",".join(f"'{s}'" for s in SORGENTI)
    return (
        "id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        "        uid TEXT NOT NULL UNIQUE,\n"
        "        tipo TEXT NOT NULL,\n"
        "        versione INTEGER NOT NULL,\n"
        "        ts_utc TEXT NOT NULL,\n"
        "        offset_min INTEGER NOT NULL,\n"
        "        attore_id TEXT NOT NULL,\n"
        "        entita_id TEXT,\n"
        f"        sorgente TEXT NOT NULL DEFAULT 'MANUALE' CHECK (sorgente IN ({check}))")


def crea_log(con, tabella: str, colonne_dominio: dict[str, str] | None = None) -> None:
    """Crea (IF NOT EXISTS) un log con la busta + colonne di dominio, e gli
    indici su entita_id e tipo. Se il log esiste gia' (anche 0.x senza busta)
    aggiunge in modo additivo le colonne mancanti: busta e dominio."""
    t = migrate.ident(tabella)
    dominio = colonne_dominio or {}
    extra = "".join(f",\n        {migrate.ident(c)} {tipo}" for c, tipo in dominio.items())
    migrate.ensure_table(con, f"CREATE TABLE IF NOT EXISTS {t} (\n        "
                              f"{ddl_busta()}{extra}\n    )")
    aggiungi_busta(con, tabella)
    for c, tipo in dominio.items():
        migrate.ensure_column(con, tabella, c, tipo)
    for col in ("entita_id", "tipo"):
        con.execute(f"CREATE INDEX IF NOT EXISTS "
                    f"{migrate.ident(f'ix_{tabella}_{col}')} ON {t} ({col})")


def aggiungi_busta(con, tabella: str) -> list[str]:
    """Adozione di un log 0.x: aggiunge le colonne della busta che mancano,
    NULLABLE (le righe vecchie restano com'erano: versione NULL = 0.x).
    Mai UPDATE delle righe esistenti (regola append-only). Ritorna le aggiunte."""
    aggiunte = []
    for col, tipo in (("uid", "TEXT"), ("tipo", "TEXT"), ("versione", "INTEGER"),
                      ("ts_utc", "TEXT"), ("offset_min", "INTEGER"),
                      ("attore_id", "TEXT"), ("entita_id", "TEXT")):
        if migrate.ensure_column(con, tabella, col, tipo):
            aggiunte.append(col)
    if "sorgente" not in migrate.table_columns(con, tabella):
        migrate.ensure_column(con, tabella, "sorgente",
                              "TEXT NOT NULL DEFAULT 'MANUALE'")
        aggiunte.append("sorgente")
    if "uid" in aggiunte:
        con.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS "
                    f"{migrate.ident(f'ux_{tabella}_uid')} "
                    f"ON {migrate.ident(tabella)} (uid)")
    return aggiunte


# --- scrittura: il single write-point di tutti i log ---------------------------

def scrivi(con, tabella: str, *, tipo: str, manifest, entita_id: str | None = None,
           attore_id: str | None = None, sorgente: str = "MANUALE",
           dati: dict[str, Any] | None = None, ora: datetime | None = None) -> int:
    """Appende un evento e ritorna il suo id. Solo INSERT, poi commit.

    - `tipo` deve essere dichiarato nel manifest del modulo; la `versione`
      scritta e' quella dichiarata li' come corrente.
    - `entita_id` obbligatorio se il tipo dichiara un'entita', vietato se no.
    - `attore_id`: in una richiesta web si prende da flask.g.utente; fuori da
      una richiesta va passato (ID utente o busta.SISTEMA). Mai dedotto.
    - `ora` (datetime aware): per test e import; default adesso.
    """
    ev = manifest.evento(tipo)
    if ev is None:
        raise BustaError(f"tipo di evento non dichiarato nel manifest di "
                         f"{manifest.nome}: {tipo!r}")
    if ev.entita and not entita_id:
        raise BustaError(f"{tipo}: serve entita_id (entita' di tipo {ev.entita!r})")
    if not ev.entita and entita_id is not None:
        raise BustaError(f"{tipo}: il manifest non dichiara un'entita', "
                         f"entita_id non ammesso")
    return inserisci(con, tabella, tipo=tipo, versione=ev.versione,
                     attore_id=attore_id or attore_dalla_richiesta(),
                     entita_id=entita_id, sorgente=sorgente, dati=dati, ora=ora)


def inserisci(con, tabella: str, *, tipo: str, versione: int, attore_id: str | None,
              entita_id: str | None = None, sorgente: str = "MANUALE",
              dati: dict[str, Any] | None = None, ora: datetime | None = None,
              commit: bool = True) -> int:
    """Scrittura a basso livello, SENZA manifest: per i log del kernel (auth,
    anagrafica) i cui tipi `core.*` non stanno in un manifest di modulo.
    I moduli usano scrivi()."""
    if not attore_id:
        raise BustaError("ogni evento richiede un attore (ID utente o 'sistema')")
    if sorgente not in SORGENTI:
        raise BustaError(f"sorgente non valida: {sorgente!r} (ammesse: {SORGENTI})")
    ts, offset = istante(ora)
    cols = ["uid", "tipo", "versione", "ts_utc", "offset_min", "attore_id",
            "entita_id", "sorgente"]
    vals: list[Any] = [str(uuid.uuid4()), tipo, versione, ts, offset, attore_id,
                       entita_id, sorgente]
    for c, v in (dati or {}).items():
        if c in COLONNE:
            raise BustaError(f"{c!r} e' una colonna della busta, non di dominio")
        cols.append(c)
        vals.append(v)
    cur = con.execute(
        f"INSERT INTO {migrate.ident(tabella)} "
        f"({','.join(migrate.ident(c) for c in cols)}) "
        f"VALUES ({','.join('?' * len(vals))})", vals)
    if commit:
        con.commit()
    return cur.lastrowid


def attore_dalla_richiesta() -> str | None:
    """ID dell'utente della richiesta Flask corrente (core.auth), se c'e'."""
    try:
        from flask import g, has_request_context
    except ImportError:
        return None
    if not has_request_context():
        return None
    u = g.get("utente")
    return u["id"] if u else None


# --- tempo -----------------------------------------------------------------------

def istante(ora: datetime | None = None) -> tuple[str, int]:
    """(ts_utc, offset_min) di `ora` (datetime AWARE) o di adesso. L'offset di
    adesso e' quello locale del PC, letto dal sistema operativo (niente tzdata)."""
    if ora is None:
        ora = datetime.now().astimezone()
    elif ora.tzinfo is None:
        raise BustaError("ora deve avere un fuso (datetime aware)")
    offset = int(ora.utcoffset().total_seconds() // 60)
    utc = ora.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z", offset


def ora_locale(riga) -> datetime:
    """Ora locale "dell'orologio a muro" di un evento.

    Riga con busta: datetime AWARE (UTC + offset salvato). Riga 0.x senza
    busta: datetime NAIVE dalla vecchia colonna `ts` (ora locale di allora).
    """
    r = dict(riga)
    if r.get("ts_utc"):
        utc = datetime.strptime(r["ts_utc"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc)
        return utc.astimezone(timezone(timedelta(minutes=r["offset_min"] or 0)))
    if r.get("ts"):
        return datetime.fromisoformat(r["ts"])
    raise BustaError("riga senza ts_utc ne' ts: non e' un evento")


def e_legacy(riga) -> bool:
    """True per le righe scritte prima della busta (0.x)."""
    return not dict(riga).get("versione")
