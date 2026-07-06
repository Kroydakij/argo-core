"""Test di core.forms (form-engine dichiarativo). Solo stdlib."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import forms  # noqa: E402

CAMPI = [
    {"nome": "targa", "label": "Targa", "tipo": "text", "obbligatorio": True},
    {"nome": "ore", "label": "Ore", "tipo": "number", "min": 0, "max": 24},
    {"nome": "reparto", "tipo": "select", "opzioni": ["A", "B"], "obbligatorio": True},
    {"nome": "giorno", "tipo": "date"},
    {"nome": "urgente", "tipo": "checkbox"},
    {"nome": "note", "tipo": "textarea", "maxlen": 5},
]


class TestValida(unittest.TestCase):
    def test_form_valido(self):
        dati = {"targa": " AB123 ", "ore": "8", "reparto": "A",
                "giorno": "2026-07-06", "urgente": "on", "note": "ciao"}
        puliti, errori = forms.valida(CAMPI, dati)
        self.assertEqual(errori, {})
        self.assertEqual(puliti["targa"], "AB123")     # strip
        self.assertEqual(puliti["ore"], 8)             # int se intero
        self.assertIs(puliti["urgente"], True)

    def test_obbligatori_mancanti(self):
        puliti, errori = forms.valida(CAMPI, {"ore": "3"})
        self.assertIn("targa", errori)
        self.assertIn("reparto", errori)
        self.assertNotIn("ore", errori)

    def test_number_non_numerico_e_range(self):
        _, e1 = forms.valida(CAMPI, {"targa": "X", "reparto": "A", "ore": "otto"})
        self.assertIn("ore", e1)
        _, e2 = forms.valida(CAMPI, {"targa": "X", "reparto": "A", "ore": "99"})
        self.assertIn("ore", e2)                       # oltre il max
        p, e3 = forms.valida(CAMPI, {"targa": "X", "reparto": "A", "ore": "1.5"})
        self.assertEqual(e3, {})
        self.assertEqual(p["ore"], 1.5)                # float se non intero

    def test_select_e_date_invalidi(self):
        _, e1 = forms.valida(CAMPI, {"targa": "X", "reparto": "Z"})
        self.assertIn("reparto", e1)
        _, e2 = forms.valida(CAMPI, {"targa": "X", "reparto": "A", "giorno": "06/07/2026"})
        self.assertIn("giorno", e2)

    def test_maxlen_textarea(self):
        _, e = forms.valida(CAMPI, {"targa": "X", "reparto": "A", "note": "troppo lungo"})
        self.assertIn("note", e)

    def test_checkbox_falsy(self):
        p, _ = forms.valida(CAMPI, {"targa": "X", "reparto": "A"})
        self.assertIs(p["urgente"], False)


class TestRender(unittest.TestCase):
    def test_render_contiene_controlli(self):
        h = forms.render_html(CAMPI, valori={"targa": "AB", "reparto": "B"})
        self.assertIn('name="targa"', h)
        self.assertIn('value="AB"', h)
        self.assertIn('<select id="reparto"', h)
        self.assertIn('<option value="B" selected>', h)  # valore pre-selezionato
        self.assertIn("required", h)                     # campo obbligatorio

    def test_render_mostra_errori(self):
        h = forms.render_html(CAMPI, errori={"targa": "campo obbligatorio"})
        self.assertIn("campo obbligatorio", h)
        self.assertIn('class="errore"', h)

    def test_render_escapa_xss(self):
        h = forms.render_html(CAMPI, valori={"targa": '"><script>alert(1)</script>'})
        self.assertNotIn("<script>", h)
        self.assertIn("&lt;script&gt;", h)


class TestDefinizione(unittest.TestCase):
    def test_definizione_fail_fast(self):
        for cattiva in ([], [{"tipo": "text"}],                       # senza nome
                        [{"nome": "a", "tipo": "boh"}],               # tipo ignoto
                        [{"nome": "s", "tipo": "select"}],            # select senza opzioni
                        [{"nome": "d"}, {"nome": "d"}]):              # nome duplicato
            with self.assertRaises(ValueError):
                forms.valida(cattiva, {})


if __name__ == "__main__":
    unittest.main()
