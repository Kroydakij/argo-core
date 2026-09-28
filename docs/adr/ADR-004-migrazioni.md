# ADR-004 — Migrazioni schema SQLite automatiche, con backup

- **Stato**: Accettato (2026-09-28) — implementato in `core/migrazioni.py`
- **Data**: 2026-09-27
- **Riguarda**: nuovo `core.migrazioni` (runner), convenzione `migrate_db()`, scaffolder; `core.migrate` resta
- **Rompe l'API**: solo la convenzione (gli helper restano)

## Contesto

Oggi ogni modulo ha una `migrate_db()` eseguita all'avvio, composta dagli
helper di `core.migrate` (`ensure_table` con `IF NOT EXISTS`,
`ensure_column` idempotente, `rebuild_views` alla fine). Funziona perché è
idempotente, ma:

- **non sa a che versione è il DB**: non si può dire "questo DB è allo
  schema 7", né rifiutare di partire se il codice è più vecchio del DB
  (caso reale: si ricopia per errore un rilascio precedente sopra quello
  nuovo);
- **non fa backup**: se una migrazione fallisce a metà, o migra in modo
  sbagliato, sul PC del cliente non c'è un punto di ritorno. Il backup
  "copia della cartella comune" è manuale e di solito non è stato fatto
  proprio prima dell'aggiornamento;
- **non lascia traccia**: non si sa quando e da quale versione del codice
  è stato migrato un DB;
- **i passi di dati** (riempire una colonna nuova) non hanno un posto: o si
  rieseguono a ogni avvio, o si mettono guardie a mano.

Vincoli: deploy = copia di cartella, niente admin, niente servizi; l'utente
che avvia il modulo ha scrittura solo sulla cartella della suite.

## Decisione

### 1. Migrazioni come lista di passi numerati

Ogni proprietario di DB dichiara, **nel codice**, la lista ordinata dei
passi:

```python
from core import migrate, migrazioni

def _p1(con):   # schema iniziale (= la migrate_db() 0.x, già idempotente)
    migrate.ensure_table(con, """CREATE TABLE IF NOT EXISTS fermate (...)""")

def _p2(con):
    migrate.ensure_column(con, "fermate", "causale", "TEXT")

PASSI = [
    migrazioni.Passo(1, "schema iniziale", _p1),
    migrazioni.Passo(2, "causale sulle fermate", _p2),
]
VISTE = {"fermate_aperte": "CREATE VIEW fermate_aperte AS ..."}

migrazioni.applica(DB_PATH, PASSI, viste=VISTE, backup_dir=COMUNE / "_backup")
```

- Numeri interi consecutivi da 1, mai riusati, mai riordinati. Un passo
  pubblicato non si modifica: si aggiunge il successivo.
- Le **viste non sono passi**: `applica()` le ricrea **sempre** dopo
  l'ultimo passo, a ogni avvio (regola 5 invariata).
- I passi usano gli helper di `core.migrate`, che restano l'API per scrivere
  DDL.

### 2. Versione registrata nel DB

- Tabella di sistema `_argo_schema` (append-only), creata dal runner:
  `numero, descrizione, applicato_il_utc, versione_codice, esito, backup`.
- Versione corrente = massimo `numero` con `esito = 'OK'`. In più il runner
  imposta `PRAGMA user_version` allo stesso valore (transazionale, leggibile
  con qualunque strumento SQLite).

### 3. Algoritmo di `applica()`

1. Apre il DB con `db.owned()`; se non ha `_argo_schema` la crea.
2. **Adozione DB 0.x**: se il DB ha già tabelle ma nessuna riga in
   `_argo_schema`, esegue comunque dal passo 1. Funziona perché il passo 1 è
   la vecchia `migrate_db()`, idempotente per costruzione (`IF NOT EXISTS`,
   `ensure_column`). Nessuno strumento di "baseline" separato.
