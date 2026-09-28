# Architecture Decision Records — argo-core 1.0

Decisioni architetturali per la 1.0 (stabilizzazione dell'API pubblica).
Formato: **Contesto / Decisione / Alternative scartate / Conseguenze**.
Ogni ADR ha uno stato: `Proposto` → `Accettato` (o `Respinto`/`Superato da ADR-NNN`).
Un ADR accettato non si riscrive: si supera con uno nuovo.

| ADR | Titolo | Stato |
|---|---|---|
| [000](ADR-000-kernel-vs-utility.md) | Kernel vs utility | Accettato |
| [001](ADR-001-auth.md) | Autenticazione e autorizzazione | Accettato — implementato |
| [002](ADR-002-anagrafica.md) | Anagrafica codici centralizzata | Accettato — parte 1 (kernel) implementata |
| [003](ADR-003-manifest-moduli.md) | Manifest dei moduli | Accettato — implementato |
| [004](ADR-004-migrazioni.md) | Migrazioni schema con backup | Accettato — implementato |
| [005](ADR-005-busta-eventi.md) | Busta standard degli eventi | Accettato — implementato |

Baseline di partenza: tag `v0.4.0-baseline` (= `v0.4.0`, commit `57daab5`).

## Scope 1.0

Dentro: i sei ADR qui sopra. Fuori: notifiche (kernel, ma 1.x, sopra la
busta eventi di ADR-005), approvazioni multi-step (modulo separato, mai core).

## Ordine di dipendenza

```
ADR-000 (criterio)
   ├── ADR-004 migrazioni ──┐         (serve a tutti i DB, kernel e moduli)
   ├── ADR-003 manifest ────┤         (dichiara permessi, tipi, eventi)
   │                        ├── ADR-001 auth         (attore)
   │                        ├── ADR-002 anagrafica   (entità)
   │                        └── ADR-005 busta eventi (usa attore + entità)
```

Ordine di implementazione suggerito: 004 → 003 → 001 → 002 → 005.

## Riepilogo delle rotture dell'API pubblica 0.4.0

Dettaglio e motivazioni in ciascun ADR, sezione *Conseguenze → Rotture API*.

| Superficie 0.4.0 | Cosa cambia in 1.0 | ADR |
|---|---|---|
| `core.auth.*` (tutte le funzioni) | tabella utenti per-modulo e `richiede(*ruoli, verifica=...)` sostituiti da identità centrale + sessione + `richiede_permesso("modulo.azione")`. `g.utente` cambia forma. `ha_ruolo` → `ha_permesso` | 001 |
| `core.portal` | diventa la **shell** (login, cornice, menu). Spariscono `ARGO_PORTAL_USER`/`ARGO_PORTAL_PASS` e `richiede_admin`; `POST /api/moduli` manuale sostituito dalla scansione dei manifest; `portal.json` → `comune/argo.toml` | 000, 001, 003 |
| `core.events.registra()` | nuova firma: `tipo`, `entita_id`, `attore_id` obbligatori; `operatore` deprecato; timestamp UTC generato in Python | 005 |
| `core.events.stato_corrente()` / `storico()` | chiave = ID anagrafica, non più codice libero | 002, 005 |
| schema del log eventi | colonne nuove (additive); `ts`, `entita`, `operatore` restano solo per le righe legacy | 005 |
| `core.inventory` | anagrafica articoli propria → tipo di anagrafica centrale; movimenti con busta; `crea_articolo`/`disattiva_articolo` rimossi | 002, 005 |
| convenzione `migrate_db()` | da "funzione idempotente all'avvio" a lista di passi numerati eseguiti dal runner con backup; gli helper `ensure_*` restano | 004 |
| `import core` | carica solo il kernel; le utility si importano per nome (`from core import board` continua a funzionare, `core.board` senza import no) | 000 |
| scaffolder | genera manifest, passi di migrazione, route protette da permesso | 003, 004 |
| `CORE_CONTESTO_AI.md` | regole 1, 3, 4, 8, 11 riformulate; nuove regole su manifest, busta, attore | tutti |

Non cambiano: `core.db`, `core.config` (si aggiunge la config di suite),
`core.codes.norm/registra` (si aggiungono le regole dichiarative),
`core.statemachine`, `core.shifts`, `core.schedule`, `core.forms`,
`core.board`, `core.export`, `core.notify`, `core.adminbrowser`.
