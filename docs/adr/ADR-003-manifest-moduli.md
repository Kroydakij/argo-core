# ADR-003 — Manifest dei moduli

- **Stato**: Accettato (2026-09-28) — implementato in `core/manifest.py` + portale
- **Data**: 2026-09-27
- **Riguarda**: nuovo `core.manifest`, registro moduli della shell (`core.sqlite`), scaffolder
- **Rompe l'API**: sì, per il portale (vedi *Rotture API*)

## Contesto

In 0.4.0 ciò che un modulo "è" non sta scritto da nessuna parte in forma
leggibile da una macchina:

- il registro del portale (`moduli`: nome, porta, descrizione, attivo) si
  compila **a mano** con `POST /api/moduli`;
- i ruoli usati da un modulo sono stringhe sparse nel codice;
- le entità e gli eventi che un modulo produce si scoprono leggendo il
  codice o il DB;
- nessun controllo di compatibilità tra la versione del modulo e quella di
  `core/` copiata accanto.

Con ADR-001 (permessi dichiarati, menu filtrato), ADR-002 (tipi di
anagrafica usati) e ADR-005 (tipi di evento versionati) servono al kernel
queste informazioni **prima** di eseguire il modulo, anche quando il modulo è
spento (menu, catalogo permessi per l'amministratore).

## Decisione

### 1. Un file statico nella cartella del modulo

`<modulo>/manifest.toml`, versionato col codice e incluso nei rilasci. È ciò
che il modulo **è** (uguale in ogni installazione); la config
`<modulo>.toml` resta ciò che l'installazione **sceglie** (porta, dominio).
Il manifest non contiene mai dati d'installazione.

```toml
[modulo]
nome = "andon"                 # = nome cartella, identificatore Python
versione = "1.2.0"             # SemVer del modulo
core = ">=1.0,<2.0"            # versioni di argo-core compatibili
titolo = "Andon"
descrizione = "Segnalazione e gestione fermate"

[[permessi]]
id = "andon.vedi"
descrizione = "Vedere il pannello fermate"
[[permessi]]
id = "andon.chiudi_fermata"
descrizione = "Chiudere una fermata aperta"

[[menu]]
titolo = "Fermate"
percorso = "/"
permesso = "andon.vedi"
[[menu]]
titolo = "Storico"
percorso = "/storico"
permesso = "andon.vedi"

[anagrafica]
tipi = ["macchina"]            # tipi usati (devono esistere in argo.toml)

[[eventi]]
tipo = "andon.fermata_aperta"
versione = 1
entita = "macchina"            # tipo di anagrafica dell'entità, o "" se nessuna
descrizione = "Apertura di una fermata"
[[eventi]]
tipo = "andon.fermata_chiusa"
versione = 2
entita = "macchina"
descrizione = "Chiusura di una fermata con causale"
```

Formato TOML perché è già lo standard della suite (`core.config`,
`tomllib` stdlib) e perché va letto e corretto da chi non è sviluppatore.

### 2. Regole di validazione (fail-fast)

`core.manifest.carica(path)` restituisce un oggetto immutabile o solleva
`ManifestError`. Controlli:

- `nome` identificatore, uguale al nome della cartella;
- `versione` SemVer; `core` è un intervallo nella sintassi minima
  `>=X.Y[,<X.Y]` (parser stdlib, niente `packaging`), e la versione di
  `core/` presente deve soddisfarlo;
- ogni `permessi.id`, `eventi.tipo` inizia con `"<nome>."`: niente collisioni
  tra moduli, niente moduli che dichiarano permessi o eventi `core.*`;
- ogni `menu.permesso` è tra i permessi dichiarati; `percorso` inizia con `/`;
- `eventi.versione` intero ≥ 1; `(tipo)` unico nel manifest;
- `eventi.entita` e `anagrafica.tipi`: la presenza dei tipi in `argo.toml`
  si verifica all'avvio del modulo e alla scansione della shell (non a
  `carica()`, che non conosce l'installazione).

### 3. Il manifest governa il runtime del modulo

- `auth.inizializza(app, manifest)` (ADR-001): `@richiede_permesso(p)` con
  `p` non dichiarato ⇒ errore all'avvio.
- La busta eventi (ADR-005) rifiuta di scrivere un `tipo` non dichiarato o
  con `versione` diversa da quella dichiarata come corrente.
- `anagrafica.risolvi()` su un tipo non dichiarato dal modulo ⇒ errore: la
  dipendenza da un tipo deve essere visibile nel manifest.

Così il manifest non può mentire: ciò che non è dichiarato non funziona.

### 4. Scoperta: la shell scansiona le cartelle sorelle

- All'avvio e su richiesta (pulsante "Rileggi moduli" per `core.admin`), la
  shell scansiona le cartelle sorelle di `core/` alla ricerca di
  `manifest.toml`. **Deploy di un modulo = copiare la cartella**, come oggi.
- Per ogni manifest valido legge la porta dalla config del modulo
  (`<modulo>/<modulo>.toml`, chiave `[app] porta`: diventa parte del
  contratto di conformità) e scrive nel registro `core.sqlite`:
  manifest completo (JSON), porta, esito della validazione, istante di scansione.
- Manifest non valido ⇒ il modulo compare nel registro come **in errore**
  con il messaggio; non entra nel menu. La shell non si ferma per un modulo
  rotto.
- Il registro è letto (read-only) dai moduli per costruire il menu della
  cornice. Il catalogo dei permessi per l'amministrazione ruoli è l'unione
  dei manifest validi + permessi `core.*`.
- Moduli legacy senza manifest: si registrano ancora a mano come
  **link esterni** (ADR-000, regola 4), dietro `core.link_esterni`.
- `attivo` (abilitato/disabilitato nel menu) resta una scelta
  dell'amministratore, registrata come evento nel registro.

## Alternative scartate

- **Manifest in Python** (`MANIFEST = {...}` in `app.py`). Per leggerlo
  bisogna importare il modulo, cioè eseguirne il codice e le sue importazioni
  (Flask, config): la shell non deve eseguire codice dei moduli per
  costruire un menu, e un modulo con un import rotto sparirebbe dal
  catalogo.
- **Dentro `<modulo>.toml`** (sezione `[manifest]`). Mescola ciò che il
  modulo è con ciò che l'installazione sceglie: un aggiornamento del modulo
  dovrebbe riscrivere un file che il cliente ha modificato.
- **Registrazione attiva** (il modulo all'avvio fa `POST` alla shell). Serve
  la shell accesa e credenziali di servizio; un modulo spento sparisce dal
  catalogo permessi e le assegnazioni diventano inspiegabili.
- **JSON o YAML.** JSON non ha commenti, YAML non è in stdlib.
- **Permessi senza namespace.** Collisioni certe tra moduli scritti da AI
  diverse (`"admin"`, `"modifica"`).
- **Dichiarare anche i DB posseduti, le dipendenze tra moduli, le route API.**
  Utili, ma fuori dallo scope richiesto. Il formato si estende in modo
  additivo con sezioni nuove, alzando il minimo `core = ">=1.x"` (vedi
  Conseguenze).

## Conseguenze

- Una fonte unica per menu, catalogo permessi, tipi di anagrafica usati e
  catalogo eventi: l'amministratore vede "cosa si può autorizzare" e "chi
  emette cosa" senza leggere codice. È anche la base per le notifiche 1.x
  (ci si abbona a un `tipo` di evento dichiarato).
- Chiavi sconosciute nel manifest ⇒ `ManifestError` (fail-fast): un
  manifest scritto per un kernel più nuovo non viene interpretato a metà da
  un kernel vecchio. Le sezioni nuove richiedono di alzare il minimo in
  `core = ">=1.x"`.
- Lo scaffolder genera `manifest.toml` con un permesso `<nome>.vedi` e una
  voce di menu; `CORE_CONTESTO_AI.md` documenta il formato e la checklist
  ("ogni permesso/evento usato è dichiarato").
- Il registro moduli in `core.sqlite` cambia schema (colonne additive:
  `manifest_json`, `stato_scansione`, `errore`, `scansionato_il`).

### Rotture API

| 0.4.0 | 1.0 |
|---|---|
| `POST /api/moduli` (upsert manuale nome/porta/descrizione) | solo per link esterni legacy; i moduli conformi arrivano dalla scansione |
| `GET /api/moduli` → `{moduli: [{id,nome,porta,descrizione,attivo,creato_il}], prossima_porta, host}` | `moduli[]` aggiunge `versione`, `titolo`, `menu`, `stato`; `prossima_porta` e `host` restano (lo scaffolder li usa) |
| `portal.upsert_modulo()`, `toggle_modulo()` | interni alla shell, basati su scansione ed eventi |
| moduli senza manifest | non conformi: solo link nel menu |

### Decisioni prese in revisione (2026-09-28)

- Nome del file: `manifest.toml` (non `argo.toml`, che si confonderebbe con
  `comune/argo.toml`).
- La tabella/DB dove finiscono gli eventi **non** si dichiara in 1.0: si
  aggiunge in 1.x insieme alle notifiche.

### Note di implementazione

- `core/manifest.py`: `carica()`, `da_dict()`, `compatibile()`,
  `verifica_tipi()`, `scansiona()`. `descrizione` in `[modulo]` è facoltativa.
- In più rispetto al testo sopra: l'`entita` di un evento, se non vuota,
  deve comparire in `[anagrafica] tipi` (la dipendenza da un tipo deve
  essere visibile in un punto solo).
- Il portale (futura shell) scansiona all'avvio e con
  `POST /api/moduli/rileggi`. Finché `comune/argo.toml` non esiste
  (ADR-001/002), la presenza dei tipi di anagrafica non viene verificata in
  scansione: `verifica_tipi()` è pronta per quando ci sarà.
- L'applicazione del manifest a runtime (permessi in `richiede_permesso`,
  tipo/versione in `busta.scrivi`, tipi in `anagrafica.risolvi`) arriva con
  i rispettivi ADR-001, 005 e 002.
