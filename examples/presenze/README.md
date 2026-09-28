# Demo — presenze attrezzatura

Modulo d'esempio della suite ARGO, **generato con lo scaffolder** e poi esteso
a prova vivente della suite (kernel 1.0 + utility). Dominio del tutto generico (attrezzi che passano
tra *disponibile*, *in uso*, *in manutenzione*): nessun dato o logica di
un'installazione reale.

Come è nato:

```
python -m core.scaffold presenze --dir examples
# poi esteso: config di dominio in presenze.toml, logica in app.py, template
```

## Cosa dimostra (mattoni di core usati)

| Mattone | Uso qui |
|---|---|
| `core.events` + `core.busta` | ogni movimento è un evento append-only con la busta standard (chi = ID utente della sessione, quando = UTC + ora locale, tipo `presenze.cambio_stato` dichiarato nel manifest); lo stato è la proiezione `latest_state_per_entity` |
| `core.auth` | login unico della suite: vedere richiede `presenze.vedi`, muovere un attrezzo `presenze.registra_movimento` (dichiarati in `manifest.toml`); pagina nella cornice comune |
| `core.migrazioni` | lo schema è una lista di passi numerati, con backup del DB prima di migrare |
| `core.statemachine` | le transizioni ammesse sono in `presenze.toml`; un movimento impossibile viene rifiutato, non registrato |
| `core.forms` | il form del movimento (select attrezzo/azione; chi lo fa lo dice la sessione): validazione e render dalla stessa definizione |
| `core.schedule` | lo stato manutenzioni è calcolato **a tempo di lettura** dall'ultimo evento di manutenzione, senza job |
| `core.board` | la board (disponibili / in uso / in manutenzione) è guidata dalla config |
| `core.shifts` | il turno corrente è risolto dai turni parametrici in config |
| `core.config` | tutto il dominio vive nel TOML, con avvio fail-fast |

## Avvio

Serve la shell della suite (login unico) sulla stessa cartella dati, con in
`comune/argo.toml` il tipo di anagrafica che il modulo dichiara nel manifest:

```toml
[auth]
durata_sessione_ore = 12

[anagrafica.tipi.attrezzo]
descrizione = "Attrezzi"
normalizzazione = ["strip", "maiuscolo"]
```

Poi:

```
pip install flask
python -m core.auth crea-admin <tuo_utente>   # solo la prima volta
python -m core.shell                          # -> http://localhost:4700
python app.py                                 # -> http://localhost:4710
```

Dalla shell (`/utenti`) crea un ruolo con `presenze.vedi` e
`presenze.registra_movimento` e assegnalo.

Al primo avvio ogni attrezzo dell'elenco viene seminato come `DISPONIBILE`
(seed idempotente, attore `sistema`). I dati stanno in `ARGO_COMUNE` (default `./dati/`), **fuori**
dalla cartella del modulo.

## Config (`presenze.toml`)

- `[attrezzi]` — elenco degli attrezzi e cadenza manutenzione in giorni.
- `[macchina]` — stato iniziale e transizioni ammesse.
- `[board]` — colonne della board e stati che vi confluiscono.
- `[[turni]]` — turni parametrici (orari d'esempio).

Cambiare attrezzi, stati o turni è una modifica al **TOML**, non al codice.
