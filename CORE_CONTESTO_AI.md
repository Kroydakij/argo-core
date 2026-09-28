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
3. **Log append-only con la busta standard**: le tabelle di log/eventi non si
   aggiornano né si cancellano, si inseriscono solo righe, e ogni scrittura
   passa da `core.busta.scrivi()` (direttamente o via `events.registra()`).
   Ogni riga ha la **busta** (ADR-005): `uid`, `tipo` dichiarato nel manifest,
   `versione`, `ts_utc` + `offset_min`, `attore_id` (ID utente della sessione,
   mai un nome scritto a mano), `entita_id`, `sorgente` (`MANUALE`/`SENSORE`).
   I dati di dominio sono colonne tipizzate accanto alla busta, mai un JSON.
4. **Migrazioni solo additive, numerate, con backup**: lo schema si evolve
   con una lista di passi numerati (`PASSI = [migrazioni.Passo(1, ...), ...]`)
   applicata all'avvio da `core.migrazioni.applica()`, che fa il backup del DB
   prima di ogni migrazione e registra la versione nel DB. I passi usano gli
   helper di `core.migrate` (`ensure_table` con `IF NOT EXISTS`,
   `ensure_column` idempotente) e **non fanno commit**. Un passo pubblicato
   non si modifica: si aggiunge il successivo. Mai `DROP TABLE`, mai ricreare
   il database; sui log append-only solo `ADD COLUMN`.
5. **Viste ricreate dopo l'ultimo passo**: la definizione di ogni vista vive
   come costante nel codice e viene passata ad `applica(..., viste=VISTE)`,
   che la ricrea a ogni avvio dopo tutti i passi. (SQLite può rompere le viste
   silenziosamente durante i rename.)
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
├── comune\              ← cartella dati (ARGO_COMUNE): TUTTI i DB live, argo.toml,
│                          core.sqlite (registro), auth.sqlite (utenti), _backup\.
│                          MAI nei rilasci. Backup = copia di questa cartella.
├── core\                ← argo-core (si aggiorna sovrascrivendo la cartella)
├── modulo_a\            ← un modulo = una cartella sorella di core\
│   ├── app.py
│   ├── modulo_a.toml
│   ├── templates\
│   └── README.md
└── modulo_b\
```

**Porte**: blocco riservato **4700–4799**. Shell = `4700`, moduli dal `4701`
in su. La shell suggerisce la prossima porta libera (`GET /api/moduli` →
`prossima_porta`); lo scaffolder la usa automaticamente se la shell è accesa.

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
- La porta viene chiesta al **registro della shell** se raggiungibile su
  `http://127.0.0.1:4700`; altrimenti vale `--porta`; altrimenti `4701`.
- Non sovrascrive mai una cartella esistente.

Genera `<dir>/<nome>/` con:

| File | Contenuto |
|---|---|
| `app.py` | bootstrap di core, `migrate_db()` additiva, config fail-fast, `create_app()` con Flask **lazy**, route `/` |
| `<nome>.toml` | config d'esempio (`[app] titolo, porta`) |
| `templates/index.html` | pagina base |
| `README.md` | porta, DB, avvio, come estendere |
| `manifest.toml` | cosa il modulo dichiara: nome, versione, core compatibile, permessi, menu, tipi di anagrafica, eventi (ADR-003) |

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

### core.migrazioni — passi numerati con backup (kernel, ADR-004)

```python
Passo(numero: int, descrizione: str, funzione: Callable[[Connection], None])
    # numeri consecutivi da 1; la funzione NON fa commit

applica(db_path, passi, *, viste=None, backup_dir=None,
        backup_da_tenere=5, versione_codice=None) -> Esito
    # Esito(da, a, applicati, backup). Backup (API online SQLite) prima dei
    # passi pendenti se il DB contiene dati; un passo = una transazione.
    # backup_dir default: <cartella del DB>/_backup/<nome_db>/
    # Solleva: PassiNonValidi, DBPiuNuovoDelCodice, BackupFallito,
    #          MigrazioneFallita(.numero, .backup) — tutte MigrazioneError

versione(db_path) -> int                     # sola lettura (PRAGMA user_version)
richiedi_versione(db_path, minima, *, suggerimento="") -> int   # < minima -> MigrazioneError
stato(db_path, *, backup_dir=None) -> dict   # versione, storia, backup presenti
```

CLI per il supporto: `python -m core.migrazioni stato <file.sqlite>`.
La storia sta nella tabella di sistema `_argo_schema` (append-only) del DB.

### core.manifest — cosa dichiara un modulo (kernel, ADR-003)

