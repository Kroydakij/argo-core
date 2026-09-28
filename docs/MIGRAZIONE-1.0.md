# Migrazione da argo-core 0.4 a 1.0

La 1.0 introduce il **kernel** della suite (ADR-000…005 in `docs/adr/`):
login unico, anagrafica centralizzata, manifest dei moduli, migrazioni con
backup, busta standard degli eventi. Questa guida porta un'installazione e i
suoi moduli dalla 0.4.0 alla 1.0.0. I **dati non si perdono e non si
riscrivono**: i log 0.x restano leggibili, le tabelle 0.x restano dove sono.

Il modulo d'esempio [`examples/presenze`](../examples/presenze/) è il
riferimento di un modulo 1.0 conforme.

## 1. Installazione (una volta)

1. **Backup**: copia la cartella dati `comune\` (con i moduli spenti).
2. **Sostituisci la cartella `core\`** con quella della 1.0.
3. **Crea `comune\argo.toml`** (obbligatorio, la shell non parte senza):

   ```toml
   [suite]
   titolo = "ARGO"            # facoltativo
   porta = 4700               # facoltativo

   [auth]
   durata_sessione_ore = 12   # obbligatorio

   # un blocco per ogni tipo di entita' condivisa (ADR-002)
   [anagrafica.tipi.articolo]
   descrizione = "Articoli di magazzino"
   normalizzazione = ["strip", "zfill:9"]   # strip, maiuscolo, minuscolo,
                                            # senza_spazi, zfill:N
   ```

   `comune\portal.json` non è più letto: riporta qui titolo e porta.
4. **Primo amministratore**: `python -m core.auth crea-admin <username>`.
5. **Utenti 0.x** (le tabelle `utenti` dei moduli): per ogni DB di modulo
   `python -m core.auth importa --db comune\<modulo>.sqlite`. Le password
   restano valide; i vecchi ruoli vengono elencati e vanno ricreati come
   ruoli della suite (insiemi di permessi) dalla pagina `/utenti` della shell.
6. **Avvia la shell**: `python -m core.shell` (porta 4700). È lei il login di
   tutti i moduli, e l'unico scrittore di `core.sqlite`, `auth.sqlite`,
   `anagrafica.sqlite`. `python -m core.portal` funziona ancora ma è
   deprecato.
7. **Anagrafica**: per ogni tipo, un CSV `codice;descrizione;...` (le
   colonne in più diventano attributi) e
   `python -m core.anagrafica importa --tipo <tipo> <file.csv>`
   (idempotente). Poi assegna ai ruoli i permessi
   `core.anagrafica.modifica.<tipo>` a chi deve poter modificare.

## 2. Ogni modulo

Lo scheletro di un modulo 1.0 è quello generato da
`python -m core.scaffold <nome>`: confrontalo con il modulo esistente.

| Cosa | 0.4 | 1.0 |
|---|---|---|
| Manifest | — | `manifest.toml` accanto ad `app.py`: nome, versione, `core = ">=1.0,<2.0"`, permessi, menu, tipi di anagrafica, eventi (ADR-003) |
| Login | tabella `utenti` del modulo, `auth.richiede(*ruoli)`, Basic Auth | `auth.inizializza(app, manifest=M, auth_db=COMUNE/"auth.sqlite")` dopo le route; `@auth.richiede_permesso("<modulo>.<azione>")`; `@auth.pubblica` per `/api/health`; `g.utente = {id, username, nome, tipo, permessi}` (ADR-001) |
| Pagine | template propri | `{% extends "argo_cornice.html" %}`: barra, menu per permessi, utente |
| Schema | `migrate_db()` idempotente all'avvio | `PASSI = [migrazioni.Passo(1, "...", fn), ...]` + `migrazioni.applica(DB_PATH, PASSI, viste=VISTE)`: backup prima di ogni migrazione (ADR-004) |
| Eventi | `events.registra(con, entita, stato, operatore=...)` | `events.registra(con, "<modulo>.<evento>", entita_id, stato, manifest=M)`: tipo dichiarato nel manifest, attore dalla sessione (ADR-005) |
| Entità condivise | codice libero come chiave | ID di anagrafica: `anagrafica.risolvi(ana, tipo, codice).id` con `ana = anagrafica.apri(COMUNE)`; crearle da un modulo: `anagrafica.client.crea(...)` (ADR-002) |
| Inventario | `Inventario(causali)`, `crea_articolo`, `movimenta(con, codice, ...)` | `Inventario(causali, manifest=M, anagrafica=anagrafica.percorso_db(COMUNE))`, articoli in anagrafica, `movimenta(con, entita_id, ...)`, `imposta_soglia()` |
| Import | `import core; core.board` | `from core import board` (le utility non si caricano da sole) |
| Identificatori SQL | `migrate._ident` | `migrate.ident` (`_ident` deprecato) |

### Log esistenti

- **`core.events`**: `events.migra(con)` in un passo aggiunge la busta al log
  0.x (colonne nuove, nessuna riga toccata). Le righe vecchie restano
  leggibili con la loro chiave (il codice); `busta.e_legacy(riga)` le
  distingue. Se il codice è quello di un'entità in anagrafica, risolvilo
  in lettura con `anagrafica.risolvi()` (l'esempio `presenze` lo fa in
  `_stati()`).
- **`core.inventory`**: le tabelle 0.x `articoli` e `movimenti` restano
  intatte. In un passo di migrazione del modulo:

  ```python
  inv.esporta_articoli_0x(con, "articoli.csv")   # poi, dalla CLI del kernel:
  # python -m core.anagrafica importa --tipo articolo articoli.csv
  esito = inv.adotta_0x(con)     # copia i movimenti nel log nuovo (idempotente)
  ```

  `esito` elenca i codici non trovati in anagrafica e gli articoli 0.x
  disattivati (da rendere obsoleti dalla shell).

## 3. Verifica

- La home della shell mostra ogni modulo con `stato = ok`; un manifest
  sbagliato compare come errore con il messaggio.
- `python -m core.migrazioni stato comune\<modulo>.sqlite` mostra versione
  di schema, storia e backup.
- Un modulo che usa un permesso o un tipo di anagrafica non dichiarato non
  parte: il messaggio dice cosa aggiungere e dove.
- Moduli non ancora convertiti: registrabili a mano nella shell come link
  esterni (permesso `core.link_esterni`), senza login integrato. È una via
  di transizione.
