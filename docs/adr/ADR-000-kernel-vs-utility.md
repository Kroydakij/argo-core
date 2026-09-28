# ADR-000 — Kernel vs utility

- **Stato**: Proposto
- **Data**: 2026-09-27
- **Riguarda**: struttura di `core/`, definizione di "modulo conforme", politica di stabilità 1.0

## Contesto

In 0.4.0 `core/` è una collezione piatta di 18 moduli con lo stesso status:
`db`, `migrate`, `config`, `events`, `auth` stanno accanto a `board`,
`shifts`, `forms`, `inventory`. `core/__init__.py` li importa tutti. Nulla
distingue ciò che un modulo *deve* usare da ciò che *può* usare.

L'esperienza su un'installazione reale ha mostrato che alcune cose, lasciate
a ogni modulo, divergono e fanno danni:

- **identità**: ogni modulo ha la sua tabella `utenti` (`core.auth.migra`
  nel DB del modulo) e il suo Basic Auth. L'operatore digita la password a
  ogni modulo; disattivare una persona vuol dire ricordarsi N tabelle; gli
  stessi ruoli si chiamano in modo diverso in moduli diversi;
- **codici**: ogni modulo tiene la sua anagrafica (`core.inventory` ha la
  tabella `articoli`, i log di `core.events` usano `entita` come testo
  libero). Lo stesso oggetto fisico ha codici diversi in moduli diversi e
  una rinomina va propagata a mano;
- **chi e quando**: `operatore` è testo libero, `ts` è ora locale senza
  offset. Due log non sono confrontabili e non si sa chi ha fatto cosa;
- **schema**: `migrate_db()` è idempotente ma non versionata e non fa
  backup; un aggiornamento andato male sul PC del cliente non ha rete.

Al tempo stesso il valore di argo-core sta nella leggerezza: stdlib +
Flask, deploy = copia di una cartella, un'AI qualunque costruisce moduli
leggendo `CORE_CONTESTO_AI.md`. Qualunque cosa entri nel "obbligatorio"
diventa un costo per ogni modulo, per sempre.

## Decisione

### Criterio

> **Nel kernel entra solo ciò che deve dare la stessa risposta a tutti i moduli.**

Test pratico, da applicare a ogni proposta futura: *se due moduli
implementassero questa cosa ciascuno a modo suo, l'installazione diventerebbe
incoerente?* Se sì → kernel. Se il peggio che succede è che due moduli
hanno due UI diverse → utility.

### Due livelli

**Kernel** — obbligatorio per i moduli conformi; API stabile 1.x.

| Componente | Domanda a cui dà la risposta unica | ADR |
|---|---|---|
| `config` | com'è configurata la suite e il modulo (fail-fast); config di suite in `comune/argo.toml` | — |
| `auth` + shell | chi è l'utente, cosa può fare | 001 |
| `anagrafica` | cos'è l'entità X, qual è il suo ID | 002 |
| `manifest` | cosa dichiara di essere e fare un modulo | 003 |
| `migrazioni` | a che versione è lo schema di un DB, come ci si arriva in sicurezza | 004 |
| `events` (busta) | chi, cosa, su quale entità, quando (in UTC), con quale schema | 005 |

