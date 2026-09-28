# Changelog

Tutte le modifiche rilevanti a argo-core. Formato ispirato a
[Keep a Changelog](https://keepachangelog.com/it/1.1.0/); versioni in
[SemVer](https://semver.org/lang/it/). Serie `0.x` = pre-1.0, API di `core.*`
ancora passibile di aggiustamenti tra minor.

## [Non rilasciato] — verso la 1.0

### Aggiunto

- **ADR del kernel 1.0** in `docs/adr/` (ADR-000…005), accettati.
- **`core.migrazioni`** (kernel, ADR-004) — migrazioni di schema come lista di
  passi numerati applicati da `applica()`: backup del DB con l'API di backup
  online di SQLite (consistente anche in WAL) e verifica di integrità prima di
  ogni migrazione, un passo per transazione, storia nella tabella di sistema
  `_argo_schema` + `PRAGMA user_version`, avvio negato se il DB è più nuovo
  del codice, retention dei backup, viste ricreate dopo l'ultimo passo.
  `versione()` / `richiedi_versione()` per i lettori read-only;
  `python -m core.migrazioni stato <db>` per il supporto.
- **`core.manifest`** (kernel, ADR-003) — `manifest.toml` statico per modulo
  (nome, versione, core compatibile, permessi, menu, tipi di anagrafica,
  eventi) con validazione fail-fast: chiavi sconosciute, id fuori dal
  namespace del modulo, duplicati, menu con permesso non dichiarato, core
  incompatibile. `scansiona()` legge le cartelle della suite e la porta da
  `<modulo>.toml`.
- **Portale: moduli scoperti dai manifest** — scansione all'avvio e con
  `POST /api/moduli/rileggi`; registro con `origine`, `stato`
  (`ok`/`errore`/`assente`), `titolo`, `versione`, `menu`. Lo schema di
  `core.sqlite` passa a `core.migrazioni` (backup + versione).
- `manifest.toml` per l'esempio `presenze`.
- **`core.auth` riscritto** (kernel, ADR-001 parte 1) — identità centrale in
  `comune/auth.sqlite`: utenti con ID stabile, gruppi, ruoli (insiemi di
  permessi `<modulo>.<azione>`), assegnazioni a utente o gruppo; tutto come
  eventi append-only (audit trail) con proiezioni in vista; credenziali a
  parte (hash stdlib in formato Werkzeug). Sessioni con token (solo lo
  SHA-256 nel DB), scadenza fissata all'apertura, logout/disattivazione/
  revoca immediati. Backend pluggable (`Backend`, `BackendLocale`).
  Integrazione nei moduli: `richiede_permesso()`, `pubblica`,
  `inizializza()` (permessi verificati contro il manifest all'avvio,
  redirect al login della shell, controllo `Origin` sui POST, Basic Auth
  solo per utenti `servizio`). CLI: `crea-admin`, `importa` (utenti 0.x).
- `migrate.ident()` pubblico (`_ident` resta come alias).
- **`core.shell`** (kernel, ADR-001 parte 2, ex portale) — login unico
  (cookie `argo_sessione` condiviso da tutti i moduli sullo stesso host,
  redirect `next` solo verso la shell o moduli del registro), logout,
  "cambia password", home con i moduli dell'utente, amministrazione di
  utenti, gruppi e ruoli (`/utenti`, permesso `core.utenti`; catalogo dei
  permessi dai manifest), registro moduli / health-check / browser DB
  (permesso `core.admin`). Avvio negato senza `comune/argo.toml` o senza
  utenti.
- **Cornice comune** `argo_cornice.html`: barra con menu filtrato per
  permessi, utente, "esci"; fornita ai moduli da `auth.inizializza()`.
- **`core.registro`** — registro dei moduli (`core.sqlite`) in stdlib,
  con `menu_per(permessi)`; lo usano shell e moduli.
- **`core.config.carica_suite()`** — config di suite `comune/argo.toml`
  (`[auth] durata_sessione_ore` obbligatoria, titolo e porta facoltativi).
- Scaffolder: i moduli generati usano `auth.inizializza()`,
  `@richiede_permesso("<nome>.vedi")`, `/api/health` pubblica e la cornice.

### Rimosso

- **API 0.x di `core.auth`** (tabella `utenti` per modulo, `migra(con,
  table)`, `crea_utente(con, u, p, ruolo)`, `verifica`, `ha_ruolo`,
  `richiede(*ruoli, verifica=)`, `lista_utenti`, `disattiva`): sostituita
  dall'identità centrale. Gli utenti 0.x si portano con
  `python -m core.auth importa --db <file>`.
- **Portale con Basic Auth**: spariscono `ARGO_PORTAL_USER`/`ARGO_PORTAL_PASS`
  e `richiede_admin`; `comune/portal.json` non è più letto (usa `argo.toml`).
  `python -m core.portal` resta come alias deprecato di `core.shell`.

### Corretto

- Il browser DB della home non inserisce più i dati delle celle come HTML
  (erano iniettabili): tabelle costruite con `textContent`.
- `POST /api/moduli/rileggi` ritorna i conteggi sotto `"moduli"`: prima il
  conteggio `ok` sovrascriveva l'esito `ok: true`.

### Cambiato

- **Scaffolder**: genera anche `manifest.toml` (permesso `<nome>.vedi`, una
  voce di menu, intervallo `core` dalla versione corrente).
- **Portale**: le tile dei moduli sono costruite con `textContent` (niente
  HTML iniettabile da nome/descrizione); mostrano titolo, versione ed errore
  di manifest.

- **Scaffolder**: lo scheletro generato usa `PASSI` + `migrazioni.applica()`
  al posto della `migrate_db()` libera.
- **Regola 4 di `CORE_CONTESTO_AI.md`**: migrazioni additive **numerate e con
  backup**. Gli helper di `core.migrate` restano invariati; una `migrate_db()`
  0.x continua a funzionare ma non è conforme.

## [0.4.0] — 2026-07-13

### Aggiunto

- **`CORE_CONTESTO_AI.md`** — il contesto da dare a un assistente AI per
  costruire moduli compatibili: regole non negoziabili, API reference delle
  firme pubbliche di `core.*`, guida allo scaffolder, pattern di composizione
  e checklist di consegna per moduli generati da AI.
- **CI (GitHub Actions)** — test automatici su push e PR: suite senza Flask
  (py3.11/3.13, verifica la promessa stdlib-only), suite completa con Flask,
  e suite completa su Windows (la piattaforma di destinazione).
- **`core.inventory`** — inventario generico event-sourced: anagrafica
  articoli (upsert, soglia minima opzionale) + movimenti append-only con
  single write-point (`movimenta()`, quantità sempre positive, segno dato
  dalla causale dichiarata in config) + giacenza come proiezione (vista
  `giacenze` = somma dei movimenti) + `sotto_scorta()` a tempo di lettura.
  Nessun dominio nel core: causali, articoli e soglie vivono nel TOML del
  modulo.

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

[0.4.0]: https://github.com/Kroydakij/argo-core/releases/tag/v0.4.0
[0.3.0]: https://github.com/Kroydakij/argo-core/releases/tag/v0.3.0
