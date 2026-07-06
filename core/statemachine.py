"""
core.statemachine — macchina a stati dichiarativa, funzione pura.

Le transizioni di stato di un'entita' sono DATI, non if sparsi nel codice:
si dichiarano una volta (in Python o in una sezione TOML) e si validano
all'avvio. Cosi' "da IN_USO si puo' solo rilasciare o segnalare guasto" e'
una regola scritta in un posto solo, non una convenzione che ogni route
reimplementa (e prima o poi sbaglia).

Modulo puro: nessun SQL, nessun Flask, nessuno stato globale. Si sposa con
core.events — la sequenza tipica in un modulo e': valida la transizione con
transita(), poi appende l'evento con events.registra(). Ma i due restano
indipendenti e testabili separatamente.

Forma dichiarativa (dict di dict) o sezione TOML equivalente:

    transizioni = {
        "LIBERA":  {"prendi":   "IN_USO"},
        "IN_USO":  {"rilascia": "LIBERA", "guasta": "GUASTA"},
        "GUASTA":  {"ripara":   "LIBERA"},   # o {} per uno stato terminale
    }
    sm = StateMachine(transizioni, iniziale="LIBERA")
    sm.transita("IN_USO", "rilascia")        # -> "LIBERA"
    sm.puo("LIBERA", "rilascia")             # -> False

    # equivalente in TOML:
    #   [macchina]
    #   iniziale = "LIBERA"
    #   [macchina.transizioni]
    #   LIBERA = { prendi = "IN_USO" }
    #   IN_USO = { rilascia = "LIBERA", guasta = "GUASTA" }
    #   GUASTA = { ripara = "LIBERA" }
    sm = StateMachine.da_config(cfg["macchina"])
"""
from __future__ import annotations


class TransizioneNonValida(Exception):
    """L'azione non e' ammessa dallo stato corrente. Alzata da transita()."""


class StateMachine:
    """Macchina a stati validata alla costruzione (fail-fast)."""

    def __init__(self, transizioni: dict[str, dict[str, str]], iniziale: str):
        if not isinstance(transizioni, dict) or not transizioni:
            raise ValueError("transizioni deve essere un dict non vuoto")
        # gli stati sono le sorgenti dichiarate + tutte le destinazioni
        stati = set(transizioni)
        for sorg, azioni in transizioni.items():
            if not isinstance(azioni, dict):
                raise ValueError(f"transizioni[{sorg!r}] deve essere un dict")
            for azione, dest in azioni.items():
                if not isinstance(dest, str) or not dest:
                    raise ValueError(
                        f"destinazione non valida per {sorg!r}->{azione!r}: {dest!r}")
                stati.add(dest)
        if iniziale not in stati:
            raise ValueError(
                f"stato iniziale {iniziale!r} non tra gli stati noti: {sorted(stati)}")
        self._trans = {s: dict(a) for s, a in transizioni.items()}
        self._stati = frozenset(stati)
        self.iniziale = iniziale

    @classmethod
    def da_config(cls, sezione: dict) -> "StateMachine":
        """Costruisce da una sezione di config {iniziale, transizioni}.

        Fail-fast: chiavi mancanti o malformate -> ValueError, l'app non parte.
        """
        try:
            return cls(sezione["transizioni"], sezione["iniziale"])
        except KeyError as e:
            raise ValueError(f"config state machine: chiave mancante {e}") from e

    def stati(self) -> frozenset[str]:
        """Tutti gli stati noti (sorgenti + destinazioni)."""
        return self._stati

    def azioni(self, stato: str) -> dict[str, str]:
        """Azioni ammesse da uno stato: {azione: destinazione}. {} se terminale."""
        return dict(self._trans.get(stato, {}))

    def terminali(self) -> frozenset[str]:
        """Stati senza azioni in uscita (nessuna transizione possibile)."""
        return frozenset(s for s in self._stati if not self._trans.get(s))

    def puo(self, stato: str, azione: str) -> bool:
        """True se `azione` e' ammessa da `stato`."""
        return azione in self._trans.get(stato, {})

    def transita(self, stato: str, azione: str) -> str:
        """Stato risultante dopo `azione`. Transizione non ammessa -> eccezione."""
        azioni = self._trans.get(stato, {})
        if azione not in azioni:
            raise TransizioneNonValida(
                f"da {stato!r} l'azione {azione!r} non e' ammessa "
                f"(ammesse: {sorted(azioni) or 'nessuna, stato terminale'})")
        return azioni[azione]
