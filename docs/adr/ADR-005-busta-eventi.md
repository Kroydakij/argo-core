# ADR-005 — Busta standard degli eventi

- **Stato**: Proposto
- **Data**: 2026-09-27
- **Riguarda**: `core.events` (riscritto sopra la busta), `core.inventory`, log del kernel (auth, anagrafica), notifiche 1.x
- **Rompe l'API**: sì (vedi *Rotture API*)

## Contesto

I log append-only 0.4.0 hanno colonne simili ma non uguali:

| | `core.events` | `core.inventory` (movimenti) |
|---|---|---|
| quando | `ts TEXT DEFAULT datetime('now','localtime')` | idem |
| chi | `operatore TEXT` libero | idem |
| su cosa | `entita TEXT` libero | `codice TEXT` |
| cosa | `stato` | `causale`, `quantita` |
| da dove | `sorgente` MANUALE/SENSORE | idem |

Problemi:

- **Ora locale senza offset**, generata da SQLite: al cambio dell'ora legale
  l'ora 02:00–03:00 di ottobre esiste due volte e gli eventi non si ordinano
  né si confrontano tra moduli; la precisione è al secondo.
- **"Chi" è testo libero**: "rossi", "Rossi M.", "mario" sono la stessa
  persona? Non si può rispondere, e ADR-001 ora dà un ID utente certo.
- **"Su cosa" è testo libero**: ADR-002 dà un ID anagrafica stabile.
- **Nessun tipo, nessuna versione**: un log contiene "cambi di stato" senza
  dire di che genere; se il significato di una colonna cambia tra due
  versioni del modulo, le righe vecchie e nuove sono indistinguibili.
- Le notifiche (1.x) dovranno reagire a eventi di moduli diversi: senza una
  forma comune ogni abbonamento è un caso speciale.

## Decisione

### 1. La busta

Ogni log append-only della suite — dei moduli, delle utility, del kernel —
ha queste colonne, con questi nomi e questo significato:

| Colonna | Tipo SQLite | Significato |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | ordine locale nel log (monotono, come oggi) |
| `uid` | TEXT NOT NULL UNIQUE | UUID v4 dell'evento: identità globale, per deduplicare e riferire eventi tra DB (notifiche 1.x) |
| `tipo` | TEXT NOT NULL | `"<modulo>.<evento>"`, dichiarato nel manifest (ADR-003) |
| `versione` | INTEGER NOT NULL | versione dello schema di quel `tipo` (≥ 1; 0 = riga legacy) |
| `ts_utc` | TEXT NOT NULL | istante UTC, ISO 8601 al millisecondo con `Z`: `2026-10-25T01:30:00.123Z` |
| `offset_min` | INTEGER NOT NULL | offset dell'ora locale del PC in quel momento, in minuti (`+60`, `+120`) |
| `attore_id` | TEXT NOT NULL | ID utente (ADR-001): persona, utente di servizio, o `"sistema"` |
| `entita_id` | TEXT NULL | ID anagrafica canonico (ADR-002); NULL solo se il tipo è dichiarato senza entità |
| `sorgente` | TEXT NOT NULL | `MANUALE` \| `SENSORE` (invariato, CHECK a livello DB) |

Le **colonne di dominio restano colonne tipizzate** accanto alla busta
(`stato` per `core.events`, `quantita`/`causale` per `inventory`, ciò che
serve a un modulo). La busta non introduce un campo `payload` JSON: i dati
restano interrogabili in SQL e leggibili nel browser DB.

### 2. Un solo modo di scrivere

```python
from core import busta

busta.scrivi(con, tabella, tipo="andon.fermata_chiusa",
             entita_id=eid, dati={"causale": "GUASTO"},
             sorgente="MANUALE")          # attore preso dal contesto
```

- È il single write-point di tutti i log (regola 3): `events.registra()` e
  `Inventario.movimenta()` lo chiamano internamente.
- Genera `uid`, `ts_utc`, `offset_min` **in Python**, non con i DEFAULT di
  SQLite: millisecondi, UTC, offset letto dal sistema operativo
  (`datetime.now().astimezone().utcoffset()`).
