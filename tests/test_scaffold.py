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
        base = scaffold.genera("vetrina", 4703, self.dir)
        import os
        os.environ["ARGO_COMUNE"] = str(base / "dati")
        try:
            mod = _importa(base / "app.py", "modgen_vetrina")
            mod.migrate_db()
            client = mod.create_app().test_client()
            r = client.get("/")
            self.assertEqual(r.status_code, 200)
        finally:
            os.environ.pop("ARGO_COMUNE", None)


if __name__ == "__main__":
    unittest.main()
