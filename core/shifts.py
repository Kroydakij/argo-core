"""
core.shifts — turni parametrici risolti a tempo di lettura.

Un turno e' un intervallo orario con un nome. Quali turni esistano e a che ora
comincino/finiscano NON e' cablato qui: e' un parametro (di solito una sezione
di config del modulo). Il core fornisce solo la regola generica "a che turno
appartiene questo istante", calcolata su richiesta.

Perche' a tempo di lettura: i reset legati al cambio turno non si implementano
con job che modificano i dati a un certo orario (fragili, e un job saltato
falsa lo storico), ma calcolando il turno dall'orario ogni volta che serve.
Stessa filosofia di core.schedule.

Gli intervalli sono semiaperti [inizio, fine): due turni contigui non si
sovrappongono sul minuto di confine. Un turno con fine < inizio attraversa
la mezzanotte (es. notte). Modulo puro: niente SQL, niente Flask.

Definizione parametrica (lista di turni; qui orari illustrativi, NON prescritti):

    turni = [
        {"nome": "A", "inizio": "06:00", "fine": "14:00"},
        {"nome": "B", "inizio": "14:00", "fine": "22:00"},
        {"nome": "C", "inizio": "22:00", "fine": "06:00"},   # oltre mezzanotte
    ]
    t = Turni(turni)
    t.turno_di("15:30")     # -> "B"
    t.turno_di("23:10")     # -> "C"

    # equivalente in TOML del modulo:
    #   [[turni]]
    #   nome = "A"; inizio = "06:00"; fine = "14:00"
"""
from __future__ import annotations

from datetime import datetime, time


class Turni:
    """Insieme di turni parametrici, validato alla costruzione (fail-fast)."""

    def __init__(self, turni: list[dict]):
        if not turni:
            raise ValueError("serve almeno un turno")
        self._turni: list[tuple[str, int, int]] = []
        visti: set[str] = set()
        for t in turni:
            nome = str(t.get("nome", "")).strip()
            if not nome:
                raise ValueError(f"turno senza nome: {t!r}")
            if nome in visti:
                raise ValueError(f"nome turno duplicato: {nome!r}")
            visti.add(nome)
            self._turni.append((nome, _minuti(t["inizio"]), _minuti(t["fine"])))

    @classmethod
    def da_config(cls, lista: list[dict]) -> "Turni":
        """Costruisce dalla lista di turni della config. Fail-fast sugli errori."""
        return cls(lista)

    def nomi(self) -> list[str]:
        """Nomi dei turni, nell'ordine di definizione."""
        return [n for n, _, _ in self._turni]

    def turno_di(self, ora) -> str | None:
        """Nome del turno che contiene `ora`, o None se nessuno la copre.

        `ora` accetta "HH:MM", datetime.time o datetime.datetime.
        """
        m = _minuti(ora)
        for nome, ini, fin in self._turni:
            if ini == fin:                       # turno di 24h: copre tutto
                return nome
            dentro = (ini <= m < fin) if ini < fin else (m >= ini or m < fin)
            if dentro:
                return nome
        return None


def _minuti(x) -> int:
    """Converte "HH:MM" / time / datetime in minuti dalla mezzanotte."""
    if isinstance(x, datetime):
        x = x.time()
    if isinstance(x, time):
        return x.hour * 60 + x.minute
    s = str(x).strip()
    try:
        hh, mm = s.split(":")
        h, m = int(hh), int(mm)
    except ValueError as e:
        raise ValueError(f"orario non valido: {x!r} (atteso 'HH:MM')") from e
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"orario fuori range: {x!r}")
    return h * 60 + m