3. Versione DB **>** ultimo passo noto al codice ⇒ **avvio negato**
   ("DB allo schema 9, questo codice conosce fino a 7: stai avviando un
   rilascio più vecchio?"). Mai toccare un DB più nuovo del codice.
4. Nessun passo pendente ⇒ solo ricreazione viste, niente backup.
5. Passi pendenti ⇒ **backup prima di tutto**:
   - con l'API di backup online di SQLite (`sqlite3.Connection.backup()`,
     stdlib), non con una copia di file: è consistente anche in WAL e con
     lettori aperti, e produce un file unico senza `-wal`/`-shm`;
   - destinazione `comune/_backup/<nome_db>/<nome_db>.v<da>-<ts_utc>.sqlite`;
   - si verifica il backup (`PRAGMA integrity_check` sul file prodotto);
     backup fallito o corrotto ⇒ **avvio negato**, nessun passo eseguito.
6. Ogni passo in una transazione propria (`BEGIN IMMEDIATE` … `COMMIT`),
   con registrazione in `_argo_schema` nella stessa transazione. Prima di
   eseguire il passo si rilegge la versione dentro il lock (due processi
   avviati insieme non migrano due volte).
7. Passo fallito ⇒ `ROLLBACK`, riga `esito = 'ERRORE'` con il messaggio,
   **avvio negato** con l'indicazione del backup da cui ripartire. I passi
   già riusciti restano applicati (ciascuno è atomico) e il DB è a una
   versione coerente e nota.
8. Ricreazione delle viste; `COMMIT`.

Il ripristino è manuale e documentato: fermare il modulo, copiare il file di
backup al posto del DB. Nessun ripristino automatico (vedi alternative).

### 4. Cosa è ammesso in un passo

- DDL additivo con gli helper (regola 4 invariata: niente `DROP TABLE`,
  niente ricreazione del DB).
- **Passi di dati** su tabelle non-log (es. riempire una colonna nuova di una
  tabella di registro): ammessi, girano una volta sola.
- Sui **log append-only** solo `ADD COLUMN`: mai `UPDATE`/`DELETE` di righe,
  nemmeno in migrazione (regola 3). Le righe legacy si interpretano a tempo
  di lettura (ADR-005).
- Ristrutturazioni non additive (cambiare un vincolo, rinominare una
  colonna): fuori scope 1.0. Il backup automatico le renderebbe meno
  pericolose, ma sono un'altra decisione (ADR futuro).

### 5. Retention dei backup

- Si tengono gli ultimi N backup per DB (`[migrazioni] backup_da_tenere` in
  `comune/argo.toml`, obbligatorio, minimo 1). Il backup appena fatto non si
  cancella mai. È l'unica cancellazione automatica di file della suite.
- Spazio libero insufficiente per il backup (stima: dimensione del DB × 1,1)
  ⇒ avvio negato con messaggio chiaro, prima di iniziare.

### 6. DB del kernel

