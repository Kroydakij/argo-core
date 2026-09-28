"""Test di core.scaffold. Genera un modulo e ne verifica scheletro e avvio.
Il test HTTP su '/' gira solo se Flask e' installato; il resto e' stdlib puro."""
import importlib.util
import py_compile
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import scaffold  # noqa: E402

try:
    import flask  # noqa: F401
    HA_FLASK = True
except ImportError:
    HA_FLASK = False


def _importa(app_py: Path, nome_modulo: str):
    spec = importlib.util.spec_from_file_location(nome_modulo, app_py)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestScaffold(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_genera_struttura(self):
        base = scaffold.genera("presenze", 4701, self.dir)
        self.assertTrue((base / "app.py").exists())
        self.assertTrue((base / "presenze.toml").exists())
        self.assertTrue((base / "templates" / "index.html").exists())
        self.assertTrue((base / "README.md").exists())

    def test_app_py_compila_e_config_valida(self):
        base = scaffold.genera("presenze", 4701, self.dir)
        py_compile.compile(str(base / "app.py"), doraise=True)   # sintassi valida
        cfg = tomllib.loads((base / "presenze.toml").read_text(encoding="utf-8"))
        self.assertEqual(cfg["app"]["porta"], 4701)

    def test_manifest_generato_valido(self):
        from core import manifest
        base = scaffold.genera("magazzino", 4702, self.dir)
        m = manifest.carica(base)                  # contro il core corrente
        self.assertEqual(m.nome, "magazzino")
        self.assertTrue(m.dichiara_permesso("magazzino.vedi"))
        self.assertEqual(m.menu[0].permesso, "magazzino.vedi")
        esiti = manifest.scansiona(self.dir)       # la shell lo trova da sola
        self.assertEqual([(s.nome, s.porta, s.errore) for s in esiti],
                         [("magazzino", 4702, None)])

    def test_migrate_db_gira_senza_flask(self):
        base = scaffold.genera("magazzino", 4702, self.dir)
        import os
        os.environ["ARGO_COMUNE"] = str(base / "dati")
        try:
            mod = _importa(base / "app.py", "modgen_magazzino")
            mod.migrate_db()                       # crea il DB con gli helper core
            self.assertTrue((base / "dati" / "magazzino.sqlite").exists())
            # ri-eseguibile (migrazione additiva/idempotente)
            mod.migrate_db()
        finally:
            os.environ.pop("ARGO_COMUNE", None)

    def test_nome_non_valido(self):
        for cattivo in ("9x", "con spazio", "punto.py", ""):
            with self.assertRaises(ValueError):
                scaffold.genera(cattivo, 4701, self.dir)

    def test_non_sovrascrive(self):
        scaffold.genera("dup", 4701, self.dir)
        with self.assertRaises(FileExistsError):
            scaffold.genera("dup", 4701, self.dir)

    def test_porta_suggerita_offline(self):
        # portale non raggiungibile -> usa l'argomento, o il default del blocco
        self.assertEqual(scaffold.porta_suggerita(4750, portale="http://127.0.0.1:1",
                                                  timeout=0.2), 4750)
        self.assertEqual(scaffold.porta_suggerita(None, portale="http://127.0.0.1:1",
                                                  timeout=0.2), scaffold.PRIMA_PORTA_MODULI)

    @unittest.skipUnless(HA_FLASK, "Flask non installato")
    def test_scheletro_risponde_su_root(self):
        from core import auth, db
        base = scaffold.genera("vetrina", 4703, self.dir)
        import os
        dati = base / "dati"
        # identita' della suite come la prepara la shell: utente con vetrina.vedi
        admin = auth.crea_admin(dati / "auth.sqlite", "admin", "pw")
        con = db.owned(dati / "auth.sqlite")
        r = auth.definisci_ruolo(con, "Vetrina", ["vetrina.vedi"], attore=admin)
        auth.assegna_ruolo(con, r, utente_id=admin, attore=admin)
        token = auth.login(con, "admin", "pw", durata_ore=1)
        con.close()
        os.environ["ARGO_COMUNE"] = str(dati)
        try:
            mod = _importa(base / "app.py", "modgen_vetrina")
            mod.migrate_db()
            client = mod.create_app().test_client()
            self.assertEqual(client.get("/", headers={"Accept": "text/html"})
                             .status_code, 302)            # senza login: alla shell
            self.assertEqual(client.get("/api/health").status_code, 200)
            client.set_cookie(auth.COOKIE, token, domain="localhost")
            r = client.get("/")
            self.assertEqual(r.status_code, 200)
            self.assertIn("argo-barra", r.get_data(as_text=True))   # cornice comune
        finally:
            os.environ.pop("ARGO_COMUNE", None)

    @unittest.skipUnless(HA_FLASK, "Flask non installato")
    def test_scheletro_senza_shell_non_parte(self):
        from core import auth
        base = scaffold.genera("orfano", 4704, self.dir)
        import os
        os.environ["ARGO_COMUNE"] = str(base / "dati")
        try:
            mod = _importa(base / "app.py", "modgen_orfano")
            with self.assertRaises(auth.AuthError):
                mod.create_app()                            # auth.sqlite assente
        finally:
            os.environ.pop("ARGO_COMUNE", None)


if __name__ == "__main__":
    unittest.main()
