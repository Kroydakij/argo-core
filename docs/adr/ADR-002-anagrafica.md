# ADR-002 — Anagrafica codici centralizzata

- **Stato**: Proposto
- **Data**: 2026-09-27
- **Riguarda**: nuovo `core.anagrafica`, `core.codes`, `core.inventory`, chiave entità di `core.events`
- **Rompe l'API**: sì (vedi *Rotture API*)

## Contesto

In 0.4.0 l'identità degli oggetti di dominio è sparsa:

- `core.events` usa `entita TEXT` libero: il codice umano **è** la chiave.
  Rinominare `PRESSA-01` in `PR-001` spezza lo storico in due.
- `core.inventory` ha una sua tabella `articoli` con `codice UNIQUE`, upsert e
  `UPDATE attivo` (disattivazione senza traccia di chi/quando).
- Ogni modulo che parla della stessa macchina, articolo o commessa ne tiene
  una copia propria; le copie divergono (descrizioni, stato, codici).
- `core.codes` garantisce **una regola di normalizzazione per famiglia**, ma
  il registro è in memoria, per processo: due moduli possono registrare la
  stessa famiglia con due funzioni diverse senza che nessuno se ne accorga.
- Con i sistemi esterni (gestionale, fornitori, vecchi fogli) lo stesso
  oggetto ha più codici; oggi si risolve con tabelle di transcodifica fatte
  a mano nei moduli.

Serve una risposta unica a "cos'è l'entità X" (criterio di ADR-000), senza
trasformare il kernel in un database generico "a attributi" (EAV) in cui
finisce tutto il dominio: il dominio resta nei moduli (regola 12).

## Decisione

### 1. Dove vive, chi scrive

- `comune/anagrafica.sqlite`, **proprietario il kernel**. Scritture solo dalla
  shell (API HTTP e pagine di amministrazione) e dalla CLI del kernel
  (`python -m core.anagrafica importa ...`).
- I moduli **leggono** con `db.readonly()` (risoluzione codici, liste,
  join) e **scrivono** tramite l'API HTTP della shell, inoltrando la sessione
  dell'utente: l'attore dell'evento è l'utente vero e il permesso del tipo
  (`core.anagrafica.modifica.<tipo>`, punto 3) lo controlla la shell. Il kernel fornisce il client (`urllib`, stdlib):

  ```python
  from core import anagrafica
  eid = anagrafica.client.crea(request, tipo="macchina", codice="PR-001",
                               descrizione="Pressa 1")
  ```

- Per i join tra log del modulo e anagrafica, `core.db` offre
  `attach_readonly(con, path, alias)` (ATTACH in `mode=ro`). Nota di
  implementazione: l'ATTACH con URI `mode=ro` richiede che `db.owned()`
  apra la connessione con `uri=True`; cambia l'interno, non la firma.

### 2. Nucleo tipizzato piccolo

Un'entità ha **solo** questi campi:

| Campo | Tipo | Note |
|---|---|---|
| `id` | TEXT | UUID v4, generato dal kernel, **immutabile**, mai mostrato all'utente |
| `tipo` | TEXT | tipo registrato (punto 3) |
| `codice` | TEXT | codice umano **corrente**, normalizzato secondo il tipo |
| `descrizione` | TEXT | breve, libera |
| `stato` | TEXT | `ATTIVO` \| `OBSOLETO` \| `FUSO` |
| `fusa_in` | TEXT | ID di destinazione se `FUSO`, altrimenti NULL |
| `attributi` | TEXT (JSON) | oggetto piatto `{chiave: scalare}` |
| alias | tabella | codici esterni `(sistema, codice)` |

- **Unicità del codice**: per tipo, sulla proiezione corrente (attivi e
  obsoleti). Un codice non si riusa finché un'entità, anche obsoleta, lo
  porta; per liberarlo si rinomina prima quella vecchia (evento esplicito).
- **Alias**: `(sistema, codice)` unico per tipo. `sistema` è una stringa
  scelta dall'installazione (`"gestionale"`, `"fornitore_x"`, `"legacy"`).
- **Attributi liberi**: il kernel valida solo la forma (oggetto JSON, chiavi
  identificatore, valori stringa/numero/booleano/null). **Non** li
  interpreta, **non** li indicizza, **non** ci costruisce logica. Servono a
  descrivere (es. `reparto`, `marca`) e a filtrare nelle UI. Tutto ciò che
  ha regole, storico proprio o relazioni (cicli, distinte, parametri di
  processo, giacenze) sta nel DB del modulo, **chiave = ID anagrafica**.
  Questa è la linea anti-EAV.