- `attore_id`: in una richiesta web lo prende da `flask.g.utente` (ADR-001);
  fuori da una richiesta (CLI, script, seed) va passato esplicitamente
  (`attore_id="sistema"` o l'ID di un utente di servizio). Mai dedotto in
  silenzio: senza attore la scrittura fallisce.
- Verifica che `tipo` sia nel manifest del modulo e scrive la `versione`
  dichiarata lì come corrente.
- Verifica che `entita_id` esista nell'anagrafica (lettura read-only), sia
  del tipo di anagrafica dichiarato per quell'evento, e lo sostituisce con
  il **canonico** se l'entità è stata fusa (ADR-002).

### 3. Perché UTC + offset, e non UTC + fuso orario

Turni (`core.shifts`), scadenze e report ragionano in **ora locale**. Per
ricavarla da un istante UTC servirebbe il database dei fusi orari, che su
Windows **non** è nella libreria standard (`zoneinfo` richiede il pacchetto
`tzdata`, una dipendenza in più). Salvare l'offset in vigore al momento
dell'evento permette di ricostruire l'ora locale "come la vedeva
l'orologio a muro" senza dipendenze e senza ambiguità al cambio dell'ora
legale. Helper: `busta.ora_locale(riga) -> datetime` (aware, con l'offset
salvato).

### 4. Versione dello schema dell'evento

- Un `tipo` nasce a `versione = 1`. Si incrementa quando cambia il
  **significato o la forma** delle colonne di dominio per quel tipo (es.
  `causale` diventa obbligatoria, un valore cambia unità di misura). Non si
  incrementa per colonne aggiunte e facoltative.
- Il manifest dichiara la versione corrente; il codice di lettura del modulo
  gestisce **tutte** le versioni presenti nel log ("upcasting" a tempo di
  lettura). Le righe vecchie non si riscrivono mai.
- `versione = 0` è riservata alle righe scritte prima della 1.0 (punto 6).

### 5. Log del kernel

`auth_eventi` (ADR-001), `anagrafica_eventi` (ADR-002) e l'eventuale log del
registro moduli usano la stessa busta, con `tipo` nel namespace `core.*`.
Per gli eventi di `auth`, che riguardano utenti e non entità di anagrafica,
`entita_id` è NULL e il soggetto sta in una colonna di dominio
(`utente_id`, `gruppo_id`, `ruolo_id`).

### 6. Log esistenti (0.x)

- Le colonne della busta si aggiungono ai log esistenti con un passo di
  migrazione (ADR-004), in modo additivo. Le colonne 0.x (`ts`, `entita`,
  `operatore`, `note`) restano; `note` resta una colonna di dominio
  facoltativa, le altre non si scrivono più.
- Le righe 0.x **non si riscrivono** (niente backfill con UPDATE sul log,
  regola 3): hanno `versione` NULL/0 e le colonne nuove vuote.
- La lettura le interpreta a tempo di lettura: `events` fornisce una vista
  di compatibilità che espone per ogni riga `ts_utc` (se presente) o `ts`
  locale, `attore_id` o `operatore`, `entita_id` oppure il risultato della
  risoluzione di `entita` (codice) in anagrafica. Una riga legacy il cui
  codice non si risolve resta visibile, marcata come non risolta.
- Chi vuole chiudere il periodo di transizione può appendere un evento di
  riconciliazione `core.evento_riconciliato` che collega `uid` legacy ed
  entità: è un'aggiunta, non una modifica.

### 7. Cosa non è la busta

- Non è un bus di messaggi: gli eventi restano nel DB del modulo che li
  possiede. Nessuna tabella centrale "tutti gli eventi".
- Non è event sourcing obbligatorio per tutto: un modulo può avere tabelle
  di registro aggiornabili (configurazioni, bozze). La busta vale per i log.

## Alternative scartate

- **Timestamp in ora locale (status quo).** Ambiguo al cambio dell'ora
  legale, non confrontabile tra PC con orologi/fusi diversi.
- **Solo UTC, ora locale calcolata con `zoneinfo`.** Richiede `tzdata` su
  Windows: dipendenza vietata dallo stack. L'offset salvato costa una colonna.
- **Timestamp come intero (epoch ms).** Compatto e ordinabile, ma illeggibile
  nel browser DB e negli export CSV, che sono lo strumento di chi mantiene.
- **`payload` JSON al posto delle colonne di dominio.** Uniforma la busta, ma
  sposta il dominio in un blob non interrogabile e non validato dal motore:
  un EAV mascherato. Le colonne tipizzate restano.
- **Log eventi centrale unico per la suite.** Violerebbe l'ownership (un DB,
  uno scrittore) e creerebbe un collo di bottiglia per tutti i moduli.
