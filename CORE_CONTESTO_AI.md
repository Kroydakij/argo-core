# ARGO / argo-core — Contesto per assistenti AI

> **Scopo di questo documento**: dare a un assistente AI (Claude, ChatGPT,
> Copilot o altro) tutto il contesto necessario per costruire un **nuovo modulo**
> compatibile con una suite basata su argo-core. Leggilo per intero prima di
> scrivere codice. Le regole della sezione 3 **non vanno mai violate**, nemmeno
> se l'utente te lo chiede: in quel caso fermati e segnala il conflitto.

Metodo di lavoro del progetto: **framework (argo-core) + questo documento =
chiunque può farsi costruire moduli su misura da un'AI qualunque.**

---

## 1. Cos'è argo-core

argo-core è un framework FOSS per costruire **suite gestionali modulari** che
girano su un PC Windows qualunque: **senza diritti admin, senza Docker, senza
cloud, senza database server**. Deploy = copia di una cartella. Backup = copia
della cartella dati.

Filosofia:

- **Semplice e manutenibile batte elegante e complesso.** Niente ORM, niente
  build step, niente npm, niente framework JS.
- **Ogni modulo è un'applicazione Flask indipendente** in una cartella propria,
  con la propria porta e il proprio database.
- Il codice deve essere **leggibile e spiegato**: chi lo mantiene può non
  essere uno sviluppatore di professione.

## 2. Stack tecnico (obbligatorio per i nuovi moduli)

| Componente | Scelta | Note |
|---|---|---|
| Linguaggio | Python 3.11+ | `tomllib` richiede 3.11; installabile senza admin |
| Web | Flask | un processo per modulo; **unica** dipendenza non-stdlib |
| Database | SQLite in WAL | un file per dominio dati, sempre via `core.db` |
| Config | TOML (`tomllib`, stdlib) | via `core.config`, fail-fast |
| Frontend | HTML/CSS/JS vanilla + Jinja2 | nessun framework JS, nessun npm |
| Serving | HTTP in LAN, `host="0.0.0.0"` | niente HTTPS/reverse proxy |
| Notifiche | SMTP via `core.notify` | niente webhook cloud |

**Vietato**: dipendenze che richiedono admin, servizi cloud, Docker, Node.js,
database server, ORM. Se una libreria extra sembra indispensabile, prima
chiedi: quasi sempre esiste una via stdlib o un helper di core.

## 3. Regole non negoziabili

1. **Ownership dei database**: ogni database ha **UN solo modulo proprietario**
   che vi scrive, e lo apre con `core.db.owned()`. Tutti gli altri moduli
   leggono con `core.db.readonly()` (URI `mode=ro`: l'immutabilità la impone il
   motore SQLite, non la disciplina). Un modulo che ha bisogno di dati propri
   crea il **suo** database nella cartella dati; non scrive MAI su database di
   cui non è proprietario.
