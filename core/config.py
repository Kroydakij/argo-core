"""
core.config — configurazione TOML con avvio fail-fast.

Regola della suite: una config rotta NON deve mai tradursi in un default
silenzioso su un valore critico. Meglio un avvio negato con un messaggio
chiaro che un modulo che parte con la porta sbagliata o il DB sbagliato.

Perche' TOML (e non JSON): commenti, tipi nativi (int/bool/date) e sezioni
leggibili da un non programmatore che deve mettere mano al file in produzione.
tomllib e' nella libreria standard dal 3.11, quindi zero dipendenze aggiunte.

Uso tipico all'avvio di un modulo:

    from core import config
    cfg = config.load(r"..\\comune\\ilmiomodulo.toml")
    porta = config.require(cfg, "app", "porta")          # obbligatoria
    titolo = config.optional(cfg, "app", "titolo", default="Modulo")

Qualunque chiave obbligatoria assente -> ConfigError -> l'app non parte.
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

_MANCANTE = object()   # sentinella: distingue "assente" da "None esplicito"


class ConfigError(Exception):
    """Config assente, illeggibile o incompleta. Fatale: l'app non deve partire."""


def load(path: str | Path) -> dict:
    """Legge un file TOML. Fail-fast: file mancante o malformato -> ConfigError.

    Non inventa una config vuota se il file non c'e': un modulo che si aspetta
    una config e non la trova e' un errore di installazione, non un caso normale.
    """
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config mancante: {p}")
    try:
        with p.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"config TOML malformato ({p}): {e}") from e


def require(cfg: dict, *chiavi: str) -> Any:
    """Estrae una chiave (anche annidata) OBBLIGATORIA. Assente -> ConfigError.

        require(cfg, "smtp", "server")   # cfg["smtp"]["server"], o esplode
    """
    val = _cammina(cfg, chiavi, _MANCANTE)
    if val is _MANCANTE:
        raise ConfigError(f"chiave di config obbligatoria assente: {'.'.join(chiavi)}")
    return val


def optional(cfg: dict, *chiavi: str, default: Any = None) -> Any:
    """Estrae una chiave (anche annidata) OPZIONALE, con default ESPLICITO.

    Il default va sempre passato dal chiamante: nessun default nascosto nel core.
    """
    val = _cammina(cfg, chiavi, _MANCANTE)
    return default if val is _MANCANTE else val


#: config di SUITE (non di un modulo): vive nella cartella dati comune.
NOME_CONFIG_SUITE = "argo.toml"

ESEMPIO_CONFIG_SUITE = """\
# comune/argo.toml — configurazione della suite (ADR-001).
[suite]
titolo = "ARGO"            # facoltativo
porta = 4700               # facoltativo: porta della shell

[auth]
durata_sessione_ore = 12   # OBBLIGATORIO: dopo quanto scade un login
"""


def carica_suite(comune: str | Path) -> dict:
    """Legge e valida comune/argo.toml. Fail-fast: la shell non parte senza.

    Ritorna {"titolo", "porta", "durata_sessione_ore"} gia' validati.
    """
    p = Path(comune) / NOME_CONFIG_SUITE
    if not p.exists():
        vecchio = " (portal.json non e' piu' letto: riporta qui titolo e porta)" \
            if (Path(comune) / "portal.json").exists() else ""
        raise ConfigError(f"config di suite mancante: {p}{vecchio}. "
                          f"Contenuto minimo:\n\n{ESEMPIO_CONFIG_SUITE}")
    cfg = load(p)
    durata = require(cfg, "auth", "durata_sessione_ore")
    if isinstance(durata, bool) or not isinstance(durata, (int, float)) or durata <= 0:
        raise ConfigError(f"{p}: [auth] durata_sessione_ore deve essere un "
                          f"numero positivo di ore")
    porta = optional(cfg, "suite", "porta", default=4700)
    if isinstance(porta, bool) or not isinstance(porta, int) or not 1 <= porta <= 65535:
        raise ConfigError(f"{p}: [suite] porta non valida: {porta!r}")
    titolo = optional(cfg, "suite", "titolo", default="ARGO")
    if not isinstance(titolo, str) or not titolo.strip():
        raise ConfigError(f"{p}: [suite] titolo non valido")
    return {"titolo": titolo, "porta": porta, "durata_sessione_ore": float(durata)}


def _cammina(cfg: dict, chiavi: tuple, mancante: Any) -> Any:
    """Percorre le chiavi annidate; ritorna `mancante` appena una non esiste."""
    if not chiavi:
        raise ValueError("serve almeno una chiave")
    nodo: Any = cfg
    for k in chiavi:
        if not isinstance(nodo, dict) or k not in nodo:
            return mancante
        nodo = nodo[k]
    return nodo
