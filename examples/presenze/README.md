# Demo — presenze attrezzatura

Modulo d'esempio della suite ARGO, **generato con lo scaffolder** e poi esteso
a prova vivente della Fase 2. Dominio del tutto generico (attrezzi che passano
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
| `core.events` | ogni movimento è un evento append-only; lo stato è la proiezione `latest_state_per_entity` |
| `core.statemachine` | le transizioni ammesse sono in `presenze.toml`; un movimento impossibile viene rifiutato, non registrato |
| `core.forms` | il form del movimento (select attrezzo/azione + operatore): validazione e render dalla stessa definizione |
| `core.schedule` | lo stato manutenzioni è calcolato **a tempo di lettura** dall'ultimo evento di manutenzione, senza job |
| `core.board` | la board (disponibili / in uso / in manutenzione) è guidata dalla config |
| `core.shifts` | il turno corrente è risolto dai turni parametrici in config |
| `core.config` | tutto il dominio vive nel TOML, con avvio fail-fast |

## Avvio

```
pip install flask
python app.py            # -> http://localhost:4710
```

Al primo avvio ogni attrezzo dell'elenco viene seminato come `DISPONIBILE`
(seed idempotente). I dati stanno in `ARGO_COMUNE` (default `./dati/`), **fuori**
dalla cartella del modulo.

## Config (`presenze.toml`)

- `[attrezzi]` — elenco degli attrezzi e cadenza manutenzione in giorni.
- `[macchina]` — stato iniziale e transizioni ammesse.
- `[board]` — colonne della board e stati che vi confluiscono.
- `[[turni]]` — turni parametrici (orari d'esempio).

Cambiare attrezzi, stati o turni è una modifica al **TOML**, non al codice.