`<modulo>/manifest.toml`, statico e versionato col codice (mai dati
d'installazione: quelli stanno in `<modulo>.toml`). Formato:

```toml
[modulo]
nome = "andon"             # = nome della cartella
versione = "1.2.0"         # SemVer X.Y.Z
core = ">=1.0,<2.0"        # versioni di argo-core compatibili
titolo = "Andon"
descrizione = "..."        # facoltativa

[[permessi]]               # id sempre "<nome>.<azione>", minuscolo snake_case
id = "andon.chiudi_fermata"
descrizione = "Chiudere una fermata aperta"

[[menu]]
titolo = "Fermate"
percorso = "/"
permesso = "andon.chiudi_fermata"   # deve essere dichiarato in [[permessi]]

[anagrafica]
tipi = ["macchina"]        # tipi di anagrafica usati

[[eventi]]                 # tipo sempre "<nome>.<evento>"
tipo = "andon.fermata_chiusa"
versione = 1               # versione corrente dello schema dell'evento
entita = "macchina"        # tipo di anagrafica (in [anagrafica] tipi) o ""
descrizione = "..."
```

Chiavi sconosciute, id fuori namespace, duplicati, menu con permesso non
dichiarato, `core` incompatibile → `ManifestError` (fail-fast).

```python
class ManifestError(ValueError)
carica(percorso, *, versione_core=None) -> Manifest   # file o cartella del modulo
da_dict(dati, *, nome_cartella=None, versione_core=None) -> Manifest
Manifest: nome, versione, core, titolo, descrizione, permessi, menu,
          tipi_anagrafica, eventi; .dichiara_permesso(id) -> bool;
          .evento(tipo) -> Evento | None; .to_dict()
compatibile(intervallo, versione) -> bool             # ">=1.0,<2.0", "1.3.0"
verifica_tipi(manifest, tipi_disponibili) -> None     # tipo mancante -> ManifestError
scansiona(radice) -> list[Scansione]                  # cartelle figlie con manifest.toml
    # Scansione(cartella, nome, manifest|None, porta|None, errore|None)
    # porta letta da <cartella>/<nome>.toml [app] porta (contratto di conformità)
```

### core.config — TOML fail-fast

```python
class ConfigError(Exception)                 # fatale: l'app non deve partire

load(path) -> dict                           # file mancante/malformato -> ConfigError
require(cfg, *chiavi) -> Any                 # chiave annidata OBBLIGATORIA -> ConfigError se assente
optional(cfg, *chiavi, default=None) -> Any  # opzionale, default ESPLICITO del chiamante
```

### core.busta — la busta standard degli eventi (kernel, ADR-005)

```python
SORGENTI = ("MANUALE", "SENSORE"); SISTEMA = "sistema"
crea_log(con, tabella, colonne_dominio: dict[str, str] | None = None)
    # CREATE TABLE IF NOT EXISTS con busta + dominio + indici; su un log 0.x
    # aggiunge la busta (colonne nullable, righe vecchie intatte). In un Passo.
scrivi(con, tabella, *, tipo, manifest, entita_id=None, attore_id=None,
       sorgente="MANUALE", dati: dict | None = None, ora=None) -> int
    # SINGLE WRITE-POINT: tipo dichiarato nel manifest (versione da li');
    # entita_id obbligatorio se il tipo ha un'entita', vietato se no;
    # attore dalla sessione Flask o esplicito (busta.SISTEMA): mai dedotto.
ora_locale(riga) -> datetime     # ora "dell'orologio a muro" (UTC + offset)
e_legacy(riga) -> bool           # riga 0.x senza busta
```

Colonne della busta: `id, uid, tipo, versione, ts_utc, offset_min, attore_id,
entita_id, sorgente`. L'ordine nel log è per `id`. Per turni e date usa
`ora_locale(riga)`, mai il testo di `ts_utc`.

### core.events — log di stati sulla busta

```python
TABELLA_DEFAULT = "eventi"; VISTA_DEFAULT = "latest_state_per_entity"

migra(con, table=TABELLA_DEFAULT, vista=VISTA_DEFAULT, *,
      extra_colonne: dict[str, str] | None = None) -> None
    # log (busta + stato + note + extra) + vista di proiezione; in un Passo

registra(con, tipo, entita_id, stato, *, manifest, table=..., sorgente="MANUALE",
         attore_id=None, note=None, extra: dict | None = None) -> int
    # busta.scrivi(): UN tipo per log, dichiarato nel manifest

stato_corrente(con, entita_id=None, *, vista=...) -> list[dict] | dict | None
storico(con, entita_id, *, table=...) -> list[dict]   # cronologia completa
```

La proiezione è "evento con id massimo per entità" (id monotono). Ogni riga
restituita ha anche `entita` (la chiave), comoda per `core.board`. Sui log
0.x la chiave è il vecchio codice per le righe vecchie e `entita_id` per le
nuove; `operatore=` è deprecato (finisce in `note`).

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
`events.registra(con, tipo, entita_id, nuovo, manifest=M)` — il log non registra mai un
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

### core.auth — identità centrale, sessione condivisa, permessi (kernel, ADR-001)

Un solo archivio utenti per la suite: `comune/auth.sqlite`, **scritto solo
dal kernel** (shell e `python -m core.auth`). **Un modulo non crea né
modifica utenti**: legge la sessione e i permessi in sola lettura.

Nel modulo (Flask):

```python
@app.get("/")
@auth.richiede_permesso("andon.vedi")     # permesso DICHIARATO nel manifest
def home(): ...                           # utente in flask.g.utente

@app.get("/api/health")
@auth.pubblica                            # unica eccezione: niente sessione
def health(): ...

auth.inizializza(app, manifest=M, auth_db=COMUNE / "auth.sqlite")  # DOPO le route
# fail-fast: permesso usato ma non dichiarato -> ManifestError;
#            auth.sqlite assente o vecchio -> errore ("avvia prima la shell")
# da qui OGNI route richiede sessione valida (redirect al login della shell,
# 401 per le API), salvo @pubblica; POST con Origin di un altro host -> 403.

g.utente = {"id": UUID, "username", "nome", "tipo": "persona"|"servizio",
            "permessi": frozenset}
ha_permesso(utente, permesso) -> bool     # per mostrare/nascondere pulsanti
```

- Sessione: cookie `argo_sessione` (riservato) emesso dalla shell; vale per
  tutti i moduli sullo stesso host. Logout, utente disattivato e permesso
  revocato hanno effetto alla richiesta successiva.
- Basic Auth solo per utenti di tipo `servizio` (script, sensori).
- La sessione Flask del modulo usa il cookie `argo_<nome>` (lo imposta
  `inizializza`): mai il nome `session` di default.
- Riferisci sempre l'utente per `g.utente["id"]` (stabile), mai per username.

Lato kernel (shell, CLI): `prepara_db`, `crea_utente`, `rinomina_utente`,
`disattiva_utente`/`riattiva_utente`, `imposta_password`, `crea_gruppo`,
`aggiungi_membro`/`rimuovi_membro`, `definisci_ruolo`, `assegna_ruolo`/
`revoca_ruolo`, `login`, `apri_sessione`/`chiudi_sessione`, `sessione`,
`permessi_effettivi`, `storico`. Ogni scrittura richiede `attore=` (ID utente
o `auth.SISTEMA`) ed è un evento nel log `auth_eventi` (audit trail).

```
python -m core.auth crea-admin <username>          # primo amministratore
python -m core.auth importa --db <modulo_0x.sqlite> # utenti 0.x, senza reset password
```

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

### core.inventory — inventario generico event-sourced

```python
class GiacenzaInsufficiente(Exception)   # scarico rifiutato da movimenta()

Inventario(causali: dict[str, str], *, consenti_negativo=False,
           tabella_articoli="articoli", tabella_movimenti="movimenti",
           vista_giacenze="giacenze")
    # causali: {nome: "+"|"-"} — verso fisso per causale, dichiarato in config
Inventario.da_config(sezione) -> Inventario   # sezione [inventario] del TOML
.migra(con, *, extra_articoli=None, extra_movimenti=None) -> None
    # anagrafica + log movimenti + vista giacenze (ricreata alla fine)
.crea_articolo(con, codice, descrizione="", unita="pz", soglia_minima=None)  # upsert
.disattiva_articolo(con, codice, *, attivo=False)  # via dalle giacenze, storico intatto
.lista_articoli(con, *, solo_attivi=True) -> list[dict]
.movimenta(con, codice, quantita, causale, *, sorgente="MANUALE",
           operatore=None, note=None, extra=None) -> int
    # SINGLE WRITE-POINT (solo INSERT). quantita SEMPRE positiva: il segno lo
    # da' la causale. Rifiuta causale ignota, quantita<=0, articolo ignoto o
    # disattivato, scarico sotto zero (salvo consenti_negativo) -> GiacenzaInsufficiente
.giacenza(con, codice=None) -> list[dict] | dict | None   # proiezione SUM(movimenti)
.sotto_scorta(con) -> list[dict]     # giacenza <= soglia_minima, a tempo di lettura
.storico(con, codice) -> list[dict]  # movimenti in ordine cronologico
```

La giacenza non è un campo: è la **somma dei movimenti** (vista `giacenze`,
solo articoli attivi). Le correzioni sono movimenti di rettifica (causali
dedicate), mai UPDATE. La normalizzazione dei codici articolo resta compito
del modulo, con `core.codes`, **prima** di chiamare queste funzioni.

Config tipo:

```toml
[inventario]
consenti_negativo = false
[inventario.causali]
CARICO = "+"
CONSUMO = "-"
RETTIFICA_PIU = "+"
RETTIFICA_MENO = "-"
```

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

### core.shell — la shell della suite (kernel, processo, richiede Flask)

```
python -m core.auth crea-admin <username>   # solo la prima volta
python -m core.shell                        # -> http://localhost:4700
```

Login unico (cookie `argo_sessione`, vale per tutti i moduli sullo stesso
host), logout, "cambia password", home con i moduli che l'utente può usare,
amministrazione utenti/gruppi/ruoli (`/utenti`, permesso `core.utenti`),
registro moduli + health-check + browser DB read-only (permesso
`core.admin`). Unico scrittore di `core.sqlite` e `auth.sqlite`.

Config obbligatoria `comune/argo.toml` (fail-fast, `core.config.carica_suite`):

```toml
[suite]
titolo = "ARGO"            # facoltativo
porta = 4700               # facoltativo
[auth]
durata_sessione_ore = 12   # obbligatorio
```

**Cornice comune**: i template di un modulo inizializzato con
`auth.inizializza()` possono fare `{% extends "argo_cornice.html" %}` (blocchi
`titolo`, `stile`, `contenuto`, `script`): barra con titolo della suite, menu
dei moduli **filtrato per i permessi** dell'utente, nome utente, "esci". La
variabile `argo` (utente, menu, url della shell) è disponibile in ogni
template. Il registro si legge con `core.registro` (stdlib).

`core.portal` resta come alias deprecato di `core.shell` per una minor.

I moduli con `manifest.toml` nelle cartelle sorelle di `core\` vengono
**scoperti da soli** all'avvio e con `POST /api/moduli/rileggi` (admin):
deploy di un modulo = copiarne la cartella. Un manifest non valido lascia il
modulo nel registro con `stato = "errore"` e il messaggio; un modulo non più
su disco diventa `"assente"`. `POST /api/moduli` (registrazione manuale)
resta per i moduli legacy senza manifest. `GET /api/moduli` espone per ogni
modulo `origine`, `stato`, `titolo`, `versione`, `menu`, e `prossima_porta`
(usato dallo scaffolder).

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

# 2. migrazione: passi numerati + backup automatico (core.migrazioni)
PASSI = [migrazioni.Passo(1, "log eventi",
                          lambda con: events.migra(con, extra_colonne={...}))]
def migrate_db():
    migrazioni.applica(DB_PATH, PASSI)

# 3. scrittura: valida la transizione, POI appendi l'evento (single write-point)
nuovo = sm.transita(stato_corrente, azione)          # TransizioneNonValida se vietata
events.registra(con, "mio.cambio_stato", entita_id, nuovo, manifest=M)  # attore = sessione

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
      struttura: `app.py`, `<nome>.toml`, `manifest.toml`, `templates/`,
      `README.md`)?
- [ ] `manifest.toml` è valido (`core.manifest.carica()` non solleva) e
      dichiara **ogni** permesso, voce di menu, tipo di anagrafica ed evento
      che il codice usa?
- [ ] Scrive **solo** sul proprio database, aperto con `db.owned()`?
- [ ] Le letture da DB altrui usano `db.readonly()` (mode=ro)?
- [ ] I dati stanno in `ARGO_COMUNE`, **mai** dentro la cartella del modulo?
- [ ] Lo schema è una lista `PASSI` numerata applicata con
      `core.migrazioni.applica()` (backup automatico), passi additivi senza
      commit, viste passate come `viste=`?
- [ ] I log/eventi sono append-only e scritti **solo** con `busta.scrivi()`
      (o `events.registra()`), con tipo dichiarato nel manifest e attore =
      utente della sessione? Nessun campo "operatore" digitato a mano?
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
- [ ] Le route sono protette con `auth.richiede_permesso(...)` (o marcate
      `@auth.pubblica`) e `auth.inizializza()` è chiamata dopo le route? Il
      modulo **non** ha una sua tabella utenti né un suo login?
- [ ] Nessun identificatore SQL interpolato senza validazione?
- [ ] Flask è importato **lazy** (dentro `create_app()`), così il modulo è
      importabile e testabile senza Flask?
- [ ] Zero dipendenze oltre stdlib + Flask? Niente npm/build step?
- [ ] Porta nel blocco 4700–4799, presa dal registro della shell se possibile,
      e documentata nel README del modulo?
- [ ] Ci sono i test (`unittest`), verdi con
      `python -m unittest discover tests -v`, anche senza Flask installato?
- [ ] Il README del modulo dichiara porta, DB (owned vs read-only) e avvio?
- [ ] Nel codice non c'è **nessun dato/nome/orario/formula** proveniente da
      un'installazione specifica (regola del flusso IP a senso unico)?

---

*Questo documento vive nel repository e si aggiorna insieme al codice: fa fede
la versione presente sullo stesso commit. Se le firme in `core/` divergono da
questo file, fa fede il codice — e questo file va aggiornato.*
