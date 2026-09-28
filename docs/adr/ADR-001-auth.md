# ADR-001 — Autenticazione e autorizzazione

- **Stato**: Accettato (2026-09-28) — parte 1 implementata in `core/auth.py`
  (identità, sessioni, permessi); parte 2 (shell: login, cornice, menu) in corso
- **Data**: 2026-09-27
- **Riguarda**: `core.auth` (riscritto), `core.portal` → shell, permessi nei manifest (ADR-003)
- **Rompe l'API**: sì (vedi *Rotture API*)

## Contesto

`core.auth` 0.4.0 (148 righe) fornisce una tabella `utenti` **nel DB di
ciascun modulo** (`username`, `password_hash`, `ruolo` singolo, `attivo`) e
il decoratore `richiede(*ruoli, verifica=...)` che fa **Basic Auth** e un
gate sul ruolo. Il portale ha un proprio admin separato, preso da
`ARGO_PORTAL_USER`/`ARGO_PORTAL_PASS` (default `admin/admin`).

Problemi emersi in produzione:

1. **Una password per modulo.** Ogni modulo è un processo su una porta
   diversa, quindi un'origine diversa: il browser non riusa le credenziali
   Basic Auth e le richiede a ogni modulo. Basic Auth inoltre non ha logout.
2. **N anagrafiche utenti.** Creare, disattivare o cambiare ruolo a una
   persona significa toccare N database. Nessuno sa con certezza chi può
   fare cosa nell'installazione.
3. **Ruoli come stringhe libere scelte dal modulo.** `"admin"` di un modulo
   non è `"admin"` di un altro; non esiste un elenco di ciò che è
   autorizzabile.
4. **Nessun audit.** `crea_utente` è un upsert, `disattiva` e
   `imposta_password` sono `UPDATE`: non resta traccia di chi ha dato quale
   permesso a chi e quando.
5. **Collisione dei cookie tra moduli** (bug latente, già presente): i
   cookie HTTP sono per host, **non per porta** (RFC 6265 §8.5). Due moduli
   sullo stesso host che usano la sessione Flask (es. `flash()` in
   `examples/presenze`) scrivono entrambi il cookie `session`, firmato con
   chiavi diverse, e si invalidano a vicenda.

Il punto 5 è anche l'opportunità: se i cookie ignorano la porta, **un cookie
emesso dalla shell su `host:4700` arriva a tutti i moduli su `host:47xx`**.
La sessione condivisa non richiede reverse proxy, HTTPS o un dominio comune.

Vincoli: niente admin sul PC, niente servizi esterni, HTTP in chiaro in LAN
(protezione "da colleghi", non da attaccanti: resta così), stdlib + Flask.

## Decisione

### 1. Identità centrale, di proprietà della shell

- Utenti, gruppi, ruoli, credenziali e sessioni vivono in
  `comune/auth.sqlite`. **Proprietario unico: il kernel**, che ci scrive
  solo dalla shell (processo `python -m core.shell`, ex `core.portal`) e
  dalla CLI del kernel (`python -m core.auth ...`, per il bootstrap).
- I moduli aprono `auth.sqlite` **solo con `db.readonly()`**. La regola di
  ownership (regola 1) resta vera e imposta dal motore.
- Ogni utente ha un **ID interno stabile** (UUID v4 testuale) distinto dallo
  `username`, che è rinominabile. Tutto il resto della suite (busta eventi,
  ADR-005) riferisce l'utente per ID.

### 2. Login unico, sessione condivisa via cookie

- Il login avviene **solo nella shell** (`/login`, form HTML, POST).
  Un modulo che riceve una richiesta senza sessione valida fa redirect a
  `http://<stesso host>:<porta shell>/login?next=<url del modulo>`.
  La shell accetta come `next` solo URL sullo stesso host e su una porta
  del registro moduli (niente open redirect).
- A login riuscito la shell genera un token casuale (`secrets.token_urlsafe(32)`),
  salva in `auth.sqlite` **l'hash SHA-256 del token** (mai il token) con
  utente, apertura e scadenza, e imposta il cookie:

  ```
  argo_sessione=<token>; Path=/; HttpOnly; SameSite=Lax
  ```

  Senza `Domain` (cookie host-only) e senza `Secure` (HTTP in LAN).
- Ogni modulo, a ogni richiesta, legge il cookie, calcola l'hash e lo cerca
  in `auth.sqlite` (read-only). Sessione valida ⇒ carica utente e permessi
  effettivi in `flask.g.utente`. Costo: una query indicizzata su SQLite
  locale per richiesta.
