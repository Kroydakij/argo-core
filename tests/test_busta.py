"""Test di core.busta (ADR-005). Solo stdlib."""
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import busta, manifest, migrate  # noqa: E402
from core.busta import BustaError  # noqa: E402

M = manifest.da_dict({
    "modulo": {"nome": "andon", "versione": "1.0.0", "core": ">=1.0", "titolo": "A"},
    "anagrafica": {"tipi": ["macchina"]},
    "eventi": [
        {"tipo": "andon.fermata_chiusa", "versione": 3, "entita": "macchina",
         "descrizione": "chiusura"},
        {"tipo": "andon.turno_chiuso", "versione": 1, "descrizione": "senza entita"},
    ],
})

ROMA_ESTATE = timezone(timedelta(hours=2))
ROMA_INVERNO = timezone(timedelta(hours=1))


class TestBusta(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        busta.crea_log(self.con, "fermate", {"causale": "TEXT", "durata_min": "REAL"})

    def riga(self, i):
        return dict(self.con.execute("SELECT * FROM fermate WHERE id=?", (i,)).fetchone())

    def test_schema(self):
        cols = migrate.table_columns(self.con, "fermate")
        self.assertTrue(set(busta.COLONNE) | {"causale", "durata_min"} <= cols)
        busta.crea_log(self.con, "fermate", {"causale": "TEXT", "extra": "TEXT"})  # idempotente
        self.assertIn("extra", migrate.table_columns(self.con, "fermate"))

    def test_scrivi_riempie_la_busta(self):
        i = busta.scrivi(self.con, "fermate", tipo="andon.fermata_chiusa", manifest=M,
                         entita_id="MAC-1", attore_id="u-1",
                         dati={"causale": "GUASTO", "durata_min": 12.5})
        r = self.riga(i)
        self.assertEqual((r["tipo"], r["versione"], r["attore_id"], r["entita_id"],
                          r["sorgente"], r["causale"], r["durata_min"]),
                         ("andon.fermata_chiusa", 3, "u-1", "MAC-1", "MANUALE",
                          "GUASTO", 12.5))
        self.assertEqual(len(r["uid"]), 36)
        self.assertRegex(r["ts_utc"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
        self.assertIsInstance(r["offset_min"], int)

    def test_tipo_ed_entita_dal_manifest(self):
        with self.assertRaises(BustaError):          # tipo non dichiarato
            busta.scrivi(self.con, "fermate", tipo="andon.boh", manifest=M,
                         attore_id="u")
        with self.assertRaises(BustaError):          # entita' obbligatoria
            busta.scrivi(self.con, "fermate", tipo="andon.fermata_chiusa",
                         manifest=M, attore_id="u")
        with self.assertRaises(BustaError):          # entita' vietata
            busta.scrivi(self.con, "fermate", tipo="andon.turno_chiuso",
                         manifest=M, attore_id="u", entita_id="X")
        busta.scrivi(self.con, "fermate", tipo="andon.turno_chiuso", manifest=M,
                     attore_id="u")

    def test_attore_mai_dedotto(self):
        with self.assertRaises(BustaError):          # fuori da una richiesta
            busta.scrivi(self.con, "fermate", tipo="andon.turno_chiuso", manifest=M)
        busta.scrivi(self.con, "fermate", tipo="andon.turno_chiuso", manifest=M,
                     attore_id=busta.SISTEMA)

    def test_sorgente_e_colonne(self):
        with self.assertRaises(BustaError):
            busta.scrivi(self.con, "fermate", tipo="andon.turno_chiuso", manifest=M,
                         attore_id="u", sorgente="INVENTATA")
        with self.assertRaises(BustaError):          # colonna della busta nei dati
            busta.scrivi(self.con, "fermate", tipo="andon.turno_chiuso", manifest=M,
                         attore_id="u", dati={"tipo": "x"})
        with self.assertRaises(ValueError):          # identificatore maligno
            busta.scrivi(self.con, "fermate", tipo="andon.turno_chiuso", manifest=M,
                         attore_id="u", dati={"x; drop": 1})
        with self.assertRaises(sqlite3.IntegrityError):   # CHECK nel DB
            self.con.execute("INSERT INTO fermate (uid, tipo, versione, ts_utc, "
                             "offset_min, attore_id, sorgente) "
                             "VALUES ('u','t',1,'x',0,'a','BOH')")

    def test_ora_locale_e_cambio_ora_legale(self):
        # 25/10/2026: alle 03:00 locali si torna alle 02:00. Le due "02:30"
        # sono istanti diversi: con UTC + offset restano distinguibili e ordinati.
        prima = datetime(2026, 10, 25, 2, 30, tzinfo=ROMA_ESTATE)
        dopo = datetime(2026, 10, 25, 2, 30, tzinfo=ROMA_INVERNO)
        i1 = busta.scrivi(self.con, "fermate", tipo="andon.turno_chiuso", manifest=M,
                          attore_id="u", ora=prima)
        i2 = busta.scrivi(self.con, "fermate", tipo="andon.turno_chiuso", manifest=M,
                          attore_id="u", ora=dopo)
        r1, r2 = self.riga(i1), self.riga(i2)
        self.assertEqual((r1["ts_utc"], r1["offset_min"]), ("2026-10-25T00:30:00.000Z", 120))
        self.assertEqual((r2["ts_utc"], r2["offset_min"]), ("2026-10-25T01:30:00.000Z", 60))
        self.assertLess(r1["ts_utc"], r2["ts_utc"])
        l1, l2 = busta.ora_locale(r1), busta.ora_locale(r2)
        self.assertEqual((l1.hour, l1.minute, l2.hour, l2.minute), (2, 30, 2, 30))
        self.assertEqual(l1.utcoffset(), timedelta(hours=2))
        with self.assertRaises(BustaError):
            busta.scrivi(self.con, "fermate", tipo="andon.turno_chiuso", manifest=M,
                         attore_id="u", ora=datetime(2026, 1, 1))     # naive: no

    def test_attore_dalla_sessione_flask(self):
        try:
            from flask import Flask, g
        except ImportError:
            self.skipTest("Flask non installato")
        app = Flask(__name__)
        with app.test_request_context():
            g.utente = {"id": "u-sessione"}
            i = busta.scrivi(self.con, "fermate", tipo="andon.turno_chiuso", manifest=M)
        self.assertEqual(self.riga(i)["attore_id"], "u-sessione")


class TestAdozione0x(unittest.TestCase):
    def test_log_0x_prende_la_busta_senza_riscrivere(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.execute("CREATE TABLE vecchio (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "ts TEXT NOT NULL DEFAULT (datetime('now','localtime')), "
                    "entita TEXT NOT NULL, stato TEXT NOT NULL, "
                    "sorgente TEXT NOT NULL DEFAULT 'MANUALE', operatore TEXT)")
        con.execute("INSERT INTO vecchio (ts, entita, stato, operatore) "
                    "VALUES ('2026-03-01 08:15:00', 'P1', 'ON', 'rossi')")
        prima = dict(con.execute("SELECT * FROM vecchio").fetchone())
        aggiunte = busta.aggiungi_busta(con, "vecchio")
        self.assertIn("ts_utc", aggiunte)
        self.assertEqual(busta.aggiungi_busta(con, "vecchio"), [])      # idempotente
        dopo = dict(con.execute("SELECT * FROM vecchio").fetchone())
        self.assertEqual({k: dopo[k] for k in prima}, prima)            # riga intatta
        self.assertIsNone(dopo["versione"])
        self.assertTrue(busta.e_legacy(dopo))
        self.assertEqual(busta.ora_locale(dopo), datetime(2026, 3, 1, 8, 15))  # naive


if __name__ == "__main__":
    unittest.main()
