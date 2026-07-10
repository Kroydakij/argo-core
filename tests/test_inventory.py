"""Test di core.inventory (inventario generico event-sourced). Solo stdlib."""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import inventory  # noqa: E402

CAUSALI = {"CARICO": "+", "CONSUMO": "-", "RETTIFICA_PIU": "+", "RETTIFICA_MENO": "-"}


def _con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")   # come db.owned
    return con


class TestCostruzione(unittest.TestCase):
    def test_fail_fast(self):
        with self.assertRaises(ValueError):
            inventory.Inventario({})                          # causali vuote
        with self.assertRaises(ValueError):
            inventory.Inventario({"X": "su"})                 # verso non valido
        with self.assertRaises(ValueError):
            inventory.Inventario(CAUSALI, tabella_movimenti="m; DROP TABLE m")
        with self.assertRaises(ValueError):
            inventory.Inventario.da_config({"consenti_negativo": True})  # senza causali

    def test_da_config(self):
        inv = inventory.Inventario.da_config(
            {"causali": CAUSALI, "consenti_negativo": True})
        self.assertTrue(inv.consenti_negativo)
        self.assertEqual(inv.causali["CONSUMO"], "-")


class TestInventario(unittest.TestCase):
    def setUp(self):
        self.inv = inventory.Inventario(CAUSALI)
        self.con = _con()
        self.inv.migra(self.con)
        self.inv.crea_articolo(self.con, "A-1", "Guanti", unita="paia",
                               soglia_minima=10)

    def test_migra_crea_tabelle_e_vista(self):
        tab = {r[0] for r in self.con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        vis = {r[0] for r in self.con.execute(
            "SELECT name FROM sqlite_master WHERE type='view'")}
        self.assertIn("articoli", tab)
        self.assertIn("movimenti", tab)
        self.assertIn("giacenze", vis)
        self.inv.migra(self.con)                              # ri-eseguibile

    def test_giacenza_come_proiezione(self):
        self.assertEqual(self.inv.giacenza(self.con, "A-1")["giacenza"], 0)
        self.inv.movimenta(self.con, "A-1", 50, "CARICO", operatore="rossi")
        self.inv.movimenta(self.con, "A-1", 2, "CONSUMO")
        g = self.inv.giacenza(self.con, "A-1")
        self.assertEqual(g["giacenza"], 48)
        self.assertEqual(g["unita"], "paia")
        sto = self.inv.storico(self.con, "A-1")
        self.assertEqual([m["quantita"] for m in sto], [50.0, -2.0])  # segni da causale

    def test_movimenti_rifiutati(self):
        with self.assertRaises(ValueError):
            self.inv.movimenta(self.con, "A-1", 1, "INVENTATA")     # causale ignota
        with self.assertRaises(ValueError):
            self.inv.movimenta(self.con, "A-1", -5, "CARICO")       # quantita negativa
        with self.assertRaises(ValueError):
            self.inv.movimenta(self.con, "A-1", 0, "CARICO")        # quantita zero
        with self.assertRaises(ValueError):
            self.inv.movimenta(self.con, "A-1", 1, "CARICO", sorgente="BOH")
        with self.assertRaises(ValueError):
            self.inv.movimenta(self.con, "MAI-VISTO", 1, "CARICO")  # non in anagrafica

    def test_giacenza_insufficiente(self):
        self.inv.movimenta(self.con, "A-1", 5, "CARICO")
        with self.assertRaises(inventory.GiacenzaInsufficiente):
            self.inv.movimenta(self.con, "A-1", 6, "CONSUMO")
        # con consenti_negativo lo stesso scarico passa
        inv2 = inventory.Inventario(CAUSALI, consenti_negativo=True)
        con2 = _con()
        inv2.migra(con2)
        inv2.crea_articolo(con2, "B-1")
        inv2.movimenta(con2, "B-1", 6, "CONSUMO")
        self.assertEqual(inv2.giacenza(con2, "B-1")["giacenza"], -6)

    def test_sotto_scorta_a_tempo_di_lettura(self):
        self.inv.crea_articolo(self.con, "A-2", "Senza soglia")     # mai segnalato
        self.inv.movimenta(self.con, "A-1", 11, "CARICO")
        self.assertEqual(self.inv.sotto_scorta(self.con), [])       # 11 > 10
        self.inv.movimenta(self.con, "A-1", 1, "CONSUMO")           # 10 <= 10
        sotto = self.inv.sotto_scorta(self.con)
        self.assertEqual([r["codice"] for r in sotto], ["A-1"])

    def test_upsert_articolo_e_disattivazione(self):
        self.inv.crea_articolo(self.con, "A-1", "Guanti nitrile", soglia_minima=5)
        arts = self.inv.lista_articoli(self.con)
        self.assertEqual(len(arts), 1)                              # upsert, non duplica
        self.assertEqual(arts[0]["descrizione"], "Guanti nitrile")
        self.inv.movimenta(self.con, "A-1", 3, "CARICO")
        self.inv.disattiva_articolo(self.con, "A-1")
        self.assertIsNone(self.inv.giacenza(self.con, "A-1"))       # sparisce dalla vista
        self.assertEqual(len(self.inv.storico(self.con, "A-1")), 1)  # storico intatto
        with self.assertRaises(ValueError):
            self.inv.movimenta(self.con, "A-1", 1, "CARICO")        # disattivato

    def test_check_sorgente_a_livello_db(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute(
                "INSERT INTO movimenti (codice, quantita, causale, sorgente) "
                "VALUES ('A-1', 1, 'CARICO', 'BOH')")

    def test_colonne_extra(self):
        inv = inventory.Inventario(CAUSALI)
        con = _con()
        inv.migra(con, extra_movimenti={"commessa": "TEXT"})
        inv.crea_articolo(con, "C-1")
        inv.movimenta(con, "C-1", 2, "CARICO", extra={"commessa": "K42"})
        self.assertEqual(inv.storico(con, "C-1")[0]["commessa"], "K42")
        with self.assertRaises(ValueError):
            inv.movimenta(con, "C-1", 1, "CARICO", extra={"bad; drop": 1})


if __name__ == "__main__":
    unittest.main()
