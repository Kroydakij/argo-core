"""Modulo demo "presenze attrezzatura".

Generato con `python -m core.scaffold presenze --dir examples`, poi esteso a
prova vivente della suite. Traccia il ciclo di vita di alcuni attrezzi
(disponibile / in uso / in manutenzione) usando i mattoni del kernel e delle utility:

  core.events        stato event-sourced sulla busta standard (ADR-005)
  core.auth          login unico della suite, permessi dal manifest (ADR-001)
  core.migrazioni    schema a passi numerati con backup (ADR-004)
  core.statemachine  transizioni ammesse, dichiarate in config
  core.forms         il form del movimento (validazione + render)
  core.schedule      manutenzioni "a tempo di lettura" (nessun job)
  core.board         board config-driven
  core.shifts        turno corrente
  core.config        tutto il dominio (attrezzi, stati, turni) da TOML, fail-fast

Dominio del tutto generico: nessun dato/logica di un'installazione reale.
"""
import os
import sys
from datetime import datetime
from pathlib import Path


def _aggiungi_core_al_path() -> None:
    """Rende importabile core/ risalendo le cartelle. No-op se gia' importabile."""
    try:
        import core  # noqa: F401
        return
    except ImportError:
        pass
    qui = Path(__file__).resolve()
    for base in qui.parents:
        if (base / "core" / "__init__.py").exists():
            sys.path.insert(0, str(base))
            return
    raise RuntimeError("cartella core/ non trovata risalendo da " + str(qui))


_aggiungi_core_al_path()
from core import auth, busta, db, events, forms, manifest  # noqa: E402
from core import board as coreboard  # noqa: E402
from core import config as corecfg   # noqa: E402
from core import migrazioni, schedule, shifts, statemachine  # noqa: E402

QUI = Path(__file__).resolve().parent
CONFIG_PATH = QUI / "presenze.toml"
COMUNE = Path(os.environ.get("ARGO_COMUNE", QUI / "dati"))
DB_PATH = COMUNE / "presenze.sqlite"
AUTH_DB = COMUNE / "auth.sqlite"
MANIFEST = manifest.carica(QUI)
EVENTO = "presenze.cambio_stato"                     # dichiarato in manifest.toml


# --- config ------------------------------------------------------------

def carica_config() -> dict:
    return corecfg.load(CONFIG_PATH)                 # fail-fast


def _macchina(cfg: dict) -> statemachine.StateMachine:
    return statemachine.StateMachine.da_config(corecfg.require(cfg, "macchina"))


def _attrezzi(cfg: dict) -> list[str]:
    return list(corecfg.require(cfg, "attrezzi", "elenco"))


# --- migrazione + seed -------------------------------------------------

PASSI = [migrazioni.Passo(1, "log degli stati degli attrezzi", lambda con: events.migra(con))]


def migrate_db() -> None:
    """DB di proprieta': schema a passi (backup) + seed idempotente degli attrezzi.
    Il seed lo fa il kernel stesso: attore 'sistema'."""
    migrazioni.applica(DB_PATH, PASSI)
    con = db.owned(DB_PATH)
    try:
        cfg = carica_config()
        sm = _macchina(cfg)
        for a in _attrezzi(cfg):                     # ogni attrezzo nuovo -> iniziale
            if not events.storico(con, a):
                events.registra(con, EVENTO, a, sm.iniziale, manifest=MANIFEST,
                                attore_id=busta.SISTEMA)
    finally:
        con.close()


# --- logica di dominio (pura rispetto a una connessione: testabile) ----

def stato_di(con, attrezzo: str, sm: statemachine.StateMachine) -> str:
    st = events.stato_corrente(con, attrezzo)
    return st["stato"] if st else sm.iniziale


def registra_movimento(con, attrezzo: str, azione: str,
                       sm: statemachine.StateMachine, *, attore_id: str | None = None) -> str:
    """Valida la transizione con la macchina a stati, poi appende l'evento.

    Solleva statemachine.TransizioneNonValida se l'azione non e' ammessa dallo
    stato corrente: il log non registra mai un movimento impossibile.
    L'attore e' l'utente della sessione (o quello passato, nei test/script).
    """
    corrente = stato_di(con, attrezzo, sm)
    nuovo = sm.transita(corrente, azione)
    events.registra(con, EVENTO, attrezzo, nuovo, manifest=MANIFEST, attore_id=attore_id)
    return nuovo


