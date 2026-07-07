"""Test di core.shifts (turni parametrici). Solo stdlib."""
import sys
import unittest
from datetime import datetime, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import shifts  # noqa: E402

# orari illustrativi, non prescritti dal core
TURNI = [
    {"nome": "A", "inizio": "06:00", "fine": "14:00"},
    {"nome": "B", "inizio": "14:00", "fine": "22:00"},
    {"nome": "C", "inizio": "22:00", "fine": "06:00"},   # attraversa mezzanotte
]


class TestShifts(unittest.TestCase):
    def setUp(self):
        self.t = shifts.Turni(TURNI)

    def test_nomi_in_ordine(self):
        self.assertEqual(self.t.nomi(), ["A", "B", "C"])

    def test_turno_di_intervalli_normali(self):
        self.assertEqual(self.t.turno_di("06:00"), "A")   # confine inferiore incluso
        self.assertEqual(self.t.turno_di("13:59"), "A")
        self.assertEqual(self.t.turno_di("14:00"), "B")   # confine = turno successivo
        self.assertEqual(self.t.turno_di("21:59"), "B")

    def test_turno_notturno_oltre_mezzanotte(self):
        self.assertEqual(self.t.turno_di("22:00"), "C")
        self.assertEqual(self.t.turno_di("23:30"), "C")
        self.assertEqual(self.t.turno_di("00:10"), "C")
        self.assertEqual(self.t.turno_di("05:59"), "C")

    def test_accetta_time_e_datetime(self):
        self.assertEqual(self.t.turno_di(time(15, 0)), "B")
        self.assertEqual(self.t.turno_di(datetime(2026, 7, 6, 23, 0)), "C")

    def test_nessuna_copertura(self):
        parziale = shifts.Turni([{"nome": "solo", "inizio": "08:00", "fine": "12:00"}])
        self.assertEqual(parziale.turno_di("09:00"), "solo")
        self.assertIsNone(parziale.turno_di("13:00"))

    def test_turno_24h(self):
        h24 = shifts.Turni([{"nome": "unico", "inizio": "00:00", "fine": "00:00"}])
        self.assertEqual(h24.turno_di("03:00"), "unico")
        self.assertEqual(h24.turno_di("18:00"), "unico")

    def test_costruzione_fail_fast(self):
        with self.assertRaises(ValueError):
            shifts.Turni([])                                   # vuoto
        with self.assertRaises(ValueError):
            shifts.Turni([{"nome": "", "inizio": "1:00", "fine": "2:00"}])
        with self.assertRaises(ValueError):
            shifts.Turni([{"nome": "x", "inizio": "25:00", "fine": "02:00"}])
        with self.assertRaises(ValueError):
            shifts.Turni([{"nome": "d", "inizio": "1:00", "fine": "2:00"},
                          {"nome": "d", "inizio": "3:00", "fine": "4:00"}])  # dup

    def test_da_config(self):
        t = shifts.Turni.da_config(TURNI)
        self.assertEqual(t.turno_di("15:30"), "B")


if __name__ == "__main__":
    unittest.main()
