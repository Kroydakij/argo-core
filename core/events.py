"""
core.events — log di stati event-sourced, costruito sulla busta (ADR-005).

Il pattern in una riga: non si aggiorna MAI lo stato di un'entita', si
APPENDE un evento che ne dichiara il nuovo stato. Lo stato corrente e' una
proiezione dello storico (vista `latest_state_per_entity`: per ogni entita',
l'evento piu' recente).

Ogni riga ha la busta standard (core.busta: uid, tipo, versione, ts_utc,
offset_min, attore_id, entita_id, sorgente) + le colonne di dominio `stato`,
`note` ed eventuali colonne extra del modulo. Un log di stati ha UN tipo di
evento, dichiarato nel manifest del modulo (es. "presenze.cambio_stato").

Uso in un modulo:

    from core import events, manifest, migrazioni
    M = manifest.carica(QUI)
    PASSI = [migrazioni.Passo(1, "log stati", lambda con: events.migra(con))]

    events.registra(con, "presenze.cambio_stato", attrezzo_id, "IN_USO",
                    manifest=M)                  # attore dalla sessione
    events.stato_corrente(con, attrezzo_id)      # -> {..., 'stato': 'IN_USO'}

Log 0.x (entita/operatore/ts senza busta): migra() aggiunge la busta in modo
additivo; le righe vecchie non si riscrivono e restano leggibili: la chiave di
un'entita' e' `entita_id` per le righe nuove e il vecchio codice `entita` per
quelle 0.x (la risoluzione dei codici in ID arriva con l'anagrafica, ADR-002).
"""
from __future__ import annotations

import warnings
from typing import Any

from . import busta, migrate
from .busta import SORGENTI  # noqa: F401  (riesportato, API 0.x)

TABELLA_DEFAULT = "eventi"
VISTA_DEFAULT = "latest_state_per_entity"


def migra(con, table: str = TABELLA_DEFAULT, vista: str = VISTA_DEFAULT,
          *, extra_colonne: dict[str, str] | None = None) -> None:
    """Crea/aggiorna log + proiezione. Additiva; ricrea la vista alla fine.

    Su un log 0.x aggiunge la busta (colonne nullable, righe vecchie intatte).
    extra_colonne: {nome: tipo_ddl} colonne di dominio in piu' del modulo.
    """
    busta.crea_log(con, table, {"stato": "TEXT NOT NULL DEFAULT ''",
                                "note": "TEXT", **(extra_colonne or {})})
    migrate.rebuild_views(con, {vista: ddl_proiezione(con, table, vista)})


def ddl_proiezione(con, table: str = TABELLA_DEFAULT,
                   vista: str = VISTA_DEFAULT) -> str:
    """Vista: l'evento con id massimo per ogni chiave di entita'. La chiave e'
    entita_id, o il vecchio codice `entita` per le righe 0.x (solo se la
    tabella viene dalla 0.x)."""
    t, v = migrate.ident(table), migrate.ident(vista)
    legacy = "entita" in migrate.table_columns(con, table)
    chiave = "COALESCE(entita_id, entita)" if legacy else "entita_id"
    return (f"CREATE VIEW {v} AS SELECT e.*, u._chiave FROM {t} e "
            f"JOIN (SELECT {chiave} AS _chiave, MAX(id) AS _mid FROM {t} "
            f"WHERE {chiave} IS NOT NULL GROUP BY {chiave}) u ON e.id = u._mid")


def registra(con, tipo: str, entita_id: str, stato: str, *, manifest,
             table: str = TABELLA_DEFAULT, sorgente: str = "MANUALE",
             attore_id: str | None = None, note: str | None = None,
             extra: dict[str, Any] | None = None, operatore: str | None = None,
             ora=None) -> int:
    """SINGLE WRITE-POINT del log di stati: appende un evento (busta.scrivi).

    `tipo` dichiarato nel manifest; attore dalla sessione o esplicito.
    `operatore` e' DEPRECATO (0.x): finisce solo in `note`.
    """
    if operatore is not None:
        warnings.warn("events.registra(operatore=) e' deprecato: l'attore e' "
                      "l'utente della sessione (attore_id)", DeprecationWarning,
                      stacklevel=2)
        note = f"operatore: {operatore}" + (f" · {note}" if note else "")
    dati = {"stato": stato, "note": note, **(extra or {})}
    if "entita" in migrate.table_columns(con, table):
        dati["entita"] = entita_id           # log 0.x: colonna NOT NULL, stessa chiave
    return busta.scrivi(con, table, tipo=tipo, manifest=manifest,
                        entita_id=entita_id, attore_id=attore_id, sorgente=sorgente,
                        dati=dati, ora=ora)


def stato_corrente(con, entita_id: str | None = None, *, vista: str = VISTA_DEFAULT):
    """Stato corrente via proiezione.

    entita_id=None -> lista di tutti gli stati correnti (una riga per entita').
    entita_id="X"  -> dict dello stato corrente di X, oppure None se mai vista.
    Ogni riga ha anche `entita` = la chiave (comodo per board e UI).
    """
    if entita_id is None:
        return [_riga(r) for r in con.execute(
            f"SELECT * FROM {migrate.ident(vista)} ORDER BY _chiave")]
    r = con.execute(f"SELECT * FROM {migrate.ident(vista)} WHERE _chiave=?",
                    (entita_id,)).fetchone()
    return _riga(r) if r else None


def storico(con, entita_id: str, *, table: str = TABELLA_DEFAULT) -> list[dict]:
    """Storico completo append-only di un'entita', in ordine cronologico."""
    legacy = "entita" in migrate.table_columns(con, table)
    cond = "COALESCE(entita_id, entita)=?" if legacy else "entita_id=?"
    return [_riga(r) for r in con.execute(
        f"SELECT * FROM {migrate.ident(table)} WHERE {cond} ORDER BY id",
        (entita_id,))]


def _riga(r) -> dict:
    d = dict(r)
    d["entita"] = d.pop("_chiave", None) or d.get("entita_id") or d.get("entita")
    return d
