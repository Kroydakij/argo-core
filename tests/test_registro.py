"""Test di core.registro (registro moduli della shell). Solo stdlib."""
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import manifest, registro, scaffold  # noqa: E402


class TestRegistro(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        registro.migra(self.con)
        registro.migra(self.con)                     # idempotente

    def _suite(self, *nomi, porta=4720):
        radice = Path(self.tmp.name) / "suite"
        radice.mkdir(exist_ok=True)
        for i, n in enumerate(nomi):
            scaffold.genera(n, porta + i, radice)
        return radice

    def test_upsert_lista_toggle_prossima_porta(self):
        self.assertEqual(registro.prossima_porta(self.con), registro.PRIMA_PORTA_MODULI)
        registro.upsert_modulo(self.con, "board", 4701, "andon")
        registro.upsert_modulo(self.con, "board", 4711, "andon v2")
        registro.upsert_modulo(self.con, "b", 4705)
        mods = {m["nome"]: m for m in registro.lista_moduli(self.con)}
        self.assertEqual((mods["board"]["porta"], mods["board"]["descrizione"]),
                         (4711, "andon v2"))
        self.assertEqual(registro.prossima_porta(self.con), 4712)
        registro.toggle_modulo(self.con, "b", False)
        self.assertEqual({m["nome"]: m for m in registro.lista_moduli(self.con)}["b"]
                         ["attivo"], 0)
        self.assertEqual(registro.porte(self.con), {4711, 4705})

    def test_registra_scansione(self):
        radice = self._suite("andon", "board")
        esito = registro.registra_scansione(self.con, manifest.scansiona(radice))
        self.assertEqual(esito, {"ok": 2, "errore": 0, "assente": 0})
        mods = {m["nome"]: m for m in registro.lista_moduli(self.con)}
        self.assertEqual((mods["andon"]["origine"], mods["andon"]["stato"],
                          mods["andon"]["porta"], mods["andon"]["versione"]),
                         ("manifest", "ok", 4720, "0.1.0"))
        self.assertEqual(mods["andon"]["menu"][0]["permesso"], "andon.vedi")
        self.assertNotIn("manifest_json", mods["andon"])
        self.assertEqual([m["nome"] for m in registro.manifesti(self.con)],
                         ["andon", "board"])

        (radice / "board" / "manifest.toml").write_text("[modulo\n", encoding="utf-8")
        esito = registro.registra_scansione(self.con, manifest.scansiona(radice))
        self.assertEqual(esito, {"ok": 1, "errore": 1, "assente": 0})
        board = {m["nome"]: m for m in registro.lista_moduli(self.con)}["board"]
        self.assertEqual((board["stato"], board["porta"]), ("errore", 4721))
        self.assertIn("malformato", board["errore"])
        shutil.rmtree(radice / "andon")
        esito = registro.registra_scansione(self.con, manifest.scansiona(radice))
        self.assertEqual(esito["assente"], 1)

    def test_moduli_manuali_non_toccati_dalla_scansione(self):
        registro.upsert_modulo(self.con, "legacy", 4790, "senza manifest")
        registro.registra_scansione(self.con, manifest.scansiona(self._suite("andon")))
        legacy = {m["nome"]: m for m in registro.lista_moduli(self.con)}["legacy"]
        self.assertEqual((legacy["origine"], legacy["stato"]), ("manuale", "ok"))

    def test_menu_filtrato_per_permessi(self):
        registro.registra_scansione(self.con, manifest.scansiona(
            self._suite("andon", "board")))
        registro.upsert_modulo(self.con, "legacy", 4790)
        self.assertEqual(registro.menu_per(self.con, set(), "pc1"), [])
        voci = registro.menu_per(self.con, {"andon.vedi"}, "pc1")
        self.assertEqual(voci, [{"modulo": "andon", "titolo": "Andon",
                                 "url": "http://pc1:4720/", "esterno": False}])
        voci = registro.menu_per(self.con, {"andon.vedi", "board.vedi",
                                            registro.PERMESSO_LINK_ESTERNI}, "pc1")
        self.assertEqual([v["modulo"] for v in voci], ["andon", "board", "legacy"])
        self.assertTrue(voci[-1]["esterno"])
        registro.toggle_modulo(self.con, "board", False)      # disattivo: fuori
        self.assertEqual([v["modulo"] for v in registro.menu_per(
            self.con, {"andon.vedi", "board.vedi"}, "pc1")], ["andon"])


if __name__ == "__main__":
    unittest.main()
