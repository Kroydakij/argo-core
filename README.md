# argo-core

Fondamenta per suite gestionali **Flask + SQLite** che girano su un PC
qualunque: Windows senza diritti admin, niente Docker, niente server,
niente cloud. Serving HTTP in LAN, backup = copia di una cartella.

**1.0**: il **kernel** della suite (login unico con permessi per modulo,
anagrafica centralizzata, manifest dei moduli, migrazioni con backup, busta
standard degli eventi) più le utility opzionali. Zero dipendenze oltre la
libreria standard; Flask serve dove si serve web ed è importato lazy.
Da 0.4: [`docs/MIGRAZIONE-1.0.md`](docs/MIGRAZIONE-1.0.md). Decisioni
architetturali: [`docs/adr/`](docs/adr/).

## Cosa contiene

**Kernel** — ciò che deve dare la stessa risposta a tutti i moduli (ADR-000):

| Modulo | Cosa rende automatico |
|---|---|
| `core.shell` | Il processo della suite (porta 4700): login unico, menu per permessi, utenti/gruppi/ruoli, anagrafica, registro dei moduli |
| `core.auth` | Identità centrale e sessione condivisa; `richiede_permesso("modulo.azione")` nei moduli (ADR-001) |
| `core.anagrafica` | Entità condivise con ID stabile, codici normalizzati, alias, rinomine e fusioni come eventi (ADR-002) |
| `core.manifest` | Cosa un modulo è e usa (permessi, menu, tipi, eventi), validato all'avvio (ADR-003) |
| `core.migrazioni` | Schema a passi numerati, backup verificato prima di migrare (ADR-004) |
| `core.busta` + `core.events` | Ogni evento con chi, quando (UTC + ora locale), cosa, su quale entità (ADR-005) |
| `core.db`, `core.migrate`, `core.config`, `core.codes` | Connessioni uniformi (`owned`/`readonly`/`attach_readonly`), helper additivi, TOML fail-fast, normalizzazione codici |

**Utility** — opzionali, si importano per nome (`from core import board`):
`statemachine`, `shifts`, `schedule`, `forms`, `board`, `inventory`,
`export`, `notify`, `adminbrowser`, `scaffold`.

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

Solo stdlib: la suite gira su un Python 3.11+ appena installato (i test
che richiedono Flask si saltano se manca).

## Regole del progetto

1. **Flusso a senso unico**: il codice fluisce da argo-core verso le
   installazioni che lo usano, mai il contrario. Nessun codice, dato,
   formula o logica proveniente da un'installazione specifica entra
   in questo repository.
2. **Solo pattern generici**: connessioni, migrazioni, notifiche,
   scheduling astratto. Niente logiche di dominio.
3. **Zero dipendenze obbligatorie**: stdlib; Flask opzionale e lazy.
4. Le regole architetturali complete per costruire moduli sopra core
   (ownership dei database, log append-only, single write-point), l'API
   reference e la checklist di consegna vivono in `CORE_CONTESTO_AI.md`:
   è il documento da dare a un'AI per farsi costruire un modulo.

## Porte

La suite usa il blocco **4700-4799**: shell sulla **4700**, moduli dal
4701 in su (la shell suggerisce la prossima libera). Il blocco e' scelto
per essere fuori dai default affollati (3000/4000/5000/8000/8080), fuori
dalla lista delle porte "unsafe" che i browser rifiutano (es. la 6000) e
sotto il range effimero di Windows (49152+). Un blocco contiguo = una sola
eventuale regola firewall.

## Shell (ex portale)

```
pip install flask                           # unico requisito oltre la stdlib
python -m core.auth crea-admin <username>   # solo la prima volta
python -m core.shell                        # -> http://localhost:4700
```

Login unico per tutta la suite (un modulo manda al login della shell e la
sessione vale per tutti i moduli), menu dei moduli filtrato per permessi,
gestione di utenti, gruppi e ruoli, registro dei moduli (scoperti da soli
dai loro `manifest.toml`), health-check e browser database read-only.
La cartella dati e' `..\comune` (override: variabile ARGO_COMUNE) e contiene
anche la config di suite `argo.toml` (obbligatoria: vedi
`CORE_CONTESTO_AI.md`), cosi' sopravvive agli aggiornamenti della cartella
core. Se la shell e' spenta, chi ha gia' fatto login continua a lavorare
nei moduli, ma nessuno puo' entrare.

## Roadmap

- 1.x: notifiche nel kernel, sopra la busta degli eventi.
- Approvazioni multi-step: modulo separato, mai nel core.

Vedi `CHANGELOG.md` per il dettaglio delle versioni.
