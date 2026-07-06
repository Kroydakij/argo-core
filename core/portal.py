"""
core.portal — il portale della suite: la schermata di base.

Processo Flask sulla porta 4700 (blocco riservato alla suite: 4700-4799,
portale = 4700, moduli dal 4701 in su). Funzioni:

  - registro dei moduli (nome, porta, descrizione) con tile cliccabili;
  - health-check: ping HTTP su ogni modulo attivo;
  - browser database read-only su tutti i DB della cartella dati;
  - gestione del registro protetta da Basic Auth.

Ownership: il portale e' l'UNICO scrittore di core.sqlite (registro moduli).

Configurazione: file JSON opzionale in <comune>\portal.json — vive nella
cartella dati, NON dentro core\, cosi' sopravvive agli aggiornamenti di core
(che si fanno sovrascrivendo la cartella core\) con la stessa garanzia dei
dati. Chiavi: {"port": 4700, "titolo": "ARGO"}. La cartella dati si indica
con la variabile d'ambiente ARGO_COMUNE (default: ..\comune rispetto a
questo file).

Credenziali admin: variabili d'ambiente ARGO_PORTAL_USER / ARGO_PORTAL_PASS
(default admin/admin, con avviso a video: protezione "da colleghi", non da
attaccanti — HTTP in chiaro su LAN, come il resto della suite).

Avvio:  python -m core.portal
"""
from __future__ import annotations

import json
import os
import sqlite3
import urllib.request
from functools import wraps
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

from . import adminbrowser
from . import db as coredb
from . import migrate

PORTA_DEFAULT = 4700
PRIMA_PORTA_MODULI = 4701

COMUNE = Path(os.environ.get("ARGO_COMUNE",
                             Path(__file__).resolve().parents[1] / "comune"))
CORE_DB = COMUNE / "core.sqlite"

ADMIN_USER = os.environ.get("ARGO_PORTAL_USER", "admin")
ADMIN_PASS = os.environ.get("ARGO_PORTAL_PASS", "admin")


# --- registro moduli (funzioni pure rispetto a una connessione: testabili) ---

def migrate_core_db(con: sqlite3.Connection) -> None:
    migrate.ensure_table(con, """CREATE TABLE IF NOT EXISTS moduli (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL UNIQUE,
        porta INTEGER NOT NULL,
        descrizione TEXT NOT NULL DEFAULT '',
        attivo INTEGER NOT NULL DEFAULT 1,
        creato_il TEXT DEFAULT (datetime('now','localtime'))
    )""")
    migrate.rebuild_views(con, {})           # nessuna vista oggi; il gancio resta
    con.commit()


def lista_moduli(con) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT * FROM moduli ORDER BY porta")]


def upsert_modulo(con, nome: str, porta: int, descrizione: str = "") -> None:
    con.execute(
        "INSERT INTO moduli (nome, porta, descrizione) VALUES (?,?,?) "
        "ON CONFLICT(nome) DO UPDATE SET porta=excluded.porta, "
        "descrizione=excluded.descrizione",
        (nome.strip(), int(porta), descrizione.strip()))
    con.commit()


def toggle_modulo(con, nome: str, attivo: bool) -> None:
    con.execute("UPDATE moduli SET attivo=? WHERE nome=?",
                (1 if attivo else 0, nome))
    con.commit()


def prossima_porta(con) -> int:
    r = con.execute("SELECT MAX(porta) FROM moduli WHERE porta >= ?",
                    (PRIMA_PORTA_MODULI,)).fetchone()[0]
    return (r + 1) if r else PRIMA_PORTA_MODULI


def check_salute(porta: int, timeout: float = 1.0) -> bool:
    """True se sulla porta risponde QUALCOSA via HTTP (anche 401/404: vivo)."""
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{porta}/", timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True                          # risposta HTTP = processo vivo
    except Exception:
        return False


# --- app -----------------------------------------------------------------

def _leggi_config(comune: Path) -> dict:
    p = comune / "portal.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[PORTALE] portal.json illeggibile ({e}), uso i default")
    return {}


def richiede_admin(f):
    @wraps(f)
    def w(*a, **k):
        auth = request.authorization
        if not auth or auth.username != ADMIN_USER or auth.password != ADMIN_PASS:
            return Response("Autenticazione richiesta", 401,
                            {"WWW-Authenticate": 'Basic realm="ARGO Portale"'})
        return f(*a, **k)
    return w


def _dbs_in_comune(comune: Path):
    def _scan() -> dict:
        out = {}
        for ext in ("*.db", "*.sqlite", "*.sqlite3"):
            for p in sorted(comune.glob(ext)):
                out[p.stem] = p
        return out
    return _scan


def create_app(comune: Path | None = None) -> Flask:
    comune = Path(comune or COMUNE)
    comune.mkdir(parents=True, exist_ok=True)
    core_db = comune / "core.sqlite"

    con = coredb.owned(core_db)
    migrate_core_db(con)
    con.close()

    app = Flask(__name__)
    cfg = _leggi_config(comune)
    app.config["TITOLO"] = cfg.get("titolo", "ARGO")
    app.config["PORTA"] = int(cfg.get("port", PORTA_DEFAULT))

    def get_db():
        return coredb.owned(core_db)

    app.register_blueprint(
        adminbrowser.blueprint(_dbs_in_comune(comune), auth=richiede_admin),
        url_prefix="/api/db")

    @app.get("/")
    def home():
        return render_template("portal.html", titolo=app.config["TITOLO"])

    @app.get("/api/moduli")
    def api_moduli():
        con = get_db()
        try:
            mods = lista_moduli(con)
            return jsonify({"moduli": mods,
                            "prossima_porta": prossima_porta(con),
                            "host": request.host.split(":")[0]})
        finally:
            con.close()

    @app.post("/api/moduli")
    @richiede_admin
    def api_moduli_upsert():
        d = request.get_json(force=True, silent=True) or {}
        nome, porta = (d.get("nome") or "").strip(), d.get("porta")
        if not nome or not porta:
            return jsonify({"ok": False, "msg": "nome e porta obbligatori"}), 400
        con = get_db()
        try:
            upsert_modulo(con, nome, int(porta), d.get("descrizione", ""))
            return jsonify({"ok": True})
        finally:
            con.close()

    @app.post("/api/moduli/<nome>/toggle")
    @richiede_admin
    def api_moduli_toggle(nome):
        d = request.get_json(force=True, silent=True) or {}
        con = get_db()
        try:
            toggle_modulo(con, nome, bool(d.get("attivo", True)))
            return jsonify({"ok": True})
        finally:
            con.close()

    @app.get("/api/health")
    def api_health():
        con = get_db()
        try:
            mods = [m for m in lista_moduli(con) if m["attivo"]]
        finally:
            con.close()
        return jsonify({m["nome"]: check_salute(m["porta"]) for m in mods})

    return app


if __name__ == "__main__":
    if ADMIN_PASS == "admin":
        print("[PORTALE] ATTENZIONE: credenziali admin di default. "
              "Impostare ARGO_PORTAL_USER / ARGO_PORTAL_PASS.")
    application = create_app()
    application.run(host="0.0.0.0", port=application.config["PORTA"])
