"""
core.shell — la shell della suite: login unico, cornice, menu, amministrazione.

Kernel (ADR-001, ex core.portal). Processo Flask sulla porta 4700 (blocco
della suite 4700-4799). E' l'UNICO scrittore dei DB del kernel nella cartella
dati comune:
  - core.sqlite  registro dei moduli, scoperti dai manifest (ADR-003);
  - auth.sqlite  utenti, gruppi, ruoli, sessioni (core.auth).

Cosa fa:
  - login/logout: imposta il cookie `argo_sessione`, valido per tutti i moduli
    sullo stesso host (i cookie non distinguono le porte);
  - home: i moduli che l'utente puo' usare, nella cornice comune;
  - "cambia password" per tutti;
  - amministrazione utenti/gruppi/ruoli (permesso core.utenti);
  - registro moduli, health-check, browser DB read-only (permesso core.admin).

Configurazione: comune/argo.toml (obbligatorio, vedi core.config.carica_suite).
Primo avvio: la shell non parte senza almeno un utente:

    python -m core.auth crea-admin <username>
    python -m core.shell
"""
from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from flask import (Flask, Response, g, jsonify, redirect, render_template,
                   request)

from . import __version__, adminbrowser, auth, busta, registro
from . import config as corecfg
from . import db as coredb
from . import manifest as coremanifest
from . import migrazioni

#: cartella della suite: core\ e le cartelle dei moduli sono sue figlie.
RADICE_SUITE = Path(__file__).resolve().parents[1]
COMUNE = Path(os.environ.get("ARGO_COMUNE", RADICE_SUITE / "comune"))

# riesportati per chi usava core.portal
PRIMA_PORTA_MODULI = registro.PRIMA_PORTA_MODULI
PORTA_DEFAULT = auth.PORTA_SHELL


def check_salute(porta: int, timeout: float = 1.0) -> bool:
    """True se sulla porta risponde QUALCOSA via HTTP (anche 401/404: vivo)."""
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{porta}/", timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True                          # risposta HTTP = processo vivo
    except Exception:
        return False


def sfasamento_orologio(porta: int, timeout: float = 1.0) -> float | None:
    """Secondi di differenza fra l'orologio del modulo (header HTTP Date) e
    quello della shell; None se il modulo non risponde. La busta registra
    l'ora del PC che scrive: un orologio sbagliato va segnalato (ADR-005)."""
    from email.utils import parsedate_to_datetime
    from datetime import datetime, timezone
    try:
        r = urllib.request.urlopen(f"http://127.0.0.1:{porta}/api/health",
                                   timeout=timeout)
    except urllib.error.HTTPError as e:
        r = e
    except Exception:
        return None
    data = r.headers.get("Date")
    if not data:
        return None
    return (parsedate_to_datetime(data) - datetime.now(timezone.utc)).total_seconds()


def manifest_shell(titolo: str = "ARGO") -> coremanifest.Manifest:
    """La shell dichiara i permessi del kernel come un modulo qualunque."""
    return coremanifest.da_dict({
        "modulo": {"nome": "core", "versione": __version__, "core": ">=0.0",
                   "titolo": titolo},
        "permessi": [{"id": k, "descrizione": v}
                     for k, v in auth.PERMESSI_KERNEL.items()],
    })


def _dbs_in_comune(comune: Path):
    def _scan() -> dict:
        out = {}
        for ext in ("*.db", "*.sqlite", "*.sqlite3"):
            for p in sorted(comune.glob(ext)):
                out[p.stem] = p
        return out
    return _scan