### 3. Tipi di entità

- I tipi sono dati d'installazione, quindi stanno in config di suite
  (`comune/argo.toml`), non nel codice del core né in un singolo modulo:

  ```toml
  [anagrafica.tipi.macchina]
  descrizione = "Macchine e impianti"
  normalizzazione = ["strip", "maiuscolo"]

  [anagrafica.tipi.articolo]
  descrizione = "Articoli di magazzino"
  normalizzazione = ["strip", "zfill:9"]
  ```

- I moduli **dichiarano nel manifest i tipi che usano** (ADR-003); all'avvio
  il kernel verifica che esistano in `argo.toml` (fail-fast). Un tipo non si
  cancella: si smette di usarlo.
- **Normalizzazione dichiarativa**: `core.codes` aggiunge un piccolo insieme
  di regole componibili da config (`strip`, `maiuscolo`, `minuscolo`,
  `zfill:N`, `senza_spazi`). Un tipo condiviso non può avere una funzione
  Python scritta da un modulo: due moduli darebbero risposte diverse.
  `codes.registra()` con funzioni resta per famiglie private di un modulo.
- **Un permesso di scrittura per tipo**: per ogni tipo in `argo.toml` il
  kernel genera `core.anagrafica.modifica.<tipo>` (es.
  `core.anagrafica.modifica.articolo`), assegnabile ai ruoli come qualunque
  altro permesso (ADR-001). Copre tutti gli eventi di scrittura su entità di
  quel tipo: creazione, rinomina, descrizione, obsolescenza/riattivazione,
  alias, fusione. Si fondono solo entità dello stesso tipo. Chi importa gli
  articoli non tocca così le macchine.
- La normalizzazione si applica in scrittura (creazione, rinomina, alias) e
  in lettura (`risolvi`): chi cerca `" 252 "` trova `000000252`.

### 4. Tutto è evento

Log append-only `anagrafica_eventi` con la busta di ADR-005. Tipi:

| Evento | Effetto sulla proiezione |
|---|---|
| `core.entita_creata` | nuova entità `ATTIVO` con tipo, codice, descrizione, attributi |
| `core.entita_rinominata` | nuovo codice; il vecchio resta risolvibile come storico |
| `core.entita_descritta` | nuova descrizione e/o attributi (oggetto intero, non patch) |
| `core.entita_obsoleta` / `core.entita_riattivata` | cambio stato |
| `core.alias_aggiunto` / `core.alias_rimosso` | codici esterni |
| `core.entita_fusa` | sorgente → `FUSO`, `fusa_in` = destinazione; alias e codici storici della sorgente risolvono sulla destinazione |

Entità, alias e codici storici sono **viste di proiezione** ricreate a ogni
avvio (regola 5 e 8). I vincoli di unicità non si possono esprimere su una
vista: li applica il single write-point, dentro una transazione
`BEGIN IMMEDIATE` (un solo scrittore: la shell).

### 5. Risoluzione e canonicalizzazione (API di lettura)

```python
anagrafica.risolvi(con_ro, tipo, codice) -> Risoluzione | None
    # cerca, nell'ordine: codice corrente, alias, codici storici (rinomine);
    # restituisce id canonico + come ha trovato (CORRENTE/ALIAS/STORICO)
anagrafica.canonico(con_ro, id) -> str      # segue la catena delle fusioni
anagrafica.entita(con_ro, id) -> dict | None
anagrafica.elenco(con_ro, tipo, *, stati=("ATTIVO",)) -> list[dict]
```

- **Le fusioni non riscrivono la storia.** I log dei moduli sono append-only
  e continuano a contenere il vecchio ID. Chi aggrega per entità deve
  canonicalizzare **a tempo di lettura** (regola 9): `canonico()` in Python,
  oppure la vista `anagrafica_canonico(id, id_canonico)` via
  `attach_readonly` in SQL.
- La busta eventi (ADR-005) scrive sempre l'ID già canonico al momento della
  scrittura: i nuovi eventi non puntano mai a un'entità fusa.

## Alternative scartate

- **EAV generale** (`entita`, `attributo`, `valore` per tutto). Esattamente ciò
  che è stato escluso: query illeggibili, validazione impossibile, il kernel
  diventa il posto dove finisce il dominio.
