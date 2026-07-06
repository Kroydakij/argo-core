"""Test di argo-core fase 0. Solo stdlib: python -m unittest discover tests -v"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import codes, db, export, migrate, notify, schedule  # noqa: E402


class TestDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_owned_impostazioni(self):
        con = db.owned(self.path)
        self.assertEqual(con.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(con.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(con.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
        con.execute("CREATE TABLE t (x)")
        row = con.execute("SELECT 1 AS uno").fetchone()
        self.assertEqual(row["uno"], 1)          # row_factory attiva
        con.close()

    def test_readonly_rifiuta_scritture(self):
        con = db.owned(self.path)
        con.execute("CREATE TABLE t (x)")
        con.commit(); con.close()
        ro = db.readonly(self.path)
        with self.assertRaises(sqlite3.OperationalError):
            ro.execute("INSERT INTO t VALUES (1)")
        ro.close()

    def test_readonly_su_file_mancante_fallisce(self):
        with self.assertRaises(sqlite3.OperationalError):
            db.readonly(Path(self.tmp.name) / "non_esiste.db")


class TestMigrate(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")

    def test_ensure_table_pretende_if_not_exists(self):
        with self.assertRaises(ValueError):
            migrate.ensure_table(self.con, "CREATE TABLE t (x)")
        migrate.ensure_table(self.con, "CREATE TABLE IF NOT EXISTS t (x)")
        migrate.ensure_table(self.con, "CREATE TABLE IF NOT EXISTS t (x)")  # idempotente

    def test_ensure_column_idempotente(self):
        migrate.ensure_table(self.con, "CREATE TABLE IF NOT EXISTS t (x)")
        self.assertTrue(migrate.ensure_column(self.con, "t", "y", "TEXT"))
        self.assertFalse(migrate.ensure_column(self.con, "t", "y", "TEXT"))
        self.assertEqual(migrate.table_columns(self.con, "t"), {"x", "y"})

    def test_rebuild_views(self):
        migrate.ensure_table(self.con, "CREATE TABLE IF NOT EXISTS t (x)")
        v = {"v_t": "CREATE VIEW v_t AS SELECT x FROM t"}
        migrate.rebuild_views(self.con, v)
        migrate.rebuild_views(self.con, v)       # drop+create ripetibile
        self.con.execute("INSERT INTO t VALUES (7)")
        self.assertEqual(self.con.execute("SELECT x FROM v_t").fetchone()[0], 7)

    def test_identificatori_maligni(self):
        for cattivo in ("t; DROP TABLE t", "t--", 'a"b', ""):
            with self.assertRaises(ValueError):
                migrate.ensure_column(self.con, cattivo, "y", "TEXT")


class TestCodes(unittest.TestCase):
    def setUp(self):
        codes._registry.clear()

    def test_zfill_e_registro(self):
        codes.registra("art9", codes.zfill_numerico(9))
        self.assertEqual(codes.norm("art9", " 252 "), "000000252")
        self.assertEqual(codes.norm("art9", 252), "000000252")
        self.assertEqual(codes.norm("art9", "50300252.3"), "050300252.3")
        self.assertEqual(codes.norm("art9", "AB-12"), "AB-12")
        self.assertEqual(codes.norm("art9", None), "")

    def test_doppia_registrazione_vietata(self):
        codes.registra("x", codes.zfill_numerico(4))
        with self.assertRaises(ValueError):
            codes.registra("x", codes.zfill_numerico(5))
        with self.assertRaises(KeyError):
            codes.norm("mai_visto", "1")


class TestNotify(unittest.TestCase):
    def test_disabilitato_ritorna_false_e_logga(self):
        con = sqlite3.connect(":memory:")
        ok = notify.send_email({"smtp": {"enabled": False}}, "ogg", "corpo",
                               log_con=con)
        self.assertFalse(ok)
        r = con.execute("SELECT oggetto, esito FROM notifiche_log").fetchone()
        self.assertEqual((r[0], r[1]), ("ogg", "DISABILITATO"))

    def test_config_incompleta_non_solleva(self):
        self.assertFalse(notify.send_email(
            {"smtp": {"enabled": True, "server": "", "to": []}}, "o", "c"))
        self.assertFalse(notify.send_email({}, "o", "c"))
        self.assertFalse(notify.send_email(None, "o", "c"))


class TestSchedule(unittest.TestCase):
    OGGI = "2026-07-04"

    def test_frequenza(self):
        tasks = [{"id": 1, "soggetto": "M1", "freq_giorni": 7, "evento": None}]
        # mai eseguita -> da_fare
        s = schedule.stato_task(tasks, {}, oggi=self.OGGI)
        self.assertEqual(s["M1"]["stato"], schedule.DA_FARE)
        # eseguita ieri, cadenza 7 -> ok, prossima tra 6 giorni
        s = schedule.stato_task(tasks, {(1, "M1"): ("2026-07-03", None)}, oggi=self.OGGI)
        self.assertEqual(s["M1"]["stato"], schedule.OK)
        self.assertEqual(s["M1"]["tasks"][0]["prossima"], "2026-07-10")
        # dovuta oggi -> da_fare
        s = schedule.stato_task(tasks, {(1, "M1"): ("2026-06-27", None)}, oggi=self.OGGI)
        self.assertEqual(s["M1"]["stato"], schedule.DA_FARE)
        # oltre scadenza -> scaduta
        s = schedule.stato_task(tasks, {(1, "M1"): ("2026-06-01", None)}, oggi=self.OGGI)
        self.assertEqual(s["M1"]["stato"], schedule.SCADUTA)

    def test_evento(self):
        tasks = [{"id": 2, "soggetto": "M1", "freq_giorni": None, "evento": "setup"}]
        # nessun evento -> ok
        s = schedule.stato_task(tasks, {}, eventi={}, oggi=self.OGGI)
        self.assertEqual(s["M1"]["stato"], schedule.OK)
        # setup di oggi non seguito -> da_fare
        ev = {"M1": "2026-07-04 08:00:00"}
        s = schedule.stato_task(tasks, {}, eventi=ev, oggi=self.OGGI)
        self.assertEqual(s["M1"]["stato"], schedule.DA_FARE)
        # setup di ieri non seguito -> scaduta
        ev = {"M1": "2026-07-03 08:00:00"}
        s = schedule.stato_task(tasks, {}, eventi=ev, oggi=self.OGGI)
        self.assertEqual(s["M1"]["stato"], schedule.SCADUTA)
        # esecuzione DOPO l'evento -> ok
        ult = {(2, "M1"): ("2026-07-03", "2026-07-03 09:00:00")}
        s = schedule.stato_task(tasks, ult, eventi=ev, oggi=self.OGGI)
        self.assertEqual(s["M1"]["stato"], schedule.OK)

    def test_peggiore_vince_e_righe_ignote_ignorate(self):
        tasks = [
            {"id": 1, "soggetto": "M1", "freq_giorni": 7, "evento": None},
            {"id": 2, "soggetto": "M1", "freq_giorni": None, "evento": None},  # ignorata
            {"id": 3, "soggetto": "M1", "freq_giorni": 1, "evento": None},
        ]
        ult = {(1, "M1"): ("2026-07-03", None),      # ok
               (3, "M1"): ("2026-06-01", None)}      # scaduta
        s = schedule.stato_task(tasks, ult, oggi=self.OGGI)
        self.assertEqual(s["M1"]["stato"], schedule.SCADUTA)
        self.assertEqual(len(s["M1"]["tasks"]), 2)


class TestExport(unittest.TestCase):
    def test_csv_bytes_da_sqlite_row(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.execute("CREATE TABLE t (nome TEXT, n INTEGER)")
        con.execute("INSERT INTO t VALUES ('caffè', 3)")
        data = export.csv_bytes(con.execute("SELECT * FROM t").fetchall())
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))          # BOM
        testo = data.decode("utf-8-sig")
        self.assertIn("nome;n", testo)
        self.assertIn("caffè;3", testo)

    def test_csv_vuoto_e_sequenze(self):
        self.assertEqual(export.csv_bytes([]), b"\xef\xbb\xbf")
        data = export.csv_bytes([("a", 1)], headers=["col1", "col2"])
        self.assertIn("col1;col2", data.decode("utf-8-sig"))

    def test_csv_response_flask(self):
        try:
            import flask  # noqa: F401
        except ImportError:
            self.skipTest("Flask non installato")
        resp = export.csv_response([{"a": 1}], "dati")
        self.assertEqual(resp.mimetype, "text/csv")
        self.assertIn("dati.csv", resp.headers["Content-Disposition"])


if __name__ == "__main__":
    unittest.main()
