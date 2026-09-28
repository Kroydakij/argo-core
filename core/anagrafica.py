"""
core.anagrafica — anagrafica centralizzata delle entita' condivise (kernel, ADR-002).

Una sola risposta, per tutti i moduli, a "cos'e' l'entita' X": macchine,
articoli, attrezzi, commesse... Vive in comune/anagrafica.sqlite, proprietario
il kernel: SCRIVONO solo la shell (API HTTP, con permesso per tipo) e la CLI
del kernel; i moduli LEGGONO in sola lettura e scrivono passando dalla shell
con `anagrafica.client`, cosi' l'attore e' l'utente vero.

Un'entita' ha solo un nucleo piccolo e tipizzato:

    id           UUID generato dal kernel, immutabile, mai mostrato all'utente
    tipo         tipo configurato in comune/argo.toml ([anagrafica.tipi.<nome>])
    codice       codice umano CORRENTE, normalizzato secondo il tipo
    descrizione  breve, libera
    stato        ATTIVO | OBSOLETO | FUSO
    fusa_in      ID di destinazione se FUSO
    attributi    oggetto JSON piatto {chiave: scalare}: descrive, non ha regole

Tutto il resto (cicli, distinte, giacenze, parametri) sta nel DB del modulo con
chiave = ID anagrafica: il kernel non diventa un EAV.

Ogni modifica e' un evento con la busta di ADR-005 nel log append-only
`anagrafica_eventi`; entita', alias, codici storici e catena delle fusioni sono
viste di proiezione. Le fusioni non riscrivono la storia: chi aggrega per
entita' canonicalizza a tempo di lettura (canonico() o la vista
anagrafica_canonico).

Lettura da un modulo:

    from core import anagrafica, db
    con = anagrafica.apri(COMUNE)                 # sola lettura
    r = anagrafica.risolvi(con, "macchina", " pr-001 ")
    r.id, r.come, r.codice                        # ID canonico, CORRENTE|ALIAS|STORICO

    # JOIN dal DB del modulo
    db.attach_readonly(con_modulo, anagrafica.percorso_db(COMUNE), "ana")

Scrittura da un modulo (serve la shell accesa; l'attore e' l'utente della richiesta):

    eid = anagrafica.client.crea(tipo="macchina", codice="PR-001",
                                 descrizione="Pressa 1")

Import iniziale (CLI del kernel):

    python -m core.anagrafica importa --tipo articolo articoli.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import busta, codes, migrate, migrazioni
from . import db as coredb

SISTEMA = busta.SISTEMA

STATI = ("ATTIVO", "OBSOLETO", "FUSO")
COME = ("CORRENTE", "ALIAS", "STORICO")

#: prefisso dei permessi di scrittura, uno per tipo (generati dal kernel).
PREFISSO_PERMESSO = "core.anagrafica.modifica."

# tipi di evento (busta ADR-005, namespace core.*)
E_TIPO = "core.tipo_configurato"
E_CREATA = "core.entita_creata"
E_RINOMINATA = "core.entita_rinominata"
E_DESCRITTA = "core.entita_descritta"
E_OBSOLETA = "core.entita_obsoleta"
E_RIATTIVATA = "core.entita_riattivata"
E_ALIAS_AGGIUNTO = "core.alias_aggiunto"
E_ALIAS_RIMOSSO = "core.alias_rimosso"
E_FUSA = "core.entita_fusa"

_RE_ATTRIBUTO = re.compile(r"^[A-Za-z_]\w*$")
_RE_SISTEMA = re.compile(r"^\w[\w.\-]*$")


class AnagraficaError(ValueError):
    """Operazione di anagrafica non valida (codice gia' in uso, tipo ignoto...)."""


@dataclass(frozen=True)
class Risoluzione:
    """Esito di risolvi(): l'ID canonico e come e' stato trovato il codice."""
    id: str                  # ID canonico (fusioni gia' seguite)
    come: str                # CORRENTE | ALIAS | STORICO
    id_trovato: str          # entita' su cui il codice e' stato trovato
    codice: str              # codice corrente dell'entita' canonica


def permesso_tipo(tipo: str) -> str:
    """Il permesso che copre ogni scrittura sulle entita' di `tipo`."""
    return f"{PREFISSO_PERMESSO}{tipo}"


def permessi(tipi) -> dict[str, str]:
    """{permesso: descrizione} per i tipi configurati ({nome: {descrizione}})."""
    return {permesso_tipo(t): f"Anagrafica: creare e modificare {d['descrizione']}"
            for t, d in sorted(tipi.items())}


# --- schema (core.migrazioni) ------------------------------------------------------

COLONNE_DOMINIO = {
    "tipo_entita": "TEXT",
    "codice": "TEXT",
    "descrizione": "TEXT",
    "attributi": "TEXT",
    "sistema": "TEXT",
    "fusa_in": "TEXT",
    "normalizzazione": "TEXT",
}


def _p1_schema(con: sqlite3.Connection) -> None:
    busta.crea_log(con, "anagrafica_eventi", COLONNE_DOMINIO)
    for nome, colonne in (("ix_ana_entita", "entita_id, tipo, id"),
                          ("ix_ana_codice", "tipo_entita, codice"),
                          ("ix_ana_fusa_in", "fusa_in")):
        con.execute(f"CREATE INDEX IF NOT EXISTS {nome} ON anagrafica_eventi ({colonne})")


PASSI = [migrazioni.Passo(1, "log eventi dell'anagrafica", _p1_schema)]


def _ultimo(espressione: str, tipi: tuple[str, ...]) -> str:
    """Valore dall'ultimo evento dell'entita' c fra i tipi dati (alias e.)."""
    elenco = ",".join(f"'{t}'" for t in tipi)
    return (f"(SELECT {espressione} FROM anagrafica_eventi e "
            f"WHERE e.entita_id = c.entita_id AND e.tipo IN ({elenco}) "
            f"ORDER BY e.id DESC LIMIT 1)")


VISTE = {
    # tipi: ultima configurazione registrata per tipo (da argo.toml)
    "anagrafica_tipi": f"""CREATE VIEW anagrafica_tipi AS
        SELECT t.tipo_entita AS tipo, t.descrizione, t.normalizzazione
        FROM anagrafica_eventi t WHERE t.tipo = '{E_TIPO}' AND t.id = (
          SELECT MAX(x.id) FROM anagrafica_eventi x
          WHERE x.tipo = '{E_TIPO}' AND x.tipo_entita = t.tipo_entita)""",
    # entita': creazione + ultimo codice, ultima descrizione, ultimo stato
    "anagrafica_entita": f"""CREATE VIEW anagrafica_entita AS
        SELECT c.entita_id AS id, c.tipo_entita AS tipo,
          {_ultimo("e.codice", (E_CREATA, E_RINOMINATA))} AS codice,
          {_ultimo("e.descrizione", (E_CREATA, E_DESCRITTA))} AS descrizione,
          {_ultimo("CASE e.tipo WHEN '" + E_OBSOLETA + "' THEN 'OBSOLETO' WHEN '"
                   + E_FUSA + "' THEN 'FUSO' ELSE 'ATTIVO' END",
                   (E_CREATA, E_OBSOLETA, E_RIATTIVATA, E_FUSA))} AS stato,
          {_ultimo("e.fusa_in", (E_FUSA,))} AS fusa_in,
          {_ultimo("e.attributi", (E_CREATA, E_DESCRITTA))} AS attributi,
          c.ts_utc AS creata_il_utc
        FROM anagrafica_eventi c WHERE c.tipo = '{E_CREATA}'""",
    # alias: ultimo evento aggiunto/rimosso per (tipo, sistema, codice)
    "anagrafica_alias": f"""CREATE VIEW anagrafica_alias AS
        SELECT a.tipo_entita AS tipo, a.sistema, a.codice, a.entita_id,
               a.ts_utc AS aggiunto_il_utc
        FROM anagrafica_eventi a WHERE a.tipo = '{E_ALIAS_AGGIUNTO}' AND a.id = (
          SELECT MAX(x.id) FROM anagrafica_eventi x
          WHERE x.tipo IN ('{E_ALIAS_AGGIUNTO}','{E_ALIAS_RIMOSSO}')
            AND x.tipo_entita = a.tipo_entita AND x.sistema = a.sistema
            AND x.codice = a.codice)""",
    # ogni codice mai portato da un'entita' (corrente o storico dopo rinomina)
    "anagrafica_codici": f"""CREATE VIEW anagrafica_codici AS
        SELECT tipo_entita AS tipo, codice, entita_id, MAX(id) AS ultimo_evento
        FROM anagrafica_eventi WHERE tipo IN ('{E_CREATA}','{E_RINOMINATA}')
        GROUP BY tipo_entita, codice, entita_id""",
    # catena delle fusioni: ogni ID -> ID canonico (se stesso se non fuso)
    "anagrafica_canonico": f"""CREATE VIEW anagrafica_canonico AS
        WITH RECURSIVE
          f(id, dest) AS (SELECT entita_id, fusa_in FROM anagrafica_eventi
                          WHERE tipo = '{E_FUSA}'),
          cat(id, cur, n) AS (
            SELECT entita_id, entita_id, 0 FROM anagrafica_eventi
            WHERE tipo = '{E_CREATA}'
            UNION ALL
            SELECT cat.id, f.dest, cat.n + 1 FROM cat JOIN f ON f.id = cat.cur
            WHERE cat.n < 64)
        SELECT c.id, c.cur AS id_canonico FROM cat c
        WHERE NOT EXISTS (SELECT 1 FROM f WHERE f.id = c.cur)""",
}


def percorso_db(comune: str | Path) -> Path:
    """comune/anagrafica.sqlite."""
    return Path(comune) / "anagrafica.sqlite"


def prepara_db(path: str | Path, tipi: dict, *, attore: str = SISTEMA) -> dict:
    """Solo kernel (shell, CLI): schema all'ultima versione con backup
    (ADR-004), poi allinea i tipi alla config di suite. Fail-fast se un tipo
    gia' registrato manca da argo.toml (un tipo non si cancella).
    Ritorna {"migrazione": Esito, "tipi": [tipi registrati o cambiati]}."""
    esito = migrazioni.applica(path, PASSI, viste=VISTE)
    con = coredb.owned(path)
    try:
        cambiati = configura_tipi(con, tipi, attore=attore)
    finally:
        con.close()
    return {"migrazione": esito, "tipi": cambiati}


def migra(con: sqlite3.Connection) -> None:
    """Schema su una connessione gia' aperta, senza backup (test in memoria)."""
    _p1_schema(con)
    migrate.rebuild_views(con, VISTE)
    con.commit()


def apri(comune: str | Path) -> sqlite3.Connection:
    """Connessione in SOLA LETTURA per i moduli. Fail-fast se l'anagrafica non
    c'e' o e' a uno schema piu' vecchio (va avviata prima la shell)."""
    p = percorso_db(comune)
    if not p.exists():
        raise AnagraficaError(f"{p} non trovato: avvia prima la shell")
    migrazioni.richiedi_versione(p, len(PASSI), suggerimento="Avvia prima la shell.")
    return coredb.readonly(p)


# --- tipi ---------------------------------------------------------------------------

def configura_tipi(con, tipi: dict, *, attore: str = SISTEMA) -> list[str]:
    """Registra nel log i tipi di argo.toml nuovi o cambiati. Cambiare la
    normalizzazione NON riscrive i codici esistenti (si rinominano)."""
    esistenti = {r["tipo"]: r for r in con.execute("SELECT * FROM anagrafica_tipi")}
    tolti = sorted(set(esistenti) - set(tipi))
    if tolti:
        raise AnagraficaError(
            f"tipi di anagrafica registrati ma assenti da argo.toml: {', '.join(tolti)}. "
            f"Un tipo non si cancella: rimettilo in [anagrafica.tipi]")
    cambiati = []
    with _transazione(con):
        for nome, d in sorted(tipi.items()):
            regole = json.dumps(list(d["normalizzazione"]))
            codes.componi(d["normalizzazione"])
            r = esistenti.get(nome)
            if r is not None and (r["descrizione"], r["normalizzazione"]) == \
                    (d["descrizione"], regole):
                continue
            _registra(con, E_TIPO, attore=attore, tipo_entita=nome,
                      descrizione=d["descrizione"], normalizzazione=regole)
            cambiati.append(nome)
    return cambiati


def tipi(con) -> dict[str, dict]:
    """{nome: {"descrizione", "normalizzazione": [regole]}} dei tipi registrati."""
    return {r["tipo"]: {"descrizione": r["descrizione"],
                        "normalizzazione": json.loads(r["normalizzazione"])}
            for r in con.execute("SELECT * FROM anagrafica_tipi ORDER BY tipo")}


_cache_norm: dict[str, Callable[[str], str]] = {}


def normalizza(con, tipo: str, codice) -> str:
    """Il codice normalizzato secondo la regola del tipo (unico punto)."""
    r = con.execute("SELECT normalizzazione FROM anagrafica_tipi WHERE tipo=?",
                    (tipo,)).fetchone()
    if r is None:
        raise AnagraficaError(f"tipo di anagrafica non configurato: {tipo!r}")
    fn = _cache_norm.get(r[0])
    if fn is None:
        fn = _cache_norm[r[0]] = codes.componi(json.loads(r[0]))
    return fn("" if codice is None else str(codice))


# --- lettura ------------------------------------------------------------------------

def entita(con, entita_id: str) -> dict | None:
    """L'entita' (anche OBSOLETA o FUSA), attributi gia' decodificati."""
    r = con.execute("SELECT * FROM anagrafica_entita WHERE id=?", (entita_id,)).fetchone()
    return _decodifica(r) if r else None


def elenco(con, tipo: str, *, stati=("ATTIVO",)) -> list[dict]:
    """Entita' di un tipo negli stati dati, per codice."""
    stati = tuple(stati)
    if not stati or set(stati) - set(STATI):
        raise AnagraficaError(f"stati non validi: {stati!r} (ammessi {STATI})")
    return [_decodifica(r) for r in con.execute(
        f"SELECT * FROM anagrafica_entita WHERE tipo=? "
        f"AND stato IN ({','.join('?' * len(stati))}) ORDER BY codice",
        (tipo, *stati))]


def canonico(con, entita_id: str) -> str:
    """Segue la catena delle fusioni fino all'entita' viva. ID sconosciuto:
    ritornato com'e' (i log possono contenere ID di un'altra installazione)."""
    visti = set()
    while entita_id not in visti:
        visti.add(entita_id)
        r = con.execute("SELECT fusa_in FROM anagrafica_eventi WHERE entita_id=? "
                        "AND tipo=? ORDER BY id DESC LIMIT 1",
                        (entita_id, E_FUSA)).fetchone()
        if r is None:
            return entita_id
        entita_id = r[0]
    raise AnagraficaError(f"ciclo di fusioni su {entita_id!r}")


def risolvi(con, tipo: str, codice, *, sistema: str | None = None) -> Risoluzione | None:
    """Da un codice (qualunque forma: normalizzato qui) all'ID canonico.

    Ordine: codice corrente di un'entita' del tipo, poi alias (di `sistema`, o
    di qualunque sistema: se due entita' diverse lo portano -> errore), poi
    codici storici (rinomine; vince l'ultimo che l'ha portato). None se nulla.
    """
    norm = normalizza(con, tipo, codice)
    if not norm:
        return None
    portatori = [r["entita_id"] for r in con.execute(
        "SELECT entita_id FROM anagrafica_codici WHERE tipo=? AND codice=? "
        "ORDER BY ultimo_evento DESC", (tipo, norm))]
    for eid in portatori:
        e = entita(con, eid)
        if e is not None and e["codice"] == norm:
            return _risoluzione(con, eid, "CORRENTE")
    q, par = "SELECT entita_id FROM anagrafica_alias WHERE tipo=? AND codice=?", [tipo, norm]
    if sistema is not None:
        q, par = q + " AND sistema=?", par + [sistema]
    trovati = {r[0] for r in con.execute(q, par)}
    if len({canonico(con, e) for e in trovati}) > 1:
        raise AnagraficaError(f"alias {norm!r} ambiguo per il tipo {tipo!r}: "
                              f"indica il sistema")
    if trovati:
        return _risoluzione(con, trovati.pop(), "ALIAS")
    if portatori:
        return _risoluzione(con, portatori[0], "STORICO")
    return None


def alias(con, entita_id: str) -> list[dict]:
    """Alias correnti di un'entita'."""
    return [dict(r) for r in con.execute(
        "SELECT * FROM anagrafica_alias WHERE entita_id=? ORDER BY sistema, codice",
        (entita_id,))]


def storico(con, entita_id: str) -> list[dict]:
    """Audit trail di un'entita': i suoi eventi, in ordine (fusioni ricevute
    comprese: sono eventi della sorgente con fusa_in = entita_id)."""
    return [dict(r) for r in con.execute(
        "SELECT * FROM anagrafica_eventi WHERE entita_id=? OR fusa_in=? ORDER BY id",
        (entita_id, entita_id))]


def _risoluzione(con, trovato: str, come: str) -> Risoluzione:
    cid = canonico(con, trovato)
    return Risoluzione(id=cid, come=come, id_trovato=trovato,
                       codice=entita(con, cid)["codice"])


def _decodifica(r) -> dict:
    d = dict(r)
    d["attributi"] = json.loads(d["attributi"] or "{}")
    return d


# --- scrittura: SOLO kernel (shell e CLI), single write-point -------------------------

def crea(con, tipo: str, codice, *, attore: str, descrizione: str = "",
         attributi: dict | None = None) -> str:
    """Crea un'entita' ATTIVA e ne ritorna l'ID. Il codice (normalizzato) non
    deve essere portato da nessun'altra entita' del tipo, nemmeno obsoleta."""
    with _transazione(con):
        norm = _codice_valido(con, tipo, codice)
        _codice_libero(con, tipo, norm)
        eid = str(uuid.uuid4())
        _registra(con, E_CREATA, attore=attore, entita_id=eid, tipo_entita=tipo,
                  codice=norm, descrizione=_descrizione(descrizione),
                  attributi=_attributi(attributi))
    return eid


def rinomina(con, entita_id: str, codice, *, attore: str) -> str:
    """Nuovo codice corrente; il vecchio resta risolvibile come STORICO (finche'
    un'altra entita' non lo prende). Ritorna il codice normalizzato."""
    with _transazione(con):
        e = _modificabile(con, entita_id)
        norm = _codice_valido(con, e["tipo"], codice)
        if norm == e["codice"]:
            raise AnagraficaError(f"{norm!r} e' gia' il codice corrente")
        _codice_libero(con, e["tipo"], norm)
        _registra(con, E_RINOMINATA, attore=attore, entita_id=entita_id,
                  tipo_entita=e["tipo"], codice=norm)
    return norm


def descrivi(con, entita_id: str, *, attore: str, descrizione: str | None = None,
             attributi: dict | None = None) -> None:
    """Nuova descrizione e/o attributi. Gli attributi passati sostituiscono
    l'oggetto intero (niente patch); None = invariato."""
    with _transazione(con):
        e = _modificabile(con, entita_id)
        d = e["descrizione"] if descrizione is None else _descrizione(descrizione)
        a = _attributi(e["attributi"] if attributi is None else attributi)
        _registra(con, E_DESCRITTA, attore=attore, entita_id=entita_id,
                  tipo_entita=e["tipo"], descrizione=d, attributi=a)


def rendi_obsoleta(con, entita_id: str, *, attore: str) -> None:
    """ATTIVO -> OBSOLETO. Il codice resta riservato; lo storico resta."""
    _cambia_stato(con, entita_id, "ATTIVO", E_OBSOLETA, attore)


def riattiva(con, entita_id: str, *, attore: str) -> None:
    """OBSOLETO -> ATTIVO."""
    _cambia_stato(con, entita_id, "OBSOLETO", E_RIATTIVATA, attore)


def aggiungi_alias(con, entita_id: str, sistema: str, codice, *, attore: str) -> str:
    """Codice esterno (gestionale, fornitore...). (sistema, codice) unico per
    tipo. Ritorna il codice normalizzato."""
    with _transazione(con):
        e = _modificabile(con, entita_id)
        sistema = _sistema(sistema)
        norm = _codice_valido(con, e["tipo"], codice)
        r = con.execute("SELECT entita_id FROM anagrafica_alias WHERE tipo=? "
                        "AND sistema=? AND codice=?", (e["tipo"], sistema, norm)).fetchone()
        if r is not None:
            raise AnagraficaError(f"alias {sistema}:{norm} gia' in uso")
        _registra(con, E_ALIAS_AGGIUNTO, attore=attore, entita_id=entita_id,
                  tipo_entita=e["tipo"], sistema=sistema, codice=norm)
    return norm


def rimuovi_alias(con, entita_id: str, sistema: str, codice, *, attore: str) -> None:
    with _transazione(con):
        e = _esistente(con, entita_id)
        sistema = _sistema(sistema)
        norm = normalizza(con, e["tipo"], codice)
        r = con.execute("SELECT 1 FROM anagrafica_alias WHERE entita_id=? "
                        "AND sistema=? AND codice=?", (entita_id, sistema, norm)).fetchone()
        if r is None:
            raise AnagraficaError(f"alias {sistema}:{norm} non presente su questa entita'")
        _registra(con, E_ALIAS_RIMOSSO, attore=attore, entita_id=entita_id,
                  tipo_entita=e["tipo"], sistema=sistema, codice=norm)


def fondi(con, sorgente: str, destinazione: str, *, attore: str) -> None:
    """La sorgente diventa FUSA nella destinazione (stesso tipo). I suoi codici
    e alias risolvono sulla destinazione; i log dei moduli NON si riscrivono:
    si canonicalizza in lettura. Una fusione e' definitiva."""
    with _transazione(con):
        s = _modificabile(con, sorgente)
        d = _modificabile(con, destinazione)
        if sorgente == destinazione:
            raise AnagraficaError("un'entita' non si fonde in se stessa")
        if s["tipo"] != d["tipo"]:
            raise AnagraficaError(f"si fondono solo entita' dello stesso tipo "
                                  f"({s['tipo']} != {d['tipo']})")
        _registra(con, E_FUSA, attore=attore, entita_id=sorgente,
                  tipo_entita=s["tipo"], fusa_in=destinazione)


def _cambia_stato(con, entita_id, da: str, evento: str, attore: str) -> None:
    with _transazione(con):
        e = _esistente(con, entita_id)
        if e["stato"] != da:
            raise AnagraficaError(f"entita' {e['codice']!r} e' {e['stato']}, non {da}")
        _registra(con, evento, attore=attore, entita_id=entita_id, tipo_entita=e["tipo"])


def _registra(con, tipo: str, *, attore: str, entita_id: str | None = None,
              **campi) -> None:
    busta.inserisci(con, "anagrafica_eventi", tipo=tipo, versione=1,
                    attore_id=attore, entita_id=entita_id, dati=campi, commit=False)


@contextmanager
def _transazione(con):
    """BEGIN IMMEDIATE: i vincoli di unicita' (su viste) si verificano e si
    scrivono senza che un altro scrittore si infili in mezzo."""
    if con.in_transaction:
        raise AnagraficaError("transazione gia' aperta sulla connessione: "
                              "fai commit prima di scrivere in anagrafica")
    con.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        con.rollback()
        raise
    con.commit()


def _esistente(con, entita_id: str) -> dict:
    e = entita(con, entita_id)
    if e is None:
        raise AnagraficaError(f"entita' sconosciuta: {entita_id!r}")
    return e


def _modificabile(con, entita_id: str) -> dict:
    e = _esistente(con, entita_id)
    if e["stato"] == "FUSO":
        raise AnagraficaError(f"entita' {e['codice']!r} fusa: si modifica "
                              f"quella in cui e' stata fusa")
    return e


def _codice_valido(con, tipo: str, codice) -> str:
    norm = normalizza(con, tipo, codice)
    if not norm:
        raise AnagraficaError("codice vuoto")
    return norm


def _codice_libero(con, tipo: str, norm: str) -> None:
    for (eid,) in con.execute("SELECT entita_id FROM anagrafica_codici "
                              "WHERE tipo=? AND codice=?", (tipo, norm)).fetchall():
        e = entita(con, eid)
        if e["codice"] == norm:
            raise AnagraficaError(
                f"codice {norm!r} gia' portato da un'entita' {e['stato']} del tipo "
                f"{tipo!r}: per riusarlo rinomina prima quella")


def _descrizione(d) -> str:
    if not isinstance(d, str):
        raise AnagraficaError("descrizione deve essere una stringa")
    return d.strip()


def _attributi(a) -> str:
    """Solo la FORMA: oggetto piatto, chiavi identificatore, valori scalari."""
    a = {} if a is None else a
    if not isinstance(a, dict):
        raise AnagraficaError("attributi deve essere un oggetto {chiave: valore}")
    for k, v in a.items():
        if not isinstance(k, str) or not _RE_ATTRIBUTO.match(k):
            raise AnagraficaError(f"chiave di attributo non valida: {k!r}")
        if v is not None and not isinstance(v, (str, int, float, bool)):
            raise AnagraficaError(f"attributo {k!r}: solo stringa, numero, "
                                  f"booleano o null (niente oggetti o liste)")
    return json.dumps(a, ensure_ascii=False, sort_keys=True)


def _sistema(s) -> str:
    s = (s or "").strip() if isinstance(s, str) else ""
    if not _RE_SISTEMA.match(s):
        raise AnagraficaError(f"sistema non valido: {s!r} (es. 'gestionale')")
    return s


# --- scrittura dai moduli: client HTTP verso la shell ---------------------------------

class ClientHTTP:
    """Scritture dei moduli, inoltrate alla shell con la sessione dell'utente
    della richiesta Flask corrente: l'attore e' l'utente vero e il permesso del
    tipo lo verifica la shell. Solo urllib (stdlib). Errori -> AnagraficaError
    (PermissionError se manca il permesso)."""

    def crea(self, *, tipo: str, codice: str, descrizione: str = "",
             attributi: dict | None = None) -> str:
        return self._post("/api/anagrafica/entita", {
            "tipo": tipo, "codice": codice, "descrizione": descrizione,
            "attributi": attributi or {}})["id"]

    def rinomina(self, entita_id: str, codice: str) -> str:
        return self._post(f"/api/anagrafica/entita/{entita_id}/rinomina",
                          {"codice": codice})["codice"]

    def descrivi(self, entita_id: str, *, descrizione: str | None = None,
                 attributi: dict | None = None) -> None:
        self._post(f"/api/anagrafica/entita/{entita_id}/descrivi",
                   {"descrizione": descrizione, "attributi": attributi})

    def rendi_obsoleta(self, entita_id: str) -> None:
        self._post(f"/api/anagrafica/entita/{entita_id}/stato", {"stato": "OBSOLETO"})

    def riattiva(self, entita_id: str) -> None:
        self._post(f"/api/anagrafica/entita/{entita_id}/stato", {"stato": "ATTIVO"})

    def aggiungi_alias(self, entita_id: str, sistema: str, codice: str) -> str:
        return self._post(f"/api/anagrafica/entita/{entita_id}/alias",
                          {"sistema": sistema, "codice": codice})["codice"]

    def rimuovi_alias(self, entita_id: str, sistema: str, codice: str) -> None:
        self._post(f"/api/anagrafica/entita/{entita_id}/alias",
                   {"sistema": sistema, "codice": codice, "rimuovi": True})

    def fondi(self, sorgente: str, destinazione: str) -> None:
        self._post(f"/api/anagrafica/entita/{sorgente}/fondi",
                   {"destinazione": destinazione})

    def _post(self, percorso: str, dati: dict) -> dict:
        import urllib.error
        import urllib.request
        from flask import current_app, request
        cfg = current_app.extensions.get("argo_auth")
        if cfg is None:
            raise AnagraficaError("anagrafica.client richiede auth.inizializza()")
        intestazioni = {"Content-Type": "application/json"}
        from .auth import COOKIE
        if request.cookies.get(COOKIE):
            intestazioni["Cookie"] = f"{COOKIE}={request.cookies[COOKIE]}"
        elif request.headers.get("Authorization"):
            intestazioni["Authorization"] = request.headers["Authorization"]
        req = urllib.request.Request(
            f"http://127.0.0.1:{cfg['porta_shell']}{percorso}",
            data=json.dumps(dati).encode(), headers=intestazioni, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read() or b"{}").get("msg") or e.reason
            except ValueError:
                msg = e.reason
            if e.code in (401, 403):
                raise PermissionError(f"anagrafica: {msg}") from None
            raise AnagraficaError(msg) from None
        except OSError as e:
            raise AnagraficaError(f"shell non raggiungibile ({e}): l'anagrafica si "
                                  f"modifica solo con la shell accesa") from None


client = ClientHTTP()


# --- CLI del kernel -----------------------------------------------------------------

def importa_csv(con, tipo: str, file: str | Path, *, attore: str = SISTEMA) -> dict:
    """Import iniziale da CSV (separatore ';', UTF-8 anche con BOM, prima riga
    = intestazione con almeno `codice`; `descrizione` facoltativa; le altre
    colonne diventano attributi stringa). Idempotente sul codice normalizzato:
    una riga il cui codice esiste gia' (anche obsoleto) si salta."""
    esito = {"create": 0, "gia_presenti": 0, "errori": []}
    normalizza(con, tipo, "")                         # tipo configurato o errore
    with open(file, newline="", encoding="utf-8-sig") as f:
        righe = csv.DictReader(f, delimiter=";")
        if not righe.fieldnames or "codice" not in righe.fieldnames:
            raise AnagraficaError("CSV senza colonna 'codice' nell'intestazione")
        for n, r in enumerate(righe, start=2):
            codice = r.pop("codice")
            descrizione = r.pop("descrizione", "") or ""
            attributi = {k: v for k, v in r.items() if k and v not in (None, "")}
            try:
                ris = risolvi(con, tipo, codice)
                if ris is not None and ris.come == "CORRENTE":
                    esito["gia_presenti"] += 1
                    continue
                crea(con, tipo, codice, attore=attore, descrizione=descrizione,
                     attributi=attributi)
                esito["create"] += 1
            except AnagraficaError as e:
                esito["errori"].append(f"riga {n}: {e}")
    return esito


def main(argv: list[str] | None = None) -> int:
    import os
    from .config import ConfigError, carica_suite
    ap = argparse.ArgumentParser(prog="python -m core.anagrafica",
                                 description="Anagrafica della suite ARGO.")
    ap.add_argument("--comune", default=None,
                    help="cartella dati (default: ARGO_COMUNE o ../comune)")
    sub = ap.add_subparsers(dest="comando", required=True)
    s = sub.add_parser("importa", help="import iniziale da CSV (codice;descrizione;...)")
    s.add_argument("--tipo", required=True)
    s.add_argument("--come", default=None,
                   help="username dell'attore (default: sistema)")
    s.add_argument("file")
    a = ap.parse_args(argv)
    comune = Path(a.comune or os.environ.get(
        "ARGO_COMUNE", Path(__file__).resolve().parents[1] / "comune"))
    try:
        suite = carica_suite(comune)
        attore = SISTEMA
        if a.come:
            from . import auth
            ca = coredb.readonly(auth.percorso_db(comune))
            try:
                u = auth.utente_per_nome(ca, a.come)
            finally:
                ca.close()
            if u is None:
                raise AnagraficaError(f"utente sconosciuto: {a.come!r}")
            attore = u["id"]
        path = percorso_db(comune)
        prepara_db(path, suite["tipi"])
        con = coredb.owned(path)
        try:
            esito = importa_csv(con, a.tipo, a.file, attore=attore)
        finally:
            con.close()
    except (ConfigError, AnagraficaError, migrazioni.MigrazioneError,
            sqlite3.Error, OSError) as e:
        print(f"[anagrafica] errore: {e}", file=sys.stderr)
        return 1
    print(f"[anagrafica] {a.tipo}: create {esito['create']}, "
          f"gia' presenti {esito['gia_presenti']}, errori {len(esito['errori'])}")
    for err in esito["errori"]:
        print(f"  {err}", file=sys.stderr)
    return 1 if esito["errori"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
