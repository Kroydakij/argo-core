"""
core.inventory — inventario generico event-sourced sopra l'anagrafica della suite.

Il pattern, senza alcun dominio dentro: gli ARTICOLI sono entita' di
core.anagrafica (tipo configurato in comune/argo.toml, ADR-002), i MOVIMENTI
sono un log append-only con la busta standard (ADR-005); la giacenza non e'
un campo aggiornabile ma la SOMMA dei movimenti, calcolata per entita'
CANONICA: se due articoli vengono fusi in anagrafica, le loro giacenze si
sommano senza riscrivere un solo movimento.

Cosa decide il MODULO (config TOML + manifest), non il core:
  - quale tipo di anagrafica sono i suoi articoli;
  - quali causali esistono e con che verso ("+" carico, "-" scarico);
  - se la giacenza puo' andare sotto zero;
  - le soglie di riordino (eventi anche loro: chi e quando le ha cambiate).

    [inventario]
    tipo = "articolo"               # tipo di anagrafica (in argo.toml)
    consenti_negativo = false
    [inventario.causali]
    CARICO = "+"
    CONSUMO = "-"

Il manifest del modulo dichiara il tipo e i due eventi (nomi di default):

    [anagrafica]
    tipi = ["articolo"]
    [[eventi]]
    tipo = "magazzino.movimento"
    versione = 1
    entita = "articolo"
    descrizione = "Carico o scarico di un articolo"
    [[eventi]]
    tipo = "magazzino.soglia_impostata"
    versione = 1
    entita = "articolo"
    descrizione = "Nuova soglia di riordino di un articolo"

Uso in un modulo (proprietario del proprio DB, lettore dell'anagrafica):

    from core import anagrafica, inventory
    inv = inventory.Inventario.da_config(cfg["inventario"], manifest=M,
                                         anagrafica=anagrafica.percorso_db(COMUNE))
    PASSI = [migrazioni.Passo(1, "inventario", inv.migra)]

    eid = anagrafica.risolvi(ana, "articolo", " 252 ").id   # dal codice digitato
    inv.movimenta(con, eid, 50, "CARICO")          # attore = utente della sessione
    inv.imposta_soglia(con, eid, 10)
    inv.giacenza(con, eid)       # -> {entita_id, codice, descrizione, unita, soglia_minima, giacenza}
    inv.sotto_scorta(con)        # -> articoli con giacenza <= soglia

Regole rese automatiche:
  - movimenti e soglie append-only, scritti solo da movimenta() /
    imposta_soglia() (busta.scrivi: tipo dal manifest, attore mai dedotto);
    le correzioni sono movimenti di rettifica, mai UPDATE/DELETE;
  - si movimenta solo un articolo ATTIVO del tipo giusto; un ID di un'entita'
    fusa viene scritto gia' canonico;
  - identificatori SQL validati prima di ogni interpolazione.

Inventari 0.x (tabelle `articoli` + `movimenti` col codice): restano nel DB,
intatti e non piu' letti. esporta_articoli_0x() scrive il CSV per
`python -m core.anagrafica importa`, poi adotta_0x() copia i vecchi movimenti
nel nuovo log (idempotente).
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from . import busta, migrate
from . import db as coredb
from .migrate import ident


def _anagrafica():
    """Import lazy: `import core` non deve caricare i moduli con CLI del kernel
    (python -m core.anagrafica / core.migrazioni avviserebbero del doppio import)."""
    from . import anagrafica
    return anagrafica


class GiacenzaInsufficiente(Exception):
    """Scarico rifiutato: porterebbe la giacenza sotto zero. Alzata da movimenta()."""


class Inventario:
    """Inventario generico, validato alla costruzione (fail-fast).

    causali: {nome: "+"|"-"} — il verso e' fisso per causale; movimenta()
    riceve sempre quantita' positive e applica il segno della causale.
    anagrafica: percorso di comune/anagrafica.sqlite (letto in sola lettura).
    """

    def __init__(self, causali: dict[str, str], *, manifest, anagrafica: str | Path,
                 tipo: str = "articolo", evento_movimento: str | None = None,
                 evento_soglia: str | None = None, consenti_negativo: bool = False,
                 tabella_movimenti: str = "inventario_movimenti",
                 tabella_soglie: str = "inventario_soglie",
                 vista_saldi: str = "inventario_saldi"):
        if not isinstance(causali, dict) or not causali:
            raise ValueError("causali deve essere un dict non vuoto {nome: '+'|'-'}")
        for nome, verso in causali.items():
            if verso not in ("+", "-"):
                raise ValueError(
                    f"verso non valido per la causale {nome!r}: {verso!r} "
                    f"(ammessi '+' e '-')")
        self.causali = dict(causali)
        self.consenti_negativo = bool(consenti_negativo)
        self.manifest = manifest
        self.anagrafica = Path(anagrafica)
        self.tipo = tipo
        self.evento_movimento = evento_movimento or f"{manifest.nome}.movimento"
        self.evento_soglia = evento_soglia or f"{manifest.nome}.soglia_impostata"
        if tipo not in manifest.tipi_anagrafica:
            raise ValueError(f"il manifest di {manifest.nome} non dichiara il tipo "
                             f"di anagrafica {tipo!r} in [anagrafica] tipi")
        for ev in (self.evento_movimento, self.evento_soglia):
            d = manifest.evento(ev)
            if d is None or d.entita != tipo:
                raise ValueError(f"il manifest di {manifest.nome} deve dichiarare "
                                 f"l'evento {ev!r} con entita = {tipo!r}")
        # nomi validati subito (fail-fast): finiscono interpolati nei DDL/DML
        self.tabella_movimenti = tabella_movimenti
        self.tabella_soglie = tabella_soglie
        self.vista_saldi = vista_saldi
        self._mov = ident(tabella_movimenti)
        self._sog = ident(tabella_soglie)
        self._sal = ident(vista_saldi)

    @classmethod
    def da_config(cls, sezione: dict, *, manifest, anagrafica: str | Path) -> "Inventario":
        """Costruisce dalla sezione [inventario] della config. Fail-fast."""
        try:
            return cls(sezione["causali"], manifest=manifest, anagrafica=anagrafica,
                       tipo=sezione.get("tipo", "articolo"),
                       consenti_negativo=sezione.get("consenti_negativo", False))
        except KeyError as e:
            raise ValueError(f"config inventario: chiave mancante {e}") from e

    # --- schema (da chiamare in un Passo di core.migrazioni) -------------------

    def migra(self, con, *, extra_movimenti: dict[str, str] | None = None) -> None:
        """Log dei movimenti e delle soglie (busta + dominio) e vista dei saldi.
        extra_movimenti: {colonna: tipo_ddl} colonne aggiuntive del modulo."""
        busta.crea_log(con, self.tabella_movimenti, {
            "quantita": "REAL NOT NULL", "causale": "TEXT NOT NULL", "note": "TEXT",
            "id_0x": "INTEGER", **(extra_movimenti or {})})
        busta.crea_log(con, self.tabella_soglie, {"soglia_minima": "REAL"})
        migrate.rebuild_views(con, {self.vista_saldi: f"""
            CREATE VIEW {self._sal} AS
            SELECT entita_id, SUM(quantita) AS giacenza, COUNT(*) AS movimenti
            FROM {self._mov} GROUP BY entita_id"""})

    # --- scrittura (append-only, single write-point) ----------------------------

    def movimenta(self, con, entita_id: str, quantita: float, causale: str, *,
                  sorgente: str = "MANUALE", attore_id: str | None = None,
                  note: str | None = None, extra: dict[str, Any] | None = None,
                  ora: datetime | None = None) -> int:
        """SINGLE WRITE-POINT dei movimenti: appende un movimento, ritorna l'id.

        quantita e' SEMPRE positiva: il segno lo mette il verso della causale.
        Rifiuta: causale ignota, quantita <= 0, articolo sconosciuto, di un
        altro tipo o non ATTIVO, scarico che porterebbe la giacenza sotto zero
        (salvo consenti_negativo). Un ID fuso si scrive gia' canonico.
        """
        if causale not in self.causali:
            raise ValueError(f"causale sconosciuta: {causale!r} "
                             f"(ammesse: {sorted(self.causali)})")
        q = float(quantita)
        if q <= 0:
            raise ValueError(f"quantita deve essere positiva, non {quantita!r} "
                             f"(il segno lo da' la causale)")
        ana = self._ana()
        try:
            eid, e = self._articolo_attivo(ana, entita_id)
            ids = _anagrafica().equivalenti(ana, eid)
        finally:
            ana.close()
        delta = q if self.causali[causale] == "+" else -q
        if delta < 0 and not self.consenti_negativo:
            attuale = self._somma(con, ids)
            if attuale + delta < 0:
                raise GiacenzaInsufficiente(
                    f"{e['codice']}: giacenza {attuale}, scarico {q} rifiutato")
        dati = {"quantita": delta, "causale": causale, "note": note, **(extra or {})}
        return busta.scrivi(con, self.tabella_movimenti, tipo=self.evento_movimento,
                            manifest=self.manifest, entita_id=eid, attore_id=attore_id,
                            sorgente=sorgente, dati=dati, ora=ora)

    def imposta_soglia(self, con, entita_id: str, soglia_minima: float | None, *,
                       attore_id: str | None = None) -> int:
        """Nuova soglia di riordino (None = nessuna soglia). E' un evento."""
        if soglia_minima is not None:
            soglia_minima = float(soglia_minima)
            if soglia_minima < 0:
                raise ValueError("soglia_minima non puo' essere negativa")
        ana = self._ana()
        try:
            eid, _ = self._articolo_attivo(ana, entita_id)
        finally:
            ana.close()
        return busta.scrivi(con, self.tabella_soglie, tipo=self.evento_soglia,
                            manifest=self.manifest, entita_id=eid, attore_id=attore_id,
                            dati={"soglia_minima": soglia_minima})

    # --- letture (proiezioni, a tempo di lettura) ---------------------------------

    def giacenza(self, con, entita_id: str | None = None):
        """Giacenze correnti degli articoli ATTIVI del tipo, per entita' canonica.

        entita_id=None -> lista per codice; un ID -> dict, o None se l'articolo
        non e' attivo o non esiste. Codice, descrizione e unita' (attributo
        `unita`) vengono dall'anagrafica: una rinomina si vede subito.
        """
        ana = self._ana()
        try:
            canonici = _anagrafica().mappa_canonici(ana)
            if entita_id is not None:
                cid = canonici.get(entita_id, entita_id)
                e = _anagrafica().entita(ana, cid)
                articoli = [e] if e and e["tipo"] == self.tipo and e["stato"] == "ATTIVO" \
                    else []
            else:
                articoli = _anagrafica().elenco(ana, self.tipo)
        finally:
            ana.close()
        saldi: dict[str, float] = {}
        for r in con.execute(f"SELECT entita_id, giacenza FROM {self._sal}"):
            cid = canonici.get(r[0], r[0])
            saldi[cid] = saldi.get(cid, 0.0) + r[1]
        soglie = self._soglie(con)
        righe = [{"entita_id": a["id"], "codice": a["codice"],
                  "descrizione": a["descrizione"], "unita": a["attributi"].get("unita"),
                  "soglia_minima": soglie.get(a["id"]),
                  "giacenza": saldi.get(a["id"], 0.0)} for a in articoli]
        if entita_id is not None:
            return righe[0] if righe else None
        return righe

    def sotto_scorta(self, con) -> list[dict]:
        """Articoli con giacenza <= soglia_minima (a tempo di lettura; senza
        soglia non compaiono mai)."""
        return [r for r in self.giacenza(con)
                if r["soglia_minima"] is not None and r["giacenza"] <= r["soglia_minima"]]

    def storico(self, con, entita_id: str) -> list[dict]:
        """Movimenti di un articolo (e di quelli fusi in lui), in ordine."""
        ana = self._ana()
        try:
            ids = _anagrafica().equivalenti(ana, entita_id)
        finally:
            ana.close()
        return [dict(r) for r in con.execute(
            f"SELECT * FROM {self._mov} WHERE entita_id IN ({','.join('?' * len(ids))}) "
            f"ORDER BY id", ids)]

    # --- adozione di un inventario 0.x ----------------------------------------------

    def esporta_articoli_0x(self, con, file: str | Path, *,
                            tabella_articoli: str = "articoli") -> int:
        """CSV (codice;descrizione;unita) degli articoli 0.x, da importare con
        `python -m core.anagrafica importa --tipo <tipo> <file>`. Ritorna le righe."""
        righe = con.execute(f"SELECT codice, descrizione, unita FROM "
                            f"{ident(tabella_articoli)} ORDER BY codice").fetchall()
        with open(file, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["codice", "descrizione", "unita"])
            w.writerows([tuple(r) for r in righe])
        return len(righe)

    def adotta_0x(self, con, *, tabella_articoli: str = "articoli",
                  tabella_movimenti: str = "movimenti",
                  attore_id: str = busta.SISTEMA) -> dict:
        """Copia i movimenti 0.x nel log nuovo (idempotente: `id_0x`), con l'ID
        di anagrafica risolto dal codice e l'ora locale di allora. Le soglie
        0.x diventano eventi di soglia; gli articoli 0.x disattivati si
        segnalano (vanno resi obsoleti in anagrafica, dalla shell).
        Le tabelle 0.x restano intatte."""
        art, mov = ident(tabella_articoli), ident(tabella_movimenti)
        esito = {"movimenti": 0, "soglie": 0, "gia_adottati": 0,
                 "codici_non_trovati": [], "disattivati": []}
        ana = self._ana()
        try:
            def risolvi(codice):
                r = _anagrafica().risolvi(ana, self.tipo, codice)
                return r.id if r else None
            ids = {}
            for a in con.execute(f"SELECT * FROM {art} ORDER BY codice").fetchall():
                eid = ids[a["codice"]] = risolvi(a["codice"])
                if eid is None:
                    esito["codici_non_trovati"].append(a["codice"])
                    continue
                if not a["attivo"]:
                    esito["disattivati"].append(a["codice"])
                if a["soglia_minima"] is not None and eid not in self._soglie(con):
                    busta.scrivi(con, self.tabella_soglie, tipo=self.evento_soglia,
                                 manifest=self.manifest, entita_id=eid,
                                 attore_id=attore_id,
                                 dati={"soglia_minima": a["soglia_minima"]})
                    esito["soglie"] += 1
        finally:
            ana.close()
        fatti = {r[0] for r in con.execute(
            f"SELECT id_0x FROM {self._mov} WHERE id_0x IS NOT NULL")}
        for m in con.execute(f"SELECT * FROM {mov} ORDER BY id").fetchall():
            if m["id"] in fatti:
                esito["gia_adottati"] += 1
                continue
            eid = ids.get(m["codice"])
            if eid is None:
                continue
            m = dict(m)
            note = "; ".join(x for x in (m.get("note"),
                                         f"operatore: {m['operatore']}"
                                         if m.get("operatore") else None) if x)
            busta.scrivi(con, self.tabella_movimenti, tipo=self.evento_movimento,
                         manifest=self.manifest, entita_id=eid, attore_id=attore_id,
                         sorgente=m.get("sorgente") or "MANUALE",
                         ora=datetime.fromisoformat(m["ts"]).astimezone(),
                         dati={"quantita": m["quantita"], "causale": m["causale"],
                               "note": note or None, "id_0x": m["id"]})
            esito["movimenti"] += 1
        return esito

    # --- interni -----------------------------------------------------------------

    def _ana(self):
        if not self.anagrafica.exists():
            raise ValueError(f"{self.anagrafica} non trovato: avvia prima la shell")
        return coredb.readonly(self.anagrafica)

    def _articolo_attivo(self, ana, entita_id: str) -> tuple[str, dict]:
        eid = _anagrafica().canonico(ana, entita_id)
        e = _anagrafica().entita(ana, eid)
        if e is None:
            raise ValueError(f"articolo sconosciuto in anagrafica: {entita_id!r}")
        if e["tipo"] != self.tipo:
            raise ValueError(f"{e['codice']!r} e' di tipo {e['tipo']!r}, non {self.tipo!r}")
        if e["stato"] != "ATTIVO":
            raise ValueError(f"articolo {e['codice']!r} non attivo ({e['stato']})")
        return eid, e

    def _somma(self, con, ids: list[str]) -> float:
        return con.execute(
            f"SELECT COALESCE(SUM(quantita), 0) FROM {self._mov} "
            f"WHERE entita_id IN ({','.join('?' * len(ids))})", ids).fetchone()[0]

    def _soglie(self, con) -> dict[str, float | None]:
        """{entita_id: ultima soglia} (None = soglia tolta)."""
        return {r[0]: r[1] for r in con.execute(
            f"SELECT s.entita_id, s.soglia_minima FROM {self._sog} s WHERE s.id = "
            f"(SELECT MAX(x.id) FROM {self._sog} x WHERE x.entita_id = s.entita_id)")}