def stato_manutenzioni(con, cfg: dict, oggi: str | None = None) -> dict:
    """Stato manutenzioni per attrezzo, calcolato a tempo di lettura.

    L'ultima manutenzione = ultimo evento con stato MANUTENZIONE; core.schedule
    ne deriva ok / da_fare / scaduta secondo la cadenza in config. Le date sono
    in ora LOCALE dell'evento (busta: UTC + offset salvato).
    """
    attrezzi = _attrezzi(cfg)
    giorni = corecfg.optional(cfg, "attrezzi", "manutenzione_giorni", default=30)
    tasks = [{"id": "manut", "soggetto": a, "freq_giorni": giorni, "evento": None}
             for a in attrezzi]
    ultime: dict = {}
    for a in attrezzi:
        manut = [e for e in events.storico(con, a) if e["stato"] == "MANUTENZIONE"]
        if manut:
            quando = busta.ora_locale(manut[-1])
            ultime[("manut", a)] = (quando.strftime("%Y-%m-%d"),
                                    quando.strftime("%Y-%m-%d %H:%M:%S"))
    return schedule.stato_task(tasks, ultime, oggi=oggi)


def con_nomi(stati: list[dict]) -> list[dict]:
    """Aggiunge 'chi' (username) alle righe di stato: la busta registra l'ID
    stabile dell'utente, la UI mostra il nome corrente (letto da auth.sqlite)."""
    nomi = {busta.SISTEMA: "sistema"}
    if AUTH_DB.exists():
        ro = db.readonly(AUTH_DB)
        try:
            nomi.update({u["id"]: u["username"] for u in auth.utenti(ro)})
        finally:
            ro.close()
    return [{**s, "chi": nomi.get(s["attore_id"], s["attore_id"])} for s in stati]


def campi_form(cfg: dict, sm: statemachine.StateMachine) -> list[dict]:
    """Definizione dichiarativa del form del movimento (chi lo fa lo dice la
    sessione: niente campo 'operatore' da compilare a mano)."""
    azioni = sorted({az for s in sm.stati() for az in sm.azioni(s)})
    return [
        {"nome": "attrezzo", "label": "Attrezzo", "tipo": "select",
         "opzioni": _attrezzi(cfg), "obbligatorio": True},
        {"nome": "azione", "label": "Azione", "tipo": "select",
         "opzioni": azioni, "obbligatorio": True},
    ]


# --- Flask (lazy) ------------------------------------------------------

def create_app():
    from flask import (Flask, flash, redirect, render_template, request,
                       url_for)
    cfg = carica_config()
    sm = _macchina(cfg)
    board = coreboard.Board.da_config(corecfg.require(cfg, "board"))
    turni = shifts.Turni.da_config(corecfg.require(cfg, "turni"))
    campi = campi_form(cfg, sm)

    app = Flask(__name__, template_folder=str(QUI / "templates"))
    app.config["TITOLO"] = corecfg.optional(cfg, "app", "titolo", default="Presenze")
    app.secret_key = "demo-presenze"                 # solo per i flash (demo locale)

    @app.get("/")
    @auth.richiede_permesso("presenze.vedi")
    def home():
        con = db.owned(DB_PATH)
        try:
            board_html = board.render_html(con_nomi(events.stato_corrente(con)))
            manut = stato_manutenzioni(con, cfg)
        finally:
            con.close()
        return render_template(
            "index.html", titolo=app.config["TITOLO"],
            board=board_html, form=forms.render_html(campi),
            manutenzioni=manut, turno=turni.turno_di(datetime.now()) or "-")

    @app.post("/movimento")
    @auth.richiede_permesso("presenze.registra_movimento")
    def movimento():
        puliti, errori = forms.valida(campi, request.form)
        if errori:
            flash("Dati non validi: " + ", ".join(f"{k} ({v})"
                                                  for k, v in errori.items()))
            return redirect(url_for("home"))
        con = db.owned(DB_PATH)
        try:
            nuovo = registra_movimento(con, puliti["attrezzo"], puliti["azione"], sm)
            flash(f"{puliti['attrezzo']} → {nuovo}")
        except statemachine.TransizioneNonValida as e:
            flash(str(e))
        finally:
            con.close()
        return redirect(url_for("home"))

    @app.get("/api/health")
    @auth.pubblica
    def health():
        return {"ok": True}

    # per ultima: login unico della suite, permessi dal manifest, cornice
    return auth.inizializza(app, manifest=MANIFEST, auth_db=AUTH_DB)


if __name__ == "__main__":
    migrate_db()
    porta = corecfg.require(carica_config(), "app", "porta")
    create_app().run(host="0.0.0.0", port=porta)
