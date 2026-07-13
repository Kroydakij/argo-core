"""
core.inventory — inventario generico event-sourced: anagrafica + movimenti.

Il pattern, senza alcun dominio dentro: un'anagrafica di articoli (registro,
come gli utenti di core.auth) e un log di MOVIMENTI append-only; la giacenza
non e' un campo aggiornabile ma la SOMMA dei movimenti (proiezione, vista
`giacenze`). Stessa filosofia di core.events: lo storico e' la verita',
lo stato e' una funzione di esso.

Cosa decide il MODULO (in config TOML), non il core:
  - quali causali di movimento esistono e con che verso ("+" carico, "-" scarico);
  - se la giacenza puo' andare sotto zero;
  - quali articoli, con che unita' e soglie.

    [inventario]
    consenti_negativo = false
    [inventario.causali]
    CARICO = "+"
    CONSUMO = "-"
    RETTIFICA_PIU = "+"
    RETTIFICA_MENO = "-"

Uso in un modulo proprietario del proprio DB:

    from core import db, inventory
    inv = inventory.Inventario.da_config(cfg["inventario"])

    def migrate_db():
        con = db.owned(DB_PATH); inv.migra(con); con.commit(); con.close()

    inv.crea_articolo(con, "000000252", "Guanti taglia M", unita="paia",
                      soglia_minima=10)
    inv.movimenta(con, "000000252", 50, "CARICO", operatore="rossi")
    inv.movimenta(con, "000000252", 2, "CONSUMO", operatore="bianchi")
    inv.giacenza(con, "000000252")       # -> {..., "giacenza": 48.0}
    inv.sotto_scorta(con)                # -> articoli con giacenza <= soglia

Regole rese automatiche:
  - movimenti append-only: l'unica scrittura e' movimenta() (single
    write-point, solo INSERT); le correzioni sono movimenti di rettifica,
    mai UPDATE/DELETE.
  - sorgente vincolata MANUALE/SENSORE (in Python e con CHECK a livello DB).
  - identificatori SQL validati prima di ogni interpolazione (migrate._ident).
  - la normalizzazione dei codici resta compito del modulo, con core.codes,
    PRIMA di chiamare queste funzioni (unico punto di normalizzazione).
"""
from __future__ import annotations

from typing import Any

from .events import SORGENTI
from .migrate import _ident


class GiacenzaInsufficiente(Exception):
    """Scarico rifiutato: porterebbe la giacenza sotto zero. Alzata da movimenta()."""


