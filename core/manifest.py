"""
core.manifest — cosa un modulo dichiara di essere, in un file solo (ADR-003).

Ogni modulo conforme ha nella sua cartella un `manifest.toml` STATICO,
versionato col codice e incluso nei rilasci. Il manifest dice cosa il modulo
E' (uguale in ogni installazione); la config `<modulo>.toml` resta cio' che
l'installazione SCEGLIE (porta, dominio). Il manifest non contiene mai dati
d'installazione.

    [modulo]
    nome = "andon"                 # = nome della cartella
    versione = "1.2.0"             # SemVer del modulo
    core = ">=1.0,<2.0"            # versioni di argo-core compatibili
    titolo = "Andon"
    descrizione = "Segnalazione e gestione fermate"     # facoltativa

    [[permessi]]
    id = "andon.chiudi_fermata"    # sempre "<nome>.<azione>"
    descrizione = "Chiudere una fermata aperta"

    [[menu]]
    titolo = "Fermate"
    percorso = "/"
    permesso = "andon.chiudi_fermata"   # deve essere tra i permessi dichiarati

    [anagrafica]
    tipi = ["macchina"]            # tipi di anagrafica usati (facoltativo)

    [[eventi]]
    tipo = "andon.fermata_chiusa"  # sempre "<nome>.<evento>"
    versione = 1                   # versione corrente dello schema dell'evento
    entita = "macchina"            # tipo di anagrafica dell'entita', "" se nessuna
    descrizione = "Chiusura di una fermata con causale"

Perche' un file statico e non codice Python: la shell deve poterlo leggere
SENZA eseguire il modulo (menu, catalogo permessi), anche a modulo spento o
con un import rotto.

Fail-fast: qualunque errore (chiave sconosciuta compresa) -> ManifestError.
Un manifest scritto per un kernel piu' nuovo non viene interpretato a meta'.

Uso:

    from core import manifest
    m = manifest.carica(QUI)                 # cartella del modulo o file
    m.dichiara_permesso("andon.chiudi_fermata")   # -> True
    m.evento("andon.fermata_chiusa").versione     # -> 1

    for s in manifest.scansiona(radice_suite):    # cosa fa la shell
        print(s.nome, s.porta, s.errore or "ok")
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

NOME_FILE = "manifest.toml"

_CHIAVI = {
    "": ({"modulo", "permessi", "menu", "anagrafica", "eventi"}, {"modulo"}),
    "modulo": ({"nome", "versione", "core", "titolo", "descrizione"},
               {"nome", "versione", "core", "titolo"}),
    "permessi": ({"id", "descrizione"}, {"id", "descrizione"}),
    "menu": ({"titolo", "percorso", "permesso"}, {"titolo", "percorso", "permesso"}),
    "anagrafica": ({"tipi"}, set()),
    "eventi": ({"tipo", "versione", "entita", "descrizione"},
               {"tipo", "versione", "descrizione"}),
}

_SEGMENTO = r"[a-z][a-z0-9_]*"
_RE_TIPO_ANAGRAFICA = re.compile(rf"^{_SEGMENTO}$")
_RE_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_RE_VINCOLO = re.compile(r"^(>=|<=|==|>|<)\s*(\d+)\.(\d+)(?:\.(\d+))?$")


class ManifestError(ValueError):
    """Manifest assente, malformato o incompatibile. Il modulo non e' conforme."""


@dataclass(frozen=True)
class Permesso:
    id: str
    descrizione: str


@dataclass(frozen=True)
class VoceMenu:
    titolo: str
    percorso: str
    permesso: str


@dataclass(frozen=True)
class Evento:
    tipo: str
    versione: int
    entita: str          # tipo di anagrafica, "" se l'evento non riguarda un'entita'
    descrizione: str


@dataclass(frozen=True)
class Manifest:
    nome: str
    versione: str
    core: str
    titolo: str
    descrizione: str
    permessi: tuple[Permesso, ...]
    menu: tuple[VoceMenu, ...]
    tipi_anagrafica: tuple[str, ...]
    eventi: tuple[Evento, ...]

    def dichiara_permesso(self, permesso: str) -> bool:
        return any(p.id == permesso for p in self.permessi)

    def evento(self, tipo: str) -> Evento | None:
        return next((e for e in self.eventi if e.tipo == tipo), None)

    def to_dict(self) -> dict:
        """Forma serializzabile (JSON) per il registro della shell."""
        return asdict(self)


@dataclass(frozen=True)
class Scansione:
    """Esito della lettura di UNA cartella di modulo da parte della shell."""
    cartella: Path
    nome: str                       # nome della cartella
    manifest: Manifest | None       # None se non valido
    porta: int | None               # da <nome>.toml [app] porta
    errore: str | None              # None se tutto valido


# --- API ------------------------------------------------------------------

