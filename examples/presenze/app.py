"""Modulo demo "presenze attrezzatura".

Generato con `python -m core.scaffold presenze --dir examples`, poi esteso a
prova vivente della suite. Traccia il ciclo di vita di alcuni attrezzi
(disponibile / in uso / in manutenzione) usando i mattoni del kernel e delle utility:

  core.anagrafica    gli attrezzi sono entita' della suite: ID stabile (ADR-002)
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
from core import anagrafica, auth, busta, db, events, forms, manifest  # noqa: E402
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
TIPO = "attrezzo"                                    # tipo di anagrafica (manifest)


# --- config ------------------------------------------------------------

def carica_config() -> dict:
    return corecfg.load(CONFIG_PATH)                 # fail-fast


def _macchina(cfg: dict) -> statemachine.StateMachine:
    return statemachine.StateMachine.da_config(corecfg.require(cfg, "macchina"))


# --- migrazione --------------------------------------------------------

PASSI = [migrazioni.Passo(1, "log degli stati degli attrezzi", lambda con: events.migra(con))]


def migrate_db() -> None:
    """DB di proprieta': schema a passi, con backup (ADR-004). Nessun seed:
    gli attrezzi sono entita' dell'anagrafica della suite (ADR-002) e chi non
    ha ancora eventi e' nello stato iniziale."""
    migrazioni.applica(DB_PATH, PASSI)


def apri_anagrafica():
    """Anagrafica della suite, in sola lettura (fail-fast: avvia la shell)."""
    return anagrafica.apri(COMUNE)


# --- logica di dominio (pura rispetto alle connessioni: testabile) ------

def attrezzi(ana) -> list[dict]:
    """Gli attrezzi attivi, dall'anagrafica (codice e descrizione correnti)."""
    return anagrafica.elenco(ana, TIPO)


def _stati(con, ana) -> dict[str, dict]:
    """Ultimo stato per attrezzo CANONICO. Le fusioni non riscrivono il log:
    si canonicalizza qui, in lettura. Le righe scritte prima dell'anagrafica
    (chiave = nome dell'attrezzo) si risolvono dal codice."""
    canonici = anagrafica.mappa_canonici(ana)
    out: dict[str, dict] = {}
    for r in events.stato_corrente(con):
        chiave = r["entita"]
        cid = canonici.get(chiave)
        if cid is None:
            ris = anagrafica.risolvi(ana, TIPO, chiave)
            if ris is None:
                continue                              # attrezzo non piu' in anagrafica
            cid = ris.id
        if cid not in out or r["id"] > out[cid]["id"]:
            out[cid] = r
    return out


def _attrezzo_attivo(ana, attrezzo_id: str) -> dict:
    e = anagrafica.entita(ana, anagrafica.canonico(ana, attrezzo_id))
    if e is None or e["tipo"] != TIPO:
        raise ValueError(f"attrezzo sconosciuto: {attrezzo_id!r}")
    if e["stato"] != "ATTIVO":
        raise ValueError(f"attrezzo {e['codice']} non attivo ({e['stato']})")
    return e


def stato_di(con, ana, attrezzo_id: str, sm: statemachine.StateMachine) -> str:
    st = _stati(con, ana).get(anagrafica.canonico(ana, attrezzo_id))
    return st["stato"] if st else sm.iniziale


def registra_movimento(con, ana, attrezzo_id: str, azione: str,
                       sm: statemachine.StateMachine, *, attore_id: str | None = None) -> str:
    """Valida la transizione con la macchina a stati, poi appende l'evento.

    Solleva statemachine.TransizioneNonValida se l'azione non e' ammessa dallo
    stato corrente: il log non registra mai un movimento impossibile.
    L'attore e' l'utente della sessione (o quello passato, nei test/script);
    l'entita' e' l'ID CANONICO dell'attrezzo in anagrafica.
    """
    e = _attrezzo_attivo(ana, attrezzo_id)
    nuovo = sm.transita(stato_di(con, ana, e["id"], sm), azione)
    events.registra(con, EVENTO, e["id"], nuovo, manifest=MANIFEST, attore_id=attore_id)
    return nuovo


def righe_board(con, ana, sm: statemachine.StateMachine) -> list[dict]:
    """Una riga per attrezzo attivo: stato corrente (o iniziale se mai mosso),
    `entita` = codice corrente per la board, `chi` = ultimo utente."""
    stati = _stati(con, ana)
    righe = [{**(stati.get(a["id"]) or {"stato": sm.iniziale, "attore_id": None}),
              "entita": a["codice"], "entita_id": a["id"]} for a in attrezzi(ana)]
    return con_nomi(righe)


