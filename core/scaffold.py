"""
core.scaffold — genera lo scheletro di un nuovo modulo della suite.

    python -m core.scaffold <nome> [--porta N] [--dir PATH]

Crea una cartella <dir>/<nome>/ con:
  - app.py        Flask app: bootstrap di core via sys.path, migrate_db() con
                  gli helper di core, config TOML, route "/". Flask importato
                  lazy dentro create_app() (lo scheletro parte davvero ed e'
                  testabile anche senza Flask installato).
  - <nome>.toml   config d'esempio (porta, titolo).
  - manifest.toml cosa il modulo dichiara: permessi, menu, eventi (ADR-003).
  - templates/index.html
  - README.md     porta, DB, avvio, come estendere.

Porta: se il portale (4700) e' raggiungibile, si usa la prossima porta libera
che suggerisce il registro; altrimenti quella passata con --porta; altrimenti
il primo valore del blocco moduli (4701).

Lo scheletro generato rispetta le regole della suite: DB di proprieta' aperto
con db.owned, migrazioni additive, dati fuori dalla cartella del modulo
(ARGO_COMUNE, default ./dati accanto al modulo per farlo girare da subito).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

PRIMA_PORTA_MODULI = 4701
PORTALE_DEFAULT = "http://127.0.0.1:4700"


def porta_suggerita(porta_arg: int | None = None, *,
                    portale: str = PORTALE_DEFAULT, timeout: float = 1.0) -> int:
    """Porta consigliata: dal registro se il portale risponde, se no da argomento.

    Non solleva mai per un portale spento: quello e' il caso normale offline.
    """
    try:
        with urllib.request.urlopen(f"{portale}/api/moduli", timeout=timeout) as r:
            dati = json.loads(r.read().decode("utf-8"))
        p = dati.get("prossima_porta")
        if isinstance(p, int) and p > 0:
            return p
    except Exception:
        pass                                    # portale non raggiungibile: offline
    return porta_arg or PRIMA_PORTA_MODULI


def genera(nome: str, porta: int, dest_dir: str | Path = ".") -> Path:
    """Genera la cartella del modulo. Ritorna il path creato.

    `nome` deve essere un identificatore valido (lettere/cifre/underscore, non
    iniziare per cifra): finisce in nomi di file, path e codice generato.
    """
    nome = nome.strip()
    if not nome.isidentifier():
        raise ValueError(
            f"nome modulo non valido: {nome!r} "
            f"(ammessi lettere, cifre e '_', non iniziare per cifra)")
    base = Path(dest_dir) / nome
    if base.exists():
        raise FileExistsError(f"esiste gia': {base}")
    (base / "templates").mkdir(parents=True)
    (base / "app.py").write_text(_app_py(nome, porta), encoding="utf-8")
    (base / f"{nome}.toml").write_text(_config_toml(nome, porta), encoding="utf-8")
    (base / "manifest.toml").write_text(_manifest_toml(nome), encoding="utf-8")
    (base / "templates" / "index.html").write_text(_index_html(nome), encoding="utf-8")
    (base / "README.md").write_text(_readme(nome, porta), encoding="utf-8")
    return base


# --- template dei file generati -----------------------------------------

def _app_py(nome: str, porta: int) -> str:
    titolo = nome.capitalize()
    return f'''"""Modulo {nome} — scheletro generato da core.scaffold."""
import os
import sys
from pathlib import Path


def _aggiungi_core_al_path() -> None:
    """Rende importabile core/ risalendo le cartelle (funziona da sibling o da
    examples/). Se core e' gia' importabile (PYTHONPATH, test) non fa nulla.
    Cosi' il modulo non dipende da quante cartelle lo separano da core."""
    try:
        import core  # noqa: F401  gia' importabile?
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
from core import config as corecfg  # noqa: E402
from core import migrate, migrazioni  # noqa: E402

QUI = Path(__file__).resolve().parent
CONFIG_PATH = QUI / "{nome}.toml"
# Dati FUORI dalla cartella del modulo (regola della suite). Default: ./dati
# accanto al modulo, per farlo girare subito; in produzione punta ad ARGO_COMUNE.
COMUNE = Path(os.environ.get("ARGO_COMUNE", QUI / "dati"))
DB_PATH = COMUNE / "{nome}.sqlite"


# --- schema: passi di migrazione numerati (core.migrazioni) ------------
# Un passo pubblicato non si modifica: per cambiare lo schema si AGGIUNGE il
# passo successivo. I passi non fanno commit; solo DDL additivo.

def _p1_schema_iniziale(con) -> None:
    migrate.ensure_table(con, """CREATE TABLE IF NOT EXISTS esempio (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        creato_il TEXT DEFAULT (datetime('now','localtime'))
    )""")


PASSI = [
    migrazioni.Passo(1, "schema iniziale", _p1_schema_iniziale),
]
VISTE: dict[str, str] = {{}}          # ricreate SEMPRE dopo l'ultimo passo


def migrate_db() -> None:
    """Porta il DB all'ultimo passo, con backup prima (in COMUNE/_backup)."""
    migrazioni.applica(DB_PATH, PASSI, viste=VISTE)


