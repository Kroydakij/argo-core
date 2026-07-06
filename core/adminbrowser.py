"""
core.adminbrowser — browser database READ-ONLY come blueprint Flask riusabile.

Qualunque modulo (e il portale) puo' montarlo per ispezionare uno o piu'
database senza poterli modificare: le connessioni sono aperte con mode=ro,
quindi l'immutabilita' e' garantita dal motore SQLite, non dalla disciplina.
Le modifiche ai dati passano SOLO dagli editor dedicati di ogni modulo:
editare a mano log append-only o contatori operativi corrompe i KPI in modo
silenzioso.

Uso:

    from core import adminbrowser
    bp = adminbrowser.blueprint({"andon": r"..\\comune\\andon.db"},
                                auth=richiede_admin)
    app.register_blueprint(bp, url_prefix="/api/db")

`dbs` puo' essere un dict {alias: path} oppure una funzione che lo ritorna
(per elenchi dinamici, es. scansione di una cartella).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from flask import Blueprint, abort, jsonify, request

from . import db as coredb
from .export import csv_response
from .migrate import _ident

RIGHE_PER_PAGINA = 50


def blueprint(dbs: dict | Callable[[], dict], auth: Callable | None = None,
              name: str = "adminbrowser") -> Blueprint:
    bp = Blueprint(name, __name__)
    wrap = auth if auth is not None else (lambda f: f)

    def _mappa() -> dict[str, Path]:
        m = dbs() if callable(dbs) else dbs
        return {str(k): Path(v) for k, v in m.items()}

    def _open(alias: str):
        m = _mappa()
        if alias not in m or not m[alias].exists():
            abort(404, "database sconosciuto")
        return coredb.readonly(m[alias])

    def _tabella_esiste(con, tab: str) -> bool:
        return con.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
            (tab,)).fetchone() is not None

    @bp.get("/databases")
    @wrap
    def databases():
        return jsonify([{"alias": a, "file": p.name, "esiste": p.exists()}
                        for a, p in sorted(_mappa().items())])

    @bp.get("/<alias>/tabelle")
    @wrap
    def tabelle(alias):
        con = _open(alias)
        try:
            out = []
            for r in con.execute(
                    "SELECT name, type FROM sqlite_master "
                    "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
                    "ORDER BY type, name"):
                n = con.execute(f"SELECT COUNT(*) FROM {_ident(r['name'])}").fetchone()[0]
                out.append({"nome": r["name"], "tipo": r["type"], "righe": n})
            return jsonify(out)
        finally:
            con.close()

    @bp.get("/<alias>/righe/<tab>")
    @wrap
    def righe(alias, tab):
        con = _open(alias)
        try:
            if not _tabella_esiste(con, tab):
                abort(404, "tabella inesistente")
            off = max(0, request.args.get("offset", 0, type=int))
            rows = con.execute(
                f"SELECT * FROM {_ident(tab)} ORDER BY rowid DESC LIMIT ? OFFSET ?",
                (RIGHE_PER_PAGINA, off)).fetchall()
            return jsonify({"colonne": list(rows[0].keys()) if rows else [],
                            "righe": [list(r) for r in rows], "offset": off})
        finally:
            con.close()

    @bp.get("/<alias>/export/<tab>.csv")
    @wrap
    def export_csv(alias, tab):
        con = _open(alias)
        try:
            if not _tabella_esiste(con, tab):
                abort(404, "tabella inesistente")
            rows = con.execute(f"SELECT * FROM {_ident(tab)}").fetchall()
            return csv_response(rows, f"{alias}_{tab}")
        finally:
            con.close()

    return bp
