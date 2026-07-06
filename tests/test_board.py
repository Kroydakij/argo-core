"""Test di core.board (board config-driven). Solo stdlib."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import board  # noqa: E402

COLONNE = [
    {"titolo": "Disponibili", "stati": ["LIBERA"]},
    {"titolo": "In uso", "stati": ["IN_USO"]},
    {"titolo": "Fuori servizio", "stati": ["GUASTA"]},
]
STATI = [
    {"entita": "M1", "stato": "LIBERA", "operatore": None},
    {"entita": "M2", "stato": "IN_USO", "operatore": "rossi"},
    {"entita": "M3", "stato": "IN_USO", "operatore": "bianchi"},
    {"entita": "M4", "stato": "SMARRITA"},          # stato non mappato
]


class TestBoard(unittest.TestCase):
    def setUp(self):
        self.b = board.Board(COLONNE, campi_tile=["operatore"])

    def test_disponi_raggruppa_per_stato(self):
        cols = self.b.disponi(STATI)
        conteggi = {c["titolo"]: [e["entita"] for e in c["entita"]] for c in cols}
        self.assertEqual(conteggi["Disponibili"], ["M1"])
        self.assertEqual(conteggi["In uso"], ["M2", "M3"])
        self.assertEqual(conteggi["Fuori servizio"], [])

    def test_stato_non_mappato_non_mostrato(self):
        cols = self.b.disponi(STATI)
        tutte = [e["entita"] for c in cols for e in c["entita"]]
        self.assertNotIn("M4", tutte)               # SMARRITA non ha colonna

    def test_render_html(self):
        h = self.b.render_html(STATI)
        self.assertIn("In uso", h)
        self.assertIn('<span class="conteggio">2</span>', h)   # 2 in uso
        self.assertIn("M2", h)
        self.assertIn("rossi", h)                              # campo_tile
        self.assertIn('data-stato="IN_USO"', h)

    def test_render_escapa(self):
        h = self.b.render_html([{"entita": "<b>x</b>", "stato": "LIBERA"}])
        self.assertNotIn("<b>x</b>", h)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", h)

    def test_costruzione_fail_fast(self):
        with self.assertRaises(ValueError):
            board.Board([])                                    # nessuna colonna
        with self.assertRaises(ValueError):
            board.Board([{"titolo": "", "stati": ["A"]}])      # senza titolo
        with self.assertRaises(ValueError):
            board.Board([{"titolo": "C", "stati": []}])        # senza stati
        with self.assertRaises(ValueError):                    # stato ambiguo
            board.Board([{"titolo": "A", "stati": ["X"]},
                         {"titolo": "B", "stati": ["X"]}])

    def test_da_config(self):
        b = board.Board.da_config({"colonne": COLONNE, "campi_tile": ["operatore"]})
        self.assertEqual(len(b.disponi(STATI)), 3)
        with self.assertRaises(ValueError):
            board.Board.da_config({"campi_tile": []})          # colonne assenti


if __name__ == "__main__":
    unittest.main()
