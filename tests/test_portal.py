"""Test fase 1 (portale + adminbrowser). Richiedono Flask: se assente, skip."""
import base64
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import flask  # noqa: F401
    HA_FLASK = True
except ImportError:
    HA_FLASK = False

from core import db as coredb  # noqa: E402

if HA_FLASK:
    from core import portal  # noqa: E402

AUTH = {"Authorization": "Basic " + base64.b64encode(b"admin:admin").decode()}


@unittest.skipUnless(HA_FLASK, "Flask non installato")
class TestRegistro(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        portal.migrate_core_db(self.con)
        portal.migrate_core_db(self.con)          # idempotente

    def tearDown(self):
        self.tmp.cleanup()

    def test_upsert_e_lista(self):
        portal.upsert_modulo(self.con, "board", 4701, "andon")
        portal.upsert_modulo(self.con, "board", 4711, "andon v2")   # update
        mods = portal.lista_moduli(self.con)
        self.assertEqual(len(mods), 1)
        self.assertEqual((mods[0]["porta"], mods[0]["descrizione"]),
                         (4711, "andon v2"))

    def test_prossima_porta(self):
        self.assertEqual(portal.prossima_porta(self.con), portal.PRIMA_PORTA_MODULI)
        portal.upsert_modulo(self.con, "a", 4701)
        portal.upsert_modulo(self.con, "b", 4705)
        self.assertEqual(portal.prossima_porta(self.con), 4706)

    def test_toggle(self):
        portal.upsert_modulo(self.con, "a", 4701)
        portal.toggle_modulo(self.con, "a", False)
        self.assertEqual(portal.lista_moduli(self.con)[0]["attivo"], 0)

    def test_check_salute_porta_muta(self):
        self.assertFalse(portal.check_salute(4799, timeout=0.2))


@unittest.skipUnless(HA_FLASK, "Flask non installato")
class TestPortaleHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        comune = Path(self.tmp.name)
        # un DB "di modulo" da esplorare col browser
        con = coredb.owned(comune / "demo.db")
        con.execute("CREATE TABLE eventi (id INTEGER PRIMARY KEY, nota TEXT)")
        con.executemany("INSERT INTO eventi (nota) VALUES (?)",
                        [(f"riga {i}",) for i in range(60)])
        con.commit(); con.close()
        self.app = portal.create_app(comune)
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_home_e_api_moduli(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        d = self.client.get("/api/moduli").get_json()
        self.assertEqual(d["moduli"], [])
        self.assertEqual(d["prossima_porta"], portal.PRIMA_PORTA_MODULI)

    def test_scrittura_richiede_auth(self):
        r = self.client.post("/api/moduli", json={"nome": "x", "porta": 4701})
        self.assertEqual(r.status_code, 401)
        r = self.client.post("/api/moduli", json={"nome": "x", "porta": 4701},
                             headers=AUTH)
        self.assertTrue(r.get_json()["ok"])
        d = self.client.get("/api/moduli").get_json()
        self.assertEqual(d["moduli"][0]["nome"], "x")

    def test_validazione_input(self):
        r = self.client.post("/api/moduli", json={"nome": ""}, headers=AUTH)
        self.assertEqual(r.status_code, 400)

    def test_browser_db(self):
        # senza auth: 401
        self.assertEqual(self.client.get("/api/db/databases").status_code, 401)
        dbs = self.client.get("/api/db/databases", headers=AUTH).get_json()
        alias = {d["alias"] for d in dbs}
        self.assertIn("demo", alias)
        self.assertIn("core", alias)               # core.sqlite visibile anch'esso
        tabs = self.client.get("/api/db/demo/tabelle", headers=AUTH).get_json()
        self.assertIn(("eventi", 60), {(t["nome"], t["righe"]) for t in tabs})
        # paginazione
        r = self.client.get("/api/db/demo/righe/eventi", headers=AUTH).get_json()
        self.assertEqual(len(r["righe"]), 50)
        r2 = self.client.get("/api/db/demo/righe/eventi?offset=50",
                             headers=AUTH).get_json()
        self.assertEqual(len(r2["righe"]), 10)
        # tabella inesistente e alias inesistente
        self.assertEqual(self.client.get("/api/db/demo/righe/nope",
                                         headers=AUTH).status_code, 404)
        self.assertEqual(self.client.get("/api/db/nope/tabelle",
                                         headers=AUTH).status_code, 404)

    def test_export_csv(self):
        r = self.client.get("/api/db/demo/export/eventi.csv", headers=AUTH)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "text/csv")
        corpo = r.data.decode("utf-8-sig")
        self.assertIn("id;nota", corpo)
        self.assertIn("riga 0", corpo)

    def test_health_vuoto(self):
        self.assertEqual(self.client.get("/api/health").get_json(), {})


if __name__ == "__main__":
    unittest.main()
