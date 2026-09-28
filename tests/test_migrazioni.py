"""Test di core.migrazioni (ADR-004). Solo stdlib."""
import io
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import db, migrate, migrazioni  # noqa: E402
from core.migrazioni import Passo  # noqa: E402


def _p1(con):
    migrate.ensure_table(con, """CREATE TABLE IF NOT EXISTS fermate (
        id INTEGER PRIMARY KEY AUTOINCREMENT, macchina TEXT NOT NULL)""")


def _p2(con):
    migrate.ensure_column(con, "fermate", "causale", "TEXT")


def _p3(con):
    migrate.ensure_column(con, "fermate", "durata_min", "REAL")


PASSI = [Passo(1, "schema iniziale", _p1), Passo(2, "causale", _p2)]
VISTE = {"v_fermate": "CREATE VIEW v_fermate AS SELECT macchina FROM fermate"}


class TestMigrazioni(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "andon.sqlite"
        self.backup_dir = self.dir / "_backup" / "andon"

    def tearDown(self):
        self.tmp.cleanup()

    def _backups(self):
        return sorted(self.backup_dir.glob("andon.v*.sqlite")) \
            if self.backup_dir.exists() else []

    def _inserisci(self, macchina="PR-001"):
        con = db.owned(self.path)
        con.execute("INSERT INTO fermate (macchina) VALUES (?)", (macchina,))
        con.commit()
        con.close()

    # --- percorso normale ---------------------------------------------

    def test_db_nuovo_applica_tutto_senza_backup(self):
        es = migrazioni.applica(self.path, PASSI, viste=VISTE)
        self.assertEqual((es.da, es.a, es.applicati), (0, 2, (1, 2)))
        self.assertIsNone(es.backup)                 # DB vuoto: backup inutile
        self.assertEqual(self._backups(), [])
        self.assertEqual(migrazioni.versione(self.path), 2)
        con = db.owned(self.path)
        self.assertIn("causale", migrate.table_columns(con, "fermate"))
        self.assertTrue(con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='view' AND name='v_fermate'"
        ).fetchone())
        con.close()

    def test_riavvio_senza_passi_pendenti_non_fa_nulla(self):
        migrazioni.applica(self.path, PASSI)
        self._inserisci()
        es = migrazioni.applica(self.path, PASSI)
        self.assertEqual((es.da, es.a, es.applicati, es.backup), (2, 2, (), None))
        self.assertEqual(self._backups(), [])

    def test_nuovo_passo_fa_backup_prima(self):
        migrazioni.applica(self.path, PASSI)
        self._inserisci()
        es = migrazioni.applica(self.path, PASSI + [Passo(3, "durata", _p3)])
        self.assertEqual(es.applicati, (3,))
        self.assertIsNotNone(es.backup)
        self.assertTrue(es.backup.name.startswith("andon.v2-"))
        # il backup e' lo stato PRIMA del passo 3: dati si', colonna nuova no
        b = sqlite3.connect(es.backup)
        cols = {r[1] for r in b.execute("PRAGMA table_info(fermate)")}
        self.assertNotIn("durata_min", cols)
        self.assertEqual(b.execute("SELECT COUNT(*) FROM fermate").fetchone()[0], 1)
        self.assertEqual(b.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        b.close()
        storia = migrazioni.stato(self.path)["storia"]
        self.assertEqual(storia[-1]["backup"], str(es.backup))

    def test_backup_include_dati_non_ancora_nel_file_principale(self):
        # in WAL le ultime scritture stanno nel -wal: una copia del file le
        # perderebbe, l'API di backup no
        migrazioni.applica(self.path, PASSI)
        tenuta_aperta = db.owned(self.path)          # impedisce il checkpoint finale
        tenuta_aperta.execute("INSERT INTO fermate (macchina) VALUES ('X')")
        tenuta_aperta.commit()
        try:
            es = migrazioni.applica(self.path, PASSI + [Passo(3, "durata", _p3)])
        finally:
            tenuta_aperta.close()
        b = sqlite3.connect(es.backup)
        self.assertEqual(b.execute("SELECT COUNT(*) FROM fermate").fetchone()[0], 1)
        b.close()

    def test_viste_ricreate_a_ogni_avvio(self):
        migrazioni.applica(self.path, PASSI, viste=VISTE)
        con = db.owned(self.path)
        con.execute("DROP VIEW v_fermate")
        con.commit()
        con.close()
        migrazioni.applica(self.path, PASSI, viste=VISTE)
        con = db.owned(self.path)
        self.assertTrue(con.execute(
            "SELECT 1 FROM sqlite_master WHERE name='v_fermate'").fetchone())
        con.close()

    def test_adozione_db_0x(self):
        # DB creato dalla vecchia migrate_db(): tabelle presenti, niente registro
        con = db.owned(self.path)
        _p1(con)
        con.execute("INSERT INTO fermate (macchina) VALUES ('PR-001')")
        con.commit()
        con.close()
        es = migrazioni.applica(self.path, PASSI)
        self.assertEqual((es.da, es.a), (0, 2))
        self.assertIsNotNone(es.backup)               # c'erano dati: backup fatto
        con = db.owned(self.path)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM fermate").fetchone()[0], 1)
        con.close()

    # --- rifiuti ------------------------------------------------------

    def test_db_piu_nuovo_del_codice_rifiutato(self):
        migrazioni.applica(self.path, PASSI + [Passo(3, "durata", _p3)])
        with self.assertRaises(migrazioni.DBPiuNuovoDelCodice):
            migrazioni.applica(self.path, PASSI)
        self.assertEqual(migrazioni.versione(self.path), 3)

    def test_passo_fallito_rollback_e_registro(self):
        migrazioni.applica(self.path, PASSI)
        self._inserisci()

        def _rotto(con):
            migrate.ensure_table(con, "CREATE TABLE IF NOT EXISTS mezza (x)")
            raise RuntimeError("bug nel passo")

        with self.assertRaises(migrazioni.MigrazioneFallita) as ctx:
            migrazioni.applica(self.path, PASSI + [Passo(3, "rotto", _rotto)])
        self.assertEqual(ctx.exception.numero, 3)
        self.assertIsNotNone(ctx.exception.backup)
        self.assertIn(str(ctx.exception.backup), str(ctx.exception))
        self.assertEqual(migrazioni.versione(self.path), 2)
        con = db.owned(self.path)
        self.assertFalse(migrate.table_exists(con, "mezza"))    # rollback
        con.close()
        ultima = migrazioni.stato(self.path)["storia"][-1]
        self.assertEqual((ultima["numero"], ultima["esito"]), (3, "ERRORE"))
        self.assertIn("bug nel passo", ultima["errore"])
        # corretto il codice, si riparte
        es = migrazioni.applica(self.path, PASSI + [Passo(3, "durata", _p3)])
        self.assertEqual(es.a, 3)

    def test_passo_che_fa_commit_e_rifiutato(self):
        def _commit(con):
            _p1(con)
            con.commit()

        with self.assertRaises(migrazioni.MigrazioneFallita):
            migrazioni.applica(self.path, [Passo(1, "fa commit", _commit)])
        self.assertEqual(migrazioni.versione(self.path), 0)

    def test_passi_non_consecutivi(self):
        for passi in ([Passo(2, "x", _p1)],
                      [Passo(1, "a", _p1), Passo(3, "b", _p2)],
                      [Passo(1, "a", _p1), Passo(1, "b", _p2)],
                      [Passo(1, " ", _p1)]):
            with self.assertRaises(migrazioni.PassiNonValidi):
                migrazioni.applica(self.path, passi)

    def test_backup_da_tenere_minimo_uno(self):
        with self.assertRaises(ValueError):
            migrazioni.applica(self.path, PASSI, backup_da_tenere=0)

    # --- retention ----------------------------------------------------

    def test_retention_tiene_gli_ultimi_n(self):
        def _col(n):
            return lambda con: migrate.ensure_column(con, "fermate", f"c{n}", "TEXT")

        passi = list(PASSI)
        migrazioni.applica(self.path, passi)
        self._inserisci()
        creati = []
        for n in range(3, 7):
            passi.append(Passo(n, f"col {n}", _col(n)))
            creati.append(migrazioni.applica(self.path, passi,
                                             backup_da_tenere=2).backup)
        rimasti = self._backups()
        self.assertEqual(len(rimasti), 2)
        self.assertIn(creati[-1], rimasti)             # l'ultimo c'e' sempre

    # --- lettura e supporto --------------------------------------------

    def test_richiedi_versione(self):
        migrazioni.applica(self.path, PASSI)
        self.assertEqual(migrazioni.richiedi_versione(self.path, 2), 2)
        with self.assertRaises(migrazioni.MigrazioneError) as ctx:
            migrazioni.richiedi_versione(self.path, 3,
                                         suggerimento="Avvia prima la shell.")
        self.assertIn("Avvia prima la shell", str(ctx.exception))

    def test_stato_e_cli(self):
        migrazioni.applica(self.path, PASSI)
        st = migrazioni.stato(self.path)
        self.assertEqual((st["versione"], st["user_version"]), (2, 2))
        self.assertEqual([r["numero"] for r in st["storia"]], [1, 2])
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(migrazioni.main(["stato", str(self.path)]), 0)
        self.assertIn("versione:      2", out.getvalue())

    def test_cli_db_mancante(self):
        err = io.StringIO()
        with redirect_stdout(io.StringIO()):
            from contextlib import redirect_stderr
            with redirect_stderr(err):
                self.assertEqual(
                    migrazioni.main(["stato", str(self.dir / "manca.sqlite")]), 1)


if __name__ == "__main__":
    unittest.main()
