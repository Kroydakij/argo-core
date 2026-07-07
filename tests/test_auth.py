"""Test di core.auth (utenti e ruoli). Le parti crittografiche richiedono
werkzeug e vengono saltate se assente; il resto e' stdlib puro."""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import auth  # noqa: E402

try:
    import werkzeug.security  # noqa: F401
    HA_WERKZEUG = True
except ImportError:
    HA_WERKZEUG = False


def _con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    auth.migra(con)
    return con


class TestPure(unittest.TestCase):
    """Coperti sempre: tabella, ruoli, utente inesistente."""

    def test_migra_crea_tabella(self):
        con = _con()
        cols = {r[1] for r in con.execute("PRAGMA table_info(utenti)")}
        self.assertEqual(cols, {"id", "username", "password_hash", "ruolo",
                                "attivo", "creato_il"})

    def test_ha_ruolo(self):
        self.assertTrue(auth.ha_ruolo({"ruolo": "admin"}, "admin", "operatore"))
        self.assertFalse(auth.ha_ruolo({"ruolo": "ospite"}, "admin"))
        self.assertFalse(auth.ha_ruolo(None, "admin"))

    def test_verifica_utente_inesistente(self):
        # nessun hashing raggiunto: funziona anche senza werkzeug
        self.assertIsNone(auth.verifica(_con(), "nessuno", "x"))

    def test_identificatore_tabella_validato(self):
        with self.assertRaises(ValueError):
            auth.ddl("utenti; DROP TABLE utenti")


@unittest.skipUnless(HA_WERKZEUG, "werkzeug non installato")
class TestHashing(unittest.TestCase):
    def test_crea_e_verifica(self):
        con = _con()
        auth.crea_utente(con, "mario", "segreta", "operatore")
        u = auth.verifica(con, "mario", "segreta")
        self.assertEqual(u["ruolo"], "operatore")
        self.assertNotIn("password_hash", u)             # mai esposto
        self.assertIsNone(auth.verifica(con, "mario", "sbagliata"))

    def test_hash_non_in_chiaro(self):
        con = _con()
        auth.crea_utente(con, "mario", "segreta", "admin")
        h = con.execute("SELECT password_hash FROM utenti").fetchone()[0]
        self.assertNotIn("segreta", h)

    def test_disattivato_non_entra(self):
        con = _con()
        auth.crea_utente(con, "mario", "segreta", "admin")
        auth.disattiva(con, "mario")
        self.assertIsNone(auth.verifica(con, "mario", "segreta"))

    def test_upsert_e_cambio_password(self):
        con = _con()
        auth.crea_utente(con, "mario", "vecchia", "admin")
        auth.imposta_password(con, "mario", "nuova")
        self.assertIsNone(auth.verifica(con, "mario", "vecchia"))
        self.assertIsNotNone(auth.verifica(con, "mario", "nuova"))
        self.assertEqual(len(auth.lista_utenti(con)), 1)  # upsert, non duplica


if __name__ == "__main__":
    unittest.main()
