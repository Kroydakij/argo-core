"""
core.schedule — scheduler "a tempo di lettura", come funzione pura.

Il pattern: attivita' ricorrenti (per cadenza in giorni O legate a un evento)
il cui stato — in regola / da fare / scaduta — viene CALCOLATO a ogni lettura
a partire dallo storico delle esecuzioni. Nessun job che modifica i dati:
i dati sono solo lo storico append-only, lo stato e' una funzione di esso.

Questa funzione non contiene SQL: riceve dati gia' letti e restituisce stati.
Ogni modulo la alimenta con le proprie query, cosi' il pattern e' riusabile
per manutenzioni, promemoria, controlli periodici, scadenze di qualunque tipo.

Semantica (per una coppia attivita'×soggetto):
  - a frequenza: mai eseguita, o dovuta oggi     -> "da_fare"
                 oltre la scadenza               -> "scaduta"
                 altrimenti                      -> "ok"
  - a evento:    nessun evento dopo l'ultima esecuzione -> "ok"
                 evento di oggi non ancora seguito      -> "da_fare"
                 evento di ieri o prima non seguito     -> "scaduta"
"""
from __future__ import annotations

from datetime import datetime, timedelta

OK, DA_FARE, SCADUTA = "ok", "da_fare", "scaduta"


def stato_task(tasks: list[dict],
               ultime: dict[tuple, tuple],
               eventi: dict | None = None,
               oggi: str | None = None) -> dict:
    """Calcola lo stato di ogni attivita' per ogni soggetto.

    tasks:  [{"id":..., "soggetto":..., "freq_giorni": int|None,
              "evento": str|None, ...campi liberi...}]
            Ogni riga = una coppia attivita'×soggetto. freq_giorni ed evento
            sono alternativi; se entrambi assenti la riga viene ignorata.
    ultime: {(task_id, soggetto): (data "YYYY-MM-DD", ts "YYYY-MM-DD HH:MM:SS")}
            ultima esecuzione registrata. ts puo' essere None.
    eventi: {soggetto: ts ultimo evento} per le attivita' a evento.
    oggi:   "YYYY-MM-DD"; default la data corrente (parametrizzata per i test).

    -> {soggetto: {"stato": ok|da_fare|scaduta, "da_fare": n, "scadute": n,
                   "tasks": [{...task, "stato":..., "prossima":..., "ultima":...}]}}
    Lo stato del soggetto e' il peggiore tra le sue attivita'.
    """
    oggi = oggi or datetime.now().strftime("%Y-%m-%d")
    eventi = eventi or {}
    out: dict = {}

    for t in tasks:
        sog = t["soggetto"]
        ld, lts = ultime.get((t["id"], sog), (None, None))

        if t.get("evento"):
            ets = eventi.get(sog)
            if not ets or (lts and lts >= ets):
                st, prossima = OK, f"al prossimo {t['evento']}"
            elif ets[:10] == oggi:
                st, prossima = DA_FARE, f"{t['evento']} del {ets[:16]}"
            else:
                st, prossima = SCADUTA, f"{t['evento']} del {ets[:16]}"
        elif t.get("freq_giorni"):
            if not ld:
                st, prossima = DA_FARE, "mai eseguita"
            else:
                due = (datetime.strptime(ld, "%Y-%m-%d")
                       + timedelta(days=t["freq_giorni"])).strftime("%Y-%m-%d")
                st = OK if oggi < due else DA_FARE if oggi == due else SCADUTA
                prossima = due
        else:
            continue

        s = out.setdefault(sog, {"da_fare": 0, "scadute": 0, "tasks": []})
        s["tasks"].append({**t, "stato": st, "prossima": prossima, "ultima": ld})
        if st == DA_FARE:
            s["da_fare"] += 1
        elif st == SCADUTA:
            s["scadute"] += 1

    for s in out.values():
        s["stato"] = SCADUTA if s["scadute"] else DA_FARE if s["da_fare"] else OK
    return out