def carica(percorso: str | Path, *, versione_core: str | None = None) -> Manifest:
    """Legge e valida un manifest. `percorso`: file manifest.toml o cartella.

    versione_core: versione di argo-core contro cui verificare `core`;
    default quella di questo core. Qualunque problema -> ManifestError.
    """
    p = Path(percorso)
    if p.is_dir():
        p = p / NOME_FILE
    if not p.exists():
        raise ManifestError(f"manifest mancante: {p}")
    try:
        with p.open("rb") as f:
            dati = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ManifestError(f"manifest TOML malformato ({p}): {e}") from e
    try:
        return da_dict(dati, nome_cartella=p.parent.name,
                       versione_core=versione_core)
    except ManifestError as e:
        raise ManifestError(f"{p}: {e}") from None


def da_dict(dati: dict, *, nome_cartella: str | None = None,
            versione_core: str | None = None) -> Manifest:
    """Valida un manifest gia' letto (dict). Vedi carica()."""
    _chiavi(dati, "", "manifest")
    mod = dati["modulo"]
    _chiavi(mod, "modulo", "[modulo]")

    nome = _stringa(mod, "nome", "[modulo]")
    if not nome.isidentifier():
        raise ManifestError(f"[modulo] nome non valido: {nome!r} "
                            f"(lettere, cifre e '_', non iniziare per cifra)")
    if nome_cartella is not None and nome != nome_cartella:
        raise ManifestError(f"[modulo] nome {nome!r} diverso dal nome della "
                            f"cartella {nome_cartella!r}")
    versione = _stringa(mod, "versione", "[modulo]")
    if not _RE_SEMVER.match(versione):
        raise ManifestError(f"[modulo] versione non SemVer X.Y.Z: {versione!r}")
    core = _stringa(mod, "core", "[modulo]")
    vincoli = _intervallo(core)
    if versione_core is None:
        from . import __version__ as versione_core
    if not _soddisfa(vincoli, _versione(versione_core)):
        raise ManifestError(f"il modulo richiede core {core!r}, "
                            f"questo core e' {versione_core}")
    titolo = _stringa(mod, "titolo", "[modulo]")
    descrizione = _stringa(mod, "descrizione", "[modulo]", default="")

    re_id = re.compile(rf"^{re.escape(nome)}\.{_SEGMENTO}(\.{_SEGMENTO})*$")

    permessi = []
    for i, d in enumerate(_lista(dati, "permessi"), start=1):
        dove = f"[[permessi]] n.{i}"
        _chiavi(d, "permessi", dove)
        pid = _stringa(d, "id", dove)
        if not re_id.match(pid):
            raise ManifestError(f"{dove}: id {pid!r} deve essere "
                                f"'{nome}.<azione>' in minuscolo snake_case")
        permessi.append(Permesso(pid, _stringa(d, "descrizione", dove)))
    _unici([p.id for p in permessi], "permesso")

    dichiarati = {p.id for p in permessi}
    menu = []
    for i, d in enumerate(_lista(dati, "menu"), start=1):
        dove = f"[[menu]] n.{i}"
        _chiavi(d, "menu", dove)
        percorso = _stringa(d, "percorso", dove)
        if not percorso.startswith("/"):
            raise ManifestError(f"{dove}: percorso {percorso!r} deve iniziare con '/'")
        permesso = _stringa(d, "permesso", dove)
        if permesso not in dichiarati:
            raise ManifestError(f"{dove}: permesso {permesso!r} non dichiarato "
                                f"in [[permessi]]")
        menu.append(VoceMenu(_stringa(d, "titolo", dove), percorso, permesso))

    ana = dati.get("anagrafica", {})
    if not isinstance(ana, dict):
        raise ManifestError("[anagrafica] deve essere una tabella")
    _chiavi(ana, "anagrafica", "[anagrafica]")
    tipi = ana.get("tipi", [])
    if not isinstance(tipi, list) or not all(isinstance(t, str) for t in tipi):
        raise ManifestError("[anagrafica] tipi deve essere una lista di stringhe")
    for t in tipi:
        if not _RE_TIPO_ANAGRAFICA.match(t):
            raise ManifestError(f"[anagrafica] tipo non valido: {t!r}")
    _unici(tipi, "tipo di anagrafica")

    eventi = []
    for i, d in enumerate(_lista(dati, "eventi"), start=1):
        dove = f"[[eventi]] n.{i}"
        _chiavi(d, "eventi", dove)
        tipo = _stringa(d, "tipo", dove)
        if not re_id.match(tipo):
            raise ManifestError(f"{dove}: tipo {tipo!r} deve essere "
                                f"'{nome}.<evento>' in minuscolo snake_case")
        v = d["versione"]
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            raise ManifestError(f"{dove}: versione deve essere un intero >= 1")
        entita = _stringa(d, "entita", dove, default="")
        if entita and entita not in tipi:
            raise ManifestError(f"{dove}: entita {entita!r} non e' tra i tipi "
                                f"dichiarati in [anagrafica] tipi")
        eventi.append(Evento(tipo, v, entita, _stringa(d, "descrizione", dove)))
    _unici([e.tipo for e in eventi], "tipo di evento")

    return Manifest(nome=nome, versione=versione, core=core, titolo=titolo,
                    descrizione=descrizione, permessi=tuple(permessi),
                    menu=tuple(menu), tipi_anagrafica=tuple(tipi),
                    eventi=tuple(eventi))