- **Scadenza calcolata a tempo di lettura** (regola 9): valida se
  `ora < apertura + durata_max` e non esiste un evento di chiusura. Durata
  in `comune/argo.toml` (`[auth] durata_sessione_ore`, obbligatoria). Niente
  scadenza "scorrevole": richiederebbe ai moduli di scrivere su `auth.sqlite`.
- **Logout** = la shell appende la chiusura della sessione. Effetto immediato
  su tutti i moduli (leggono lo stesso DB). Idem per **utente disattivato**
  o permesso revocato: la richiesta successiva, in qualunque modulo, lo vede.
- Nome del cookie riservato: `argo_sessione`. I moduli che usano la sessione
  Flask **devono** impostare `SESSION_COOKIE_NAME = "argo_<nome_modulo>"`;
  lo scaffolder e l'helper `auth.inizializza(app)` lo fanno da soli (chiude
  il bug del punto 5 del contesto).
- **Stesso host per tutti**: la sessione vale se l'utente raggiunge shell e
  moduli con lo stesso nome host (tutto per IP o tutto per nome PC). La
  shell costruisce i link del menu da `request.host` (come già fa
  `/api/moduli`), quindi il caso normale funziona da solo.
- **CSRF**: `SameSite=Lax` blocca i POST cross-site da siti esterni; in
  aggiunta `richiede_permesso` rifiuta le richieste non-GET il cui header
  `Origin` (se presente) non è sullo stesso host. Nessun token CSRF nei form
  in 1.0.
- **Client non-browser** (script, sensori, import): Basic Auth resta
  accettata **solo per utenti di tipo `servizio`**, verificata dal modulo in
  sola lettura contro lo stesso backend. Gli utenti umani passano solo dalla
  sessione.

### 3. Permessi dichiarati dai moduli

- Un **permesso** è una stringa `"<modulo>.<azione>"` in snake_case
  (es. `andon.chiudi_fermata`), dichiarata nel manifest del modulo con una
  descrizione leggibile (ADR-003). Il prefisso deve essere il nome del
  modulo: il kernel lo verifica.
- Il **registro dei permessi** è l'unione dei manifest scansionati dalla
  shell più i permessi del kernel (`core.admin`, `core.utenti`,
  `core.link_esterni` e un `core.anagrafica.modifica.<tipo>` per ogni tipo
  di anagrafica, ADR-002).
- Decoratore: `@auth.richiede_permesso("andon.chiudi_fermata")`. Un permesso
  non dichiarato nel manifest del modulo ⇒ **errore all'avvio** (fail-fast,
  regola 7), non un 403 a runtime.
- Nel codice: `auth.ha_permesso(g.utente, "andon.chiudi_fermata")` per
  mostrare/nascondere pulsanti.

### 4. Utenti, gruppi, ruoli

Modello RBAC piatto, deliberatamente piccolo:

- **Ruolo** = insieme nominato di permessi (es. "Capoturno" =
  `{andon.chiudi_fermata, andon.vedi_storico, ...}`). Lo definisce
  l'amministratore dalla shell, non il modulo: i moduli dichiarano cosa è
  autorizzabile, l'installazione decide chi.
- **Gruppo** = insieme nominato di utenti (es. "Turno A", "Manutenzione").
- **Assegnazione** = ruolo → utente, oppure ruolo → gruppo.
- **Permessi effettivi** = unione dei ruoli assegnati all'utente e ai suoi
  gruppi. Nessuna negazione, nessun gruppo annidato, nessuna gerarchia di
  ruoli, nessun permesso "per entità" (es. solo sulla linea 3) in 1.0.
- `core.admin` non è un jolly: dà la gestione di utenti/ruoli e l'accesso
  agli strumenti di amministrazione, non i permessi dei moduli.

### 5. Assegnazioni come eventi (audit trail)