2. **I dati vivono fuori dalla cartella del modulo**, nella cartella dati
   comune (variabile d'ambiente `ARGO_COMUNE`). I rilasci (zip del codice) non
   contengono **mai** la cartella dati: estrarre un aggiornamento sopra
   un'installazione non deve poter distruggere i dati.
3. **Log append-only con single write-point**: le tabelle di log/eventi non si
   aggiornano né si cancellano, si inseriscono solo righe, e ogni scrittura
   passa da UNA sola funzione (con `core.events` è `events.registra()`). Le
   righe hanno un campo `sorgente` (`MANUALE`/`SENSORE`) per distinguere il
   dato inserito a mano da quello letto da un'automazione.
4. **Migrazioni solo additive**: lo schema si evolve con una funzione
   `migrate_db()` eseguita all'avvio, composta dagli helper di `core.migrate`
   (`ensure_table` con `IF NOT EXISTS`, `ensure_column` idempotente). Mai
   `DROP TABLE`, mai ricreare il database.
5. **Viste ricreate alla fine di ogni `migrate_db()`**: la definizione di ogni
   vista vive come costante nel codice e viene passata a
   `migrate.rebuild_views()` — sempre come ultimo passo. (SQLite può rompere
   le viste silenziosamente durante i rename.)
6. **Identificatori SQL validati**: qualunque nome di tabella/colonna/vista che
   finisce interpolato in un DDL/DML passa prima dalla validazione (gli helper
   di core lo fanno già; non comporre SQL con f-string su input non fidato).
7. **Config rotta = avvio negato**: la configurazione si carica con
   `core.config` (fail-fast). Mai default silenziosi su valori critici (porta,
   percorsi, elenchi di dominio). Un modulo che parte con una config sbagliata
   è peggio di un modulo che non parte.
8. **Stato = proiezione dello storico**: dove c'è un ciclo di vita, lo stato
   corrente non è un campo aggiornabile ma l'ultimo evento del log
   (`core.events` + vista `latest_state_per_entity`). Niente `UPDATE` di stato.
9. **Regole "a tempo di lettura"**: scadenze, reset di turno e simili si
   **calcolano a ogni lettura** dallo storico (`core.schedule`, `core.shifts`),
   mai con job schedulati che modificano i dati.
10. **Pannelli admin read-only**: l'ispezione dei dati passa da
    `core.adminbrowser` (connessioni `mode=ro`) + export CSV. La modifica
    manuale di log e contatori corrompe i KPI in modo silenzioso.
11. **Normalizzazione dei codici in un unico punto**: ogni famiglia di codici
    identificativi ha UNA regola registrata con `core.codes`; import, API e UI
    passano tutti da `codes.norm()`.
12. **Niente logiche di dominio in `core/`**: il core contiene solo pattern
    generici. Il dominio (quali entità, quali stati, quali turni, quali
    cadenze) vive nella **config TOML del modulo** e nel codice del modulo.
13. **Flusso IP a senso unico**: il codice fluisce da argo-core verso le
    installazioni, mai il contrario. In questo repository non entrano dati,
    nomi, orari, formule o logiche provenienti da un'installazione specifica.

## 4. Struttura di una suite installata

```
<root della suite>\
├── comune\              ← cartella dati (ARGO_COMUNE): TUTTI i DB live + portal.json.
│                          MAI nei rilasci. Backup = copia di questa cartella.
├── core\                ← argo-core (si aggiorna sovrascrivendo la cartella)
├── modulo_a\            ← un modulo = una cartella sorella di core\
│   ├── app.py
│   ├── modulo_a.toml
│   ├── templates\
│   └── README.md
└── modulo_b\
```

**Porte**: blocco riservato **4700–4799**. Portale = `4700`, moduli dal `4701`
in su. Il portale suggerisce la prossima porta libera (`GET /api/moduli` →
`prossima_porta`); lo scaffolder la usa automaticamente se il portale è acceso.

**Bootstrap di core** (nessuna installazione: vendoring puro). In testa
all'`app.py` di ogni modulo:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import db, migrate, config
```

(Lo scheletro generato dallo scaffolder usa una variante più robusta che
risale le cartelle finché trova `core/`.)

## 5. Come nasce un nuovo modulo: lo scaffolder

**Parti sempre dallo scaffolder**, non da un file vuoto:

```
python -m core.scaffold <nome> [--porta N] [--dir PATH]
```

- `<nome>` deve essere un identificatore Python valido (lettere, cifre, `_`).
- La porta viene chiesta al **registro del portale** se raggiungibile su
  `http://127.0.0.1:4700`; altrimenti vale `--porta`; altrimenti `4701`.
- Non sovrascrive mai una cartella esistente.

Genera `<dir>/<nome>/` con:

| File | Contenuto |
|---|---|
| `app.py` | bootstrap di core, `migrate_db()` additiva, config fail-fast, `create_app()` con Flask **lazy**, route `/` |
| `<nome>.toml` | config d'esempio (`[app] titolo, porta`) |
| `templates/index.html` | pagina base |
| `README.md` | porta, DB, avvio, come estendere |

Lo scheletro generato **parte da solo** (`python app.py`) e risponde su `/`.
I dati vanno in `ARGO_COMUNE` (default di sviluppo: `./dati` accanto al
modulo). Da lì in poi si estende: schema in `migrate_db()`, dominio nel TOML,
route in `create_app()`.

Un **esempio completo e funzionante** costruito così è
[`examples/presenze`](examples/presenze/): registro presenze attrezzatura che
usa events + statemachine + forms + schedule + board + shifts. Usalo come
riferimento di stile e composizione.

## 6. API reference (firme pubbliche)

Tutto ciò che segue è stdlib-only salvo dove indicato. `con` è sempre una
`sqlite3.Connection` aperta dal **modulo proprietario** con `db.owned()`
(row_factory `sqlite3.Row` inclusa).

### core.db — connessioni uniformi

```python
BUSY_TIMEOUT_MS = 5000   # timeout unico della suite

owned(path, *, wal=True, fk=True, timeout_ms=BUSY_TIMEOUT_MS) -> sqlite3.Connection
    # DB DI PROPRIETÀ del modulo (unico scrittore). WAL + FK + busy_timeout + Row.

readonly(path, *, timeout_ms=BUSY_TIMEOUT_MS) -> sqlite3.Connection
    # DB di un ALTRO modulo, URI mode=ro: le scritture falliscono nel motore.
    # Solleva sqlite3.OperationalError se il file non esiste (non lo crea).
```

### core.migrate — migrazioni additive

```python
table_columns(con, table) -> set[str]        # colonne esistenti ({} se assente)
table_exists(con, table) -> bool
ensure_table(con, ddl) -> None               # pretende 'CREATE TABLE IF NOT EXISTS'
ensure_column(con, table, column, ddl_type) -> bool   # idempotente; True se aggiunta ora
rebuild_views(con, views: dict[str, str]) -> None     # DROP+CREATE; SEMPRE alla fine
```

### core.config — TOML fail-fast

```python
class ConfigError(Exception)                 # fatale: l'app non deve partire

load(path) -> dict                           # file mancante/malformato -> ConfigError
require(cfg, *chiavi) -> Any                 # chiave annidata OBBLIGATORIA -> ConfigError se assente
optional(cfg, *chiavi, default=None) -> Any  # opzionale, default ESPLICITO del chiamante
```

### core.events — stato event-sourced

```python
SORGENTI = ("MANUALE", "SENSORE")
TABELLA_DEFAULT = "eventi"; VISTA_DEFAULT = "latest_state_per_entity"

migra(con, table=TABELLA_DEFAULT, vista=VISTA_DEFAULT, *,
      extra_colonne: dict[str, str] | None = None) -> None
    # crea log append-only + vista di proiezione; colonne extra additive

registra(con, entita, stato, *, table=..., sorgente="MANUALE",
         operatore=None, note=None, extra: dict | None = None) -> int
    # SINGLE WRITE-POINT: l'unica scrittura ammessa sul log (solo INSERT)

stato_corrente(con, entita=None, *, vista=...) -> list[dict] | dict | None
    # None -> tutti gli stati correnti; "X" -> stato di X o None se mai vista

storico(con, entita, *, table=...) -> list[dict]   # cronologia completa
```

Colonne standard del log: `id, ts, entita, stato, sorgente, operatore, note`.
La proiezione è "evento con id massimo per entità" (id monotono).

### core.statemachine — transizioni dichiarative (puro)

```python
class TransizioneNonValida(Exception)

StateMachine(transizioni: dict[str, dict[str, str]], iniziale: str)
    # {stato: {azione: destinazione}}; validata alla costruzione (fail-fast)
StateMachine.da_config(sezione) -> StateMachine   # {iniziale, transizioni} da TOML
.iniziale: str
.stati() -> frozenset[str]
.azioni(stato) -> dict[str, str]
.terminali() -> frozenset[str]
.puo(stato, azione) -> bool
.transita(stato, azione) -> str        # non ammessa -> TransizioneNonValida
```

Pattern tipico: `nuovo = sm.transita(corrente, azione)` **poi**
`events.registra(con, entita, nuovo, ...)` — il log non registra mai un
movimento impossibile.

### core.shifts — turni parametrici (puro)

```python
Turni(turni: list[dict])               # [{"nome","inizio","fine"}], "HH:MM"
Turni.da_config(lista) -> Turni
.nomi() -> list[str]
.turno_di(ora) -> str | None           # ora: "HH:MM" | time | datetime
```

Intervalli semiaperti `[inizio, fine)`; `fine < inizio` = turno oltre la
mezzanotte; `inizio == fine` = copertura 24h. Gli orari stanno in config.

### core.schedule — scadenze a tempo di lettura (puro)

```python
OK, DA_FARE, SCADUTA = "ok", "da_fare", "scaduta"

stato_task(tasks, ultime, eventi=None, oggi=None) -> dict
    # tasks:  [{"id", "soggetto", "freq_giorni": int|None, "evento": str|None, ...}]
    # ultime: {(task_id, soggetto): ("YYYY-MM-DD", ts | None)}
    # eventi: {soggetto: ts ultimo evento}   (per le attività a evento)
    # oggi:   "YYYY-MM-DD" (default oggi; parametrizzato per i test)
    # -> {soggetto: {"stato", "da_fare", "scadute", "tasks": [...]}}
```

Zero SQL: il modulo la alimenta con le proprie query. Lo stato del soggetto è
il peggiore tra le sue attività.

### core.forms — form-engine dichiarativo (puro)

```python
TIPI = ("text", "textarea", "number", "date", "select", "checkbox")

valida(campi, dati) -> tuple[dict, dict]     # (puliti, errori); valido <=> errori == {}
render_html(campi, valori=None, errori=None) -> str   # controlli pre-compilati, escapati
```

Campo: `{"nome", "label", "tipo", "obbligatorio", "min"/"max" (number),
"maxlen" (text), "opzioni" (select)}`. Definizione malformata → `ValueError`
(errore del programmatore, fail-fast). `render_html` non emette `<form>` né
il bottone: li mette il template del modulo.

### core.auth — utenti e ruoli (hashing Werkzeug, lazy)

```python
migra(con, table="utenti") -> None
crea_utente(con, username, password, ruolo, *, table="utenti") -> None   # upsert
imposta_password(con, username, password, *, table="utenti") -> None
disattiva(con, username, *, attivo=False, table="utenti") -> None
lista_utenti(con, *, table="utenti") -> list[dict]        # senza hash
verifica(con, username, password, *, table="utenti") -> dict | None
ha_ruolo(utente, *ruoli) -> bool
richiede(*ruoli, verifica, realm="ARGO")   # decoratore Flask: Basic Auth + gate di ruolo
    # verifica: callable (username, password) -> utente | None
    # utente autenticato in flask.g.utente
```

I ruoli sono stringhe libere decise dal modulo. Le password sono sempre
hashate (mai in chiaro nel DB).

### core.board — board (kanban) config-driven (puro)

```python
Board(colonne: list[dict], campi_tile: list[str] | None = None)
    # colonne: [{"titolo", "stati": [...]}]; stesso stato in 2 colonne -> ValueError
Board.da_config(sezione) -> Board            # sezione [board] del TOML
.disponi(stati_correnti) -> list[dict]       # righe con 'entita' e 'stato'
.render_html(stati_correnti) -> str          # HTML escapato
```

Si alimenta con `events.stato_corrente(con)`. Gli stati non mappati non
vengono mostrati (vista configurata; lo storico resta intero nel log).

### core.codes — normalizzazione codici

```python
registra(nome, fn) -> None                   # una regola per famiglia, una volta
norm(nome, valore) -> str                    # famiglia non registrata -> KeyError
zfill_numerico(cifre) -> Callable[[str], str]   # factory zero-padding a N cifre
```

### core.notify — email SMTP

```python
send_email(cfg, subject, body_text, body_html=None, *, log_con=None) -> bool
    # cfg["smtp"] = {"enabled", "server", "port", "from", "to": [...]}
    # NON solleva mai; log append-only in notifiche_log del DB del mittente
```

### core.export — CSV per Excel (locale italiano)

```python
csv_bytes(rows, headers=None, *, delimiter=";", bom=True) -> bytes
csv_response(rows, filename, headers=None, *, delimiter=";", bom=True)  # Flask lazy
```

### core.adminbrowser — browser DB read-only (richiede Flask)

```python
blueprint(dbs: dict | Callable[[], dict], auth=None, name="adminbrowser") -> Blueprint
    # dbs: {alias: path} o funzione che lo ritorna; connessioni mode=ro
    # endpoints: /databases, /<alias>/tabelle, /<alias>/righe/<tab>,
    #            /<alias>/export/<tab>.csv
```

### core.portal — portale della suite (processo, richiede Flask)

```
python -m core.portal        # -> http://localhost:4700
```

Registro moduli (`comune/core.sqlite`, il portale ne è l'unico scrittore),
health-check (`/api/health`), browser DB su tutta la cartella dati, config in
`comune/portal.json`. Scritture protette da Basic Auth
(`ARGO_PORTAL_USER`/`ARGO_PORTAL_PASS`). `GET /api/moduli` espone
`prossima_porta` (usato dallo scaffolder).

### core.scaffold — generatore di moduli

```python
# CLI: python -m core.scaffold <nome> [--porta N] [--dir PATH]
porta_suggerita(porta_arg=None, *, portale="http://127.0.0.1:4700", timeout=1.0) -> int
genera(nome, porta, dest_dir=".") -> Path    # nome non valido -> ValueError;
                                             # cartella esistente -> FileExistsError
```

## 7. Composizione tipica di un modulo (il pattern completo)

```python
# 1. config: tutto il dominio nel TOML, fail-fast
cfg = corecfg.load(CONFIG_PATH)
sm = statemachine.StateMachine.da_config(corecfg.require(cfg, "macchina"))
board = coreboard.Board.da_config(corecfg.require(cfg, "board"))
turni = shifts.Turni.da_config(corecfg.require(cfg, "turni"))

# 2. migrazione additiva + event log
def migrate_db():
    con = db.owned(DB_PATH)
    events.migra(con, extra_colonne={...})   # viste ricreate alla fine, dentro migra()
    con.commit(); con.close()

# 3. scrittura: valida la transizione, POI appendi l'evento (single write-point)
nuovo = sm.transita(stato_corrente, azione)          # TransizioneNonValida se vietata
events.registra(con, entita, nuovo, operatore=op)

# 4. lettura: proiezioni e regole a tempo di lettura
board.render_html(events.stato_corrente(con))
schedule.stato_task(tasks, ultime)                    # scadenze calcolate, niente job
turni.turno_di(datetime.now())

# 5. input utente: stessa definizione per validare e renderizzare
puliti, errori = forms.valida(CAMPI, request.form)
forms.render_html(CAMPI, valori=puliti, errori=errori)
```

L'implementazione completa e testata di questo pattern è
`examples/presenze/app.py`.

## 8. Test: obbligatori, stdlib

```
python -m unittest discover tests -v
```

- Framework di test: `unittest` (niente pytest come dipendenza).
- I test che richiedono Flask/Werkzeug si marcano con
  `@unittest.skipUnless(HA_FLASK, "Flask non installato")`: la suite deve
  restare **verde anche senza Flask installato**.
- La logica di dominio va scritta **pura rispetto a una connessione**
  (funzioni che ricevono `con` e parametri) così è testabile su un DB in
  `:memory:` o in una cartella temporanea, senza server.
- Parametrizza le date (`oggi=...`) invece di dipendere dall'orologio.

## 9. Checklist di consegna per moduli generati da AI

Prima di consegnare il codice, verifica OGNI voce. Se una voce non è
soddisfatta, il modulo non è pronto.

- [ ] Il modulo è nato dallo **scaffolder** (o ne rispetta esattamente la
      struttura: `app.py`, `<nome>.toml`, `templates/`, `README.md`)?
- [ ] Scrive **solo** sul proprio database, aperto con `db.owned()`?
- [ ] Le letture da DB altrui usano `db.readonly()` (mode=ro)?
- [ ] I dati stanno in `ARGO_COMUNE`, **mai** dentro la cartella del modulo?
- [ ] `migrate_db()` è additiva, ri-eseguibile, composta dagli helper di
      `core.migrate`, con le viste ricreate **alla fine**?
- [ ] I log/eventi sono append-only con single write-point
      (`events.registra()` o equivalente unico)?
- [ ] Lo stato con ciclo di vita è una **proiezione** dello storico, non un
      campo aggiornato?
- [ ] Le transizioni di stato passano da una `StateMachine` dichiarata in
      config e validata all'avvio?
- [ ] La config è TOML caricata con `core.config` e le chiavi critiche usano
      `require()` (fail-fast, nessun default silenzioso)?
- [ ] Il dominio (entità, stati, turni, cadenze) sta nel **TOML**, non
      cablato nel codice?
- [ ] Scadenze/turni sono calcolati **a tempo di lettura**, senza job che
      modificano dati?
- [ ] I form usano `core.forms` (stessa definizione per validare e
      renderizzare); l'output HTML è escapato?
- [ ] Eventuale auth usa `core.auth` (password hashate, mai in chiaro)?
- [ ] Nessun identificatore SQL interpolato senza validazione?
- [ ] Flask è importato **lazy** (dentro `create_app()`), così il modulo è
      importabile e testabile senza Flask?
- [ ] Zero dipendenze oltre stdlib + Flask? Niente npm/build step?
- [ ] Porta nel blocco 4700–4799, presa dal registro del portale se possibile,
      e documentata nel README del modulo?
- [ ] Ci sono i test (`unittest`), verdi con
      `python -m unittest discover tests -v`, anche senza Flask installato?
- [ ] Il README del modulo dichiara porta, DB (owned vs read-only) e avvio?
- [ ] Nel codice non c'è **nessun dato/nome/orario/formula** proveniente da
      un'installazione specifica (regola del flusso IP a senso unico)?

---

*Versione del documento: allineata a argo-core 0.3.0. Se le firme in `core/`
divergono da questo file, fa fede il codice — e questo file va aggiornato.*
