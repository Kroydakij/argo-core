"""Test di core.config (loader TOML fail-fast). Solo stdlib."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import config  # noqa: E402


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _scrivi(self, testo: str) -> Path:
        p = self.dir / "c.toml"
        p.write_text(testo, encoding="utf-8")
        return p

    def test_load_ok_e_tipi_nativi(self):
        p = self._scrivi('[app]\nporta = 4701\ntitolo = "Demo"\nattivo = true\n')
        cfg = config.load(p)
        self.assertEqual(cfg["app"]["porta"], 4701)      # int nativo
        self.assertIs(cfg["app"]["attivo"], True)        # bool nativo

    def test_file_mancante_fallisce(self):
        with self.assertRaises(config.ConfigError):
            config.load(self.dir / "non_esiste.toml")

    def test_toml_malformato_fallisce(self):
        p = self._scrivi("[app\nporta = ")
        with self.assertRaises(config.ConfigError):
            config.load(p)

    def test_require_annidata(self):
        cfg = {"smtp": {"server": "s", "port": 25}}
        self.assertEqual(config.require(cfg, "smtp", "server"), "s")
        self.assertEqual(config.require(cfg, "smtp", "port"), 25)

    def test_require_assente_solleva(self):
        cfg = {"smtp": {"server": "s"}}
        with self.assertRaises(config.ConfigError):
            config.require(cfg, "smtp", "port")
        with self.assertRaises(config.ConfigError):
            config.require(cfg, "app", "porta")          # sezione assente

    def test_optional_con_default_esplicito(self):
        cfg = {"app": {"porta": 4701, "flag": False}}
        self.assertEqual(config.optional(cfg, "app", "porta", default=1), 4701)
        self.assertEqual(config.optional(cfg, "app", "manca", default=9), 9)
        # un valore falsy presente NON deve essere scambiato per assente
        self.assertIs(config.optional(cfg, "app", "flag", default=True), False)

    def test_serve_almeno_una_chiave(self):
        with self.assertRaises(ValueError):
            config.require({"a": 1})


if __name__ == "__main__":
    unittest.main()