- `auth.sqlite` contiene un log append-only `auth_eventi` con la busta di
  ADR-005 (attore = l'amministratore che agisce). Tipi:
  `core.utente_creato`, `core.utente_rinominato`, `core.utente_disattivato`,
  `core.utente_riattivato`, `core.password_impostata`, `core.gruppo_creato`,
  `core.membro_aggiunto`, `core.membro_rimosso`, `core.ruolo_definito`
  (con l'insieme completo dei permessi), `core.ruolo_assegnato`,
  `core.ruolo_revocato`, `core.sessione_aperta`, `core.sessione_chiusa`.
- Utenti, gruppi, appartenenze, assegnazioni e permessi effettivi sono
  **viste di proiezione** su questo log (regola 8), ricreate a ogni avvio.
- **Eccezione motivata — le credenziali**: gli hash delle password stanno in
  una tabella `credenziali(utente_id, password_hash)` aggiornabile, **non**
  nel log. Il log registra il *fatto* (`core.password_impostata`, chi e
  quando), non il segreto: tenere per sempre gli hash vecchi in un log
  append-only renderebbe attaccabili le password riusate.
- Le sessioni: `core.sessione_aperta` porta l'hash del token; la validità è
  calcolata come detto al punto 2.

### 6. Backend pluggable

```python
class Backend(Protocol):
    nome: str                                  # "locale", "ldap", ...
    def autentica(self, username: str, password: str) -> IdentitaEsterna | None: ...
```

- Il backend risponde **solo** a "queste credenziali sono valide, e per chi?".
  Gruppi, ruoli e permessi restano sempre locali al kernel.
- Ogni utente ha `(backend, id_esterno)`; al primo login riuscito con un
  backend esterno la shell crea l'utente locale (evento `core.utente_creato`,
  attore = `sistema`) senza ruoli.
- 1.0 implementa solo `locale` (hash Werkzeug in `credenziali`). Il backend
  si sceglie in `comune/argo.toml` (`[auth] backend = "locale"`).
- Il seam per LDAP/AD è questa interfaccia + la colonna `backend`. La
  mappatura gruppi AD → gruppi locali è un'estensione futura dell'interfaccia
  (`gruppi_esterni()`), non progettata ora.

### 7. Shell comune

- `core.portal` diventa `core.shell` (porta 4700, `python -m core.shell`;
  `python -m core.portal` resta come alias deprecato per una minor).
- La shell fa: login/logout, pagina "cambia password", amministrazione
  utenti/gruppi/ruoli (dietro `core.utenti`), registro moduli dai manifest
  (ADR-003), health-check, browser DB (dietro `core.admin`).
- **Cornice**: `core/templates/argo_cornice.html` è un layout Jinja che ogni
  modulo estende (`{% extends "argo_cornice.html" %}`). Contiene barra in
  alto con nome suite, utente, logout, e il **menu dei moduli filtrato**
  per i permessi dell'utente corrente. `auth.inizializza(app, manifest)`
  registra il template e il context processor che fornisce il menu.
- Il menu si costruisce leggendo (read-only) il registro in `core.sqlite`:
  una voce è visibile se l'utente ha il permesso dichiarato per quella voce
  nel manifest del modulo che la espone.
- Niente iframe: ogni modulo serve pagine complete con la stessa cornice;
  link profondi, "indietro" e stampa funzionano come oggi.

### 8. Bootstrap del primo amministratore

Nessuna credenziale di default. Al primo avvio con `auth.sqlite` vuoto la
shell rifiuta di partire e indica il comando:

```
python -m core.auth crea-admin <username>      # chiede la password a terminale
```

che crea l'utente, il ruolo "Amministratore" con i permessi `core.*` e
l'assegnazione (attore = `sistema`).

## Alternative scartate

- **Tenere Basic Auth, con utenti centrali.** Risolve le N anagrafiche ma non
  la password ripetuta: il browser la chiede per ogni origine (porta). Niente
  logout.
- **Token firmato stateless** (cookie firmato con `itsdangerous`, segreto
  condiviso in `comune/`). Nessuna query per richiesta, ma disattivazione e
  revoca non hanno effetto fino alla scadenza del token e i permessi
  "cotti" nel token invecchiano. In un reparto, "ho tolto il permesso ma
  per 8 ore vale ancora" non è accettabile. La query su SQLite locale costa
  meno di un millisecondo.
- **Reverse proxy davanti a tutto (un'origine sola)** o **SSO integrato
  Windows (Kerberos/NTLM)**. Richiedono IIS/servizi/admin o dipendenze
  native. Fuori dai vincoli di deploy.
- **Scrittura di `auth.sqlite` da parte dei moduli** (es. per sessioni
  scorrevoli o login locale). Rompe la regola di ownership e la protezione
  `mode=ro`; il guadagno (scadenza scorrevole) non vale.
- **Ruoli dichiarati dai moduli.** Il modulo sa cosa è autorizzabile, non
  chi, in quell'installazione, deve poterlo fare. Ruoli di modulo porterebbero
  di nuovo a N nomi per la stessa figura.
- **ABAC / permessi per entità / negazioni / gruppi annidati.** Potenti,
  difficili da capire per chi amministra senza essere sviluppatore; ogni
  domanda "perché Tizio vede questo?" diventa un'indagine. Se servirà
  l'ambito per entità, si aggiunge in 1.x come colonna dell'assegnazione.
- **Password nel log eventi.** Scartato per il motivo al punto 5.
- **Menu via iframe nella shell.** Rompe link profondi, dimensionamento,
  stampa e "indietro"; complica la cornice dei moduli invece di semplificarla.

## Conseguenze

- La shell diventa necessaria per **entrare**; se è spenta, chi ha già una
  sessione continua a lavorare nei moduli (validazione in sola lettura), ma
  nessuno può fare login né logout. Documentarlo nel README di installazione.
- Ogni richiesta autenticata di un modulo apre una connessione read-only a
  `auth.sqlite`. Con WAL non blocca la shell. Da misurare in implementazione;
  se serve, cache per richiesta (mai tra richieste: si perderebbe la revoca
  immediata).
- Gli utenti devono usare un nome host coerente per shell e moduli.
- Ogni azione protetta ha un attore certo (ID utente) da mettere nella busta
  eventi (ADR-005): `operatore` come testo libero diventa inutile.
- Le route di un modulo non sono più protette "se il modulo se ne ricorda":
  lo scaffolder genera `auth.inizializza(app, manifest)` che, per default,
  **richiede una sessione valida su tutte le route**; le route pubbliche
  (es. `/api/health`) si marcano esplicitamente con `@auth.pubblica`.
- Migrazione degli utenti esistenti: `python -m core.auth importa
  --db <modulo.sqlite> [--tabella utenti]` legge le tabelle 0.x (gli hash
  Werkzeug sono compatibili, nessun reset password), crea gli utenti e
  riporta i vecchi `ruolo` come elenco da mappare a mano sui nuovi ruoli.

### Rotture API

| 0.4.0 | 1.0 |
|---|---|
| `auth.migra(con, table=)` | rimosso: lo schema di `auth.sqlite` lo gestisce il kernel |
| `auth.crea_utente / imposta_password / disattiva` | solo nella shell/CLI, come eventi; non più chiamabili dai moduli |
| `auth.verifica(con, u, p)` | `backend.autentica(u, p)` interno al kernel |
| `auth.lista_utenti(con)` | `auth.utenti(con_ro)` sulla proiezione |
| `auth.ha_ruolo(utente, *ruoli)` | `auth.ha_permesso(utente, permesso)` |
| `auth.richiede(*ruoli, verifica=, realm=)` | `auth.richiede_permesso(permesso)`; nessun `verifica` da passare |
| `g.utente = {id, username, ruolo, attivo, creato_il}` | `g.utente = {id (UUID), username, nome, permessi: frozenset, tipo: "persona"\|"servizio"}` |
| `portal.richiede_admin`, `ARGO_PORTAL_USER/PASS` | rimossi; bootstrap con `core.auth crea-admin` |
| `python -m core.portal` | `python -m core.shell` (alias deprecato) |
| tabella `utenti` nel DB del modulo | non più usata (resta nel file, regola additiva; importabile) |

### Decisioni prese in revisione (2026-09-28)

- Nome del processo: `core.shell`.
- Durata della sessione: **unica per suite** in 1.0
  (`[auth] durata_sessione_ore` in `comune/argo.toml`).
- Postazioni condivise: in 1.0 basta il logout esplicito. Il "cambio utente
  rapido" (badge/PIN) è fuori scope; il backend pluggable è il punto
  d'aggancio per aggiungerlo.

### Note di implementazione (parte 1)

- **Hash delle password in stdlib** (`hashlib.scrypt`/`pbkdf2_hmac`) nello
  stesso formato di Werkzeug (`scrypt:N:r:p$sale$hex`): gli hash 0.x si
  importano senza reset (verificato nei test contro Werkzeug) e `core.auth`
  non dipende più da Werkzeug fuori dall'integrazione Flask.
- **Scadenza della sessione fissata all'apertura** (`scade_il_utc`
  nell'evento `core.sessione_aperta`) e confrontata a tempo di lettura. Così
  i moduli validano la sessione senza leggere `argo.toml`; la durata la
  sceglie la shell al login (parte 2, da `[auth] durata_sessione_ore`).
- Proiezioni come viste su `auth_eventi`: `auth_utenti`, `auth_gruppi`,
  `auth_membri`, `auth_ruoli`, `auth_assegnazioni`, `auth_permessi_utente`,
  `auth_sessioni`. Unicità di username/gruppo/ruolo garantita dal single
  write-point (un solo scrittore: il kernel).
- Ogni scrittura richiede un `attore` esistente (o `sistema`): niente
  modifiche anonime nel log.
- `migrate._ident` diventa pubblico come `migrate.ident` (alias mantenuto).
- Lo scaffolder resta invariato in questa parte: genera moduli protetti da
  `auth.inizializza()` solo con la parte 2, quando la shell fa il login.
