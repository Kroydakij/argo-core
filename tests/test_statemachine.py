"""Test di core.statemachine (macchina a stati dichiarativa). Solo stdlib."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import statemachine as sm  # noqa: E402

TRANS = {
    "LIBERA": {"prendi": "IN_USO"},
    "IN_USO": {"rilascia": "LIBERA", "guasta": "GUASTA"},
    "GUASTA": {"ripara": "LIBERA"},
}


class TestStateMachine(unittest.TestCase):
    def setUp(self):
        self.m = sm.StateMachine(TRANS, iniziale="LIBERA")

    def test_stati_e_iniziale(self):
        self.assertEqual(self.m.stati(), frozenset({"LIBERA", "IN_USO", "GUASTA"}))
        self.assertEqual(self.m.iniziale, "LIBERA")

    def test_transizioni_valide(self):
        self.assertEqual(self.m.transita("LIBERA", "prendi"), "IN_USO")
        self.assertEqual(self.m.transita("IN_USO", "guasta"), "GUASTA")
        self.assertEqual(self.m.transita("GUASTA", "ripara"), "LIBERA")

    def test_transizione_non_ammessa(self):
        self.assertFalse(self.m.puo("LIBERA", "rilascia"))
        with self.assertRaises(sm.TransizioneNonValida):
            self.m.transita("LIBERA", "rilascia")

    def test_azioni_e_terminali(self):
        self.assertEqual(set(self.m.azioni("IN_USO")), {"rilascia", "guasta"})
        self.assertEqual(self.m.azioni("SCONOSCIUTO"), {})
        # nessuno stato terminale in questo grafo (tutti hanno un'uscita)
        self.assertEqual(self.m.terminali(), frozenset())

    def test_stato_terminale(self):
        m = sm.StateMachine({"APERTO": {"chiudi": "CHIUSO"}, "CHIUSO": {}},
                            iniziale="APERTO")
        self.assertEqual(m.terminali(), frozenset({"CHIUSO"}))
        with self.assertRaises(sm.TransizioneNonValida):
            m.transita("CHIUSO", "qualsiasi")

    def test_costruzione_fail_fast(self):
        with self.assertRaises(ValueError):
            sm.StateMachine({}, iniziale="X")                 # vuoto
        with self.assertRaises(ValueError):
            sm.StateMachine(TRANS, iniziale="INESISTENTE")    # iniziale ignoto
        with self.assertRaises(ValueError):
            sm.StateMachine({"A": {"go": ""}}, iniziale="A")  # destinazione vuota

    def test_da_config(self):
        cfg = {"iniziale": "LIBERA", "transizioni": TRANS}
        m = sm.StateMachine.da_config(cfg)
        self.assertEqual(m.transita("LIBERA", "prendi"), "IN_USO")
        with self.assertRaises(ValueError):
            sm.StateMachine.da_config({"iniziale": "LIBERA"})  # transizioni assenti


if __name__ == "__main__":
    unittest.main()