def carica_config() -> dict:
    return corecfg.load(CONFIG_PATH)     # fail-fast se manca o e' malformata


def create_app():
    """App Flask. Flask importato lazy: lo scheletro e' testabile senza Flask."""
    from flask import Flask, render_template
    cfg = carica_config()
    # template_folder assoluto: lo scheletro risponde qualunque sia il modo di
    # lancio (python app.py, import, WSGI), senza dipendere dalla cwd.
    app = Flask(__name__, template_folder=str(QUI / "templates"))
    app.config["TITOLO"] = corecfg.optional(cfg, "app", "titolo", default="{titolo}")

    @app.get("/")
    def home():
        return render_template("index.html", titolo=app.config["TITOLO"])

    return app


if __name__ == "__main__":
    migrate_db()
    cfg = carica_config()
    porta = corecfg.require(cfg, "app", "porta")
    create_app().run(host="0.0.0.0", port=porta)
'''


def _config_toml(nome: str, porta: int) -> str:
    return f'''# Config del modulo {nome}. Config rotta = avvio negato (fail-fast).
[app]
titolo = "{nome.capitalize()}"
porta = {porta}
'''


def _intervallo_core() -> str:
    """Versioni di core compatibili con lo scheletro: da questa (major.minor)
    fino alla prossima major esclusa. Esempio: core 1.3.2 -> ">=1.3,<2.0"."""
    from . import __version__
    major, minor = (int(x) for x in __version__.split(".")[:2])
    return f">={major}.{minor},<{major + 1}.0"


def _manifest_toml(nome: str) -> str:
    return f'''# Manifest del modulo {nome} (ADR-003): cosa il modulo E', uguale in ogni
# installazione. Versionato col codice, mai dati d'installazione (quelli
# stanno in {nome}.toml). La shell lo legge senza eseguire il modulo.
# Ogni permesso o evento usato dal codice va dichiarato qui.

[modulo]
nome = "{nome}"
versione = "0.1.0"
core = "{_intervallo_core()}"
titolo = "{nome.capitalize()}"
descrizione = ""

[[permessi]]
id = "{nome}.vedi"
descrizione = "Vedere il modulo"

[[menu]]
titolo = "{nome.capitalize()}"
percorso = "/"
permesso = "{nome}.vedi"

[anagrafica]
tipi = []                  # tipi di anagrafica usati, es. ["macchina"]

# [[eventi]]
# tipo = "{nome}.qualcosa_successo"
# versione = 1
# entita = ""              # tipo di anagrafica dell'entita', "" se nessuna
# descrizione = "..."
'''


def _index_html(nome: str) -> str:
    return f'''<!doctype html>
<html lang="it">
<head><meta charset="utf-8"><title>{{{{ titolo }}}}</title></head>
<body>
  <h1>{{{{ titolo }}}}</h1>
  <p>Modulo <strong>{nome}</strong> attivo. Scheletro generato da core.scaffold.</p>
</body>
</html>
'''


def _readme(nome: str, porta: int) -> str:
    return f'''# Modulo {nome}

Scheletro generato da `core.scaffold`.

- **Porta**: {porta}
- **DB di proprieta'**: `{nome}.sqlite` (in `ARGO_COMUNE`, default `./dati/`)
- **Letture read-only** da DB di altri moduli: `core.db.readonly(...)`.

## Avvio

```
pip install flask
python app.py            # -> http://localhost:{porta}
```

## Come estenderlo

- Schema: aggiungi un nuovo passo in fondo a `PASSI` (mai modificare quelli
  esistenti), solo in modo additivo (`migrate.ensure_table`,
  `migrate.ensure_column`). Le viste stanno in `VISTE`. `core.migrazioni` fa
  il backup del DB prima di applicare passi nuovi.
  Stato per il supporto: `python -m core.migrazioni stato <file.sqlite>`.
- Stato event-sourced: `core.events` (log append-only + `latest_state_per_entity`).
- Transizioni: `core.statemachine`. Form: `core.forms`. Board: `core.board`.
  Turni: `core.shifts`. Auth a ruoli: `core.auth`.
- `manifest.toml` dichiara nome, versione, permessi, voci di menu, tipi di
  anagrafica ed eventi: aggiornalo quando aggiungi un permesso o un evento.
- Il portale trova il modulo da solo leggendo `manifest.toml` (deploy =
  copia della cartella accanto a `core`); dopo un aggiornamento: "rileggi
  moduli" (`POST /api/moduli/rileggi`).
'''


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.scaffold",
                                 description="Genera lo scheletro di un modulo ARGO.")
    ap.add_argument("nome", help="nome del modulo (identificatore valido)")
    ap.add_argument("--porta", type=int, default=None,
                    help="porta se il portale non e' raggiungibile")
    ap.add_argument("--dir", default=".", help="cartella in cui creare il modulo")
    a = ap.parse_args(argv)
    porta = porta_suggerita(a.porta)
    try:
        base = genera(a.nome, porta, a.dir)
    except (ValueError, FileExistsError) as e:
        print(f"[scaffold] errore: {e}", file=sys.stderr)
        return 1
    print(f"[scaffold] creato {base} (porta {porta})")
    print(f"[scaffold] avvio:  cd {base} && pip install flask && python app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