def compatibile(intervallo: str, versione: str) -> bool:
    """True se `versione` (X.Y[.Z]) soddisfa `intervallo` (es. ">=1.0,<2.0")."""
    return _soddisfa(_intervallo(intervallo), _versione(versione))


def verifica_tipi(m: Manifest, tipi_disponibili) -> None:
    """Fail-fast: ogni tipo di anagrafica usato dal modulo deve esistere
    nell'installazione (i tipi vivono in comune/argo.toml, ADR-002)."""
    mancanti = sorted(set(m.tipi_anagrafica) - set(tipi_disponibili))
    if mancanti:
        raise ManifestError(f"{m.nome}: tipi di anagrafica non configurati "
                            f"nell'installazione: {', '.join(mancanti)}")


def scansiona(radice: str | Path, *, versione_core: str | None = None) -> list[Scansione]:
    """Legge i manifest delle cartelle figlie di `radice` (la cartella della
    suite: le sorelle di core/). Non solleva per un modulo rotto: l'errore
    finisce nella sua Scansione. Deploy di un modulo = copiarne la cartella.

    La porta si legge da <cartella>/<nome>.toml, chiave [app] porta
    (contratto di conformita').
    """
    radice = Path(radice)
    esiti = []
    for cartella in sorted(p for p in radice.iterdir() if p.is_dir()):
        if cartella.name == "core" or not (cartella / NOME_FILE).exists():
            continue
        try:
            m = carica(cartella, versione_core=versione_core)
        except ManifestError as e:
            esiti.append(Scansione(cartella, cartella.name, None,
                                   _porta(cartella)[0], str(e)))
            continue
        porta, errore = _porta(cartella)
        esiti.append(Scansione(cartella, cartella.name,
                               m if errore is None else None, porta, errore))
    return esiti


# --- interni --------------------------------------------------------------

def _chiavi(d, sezione: str, dove: str) -> None:
    if not isinstance(d, dict):
        raise ManifestError(f"{dove}: deve essere una tabella")
    ammesse, obbligatorie = _CHIAVI[sezione]
    ignote = sorted(set(d) - ammesse)
    if ignote:
        raise ManifestError(f"{dove}: chiavi sconosciute {ignote} "
                            f"(ammesse: {sorted(ammesse)})")
    mancanti = sorted(obbligatorie - set(d))
    if mancanti:
        raise ManifestError(f"{dove}: chiavi obbligatorie mancanti {mancanti}")


def _stringa(d: dict, chiave: str, dove: str, *, default: str | None = None) -> str:
    v = d.get(chiave, default)
    if not isinstance(v, str):
        raise ManifestError(f"{dove}: {chiave} deve essere una stringa")
    if default is None and not v.strip():
        raise ManifestError(f"{dove}: {chiave} vuoto")
    return v


def _lista(dati: dict, chiave: str) -> list:
    v = dati.get(chiave, [])
    if not isinstance(v, list):
        raise ManifestError(f"[[{chiave}]] deve essere una lista di tabelle")
    return v


def _unici(valori: list[str], cosa: str) -> None:
    visti = set()
    for v in valori:
        if v in visti:
            raise ManifestError(f"{cosa} duplicato: {v!r}")
        visti.add(v)


def _versione(s: str) -> tuple[int, int, int]:
    parti = s.strip().split(".")
    if not 2 <= len(parti) <= 3 or not all(p.isdigit() for p in parti):
        raise ManifestError(f"versione non valida: {s!r}")
    n = [int(p) for p in parti] + [0] * (3 - len(parti))
    return n[0], n[1], n[2]


def _intervallo(s: str) -> list[tuple[str, tuple[int, int, int]]]:
    vincoli = []
    for pezzo in s.split(","):
        m = _RE_VINCOLO.match(pezzo.strip())
        if not m:
            raise ManifestError(f"intervallo core non valido: {s!r} "
                                f"(es. '>=1.0,<2.0')")
        op, a, b, c = m.groups()
        vincoli.append((op, (int(a), int(b), int(c or 0))))
    if not any(op in (">=", ">", "==") for op, _ in vincoli):
        raise ManifestError(f"intervallo core senza minimo: {s!r} (serve '>=X.Y')")
    return vincoli


def _soddisfa(vincoli, v: tuple[int, int, int]) -> bool:
    confronti = {">=": v.__ge__, ">": v.__gt__, "<=": v.__le__,
                 "<": v.__lt__, "==": v.__eq__}
    return all(confronti[op](rif) for op, rif in vincoli)


def _porta(cartella: Path) -> tuple[int | None, str | None]:
    cfg = cartella / f"{cartella.name}.toml"
    if not cfg.exists():
        return None, f"config {cfg.name} mancante: serve [app] porta"
    try:
        with cfg.open("rb") as f:
            porta = tomllib.load(f).get("app", {}).get("porta")
    except tomllib.TOMLDecodeError as e:
        return None, f"config {cfg.name} malformata: {e}"
    if isinstance(porta, bool) or not isinstance(porta, int) or not 1 <= porta <= 65535:
        return None, f"config {cfg.name}: [app] porta mancante o non valida"
    return porta, None
