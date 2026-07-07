# argo-core

Fondamenta per suite gestionali **Flask + SQLite** che girano su un PC
qualunque: Windows senza diritti admin, niente Docker, niente server,
niente cloud. Serving HTTP in LAN, backup = copia di una cartella.

**Fase 1** (questa release): libreria + portale. Nessun processo, nessuna
dipendenza obbligatoria oltre la libreria standard — Flask serve solo a
`export.csv_response` ed è importato lazy.

## Cosa contiene

| Modulo | Cosa rende automatico |
|---|---|
| `core.db` | Connessioni uniformi: `owned()` (WAL, FK, busy_timeout unico) e `readonly()` (sola lettura imposta dal motore, `mode=ro`) |
| `core.migrate` | Migrazioni solo additive: `ensure_table` (pretende `IF NOT EXISTS`), `ensure_column` idempotente, `rebuild_views` da chiamare sempre alla fine |
| `core.codes` | Registro delle normalizzazioni codici: una regola per famiglia, registrata una volta, usata ovunque |
| `core.notify` | Email via relay SMTP interno, mai solleva, log append-only opzionale nel DB del modulo mittente |
| `core.schedule` | Scheduler "a tempo di lettura": stato ok/da_fare/scaduta calcolato dallo storico, per attività a cadenza o a evento. Funzione pura, zero SQL |
| `core.export` | CSV per Excel in locale italiano (`;` + BOM utf-8) |

## Uso senza installazione

La cartella `core\` si copia come sorella dei moduli della suite
(vendoring). In testa all'`app.py` di ogni modulo:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import db, migrate, export
```

## Test

```
python -m unittest discover tests -v
```

Solo stdlib: la suite gira su un Python 3.12+ appena installato.

## Regole del progetto

1. **Flusso a senso unico**: il codice fluisce da argo-core verso le
   installazioni che lo usano, mai il contrario. Nessun codice, dato,
   formula o logica proveniente da un'installazione specifica entra
   in questo repository.
2. **Solo pattern generici**: connessioni, migrazioni, notifiche,
   scheduling astratto. Niente logiche di dominio.
3. **Zero dipendenze obbligatorie**: stdlib; Flask opzionale e lazy.
4. Le regole architetturali complete per costruire moduli sopra core
   (ownership dei database, log append-only, single write-point) vivono
   in `CORE_CONTESTO_AI.md` (in arrivo con la fase 4).

## Porte

La suite usa il blocco **4700-4799**: portale sulla **4700**, moduli dal
4701 in su (il portale suggerisce la prossima libera). Il blocco e' scelto
per essere fuori dai default affollati (3000/4000/5000/8000/8080), fuori
dalla lista delle porte "unsafe" che i browser rifiutano (es. la 6000) e
sotto il range effimero di Windows (49152+). Un blocco contiguo = una sola
eventuale regola firewall.

## Portale

```
pip install flask          # unico requisito oltre la stdlib
python -m core.portal      # -> http://localhost:4700
```

Registro dei moduli con tile e health-check, browser database read-only su
tutti i DB della cartella dati. Scritture protette da Basic Auth
(ARGO_PORTAL_USER / ARGO_PORTAL_PASS; default admin/admin, da cambiare).
La cartella dati e' `..\comune` (override: variabile ARGO_COMUNE); la
config opzionale vive in `comune\portal.json` cosi' sopravvive agli
aggiornamenti della cartella core.

## Fase 2 (0.3.0)

Layer applicativo event-sourced, tutto stdlib (Flask/Werkzeug lazy dove serve):

- `core.config` — configurazione TOML fail-fast
- `core.events` — log append-only + proiezione `latest_state_per_entity`
- `core.statemachine` — transizioni dichiarative
- `core.shifts` — turni parametrici a tempo di lettura
- `core.forms` — form-engine dichiarativo (validazione + render)
- `core.auth` — utenti e ruoli (hashing Werkzeug)
- `core.board` — board config-driven
- `core.scaffold` — `python -m core.scaffold <nome>`, con demo in `examples/`

Vedi `CHANGELOG.md` per il dettaglio.

## Roadmap

- `core.inventory` (inventario generico)
- `CORE_CONTESTO_AI.md` (istruzioni per AI)