Più le **fondamenta** da cui il kernel dipende e che quindi ne fanno parte:
`db` (connessioni owned/readonly), `migrate` (helper additivi), `codes`
(normalizzazione: l'anagrafica la usa per dare lo stesso codice a tutti).

Il processo `core.portal` diventa la **shell** della suite (ADR-001): login,
cornice, menu, proprietario dei DB del kernel. È l'unico processo kernel.

**Utility** — opt-in, pattern riusabili senza obbligo:
`statemachine`, `shifts`, `schedule`, `forms`, `board`, `inventory`,
`export`, `notify` (fino alle notifiche kernel 1.x), `adminbrowser`,
`scaffold` (strumento).

### Regole tra i livelli

1. **Le librerie del kernel non importano mai le utility.** Le utility possono
   importare il kernel. Un test automatico verifica gli import di `core/`
   (niente cicli, niente dipendenze verso l'alto). La shell è
   un'*applicazione* costruita sul kernel, non una libreria: come qualunque
   modulo può montare utility (oggi monta `adminbrowser`).
2. **Le utility che scrivono log usano la busta del kernel** (ADR-005).
   Opt-in è l'uso della utility, non il rispetto della busta: un log di
   `inventory` è un log della suite come quello di `events`.
3. **Un modulo è conforme** se: ha un manifest valido (003), usa le
   migrazioni del kernel (004), protegge le route con i permessi del kernel
   (001), riferisce le entità condivise per ID anagrafica (002), scrive i log
   con la busta (005). L'uso delle utility è libero.
4. **Moduli non conformi** (legacy 0.x) restano avviabili: la shell li mostra
   nel menu come semplici link, dietro un permesso generico `core.link_esterni`,
   senza integrazione di sessione. È la via di transizione, non uno stato
   permanente supportato.
5. **Dipendenze**: invariato. Kernel e utility restano stdlib + Flask/Werkzeug
   (lazy). Una dipendenza in più nel kernel richiede un ADR.

### Layout del codice

Resta **piatto** (`core/auth.py`, `core/board.py`, ...). Il livello è
dichiarato in `core/__init__.py`:

```python
KERNEL = ("config", "db", "migrate", "codes", "manifest", "migrazioni",
          "auth", "anagrafica", "busta", "events")
UTILITY = ("statemachine", "shifts", "schedule", "forms", "board",
           "inventory", "export", "notify", "adminbrowser", "scaffold")
```

`import core` carica solo il kernel. Le utility si importano per nome
(`from core import board`), come già fanno tutti i moduli esistenti.

### Stabilità e versioni

- Una sola versione per tutto `core/` (si distribuisce come una cartella).
- Dalla 1.0 SemVer vale per **tutta** l'API pubblica elencata in
  `CORE_CONTESTO_AI.md`, kernel e utility. Rotture solo in 2.0.
- Deprecazione: una funzione deprecata emette `DeprecationWarning` per almeno
  una minor prima di sparire nella major successiva.
- Ciò che inizia con `_` non è API (es. `migrate._ident`, oggi usato da
  `auth`, `events`, `inventory`, `adminbrowser`: in 1.0 diventa
  `migrate.ident` pubblico perché serve ai moduli che compongono SQL).

## Alternative scartate

- **Tutto nel core, tutto obbligatorio.** Semplice da spiegare, ma
  trasforma `board` o `shifts` in vincoli per moduli che non ne hanno
  bisogno e rende ogni utility un pezzo di API da congelare con la stessa
  severità del kernel. Contraddice "semplice batte elegante".
- **Kernel in un pacchetto separato (`argo_kernel/` accanto a `core/`).**
  Separazione netta, ma due cartelle da copiare, due versioni da allineare e
  tutti gli import esistenti da cambiare. Il beneficio (confine visibile) si
  ottiene con la dichiarazione in `__init__` + test sugli import.
- **Sottopacchetti `core.kernel.*` / `core.util.*`.** Rompe ogni `from core
  import X` esistente per un guadagno solo estetico. Scartato per 1.0.
- **Kernel "a servizi" (un processo HTTP per auth, uno per anagrafica,
  bus eventi).** Architettura da server, non da "cartella copiata su un PC
  senza admin". La shell è un processo solo e i moduli leggono i DB del
  kernel direttamente in sola lettura: se la shell è giù, i moduli
  continuano a funzionare per chi è già loggato (ADR-001).
- **Notifiche nel kernel già in 1.0.** Hanno bisogno della busta (005)
  stabile e usata da tutti prima; farle insieme vuol dire stabilizzare due
  cose in una volta. Rimandate a 1.x.
- **Approvazioni multi-step nel kernel.** Non passano il criterio: sono un
  flusso di dominio, due moduli possono legittimamente volerle diverse.
  Modulo separato.

## Conseguenze

- `CORE_CONTESTO_AI.md` si riorganizza in due parti: *Kernel (obbligatorio)*
  e *Utility (opzionali)*, con la checklist di conformità della regola 3.
- Nuovi componenti kernel: `core.manifest`, `core.migrazioni`,
  `core.anagrafica`, `core.busta`; `core.auth` e `core.portal` riscritti.
- Nuovi DB del kernel in `comune/`, tutti di proprietà della shell:
  `core.sqlite` (registro moduli, esiste già), `auth.sqlite`,
  `anagrafica.sqlite`.
- La shell diventa un processo **necessario** per il login. Non è più
  "il portale opzionale con le tile".
- `migrate._ident` diventa pubblico (`migrate.ident`), con alias deprecato.

### Rotture API

- `import core` non importa più le utility: `import core; core.board...`
  senza `from core import board` smette di funzionare. Impatto atteso basso
  (tutti gli esempi e lo scaffolder usano `from core import ...`).
- `core.portal`: vedi ADR-001 e ADR-003.
- Regola nuova e vincolante per le utility che scrivono log: `inventory`
  cambia (ADR-002, ADR-005).

### Punti aperti per la revisione

- `inventory` resta utility (il pattern "anagrafica + movimenti" è generico)
  oppure diventa un modulo di esempio fuori da `core/`? Qui si propone:
  resta utility, ma i suoi articoli diventano un tipo di anagrafica.
- `adminbrowser` legge tutti i DB: in 1.0 va dietro al permesso `core.admin`
  (ADR-001). Resta utility perché non deve dare "la stessa risposta".
