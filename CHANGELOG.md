# Changelog

Tutte le modifiche rilevanti a argo-core. Formato ispirato a
[Keep a Changelog](https://keepachangelog.com/it/1.1.0/); versioni in
[SemVer](https://semver.org/lang/it/). Serie `0.x` = pre-1.0, API di `core.*`
ancora passibile di aggiustamenti tra minor.

## [0.3.0] — 2026-07-07

### Aggiunto — Fase 2 (layer applicativo event-sourced)

- **`core.config`** — configurazione TOML (`tomllib`, stdlib) con avvio
  fail-fast: file mancante o malformato, o chiave obbligatoria assente, negano
  l'avvio. `require()` / `optional()` con default esplicito.
- **`core.events`** — layer event-sourced: log append-only, single write-point
  (`registra()`, solo INSERT), sorgente vincolata `MANUALE`/`SENSORE` (anche
  con CHECK a livello DB), proiezione dello stato via vista
  `latest_state_per_entity`. Colonne extra additive.
- **`core.statemachine`** — macchina a stati dichiarativa e pura; transizioni
  in dict o TOML, validate alla costruzione (fail-fast).
- **`core.shifts`** — turni parametrici risolti a tempo di lettura (intervalli
  semiaperti, turni oltre la mezzanotte); orari da config, mai cablati.
- **`core.forms`** — form-engine dichiarativo: validazione lato server e render
  HTML (con escaping) dalla stessa definizione. Zero Flask (stringhe + stdlib).
- **`core.auth`** — utenti e ruoli con password hashate (Werkzeug, import lazy);
  decoratore `richiede(*ruoli, ...)` per Basic Auth + gate di ruolo.
- **`core.board`** — board (kanban) config-driven sopra la proiezione di
  `core.events`.

### Aggiunto — strumenti ed esempi

- **`core.scaffold`** — `python -m core.scaffold <nome> [--porta N] [--dir P]`:
  genera lo scheletro di un modulo (app Flask con bootstrap di core, `migrate_db()`
  con gli helper, config TOML, template, README). Lo scheletro parte da solo e
  risponde su `/`; porta suggerita dal registro del portale se raggiungibile.
- **`examples/presenze`** — modulo demo "presenze attrezzatura" generato con lo
  scaffolder, che usa events, statemachine, forms, schedule, board, shifts.
- **`scripts/release.sh`** + **`.gitattributes`** — zip di release riproducibile
  via `git archive` (framework + esempio + docs; fuori i file di sviluppo).
- **`.gitignore`** — bytecode, DB/dati locali, ambienti.

### Note

- Nessuna nuova dipendenza obbligatoria: tutto il layer e' stdlib; Flask e
  Werkzeug restano opzionali e importati lazy dove servono.
- Suite di test: da 27 a 93 casi, tutti verdi.

## [0.2.0] — baseline (Fase 0-1)

Libreria di base (`db`, `migrate`, `codes`, `notify`, `schedule`, `export`) e
portale (`portal`, `adminbrowser`): registro moduli, health-check, browser DB
read-only. Punto di partenza di questo changelog.

[0.3.0]: https://github.com/Kroydakij/argo-core/releases/tag/v0.3.0
