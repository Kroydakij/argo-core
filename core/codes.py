"""
core.codes — normalizzazione dei codici identificativi in un unico punto.

I mismatch di formato ("252" vs "050300252") causano lookup falliti in modo
silenzioso: il record c'e' ma non si trova. La regola e': ogni famiglia di
codici ha UNA funzione di normalizzazione, registrata qui, e tutti i punti
del codice — import, API, UI — passano da questa.

Uso:

    from core import codes
    codes.registra("articolo9", codes.zfill_numerico(9))   # una volta, all'avvio
    ...
    codes.norm("articolo9", " 252 ")     # -> "000000252"
    codes.norm("articolo9", "AB-12")     # -> "AB-12" (non numerico: invariato)
"""
from __future__ import annotations

from typing import Callable

_registry: dict[str, Callable[[str], str]] = {}


def registra(nome: str, fn: Callable[[str], str]) -> None:
    """Registra la normalizzazione per una famiglia di codici.

    Ri-registrare lo stesso nome con una funzione diversa e' un errore:
    significherebbe avere due regole per la stessa famiglia.
    """
    if nome in _registry and _registry[nome] is not fn:
        raise ValueError(f"normalizzazione '{nome}' gia' registrata")
    _registry[nome] = fn


def norm(nome: str, valore) -> str:
    """Normalizza un valore secondo la regola registrata per la famiglia."""
    if nome not in _registry:
        raise KeyError(f"nessuna normalizzazione registrata per '{nome}'")
    return _registry[nome]("" if valore is None else str(valore))


def zfill_numerico(cifre: int) -> Callable[[str], str]:
    """Factory per la regola piu' comune: codici numerici zero-padded a N cifre.

    Strip degli spazi; se il valore (tolto un eventuale suffisso '.xx') e'
    numerico viene zero-paddato, altrimenti resta invariato.
    """
    def _fn(v: str) -> str:
        v = v.strip()
        base, dot, suff = v.partition(".")
        if base.isdigit():
            base = base.zfill(cifre)
            return f"{base}.{suff}" if dot else base
        return v
    return _fn