def create_app(comune: Path | None = None, radice: Path | None = None) -> Flask:
    """Fail-fast: argo.toml valido e almeno un utente, o la shell non parte.
    radice: cartella della suite da scansionare per i manifest dei moduli."""
    comune = Path(comune or COMUNE)
    radice = Path(radice or RADICE_SUITE)
    comune.mkdir(parents=True, exist_ok=True)
    suite = corecfg.carica_suite(comune)
    core_db, auth_db = comune / "core.sqlite", auth.percorso_db(comune)

    migrazioni.applica(core_db, registro.PASSI_CORE)   # backup + versione
    auth.prepara_db(auth_db)
    con = coredb.owned(auth_db)
    try:
        if not auth.utenti(con):
            raise auth.AuthError(
                "nessun utente: crea il primo amministratore con\n"
                "    python -m core.auth crea-admin <username>")
    finally:
        con.close()

    def db_core():
        return coredb.owned(core_db)

    def db_auth():
        return coredb.owned(auth_db)

    def rileggi() -> dict:
        con = db_core()
        try:
            return registro.registra_scansione(con, coremanifest.scansiona(radice))
        finally:
            con.close()

    esito = rileggi()                        # all'avvio: deploy = copia cartella
    if esito["errore"]:
        print(f"[SHELL] {esito['errore']} modulo/i con manifest non valido: "
              f"vedi la home (amministratori)")

    app = Flask(__name__)
    app.config.update(TITOLO=suite["titolo"], PORTA=suite["porta"],
                      DURATA_ORE=suite["durata_sessione_ore"])

    app.register_blueprint(
        adminbrowser.blueprint(_dbs_in_comune(comune),
                               auth=auth.richiede_permesso("core.admin")),
        url_prefix="/api/db")

    def next_sicuro(url: str | None) -> str:
        """Solo la shell stessa o un modulo del registro sullo stesso host:
        niente open redirect verso siti esterni."""
        if not url:
            return "/"
        if url.startswith("/") and not url.startswith("//"):
            return url
        p = urlsplit(url)
        if p.scheme != "http" or p.hostname != request.host.split(":")[0]:
            return "/"
        con = db_core()
        try:
            ammesse = registro.porte(con) | {suite["porta"]}
        finally:
            con.close()
        return url if (p.port or 80) in ammesse else "/"

    def esito_auth(f, *a, **k):
        try:
            return jsonify({"ok": True, **(f(*a, **k) or {})})
        except auth.AuthError as e:
            return jsonify({"ok": False, "msg": str(e)}), 400

    # --- login / logout / password ---------------------------------------

    @app.get("/login")
    @auth.pubblica
    def login_form():
        return render_template("shell_login.html", titolo=suite["titolo"],
                               next=request.args.get("next", ""), errore=None)

    @app.post("/login")
    @auth.pubblica
    def login():
        f = request.form
        con = db_auth()
        try:
            token = auth.login(con, f.get("username", ""), f.get("password", ""),
                               durata_ore=suite["durata_sessione_ore"])
        finally:
            con.close()
        if token is None:
            return render_template("shell_login.html", titolo=suite["titolo"],
                                   next=f.get("next", ""),
                                   errore="Credenziali non valide"), 401
        r = redirect(next_sicuro(f.get("next")))
        r.set_cookie(auth.COOKIE, token, max_age=int(suite["durata_sessione_ore"] * 3600),
                     httponly=True, samesite="Lax", path="/")
        return r

    @app.post("/logout")
    @auth.pubblica
    def logout():
        token = request.cookies.get(auth.COOKIE)
        if token:
            con = db_auth()
            try:
                auth.chiudi_sessione(con, token)
            finally:
                con.close()
        r = redirect("/login")
        r.delete_cookie(auth.COOKIE, path="/")
        return r

    @app.get("/password")
    def password_form():
        return render_template("shell_password.html", esito=None)

    @app.post("/password")
    def password():
        f = request.form
        con = db_auth()
        try:
            if auth.BackendLocale().autentica(con, g.utente["username"],
                                              f.get("attuale", "")) is None:
                esito = ("errore", "Password attuale errata")
            elif not f.get("nuova") or f.get("nuova") != f.get("conferma"):
                esito = ("errore", "Le nuove password non coincidono")
            else:
                auth.imposta_password(con, g.utente["id"], f["nuova"],
                                      attore=g.utente["id"])
                esito = ("ok", "Password aggiornata")
        finally:
            con.close()
        return render_template("shell_password.html", esito=esito), \
            (200 if esito[0] == "ok" else 400)

    # --- home e registro moduli ---------------------------------------------

    @app.get("/")
    def home():
        return render_template(
            "shell_home.html",
            admin=auth.ha_permesso(g.utente, "core.admin"),
            gestione_utenti=auth.ha_permesso(g.utente, "core.utenti"))

    @app.get("/api/moduli")
    @auth.pubblica                           # lo scaffolder legge prossima_porta
    def api_moduli():
        con = db_core()
        try:
            return jsonify({"moduli": registro.lista_moduli(con),
                            "prossima_porta": registro.prossima_porta(con),
                            "host": request.host.split(":")[0]})
        finally:
            con.close()

    @app.post("/api/moduli")
    @auth.richiede_permesso("core.admin")
    def api_moduli_upsert():
        """Registrazione manuale: solo per moduli legacy senza manifest."""
        d = request.get_json(force=True, silent=True) or {}
        nome, porta = (d.get("nome") or "").strip(), d.get("porta")
        if not nome or not porta:
            return jsonify({"ok": False, "msg": "nome e porta obbligatori"}), 400
        con = db_core()
        try:
            registro.upsert_modulo(con, nome, int(porta), d.get("descrizione", ""))
            return jsonify({"ok": True})
        finally:
            con.close()

    @app.post("/api/moduli/rileggi")
    @auth.richiede_permesso("core.admin")
    def api_moduli_rileggi():
        return jsonify({"ok": True, "moduli": rileggi()})

    @app.post("/api/moduli/<nome>/toggle")
    @auth.richiede_permesso("core.admin")
    def api_moduli_toggle(nome):
        d = request.get_json(force=True, silent=True) or {}
        con = db_core()
        try:
            registro.toggle_modulo(con, nome, bool(d.get("attivo", True)))
            return jsonify({"ok": True})
        finally:
            con.close()

    @app.get("/api/menu")
    def api_menu():
        con = db_core()
        try:
            return jsonify(registro.menu_per(con, g.utente["permessi"],
                                             request.host.split(":")[0]))
        finally:
            con.close()

    @app.get("/api/health")
    @auth.pubblica
    def api_health():
        con = db_core()
        try:
            mods = [m for m in registro.lista_moduli(con)
                    if m["attivo"] and m["stato"] == "ok"]
        finally:
            con.close()
        return jsonify({m["nome"]: check_salute(m["porta"]) for m in mods})

    @app.get("/api/orologi")
    @auth.richiede_permesso("core.admin")
    def api_orologi():
        """Moduli con l'orologio sfasato oltre busta.SOGLIA_OROLOGIO_S."""
        con = db_core()
        try:
            mods = [m for m in registro.lista_moduli(con)
                    if m["attivo"] and m["stato"] == "ok"]
        finally:
            con.close()
        fuori = {}
        for m in mods:
            s = sfasamento_orologio(m["porta"])
            if s is not None and abs(s) > busta.SOGLIA_OROLOGIO_S:
                fuori[m["nome"]] = round(s)
        return jsonify({"soglia_s": busta.SOGLIA_OROLOGIO_S, "sfasati": fuori})

    # --- amministrazione utenti (core.utenti) ----------------------------------

    @app.get("/utenti")
    @auth.richiede_permesso("core.utenti")
    def pagina_utenti():
        return render_template("shell_utenti.html")

    @app.get("/api/utenti")
    @auth.richiede_permesso("core.utenti")
    def api_utenti():
        con, cc = db_auth(), db_core()
        try:
            catalogo = [{"id": k, "descrizione": v, "modulo": "core"}
                        for k, v in auth.PERMESSI_KERNEL.items()]
            for m in registro.manifesti(cc):
                catalogo += [{"id": p["id"], "descrizione": p["descrizione"],
                              "modulo": m["nome"]} for p in m["permessi"]]
            return jsonify({"utenti": auth.utenti(con), "gruppi": auth.gruppi(con),
                            "ruoli": auth.ruoli(con), "catalogo": catalogo,
                            "assegnazioni": [dict(r) for r in con.execute(
                                "SELECT * FROM auth_assegnazioni")]})
        finally:
            con.close()
            cc.close()

    def _con_auth(f):
        con = db_auth()
        try:
            return f(con)
        finally:
            con.close()

    def _json():
        return request.get_json(force=True, silent=True) or {}

    @app.post("/api/utenti")
    @auth.richiede_permesso("core.utenti")
    def api_utente_nuovo():
        d = _json()
        return esito_auth(lambda: _con_auth(lambda con: {"id": auth.crea_utente(
            con, d.get("username", ""), password=d.get("password") or None,
            nome=d.get("nome", ""), tipo=d.get("tipo", "persona"),
            attore=g.utente["id"])}))

    @app.post("/api/utenti/<uid>/password")
    @auth.richiede_permesso("core.utenti")
    def api_utente_password(uid):
        d = _json()
        return esito_auth(lambda: _con_auth(lambda con: auth.imposta_password(
            con, uid, d.get("password", ""), attore=g.utente["id"])))

    @app.post("/api/utenti/<uid>/attivo")
    @auth.richiede_permesso("core.utenti")
    def api_utente_attivo(uid):
        attivo = bool(_json().get("attivo"))
        if not attivo and uid == g.utente["id"]:
            return jsonify({"ok": False, "msg": "non puoi disattivare te stesso"}), 400
        f = auth.riattiva_utente if attivo else auth.disattiva_utente
        return esito_auth(lambda: _con_auth(lambda con: f(con, uid,
                                                           attore=g.utente["id"])))

    @app.post("/api/gruppi")
    @auth.richiede_permesso("core.utenti")
    def api_gruppo_nuovo():
        d = _json()
        return esito_auth(lambda: _con_auth(lambda con: {"id": auth.crea_gruppo(
            con, d.get("nome", ""), attore=g.utente["id"])}))

    @app.post("/api/gruppi/<gid>/membri")
    @auth.richiede_permesso("core.utenti")
    def api_gruppo_membri(gid):
        d = _json()
        f = auth.rimuovi_membro if d.get("rimuovi") else auth.aggiungi_membro
        return esito_auth(lambda: _con_auth(lambda con: f(
            con, gid, d.get("utente_id", ""), attore=g.utente["id"])))

    @app.post("/api/ruoli")
    @auth.richiede_permesso("core.utenti")
    def api_ruolo():
        d = _json()
        return esito_auth(lambda: _con_auth(lambda con: {"id": auth.definisci_ruolo(
            con, d.get("nome", ""), d.get("permessi", []),
            ruolo_id=d.get("ruolo_id") or None, attore=g.utente["id"])}))

    @app.post("/api/ruoli/<rid>/assegnazioni")
    @auth.richiede_permesso("core.utenti")
    def api_ruolo_assegna(rid):
        d = _json()
        f = auth.revoca_ruolo if d.get("revoca") else auth.assegna_ruolo
        return esito_auth(lambda: _con_auth(lambda con: f(
            con, rid, utente_id=d.get("utente_id") or None,
            gruppo_id=d.get("gruppo_id") or None, attore=g.utente["id"])))

    return auth.inizializza(app, manifest=manifest_shell(suite["titolo"]),
                            auth_db=auth_db, url_login="/login", core_db=core_db)


def main() -> int:
    try:
        app = create_app()
    except (corecfg.ConfigError, auth.AuthError, migrazioni.MigrazioneError) as e:
        print(f"[SHELL] avvio negato: {e}", file=sys.stderr)
        return 1
    app.run(host="0.0.0.0", port=app.config["PORTA"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
