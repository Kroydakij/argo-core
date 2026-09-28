"""Test di core.events (log di stati sulla busta, ADR-005). Solo stdlib."""
import sqlite3
import sys
import unittest
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import busta, events, manifest  # noqa: E402

M = manifest.da_dict({
    "modulo": {"nome": "impianto", "versione": "1.0.0", "core": ">=1.0", "titolo": "I"},
    "anagrafica": {"tipi": ["macchina"]},
    "eventi": [{"tipo": "impianto.cambio_stato", "versione": 1, "entita": "macchina",
                "descrizione": "cambio stato"}],
})
T = "impianto.cambio_stato"


def reg(con, entita, stato, **kw):
    kw.setdefault("attore_id", "u-1")
    return events.registra(con, T, entita, stato, manifest=M, **kw)


class TestEvents(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        events.migra(self.con)

    def test_migra_crea_log_e_vista(self):
        tab = {r[0] for r in self.con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        vis = {r[0] for r in self.con.execute(
            "SELECT name FROM sqlite_master WHERE type='view'")}
        self.assertIn("eventi", tab)
        self.assertIn("latest_state_per_entity", vis)
        events.migra(self.con)                              # ri-eseguibile

    def test_registra_con_busta_e_ultimo_vince(self):
        reg(self.con, "PRESSA-01", "LIBERA")
        reg(self.con, "PRESSA-01", "IN_USO", attore_id="u-2", note="turno A")
        st = events.stato_corrente(self.con, "PRESSA-01")
        self.assertEqual((st["stato"], st["attore_id"], st["note"], st["tipo"],
                          st["versione"], st["entita"]),
                         ("IN_USO", "u-2", "turno A", T, 1, "PRESSA-01"))
        self.assertTrue(st["ts_utc"].endswith("Z"))

    def test_append_only_storico_intatto(self):
        for s in ("LIBERA", "IN_USO", "LIBERA"):
            reg(self.con, "M1", s)
        self.assertEqual([r["stato"] for r in events.storico(self.con, "M1")],
                         ["LIBERA", "IN_USO", "LIBERA"])
        self.assertEqual(events.stato_corrente(self.con, "M1")["stato"], "LIBERA")

    def test_stato_corrente_tutte_e_sconosciuta(self):
        reg(self.con, "A", "X")
        reg(self.con, "B", "Y")
        self.assertEqual({r["entita"]: r["stato"] for r in events.stato_corrente(self.con)},
                         {"A": "X", "B": "Y"})
        self.assertIsNone(events.stato_corrente(self.con, "MAI_VISTA"))

    def test_sorgente_e_tipo(self):
        reg(self.con, "T", "ON", sorgente="SENSORE")
        self.assertEqual(events.stato_corrente(self.con, "T")["sorgente"], "SENSORE")
        with self.assertRaises(busta.BustaError):
            reg(self.con, "T", "ON", sorgente="INVENTATA")
        with self.assertRaises(busta.BustaError):
            events.registra(self.con, "impianto.altro", "T", "ON", manifest=M,
                            attore_id="u")

    def test_colonne_extra(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        events.migra(con, extra_colonne={"valore": "REAL"})
        reg(con, "SENS-1", "LETTO", sorgente="SENSORE", extra={"valore": 42.5})
        self.assertEqual(events.stato_corrente(con, "SENS-1")["valore"], 42.5)

    def test_operatore_deprecato_finisce_nelle_note(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            reg(self.con, "M1", "ON", operatore="rossi")
        self.assertTrue(any(issubclass(x.category, DeprecationWarning) for x in w))
        self.assertEqual(events.stato_corrente(self.con, "M1")["note"], "operatore: rossi")

    def test_identificatori_maligni(self):
        for cattivo in ("t; DROP TABLE eventi", 'a"b', ""):
            with self.assertRaises(ValueError):
                reg(self.con, "X", "Y", table=cattivo)
        with self.assertRaises(ValueError):
            reg(self.con, "X", "Y", extra={"bad; drop": 1})


class TestLog0x(unittest.TestCase):
    """Un log eventi creato dalla 0.4 viene adottato senza riscrivere nulla."""

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.execute("""CREATE TABLE eventi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            entita TEXT NOT NULL, stato TEXT NOT NULL,
            sorgente TEXT NOT NULL DEFAULT 'MANUALE' CHECK (sorgente IN ('MANUALE','SENSORE')),
            operatore TEXT, note TEXT)""")
        self.con.executemany("INSERT INTO eventi (ts, entita, stato, operatore) VALUES (?,?,?,?)",
                             [("2026-03-01 08:00:00", "P1", "LIBERA", "rossi"),
                              ("2026-03-01 09:00:00", "P1", "IN_USO", "bianchi"),
                              ("2026-03-01 09:30:00", "P2", "GUASTA", "rossi")])
        events.migra(self.con)

    def test_righe_vecchie_leggibili(self):
        st = events.stato_corrente(self.con, "P1")
        self.assertEqual((st["stato"], st["operatore"], st["versione"]),
                         ("IN_USO", "bianchi", None))
        self.assertTrue(busta.e_legacy(st))
        self.assertEqual(len(events.storico(self.con, "P1")), 2)

    def test_evento_nuovo_sopra_le_vecchie(self):
        reg(self.con, "P1", "LIBERA")
        st = events.stato_corrente(self.con, "P1")
        self.assertEqual((st["stato"], st["versione"], st["entita_id"], st["entita"]),
                         ("LIBERA", 1, "P1", "P1"))
        sto = events.storico(self.con, "P1")
        self.assertEqual([busta.e_legacy(r) for r in sto], [True, True, False])
        self.assertEqual({r["entita"] for r in events.stato_corrente(self.con)},
                         {"P1", "P2"})


if __name__ == "__main__":
    unittest.main()