- **Schema di attributi tipizzato per tipo** (dichiarato in config, validato
  dal kernel). Più rigoroso, ma è un linguaggio di schema da mantenere e un
  invito a mettere dominio nell'anagrafica. Rivalutabile in 1.x se gli
  attributi liberi si rivelano disordinati.
- **ID interno INTEGER AUTOINCREMENT.** Più leggibile, ma stabile solo dentro
  un file: si rompe unendo due installazioni, reimportando, o confrontando
  export. L'UUID costa poco e non lo vede nessuno.
- **Codice umano come chiave (status quo).** È la causa del problema.
- **Anagrafica per modulo con sincronizzazione.** N copie e un protocollo di
  sync: la risposta non è più unica per costruzione.
- **Moduli che scrivono direttamente su `anagrafica.sqlite`** (via funzioni
  kernel nel loro processo). Più semplice da chiamare, ma perde la garanzia
  `mode=ro` della regola 1 e l'attore/permesso verificati in un punto solo.
- **Tabelle materializzate aggiornate** (invece di viste sul log). Più veloci,
  ma sono "stato aggiornabile" (contro la regola 8) e possono divergere dal
  log. A volumi di anagrafica (migliaia–decine di migliaia di righe) le viste
  bastano. Se no, cache ricostruibile, mai fonte di verità.
- **Permesso di scrittura unico (`core.anagrafica.modifica`).** Più semplice,
  ma chi deve poter creare articoli di magazzino potrebbe anche rinominare o
  fondere le macchine. Il permesso per tipo costa zero configurazione (lo
  genera il kernel) e separa responsabilità diverse.
- **Scissione (split) di entità.** Non supportata: si crea una nuova entità e
  si rende obsoleta la vecchia. Il caso è raro e le regole di
  riattribuzione dello storico sono di dominio.
- **Stato solo ATTIVO/OBSOLETO come da richiesta iniziale.** Una fusione non è
  un'obsolescenza: la sorgente deve risolvere sulla destinazione. Serve il
  terzo stato `FUSO` (estende la specifica iniziale; confermato in revisione).

## Conseguenze

- Ogni riferimento a un oggetto condiviso, nei DB dei moduli, diventa un ID
  anagrafica (colonna `entita_id`), non un codice.
- Creare un'entità da un modulo richiede la shell accesa (scrittura via API).
  Leggere no.
- Le UI mostrano sempre il codice corrente risolto dall'ID: una rinomina si
  vede subito ovunque, senza toccare i DB dei moduli.
- Import iniziale: `python -m core.anagrafica importa --tipo T file.csv`
  (colonne `codice;descrizione[;attributi...]`), idempotente sul codice
  normalizzato; ogni riga crea eventi con attore `sistema` o l'utente che
  lancia la CLI.
- Oggetti **privati di un modulo** (che nessun altro modulo nomina) possono
  restare nel DB del modulo. Criterio: se un secondo modulo potrebbe
  doverlo nominare, va in anagrafica.

### Rotture API

| 0.4.0 | 1.0 |
|---|---|
| `events.registra(con, entita="PRESSA-01", ...)` | `entita_id=<UUID>` (ADR-005) |
| `events.stato_corrente(con, "PRESSA-01")` / `storico(con, "PRESSA-01")` | per ID; per codice: prima `anagrafica.risolvi()` |
| vista `latest_state_per_entity` raggruppata per `entita` | raggruppata per `entita_id` (le righe legacy restano leggibili, vedi ADR-005) |
| `Inventario.crea_articolo()` / `disattiva_articolo()` | rimossi: gli articoli sono entità di tipo configurato (`[inventario] tipo = "articolo"`) |
| `Inventario.movimenta(con, codice, ...)` | `movimenta(con, entita_id, ...)`; la tabella `articoli` 0.x resta (additiva) ma non è più letta |
| vista `giacenze` con `codice, descrizione` dall'anagrafica locale | per `entita_id`; codice e descrizione si prendono dall'anagrafica (ATTACH) |
| `codes.registra()` per famiglie condivise | per i tipi di anagrafica: regola dichiarativa in `argo.toml` |

### Punti aperti per la revisione

- Riuso di codici dopo obsolescenza: la proposta lo vieta finché l'entità
  obsoleta porta il codice. Nella tua esperienza serve il riuso diretto?
