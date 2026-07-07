"""Modulo demo "presenze attrezzatura".

Generato con `python -m core.scaffold presenze --dir examples`, poi esteso a
prova vivente della suite. Traccia il ciclo di vita di alcuni attrezzi
(disponibile / in uso / in manutenzione) usando i mattoni della Fase 2:

  core.events        stato event-sourced (log append-only + proiezione)
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
from core import board as coreboard  # noqa: E402
from core import config as corecfg   # noqa: E402
from core import db, events, forms, schedule, shifts, statemachine  # noqa: E402

QUI = Path(__file__).resolve().parent
CONFIG_PATH = QUI / "presenze.toml"
COMUNE = Path(os.environ.get("ARGO_COMUNE", QUI / "dati"))
DB_PATH = COMUNE / "presenze.sqlite"


# --- config ------------------------------------------------------------

def carica_config() -> dict:
    return corecfg.load(CONFIG_PATH)                 # fail-fast


def _macchina(cfg: dict) -> statemachine.StateMachine:
    return statemachine.StateMachine.da_config(corecfg.require(cfg, "macchina"))


def _attrezzi(cfg: dict) -> list[str]:
    return list(corecfg.require(cfg, "attrezzi", "elenco"))


# --- migrazione + seed -------------------------------------------------

def migrate_db() -> None:
    """DB di proprieta': log eventi + seed idempotente degli attrezzi."""
    COMUNE.mkdir(parents=True, exist_ok=True)
    con = db.owned(DB_PATH)
    events.migra(con)                                # log + latest_state_per_entity
    cfg = carica_config()
    sm = _macchina(cfg)
    for a in _attrezzi(cfg):                         # ogni attrezzo nuovo -> iniziale
        if not events.storico(con, a):
            events.registra(con, a, sm.iniziale, operatore="sistema")
    con.commit()
    con.close()


# --- logica di dominio (pura rispetto a una connessione: testabile) ----

def stato_di(con, attrezzo: str, sm: statemachine.StateMachine) -> str:
    st = events.stato_corrente(con, attrezzo)
    return st["stato"] if st else sm.iniziale


def registra_movimento(con, attrezzo: str, azione: str, operatore: str,
                       sm: statemachine.StateMachine) -> str:
    """Valida la transizione con la macchina a stati, poi appende l'evento.

    Solleva statemachine.TransizioneNonValida se l'azione non e' ammessa dallo
    stato corrente: il log non registra mai un movimento impossibile.
    """
    corrente = stato_di(con, attrezzo, sm)
    nuovo = sm.transita(corrente, azione)
    events.registra(con, attrezzo, nuovo, operatore=operatore)
    return nuovo


def stato_manutenzioni(con, cfg: dict, oggi: str | None = None) -> dict:
    """Stato manutenzioni per attrezzo, calcolato a tempo di lettura.

    L'ultima manutenzione = ultimo evento con stato MANUTENZIONE; core.schedule
    ne deriva ok / da_fare / scaduta secondo la cadenza in config.
    """
    attrezzi = _attrezzi(cfg)
    giorni = corecfg.optional(cfg, "attrezzi", "manutenzione_giorni", default=30)
    tasks = [{"id": "manut", "soggetto": a, "freq_giorni": giorni, "evento": None}
             for a in attrezzi]
    ultime: dict = {}
    for a in attrezzi:
        manut = [e for e in events.storico(con, a) if e["stato"] == "MANUTENZIONE"]
        if manut:
            ts = manut[-1]["ts"]
            ultime[("manut", a)] = (ts[:10], ts)
    return schedule.stato_task(tasks, ultime, oggi=oggi)


def campi_form(cfg: dict, sm: statemachine.StateMachine) -> list[dict]:
    """Definizione dichiarativa del form del movimento."""
    azioni = sorted({az for s in sm.stati() for az in sm.azioni(s)})
    return [
        {"nome": "attrezzo", "label": "Attrezzo", "tipo": "select",
         "opzioni": _attrezzi(cfg), "obbligatorio": True},
        {"nome": "azione", "label": "Azione", "tipo": "select",
         "opzioni": azioni, "obbligatorio": True},
        {"nome": "operatore", "label": "Operatore", "tipo": "text",
         "obbligatorio": True},
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
    def home():
        con = db.owned(DB_PATH)
        try:
            board_html = board.render_html(events.stato_corrente(con))
            manut = stato_manutenzioni(con, cfg)
        finally:
            con.close()
        return render_template(
            "index.html", titolo=app.config["TITOLO"],
            board=board_html, form=forms.render_html(campi),
            manutenzioni=manut, turno=turni.turno_di(datetime.now()) or "-")

    @app.post("/movimento")
    def movimento():
        puliti, errori = forms.valida(campi, request.form)
        if errori:
            flash("Dati non validi: " + ", ".join(f"{k} ({v})"
                                                  for k, v in errori.items()))
            return redirect(url_for("home"))
        con = db.owned(DB_PATH)
        try:
            nuovo = registra_movimento(con, puliti["attrezzo"], puliti["azione"],
                                       puliti["operatore"], sm)
            flash(f"{puliti['attrezzo']} → {nuovo}")
        except statemachine.TransizioneNonValida as e:
            flash(str(e))
        finally:
            con.close()
        return redirect(url_for("home"))

    return app


if __name__ == "__main__":
    migrate_db()
    porta = corecfg.require(carica_config(), "app", "porta")
    create_app().run(host="0.0.0.0", port=porta)