- **`attore` come username.** Gli username si rinominano (ADR-001); l'ID no.
  La UI risolve ID → nome corrente, e lo storico dei nomi è in `auth_eventi`.
- **Backfill delle righe legacy con UPDATE in migrazione.** Più comodo da
  leggere, ma viola append-only proprio sul dato più delicato (lo storico).
  Si interpreta a lettura.
- **Solo `id` locale, senza `uid`.** Basta dentro un DB, non tra DB: le
  notifiche 1.x e gli export devono riferire "quell'evento" in modo univoco.
  Costo: una colonna TEXT indicizzata.

## Conseguenze

- Ogni evento risponde a chi (ID), cosa (tipo + versione), su cosa (ID
  anagrafica), quando (UTC + ora locale ricostruibile), da dove (sorgente),
  in qualunque modulo. È la base delle notifiche 1.x: ci si abbona a un
  `tipo` dichiarato, filtrando per `entita_id`.
- `core.shifts`, `core.schedule` e i report dei moduli lavorano su
  `busta.ora_locale(riga)`, non su `ts` testuale. Per `schedule` (che oggi
  prende date `"YYYY-MM-DD"` dal chiamante) non cambia la firma, cambia come
  il modulo le calcola.
- L'ordine nel log resta per `id` (monotono), non per `ts_utc`: due eventi
  nello stesso millisecondo, o un orologio del PC corretto all'indietro, non
  rompono la proiezione. La vista `latest_state_per_entity` resta "id
  massimo per entità", raggruppata per `entita_id`.
- Un orologio del PC sbagliato produce `ts_utc` sbagliati: la busta non lo
  corregge. Lo segnala: la shell mostra un avviso se l'ora del PC dei moduli
  (header `Date` dell'health-check) differisce di oltre N minuti dalla sua.

### Rotture API

| 0.4.0 | 1.0 |
|---|---|
| `events.registra(con, entita, stato, *, table, sorgente, operatore, note, extra)` | `events.registra(con, tipo, entita_id, stato, *, table, sorgente, attore_id=<dal contesto>, note, extra)` |
| `events.ddl_log()` colonne `id, ts, entita, stato, sorgente, operatore, note` | busta + `stato` + `note`; le colonne 0.x restano solo su log migrati |
| `events.stato_corrente(con, entita)` / `storico(con, entita)` | argomento = `entita_id` |
| `ts` generato da SQLite in ora locale | `ts_utc` + `offset_min` generati in Python |
| `Inventario.movimenta(con, codice, quantita, causale, *, sorgente, operatore, note, extra)` | `movimenta(con, entita_id, quantita, causale, *, sorgente, attore_id, note, extra)`; `tipo` = `"<modulo>.movimento"` dichiarato nel manifest |
| `events.SORGENTI` | invariato |
| `operatore=` (ovunque) | deprecato: accettato per una minor con `DeprecationWarning`, finisce solo in `note` |

### Punti aperti per la revisione

- Nome del componente: `core.busta` separato o funzioni dentro
  `core.events`? Proposta: `core.busta` (kernel, piccolo), con
  `core.events` che resta il pattern "log di stati" costruito sopra.
- Soglia per l'avviso di orologio sfasato (proposta: 2 minuti).
- `tipo` in `core.events`: oggi un log di stati ha un solo "genere" di
  evento. Proposta: un tipo per log dichiarato dal modulo
  (`"presenze.cambio_stato"`), con lo stato come colonna di dominio; in
  alternativa un tipo per transizione (`"presenze.preleva"`), più espressivo
  per le notifiche ma più verboso nel manifest.