`core.sqlite`, `auth.sqlite`, `anagrafica.sqlite` usano lo stesso runner,
eseguito dalla shell all'avvio. I moduli che li leggono dichiarano la
versione minima di schema kernel attesa implicitamente con `core = ">=1.x"`
(ADR-003) e, all'avvio, verificano `PRAGMA user_version` dei DB kernel:
se più vecchio del necessario (shell non ancora riavviata dopo
l'aggiornamento) ⇒ avvio negato con "avvia prima la shell".

## Alternative scartate

- **Status quo (idempotenza pura).** Non dà versione, non dà backup, non
  distingue un rilascio vecchio da uno nuovo.
- **Solo `PRAGMA user_version`, senza tabella.** Minimo, ma perde quando, da
  quale codice, con quale backup: esattamente ciò che serve al supporto
  quando qualcosa va storto sul PC del cliente. Si tengono entrambi.
- **File di migrazione separati (`migrazioni/0001_x.py` o `.sql`).** Più
  "da framework", ma aggiunge caricamento dinamico di file e un ordinamento
  per nome; una lista in Python è leggibile, testabile e basta.
- **Strumenti esterni (Alembic, yoyo, ...).** Dipendenze extra e ORM-centrici.
  Vietati dallo stack.
- **Copia del file `.sqlite` come backup.** Inconsistente in WAL (le ultime
  transazioni possono stare nel `-wal`) e con altri processi aperti.
- **`VACUUM INTO` come backup.** Valido e compatto, ma riscrive tutto il DB
  (lento sui file grandi) e dipende dalla versione SQLite di Python (≥ 3.27);
  l'API `backup()` c'è in ogni Python supportato ed è incrementale.
- **Backup in una cartella fuori da `comune/`.** Più sicuro contro la
  perdita del disco, ma su un PC senza admin non esiste un altro posto
  garantito scrivibile. `comune/_backup/` segue la regola "backup = copia
  della cartella dati": chi copia `comune/` si porta dietro anche questi.
- **Ripristino automatico dal backup in caso di errore.** Non serve alla
  consistenza (ogni passo è atomico, il DB resta a una versione nota) e
  nasconderebbe il problema: al riavvio successivo lo stesso passo fallirebbe
  di nuovo, in un ciclo ripristino/fallimento. Il codice va corretto; un
  umano decide se ripristinare. Il runner si ferma e dice esattamente cosa
  fare e dove sta il backup.
- **Transazione unica per tutti i passi.** Alcuni DDL SQLite e passi di
  dati lunghi reggono male transazioni enormi e un errore al passo 9
  annullerebbe 8 passi corretti. Passi atomici singoli + backup iniziale
  danno lo stesso livello di sicurezza con errori più leggibili.

## Conseguenze

- Ogni avvio con passi pendenti produce un file di backup; `comune/` cresce
  di N copie per DB. Documentarlo nella guida d'installazione.
- Il supporto ha una storia: `SELECT * FROM _argo_schema` dice cosa è
  successo, quando e dove sta il backup.
- Lo scaffolder genera `PASSI = [Passo(1, "schema iniziale", _p1)]` e la
  chiamata ad `applica()` invece della `migrate_db()` libera.
- Test: il kernel fornisce un helper per verificare che una lista di passi
  applicata su DB vuoto e applicata a pezzi (1..k, poi k+1..n) produca lo
  stesso schema.

### Rotture API

- Nessuna funzione rimossa: `table_columns`, `table_exists`, `ensure_table`,
  `ensure_column`, `rebuild_views` restano identici.
- Cambia la **convenzione** e quindi la regola 4 di `CORE_CONTESTO_AI.md`:
  "migrazioni solo additive, **numerate e applicate con
  `migrazioni.applica()`**". Un modulo 0.x che continua a chiamare la sua
  `migrate_db()` funziona ancora ma non è conforme (niente backup, niente
  versione).
- `events.migra()` e `Inventario.migra()` 0.x (che ricreano viste al loro
  interno) si trasformano in funzioni-passo + dizionari di viste da passare
  al runner, così che le viste siano ricreate **una volta, alla fine** di
  tutti i passi del DB.

### Decisioni prese in revisione (2026-09-28)

- `backup_da_tenere`: default **5**.
- Comando di supporto `python -m core.migrazioni stato <db>` (versione,
  passi, backup): **sì**.

### Note di implementazione

- Su un DB **appena creato** (nessuna tabella oltre a quella di sistema) il
  runner salta il backup: sarebbe un file vuoto. Il backup si fa sempre
  quando il DB contiene già oggetti, compresa l'adozione di un DB 0.x.
- Un passo che fa `commit()` da sé viene rifiutato (`MigrazioneFallita`):
  romperebbe l'atomicità "un passo = una transazione".
- Finché `comune/argo.toml` non esiste (arriva con ADR-001/003), il numero
  di backup si passa ad `applica(..., backup_da_tenere=N)`; il default è 5.
