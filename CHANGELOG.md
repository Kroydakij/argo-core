# Changelog

Tutte le modifiche rilevanti a argo-core. Formato ispirato a
[Keep a Changelog](https://keepachangelog.com/it/1.1.0/); versioni in
[SemVer](https://semver.org/lang/it/). Dalla 1.0 SemVer vale per tutta l'API
pubblica elencata in `CORE_CONTESTO_AI.md` (kernel e utility): rotture solo in
una major, con almeno una minor di `DeprecationWarning` prima (ADR-000).

## [Non rilasciato]

## [1.0.0] — 2026-09-28

Prima versione stabile: il **kernel** della suite (ADR-000…005 in
`docs/adr/`). Login unico con permessi per modulo, anagrafica centralizzata,
manifest dei moduli, migrazioni con backup, busta standard degli eventi.
Rompe l'API 0.4: **guida alla migrazione in
[`docs/MIGRAZIONE-1.0.md`](docs/MIGRAZIONE-1.0.md)**.

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
- **`core.busta`** (kernel, ADR-005) — busta standard di ogni log: `uid`,
  `tipo` dichiarato nel manifest, `versione`, `ts_utc` + `offset_min` (ora
  locale ricostruibile senza `tzdata`, anche al cambio dell'ora legale),
  `attore_id` (dalla sessione, mai dedotto), `entita_id`, `sorgente`.
  `crea_log()`, `scrivi()` (single write-point), `ora_locale()`, adozione
  dei log 0.x senza riscrivere righe. `core.auth` scrive il suo log con la
  stessa busta.
- **Avviso orologio sfasato** nella shell (`GET /api/orologi`, oltre 120 s).
- **Esempio `presenze`** conforme al kernel: login unico e permessi dal
  manifest, eventi con busta (chi = utente della sessione), schema con
  `core.migrazioni`, pagina nella cornice comune.

- **`core.anagrafica`** (kernel, ADR-002 parte 1) — entità condivise in
  `comune/anagrafica.sqlite`: ID stabile (UUID) separato dal codice umano,
  tipo configurato in `argo.toml` (`[anagrafica.tipi.<nome>]`, con regole di
  normalizzazione dichiarative), stato `ATTIVO`/`OBSOLETO`/`FUSO`, alias
  (codici esterni per sistema), attributi liberi solo descrittivi (niente
  EAV). Creazioni, rinomine, descrizioni, cambi di stato, alias e fusioni
  sono eventi con la busta; entità, alias, codici storici e catena delle
  fusioni sono viste. Lettura: `apri()`, `risolvi()` (codice corrente, alias,
  storico → ID canonico), `entita()`, `elenco()`, `canonico()`. Scrittura
  solo dalla shell e dalla CLI; i moduli usano `anagrafica.client` (HTTP
  verso la shell, come l'utente della richiesta).
- **Shell: anagrafica** — pagina `/anagrafica` (consultazione per tutti,
  modifica con il permesso del tipo) e API `/api/anagrafica/...`. Un permesso
  di scrittura per tipo, generato dal kernel:
  `core.anagrafica.modifica.<tipo>`, nel catalogo dei ruoli.
- **CLI** `python -m core.anagrafica importa --tipo T file.csv [--come utente]`:
  import iniziale idempotente sul codice normalizzato.
- **`core.codes.componi(regole)`** — normalizzazione dichiarativa (`strip`,
  `maiuscolo`, `minuscolo`, `senza_spazi`, `zfill:N`).
- **`core.db.attach_readonly(con, path, alias)`** — JOIN con un DB altrui in
  `mode=ro` (es. log del modulo × anagrafica).
- `auth.inizializza()` nega l'avvio di un modulo che dichiara nel manifest un
  tipo di anagrafica non configurato in `argo.toml`.

- **`core.inventory` sopra l'anagrafica** (ADR-002 parte 2) — gli articoli
  sono entità di `core.anagrafica` (tipo da `[inventario] tipo`), movimenti e
  soglie di riordino sono log con la busta (tipi `<modulo>.movimento` e
  `<modulo>.soglia_impostata` dichiarati nel manifest), la giacenza si somma
  per entità canonica (una fusione somma le giacenze senza riscrivere il
  log). `esporta_articoli_0x()` + `adotta_0x()` portano un inventario 0.x
  (tabelle vecchie intatte).
- `anagrafica.equivalenti()` e `anagrafica.mappa_canonici()` per aggregare i
  log dei moduli per entità canonica.
- **Esempio `presenze`**: gli attrezzi sono entità di anagrafica (tipo
  `attrezzo`, `attrezzi.csv` d'esempio da importare); niente più seed;
  rinomine e fusioni si vedono sulla board; le righe scritte col nome
  dell'attrezzo si leggono ancora.

### Rotture dell'API (dettaglio in `docs/MIGRAZIONE-1.0.md`)

- **`import core` carica solo il kernel** (ADR-000): le utility si importano
  per nome (`from core import board`); `import core; core.board` senza import
  esplicito ora solleva `AttributeError` con il suggerimento.
- **API 0.x di `core.inventory`**: `crea_articolo()`, `disattiva_articolo()`,
  `lista_articoli()` (gli articoli si gestiscono in anagrafica); `Inventario`
  richiede `manifest=` e `anagrafica=`; `movimenta(con, entita_id, ...)` con
  `attore_id` al posto di `operatore`; `giacenza()`/`storico()` per ID; le
  tabelle di default diventano `inventario_movimenti`/`inventario_soglie` e
  la vista `inventario_saldi` (la vista `giacenze` 0.x non è più ricreata).
- **Esempio `presenze`**: `[attrezzi] elenco` in `presenze.toml` (ora in
  anagrafica); `registra_movimento()`, `stato_di()`, `stato_manutenzioni()`
  prendono anche la connessione all'anagrafica.
- **`core.events.registra(con, entita, stato, operatore=...)`** (0.x):
  ora `registra(con, tipo, entita_id, stato, *, manifest, attore_id=None)`;
  `operatore=` è deprecato e finisce in `note`. `stato_corrente`/`storico`
  prendono `entita_id`. I log 0.x si adottano con `events.migra()` (busta
  aggiunta, righe vecchie intatte e leggibili).
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

- **`core/__init__.py` dichiara i livelli**: `KERNEL`, `UTILITY`,
  `APPLICAZIONI`. `migrazioni`, `auth`, `anagrafica`, `registro` si caricano
  al primo accesso (`core.auth`), così le loro CLI non importano due volte.
  `tests/test_architettura.py` verifica che il kernel non importi utility.
- **`migrate._ident`** ora emette `DeprecationWarning` (usa `migrate.ident`):
  sparisce nella 2.0.
- **`core.config.carica_suite()`** ritorna anche `tipi` (tipi di anagrafica).
- **`core.db.owned()`** apre il file come URI SQLite (`mode=rwc`); firma e
  comportamento invariati.
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

[1.0.0]: https://github.com/Kroydakij/argo-core/releases/tag/v1.0.0
[0.4.0]: https://github.com/Kroydakij/argo-core/releases/tag/v0.4.0
[0.3.0]: https://github.com/Kroydakij/argo-core/releases/tag/v0.3.0
