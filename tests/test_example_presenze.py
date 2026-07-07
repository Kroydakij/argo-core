"""Test del modulo demo examples/presenze: guida il flusso end-to-end.
La logica di dominio e' testata senza Flask; il test HTTP gira se Flask c'e'."""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
APP_PY = REPO / "examples" / "presenze" / "app.py"

try:
    import flask  # noqa: F401
    HA_FLASK = True
except ImportError:
    HA_FLASK = False


def _carica_modulo(comune: Path):
    """Importa examples/presenze/app.py con ARGO_COMUNE su una cartella temp.
    Env impostato PRIMA dell'import: i path del DB sono costanti di modulo."""
    os.environ["ARGO_COMUNE"] = str(comune)
    spec = importlib.util.spec_from_file_location("demo_presenze", APP_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestDemoPresenze(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = _carica_modulo(Path(self.tmp.name))
        self.mod.migrate_db()
        self.con = self.mod.db.owned(self.mod.DB_PATH)
        self.cfg = self.mod.carica_config()
        self.sm = self.mod._macchina(self.cfg)

    def tearDown(self):
        self.con.close()
        os.environ.pop("ARGO_COMUNE", None)
        self.tmp.cleanup()

    def test_seed_iniziale_idempotente(self):
        stati = self.mod.events.stato_corrente(self.con)
        self.assertTrue(stati)
        self.assertTrue(all(s["stato"] == "DISPONIBILE" for s in stati))
        n = len(self.mod.events.stato_corrente(self.con))
        self.mod.migrate_db()                       # ri-eseguire non ri-semina
        self.assertEqual(len(self.mod.events.stato_corrente(self.con)), n)

    def test_flusso_valido_e_proiezione(self):
        attrezzo = self.mod._attrezzi(self.cfg)[0]
        nuovo = self.mod.registra_movimento(self.con, attrezzo, "preleva", "rossi", self.sm)
        self.assertEqual(nuovo, "IN_USO")
        self.assertEqual(self.mod.stato_di(self.con, attrezzo, self.sm), "IN_USO")
        self.mod.registra_movimento(self.con, attrezzo, "restituisci", "rossi", self.sm)
        self.assertEqual(self.mod.stato_di(self.con, attrezzo, self.sm), "DISPONIBILE")

    def test_transizione_non_valida_rifiutata(self):
        attrezzo = self.mod._attrezzi(self.cfg)[0]
        with self.assertRaises(self.mod.statemachine.TransizioneNonValida):
            # da DISPONIBILE non si puo' 'restituisci'
            self.mod.registra_movimento(self.con, attrezzo, "restituisci", "x", self.sm)

    def test_manutenzioni_a_tempo_di_lettura(self):
        attrezzo = self.mod._attrezzi(self.cfg)[0]
        # mai manutenuto -> da_fare
        m = self.mod.stato_manutenzioni(self.con, self.cfg, oggi="2026-07-07")
        self.assertEqual(m[attrezzo]["stato"], "da_fare")
        # inviato in manutenzione oggi -> l'ultimo evento MANUTENZIONE azzera la cadenza
        self.mod.registra_movimento(self.con, attrezzo, "invia_manutenzione", "x", self.sm)
        m2 = self.mod.stato_manutenzioni(self.con, self.cfg)  # oggi reale
        self.assertEqual(m2[attrezzo]["stato"], "ok")

    @unittest.skipUnless(HA_FLASK, "Flask non installato")
    def test_http_home_e_movimento(self):
        client = self.mod.create_app().test_client()
        self.assertEqual(client.get("/").status_code, 200)
        attrezzo = self.mod._attrezzi(self.cfg)[0]
        r = client.post("/movimento",
                        data={"attrezzo": attrezzo, "azione": "preleva",
                              "operatore": "rossi"})
        self.assertEqual(r.status_code, 302)         # redirect post-movimento
        self.assertEqual(self.mod.stato_di(self.con, attrezzo, self.sm), "IN_USO")


if __name__ == "__main__":
    unittest.main()