def stato_manutenzioni(con, ana, cfg: dict, oggi: str | None = None) -> dict:
    """Stato manutenzioni per attrezzo (chiave = codice corrente), calcolato a
    tempo di lettura.

    L'ultima manutenzione = ultimo evento con stato MANUTENZIONE dell'attrezzo
    (e di quelli fusi in lui); core.schedule ne deriva ok / da_fare / scaduta
    secondo la cadenza in config. Le date sono in ora LOCALE dell'evento
    (busta: UTC + offset salvato).
    """
    giorni = corecfg.optional(cfg, "attrezzi", "manutenzione_giorni", default=30)
    elenco = attrezzi(ana)
    tasks = [{"id": "manut", "soggetto": a["codice"], "freq_giorni": giorni,
              "evento": None} for a in elenco]
    ultime: dict = {}
    for a in elenco:
        storia = []
        for eid in anagrafica.equivalenti(ana, a["id"]):
            storia += events.storico(con, eid)
        manut = sorted((e for e in storia if e["stato"] == "MANUTENZIONE"),
                       key=lambda e: e["id"])
        if manut:
            quando = busta.ora_locale(manut[-1])
            ultime[("manut", a["codice"])] = (quando.strftime("%Y-%m-%d"),
                                              quando.strftime("%Y-%m-%d %H:%M:%S"))
    return schedule.stato_task(tasks, ultime, oggi=oggi)


def con_nomi(righe: list[dict]) -> list[dict]:
    """Aggiunge 'chi' (username) alle righe: la busta registra l'ID stabile
    dell'utente, la UI mostra il nome corrente (letto da auth.sqlite)."""
    nomi = {busta.SISTEMA: "sistema", None: ""}
    if AUTH_DB.exists():
        ro = db.readonly(AUTH_DB)
        try:
            nomi.update({u["id"]: u["username"] for u in auth.utenti(ro)})
        finally:
            ro.close()
    return [{**r, "chi": nomi.get(r.get("attore_id"), r.get("attore_id"))} for r in righe]


def campi_form(sm: statemachine.StateMachine, codici: list[str]) -> list[dict]:
    """Definizione dichiarativa del form del movimento: gli attrezzi sono i
    codici correnti dell'anagrafica; chi lo fa lo dice la sessione."""
    azioni = sorted({az for s in sm.stati() for az in sm.azioni(s)})
    return [
        {"nome": "attrezzo", "label": "Attrezzo", "tipo": "select",
         "opzioni": codici, "obbligatorio": True},
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
    apri_anagrafica().close()                        # fail-fast: anagrafica presente

    app = Flask(__name__, template_folder=str(QUI / "templates"))
    app.config["TITOLO"] = corecfg.optional(cfg, "app", "titolo", default="Presenze")
    app.secret_key = "demo-presenze"                 # solo per i flash (demo locale)

    def _connessioni():
        return db.owned(DB_PATH), apri_anagrafica()

    @app.get("/")
    @auth.richiede_permesso("presenze.vedi")
    def home():
        con, ana = _connessioni()
        try:
            righe = righe_board(con, ana, sm)
            manut = stato_manutenzioni(con, ana, cfg)
        finally:
            con.close()
            ana.close()
        campi = campi_form(sm, [r["entita"] for r in righe])
        return render_template(
            "index.html", titolo=app.config["TITOLO"], vuoto=not righe,
            board=board.render_html(righe), form=forms.render_html(campi),
            manutenzioni=manut, turno=turni.turno_di(datetime.now()) or "-")

    @app.post("/movimento")
    @auth.richiede_permesso("presenze.registra_movimento")
    def movimento():
        con, ana = _connessioni()
        try:
            campi = campi_form(sm, [a["codice"] for a in attrezzi(ana)])
            puliti, errori = forms.valida(campi, request.form)
            if errori:
                flash("Dati non validi: " + ", ".join(f"{k} ({v})"
                                                      for k, v in errori.items()))
                return redirect(url_for("home"))
            ris = anagrafica.risolvi(ana, TIPO, puliti["attrezzo"])
            nuovo = registra_movimento(con, ana, ris.id, puliti["azione"], sm)
            flash(f"{puliti['attrezzo']} → {nuovo}")
        except (statemachine.TransizioneNonValida, ValueError) as e:
            flash(str(e))
        finally:
            con.close()
            ana.close()
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