class Inventario:
    """Inventario generico, validato alla costruzione (fail-fast).

    causali: {nome: "+"|"-"} — il verso e' fisso per causale; movimenta()
    riceve sempre quantita' positive e applica il segno della causale.
    """

    def __init__(self, causali: dict[str, str], *, consenti_negativo: bool = False,
                 tabella_articoli: str = "articoli",
                 tabella_movimenti: str = "movimenti",
                 vista_giacenze: str = "giacenze"):
        if not isinstance(causali, dict) or not causali:
            raise ValueError("causali deve essere un dict non vuoto {nome: '+'|'-'}")
        for nome, verso in causali.items():
            if verso not in ("+", "-"):
                raise ValueError(
                    f"verso non valido per la causale {nome!r}: {verso!r} "
                    f"(ammessi '+' e '-')")
        self.causali = dict(causali)
        self.consenti_negativo = bool(consenti_negativo)
        # nomi validati subito (fail-fast): finiscono interpolati nei DDL/DML.
        # Si tengono sia la forma grezza (per gli helper di migrate) sia quella
        # quotata (per l'SQL composto qui).
        self.tabella_articoli = tabella_articoli
        self.tabella_movimenti = tabella_movimenti
        self.vista_giacenze = vista_giacenze
        self._art = _ident(tabella_articoli)
        self._mov = _ident(tabella_movimenti)
        self._gia = _ident(vista_giacenze)

    @classmethod
    def da_config(cls, sezione: dict) -> "Inventario":
        """Costruisce dalla sezione [inventario] della config. Fail-fast."""
        try:
            return cls(sezione["causali"],
                       consenti_negativo=sezione.get("consenti_negativo", False))
        except KeyError as e:
            raise ValueError(f"config inventario: chiave mancante {e}") from e

    # --- migrazione ------------------------------------------------------

    def migra(self, con, *, extra_articoli: dict[str, str] | None = None,
              extra_movimenti: dict[str, str] | None = None) -> None:
        """Crea/aggiorna anagrafica, log movimenti e vista giacenze. Additiva.

        extra_*: {colonna: tipo_ddl} colonne aggiuntive del modulo, idempotenti.
        La vista viene ricreata SEMPRE alla fine.
        """
        from . import migrate
        check = ",".join(f"'{s}'" for s in SORGENTI)
        migrate.ensure_table(con, f"""CREATE TABLE IF NOT EXISTS {self._art} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codice TEXT NOT NULL UNIQUE,
            descrizione TEXT NOT NULL DEFAULT '',
            unita TEXT NOT NULL DEFAULT 'pz',
            soglia_minima REAL,
            attivo INTEGER NOT NULL DEFAULT 1,
            creato_il TEXT DEFAULT (datetime('now','localtime'))
        )""")
        migrate.ensure_table(con, f"""CREATE TABLE IF NOT EXISTS {self._mov} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            codice TEXT NOT NULL REFERENCES {self._art}(codice),
            quantita REAL NOT NULL,
            causale TEXT NOT NULL,
            sorgente TEXT NOT NULL DEFAULT 'MANUALE' CHECK (sorgente IN ({check})),
            operatore TEXT,
            note TEXT
        )""")
        for col, tipo in (extra_articoli or {}).items():
            migrate.ensure_column(con, self.tabella_articoli, col, tipo)
        for col, tipo in (extra_movimenti or {}).items():
            migrate.ensure_column(con, self.tabella_movimenti, col, tipo)
        migrate.rebuild_views(con, {self.vista_giacenze: f"""
            CREATE VIEW {self._gia} AS
            SELECT a.codice, a.descrizione, a.unita, a.soglia_minima,
                   COALESCE(SUM(m.quantita), 0) AS giacenza
            FROM {self._art} a
            LEFT JOIN {self._mov} m ON m.codice = a.codice
            WHERE a.attivo = 1
            GROUP BY a.codice"""})

    # --- anagrafica (registro: upsert ammesso, come gli utenti di auth) ---

    def crea_articolo(self, con, codice: str, descrizione: str = "",
                      unita: str = "pz", soglia_minima: float | None = None) -> None:
        """Crea o aggiorna un articolo (upsert su codice)."""
        con.execute(
            f"INSERT INTO {self._art} (codice, descrizione, unita, soglia_minima) "
            f"VALUES (?,?,?,?) ON CONFLICT(codice) DO UPDATE SET "
            f"descrizione=excluded.descrizione, unita=excluded.unita, "
            f"soglia_minima=excluded.soglia_minima",
            (str(codice).strip(), descrizione.strip(), unita.strip(), soglia_minima))
        con.commit()

    def disattiva_articolo(self, con, codice: str, *, attivo: bool = False) -> None:
        """Disattiva (o riattiva) un articolo: sparisce dalle giacenze, lo
        storico movimenti resta intatto."""
        con.execute(f"UPDATE {self._art} SET attivo=? WHERE codice=?",
                    (1 if attivo else 0, str(codice).strip()))
        con.commit()

    def lista_articoli(self, con, *, solo_attivi: bool = True) -> list[dict]:
        filtro = "WHERE attivo=1 " if solo_attivi else ""
        return [dict(r) for r in con.execute(
            f"SELECT * FROM {self._art} {filtro}ORDER BY codice")]

    # --- movimenti (append-only, single write-point) -----------------------

    def movimenta(self, con, codice: str, quantita: float, causale: str, *,
                  sorgente: str = "MANUALE", operatore: str | None = None,
                  note: str | None = None, extra: dict[str, Any] | None = None) -> int:
        """SINGLE WRITE-POINT dei movimenti: appende un movimento, ritorna l'id.

        quantita e' SEMPRE positiva: il segno lo mette il verso della causale.
        Rifiuta: causale ignota, quantita <= 0, sorgente non ammessa, articolo
        inesistente o disattivato, scarico che porterebbe la giacenza sotto
        zero (salvo consenti_negativo).
        """
        codice = str(codice).strip()
        if causale not in self.causali:
            raise ValueError(f"causale sconosciuta: {causale!r} "
                             f"(ammesse: {sorted(self.causali)})")
        q = float(quantita)
        if q <= 0:
            raise ValueError(f"quantita deve essere positiva, non {quantita!r} "
                             f"(il segno lo da' la causale)")
        if sorgente not in SORGENTI:
            raise ValueError(f"sorgente non valida: {sorgente!r} (ammesse: {SORGENTI})")
        art = con.execute(f"SELECT attivo FROM {self._art} WHERE codice=?",
                          (codice,)).fetchone()
        if art is None:
            raise ValueError(f"articolo sconosciuto: {codice!r} (va creato prima)")
        if not art[0]:
            raise ValueError(f"articolo disattivato: {codice!r}")

        delta = q if self.causali[causale] == "+" else -q
        if delta < 0 and not self.consenti_negativo:
            attuale = con.execute(
                f"SELECT COALESCE(SUM(quantita),0) FROM {self._mov} WHERE codice=?",
                (codice,)).fetchone()[0]
            if attuale + delta < 0:
                raise GiacenzaInsufficiente(
                    f"{codice}: giacenza {attuale}, scarico {q} rifiutato")

        cols = ["codice", "quantita", "causale", "sorgente", "operatore", "note"]
        vals: list[Any] = [codice, delta, causale, sorgente, operatore, note]
        for nome, valore in (extra or {}).items():
            cols.append(nome)
            vals.append(valore)
        collist = ",".join(_ident(c) for c in cols)
        cur = con.execute(
            f"INSERT INTO {self._mov} ({collist}) VALUES ({','.join('?' * len(vals))})",
            vals)
        con.commit()
        return cur.lastrowid

    # --- letture (proiezioni, a tempo di lettura) --------------------------

    def giacenza(self, con, codice: str | None = None):
        """Giacenze correnti via proiezione (solo articoli attivi).

        codice=None -> lista completa; codice="X" -> dict o None se non attivo/ignoto.
        """
        if codice is None:
            return [dict(r) for r in con.execute(
                f"SELECT * FROM {self._gia} ORDER BY codice")]
        r = con.execute(f"SELECT * FROM {self._gia} WHERE codice=?",
                        (str(codice).strip(),)).fetchone()
        return dict(r) if r else None

    def sotto_scorta(self, con) -> list[dict]:
        """Articoli con giacenza <= soglia_minima (calcolato a tempo di lettura;
        gli articoli senza soglia non compaiono mai)."""
        return [dict(r) for r in con.execute(
            f"SELECT * FROM {self._gia} "
            f"WHERE soglia_minima IS NOT NULL AND giacenza <= soglia_minima "
            f"ORDER BY codice")]

    def storico(self, con, codice: str) -> list[dict]:
        """Storico movimenti di un articolo, in ordine cronologico."""
        return [dict(r) for r in con.execute(
            f"SELECT * FROM {self._mov} WHERE codice=? ORDER BY id",
            (str(codice).strip(),))]
