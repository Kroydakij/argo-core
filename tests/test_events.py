"""Test di core.events (layer event-sourced). Solo stdlib."""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import events  # noqa: E402


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

    def test_registra_e_proiezione_ultimo_vince(self):
        events.registra(self.con, "PRESSA-01", "LIBERA")
        events.registra(self.con, "PRESSA-01", "IN_USO", operatore="rossi")
        st = events.stato_corrente(self.con, "PRESSA-01")
        self.assertEqual(st["stato"], "IN_USO")          # l'ultimo evento vince
        self.assertEqual(st["operatore"], "rossi")

    def test_append_only_storico_intatto(self):
        for s in ("LIBERA", "IN_USO", "LIBERA"):
            events.registra(self.con, "M1", s)
        sto = events.storico(self.con, "M1")
        self.assertEqual([r["stato"] for r in sto], ["LIBERA", "IN_USO", "LIBERA"])
        # la proiezione mostra l'ultimo, lo storico li conserva tutti
        self.assertEqual(events.stato_corrente(self.con, "M1")["stato"], "LIBERA")

    def test_stato_corrente_tutte_e_sconosciuta(self):
        events.registra(self.con, "A", "X")
        events.registra(self.con, "B", "Y")
        tutti = events.stato_corrente(self.con)
        self.assertEqual({r["entita"]: r["stato"] for r in tutti}, {"A": "X", "B": "Y"})
        self.assertIsNone(events.stato_corrente(self.con, "MAI_VISTA"))

    def test_sorgente_sensore_e_invalida(self):
        events.registra(self.con, "T", "ON", sorgente="SENSORE")
        self.assertEqual(events.stato_corrente(self.con, "T")["sorgente"], "SENSORE")
        with self.assertRaises(ValueError):
            events.registra(self.con, "T", "ON", sorgente="INVENTATA")

    def test_check_sorgente_a_livello_db(self):
        # difesa in profondita': il CHECK del DB rifiuta anche un INSERT diretto
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute(
                "INSERT INTO eventi (entita, stato, sorgente) VALUES ('X','Y','BOH')")

    def test_colonne_extra(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        events.migra(con, extra_colonne={"valore": "REAL"})
        events.registra(con, "SENS-1", "LETTO", sorgente="SENSORE",
                        extra={"valore": 42.5})
        self.assertEqual(events.stato_corrente(con, "SENS-1")["valore"], 42.5)

    def test_identificatori_maligni(self):
        for cattivo in ("t; DROP TABLE eventi", 'a"b', ""):
            with self.assertRaises(ValueError):
                events.registra(self.con, "X", "Y", table=cattivo)
        with self.assertRaises(ValueError):
            events.registra(self.con, "X", "Y", extra={"bad; drop": 1})


if __name__ == "__main__":
    unittest.main()
