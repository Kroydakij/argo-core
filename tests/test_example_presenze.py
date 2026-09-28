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


TIPI = {"attrezzo": {"descrizione": "Attrezzi", "normalizzazione": ["strip", "maiuscolo"]}}


class TestDemoPresenze(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        comune = Path(self.tmp.name)
        from core import anagrafica, db
        self.anagrafica = anagrafica
        anagrafica.prepara_db(anagrafica.percorso_db(comune), TIPI)   # fa la shell
        self.ana_rw = db.owned(anagrafica.percorso_db(comune))
        self.ids = {c: anagrafica.crea(self.ana_rw, "attrezzo", c, attore="sistema")
                    for c in ("trapano-01", "scala-04")}
        self.mod = _carica_modulo(comune)
        self.mod.migrate_db()
        self.con = self.mod.db.owned(self.mod.DB_PATH)
        self.ana = self.mod.apri_anagrafica()
        self.cfg = self.mod.carica_config()
        self.sm = self.mod._macchina(self.cfg)
        self.trapano = self.ids["trapano-01"]

    def tearDown(self):
        for c in (self.con, self.ana, self.ana_rw):
            c.close()
        os.environ.pop("ARGO_COMUNE", None)
        self.tmp.cleanup()

    def muovi(self, azione, attrezzo=None, attore="u-rossi"):
        return self.mod.registra_movimento(self.con, self.ana, attrezzo or self.trapano,
                                           azione, self.sm, attore_id=attore)

    def test_attrezzi_dall_anagrafica_senza_seed(self):
        righe = self.mod.righe_board(self.con, self.ana, self.sm)
        self.assertEqual([(r["entita"], r["stato"], r["chi"]) for r in righe],
                         [("SCALA-04", "DISPONIBILE", ""), ("TRAPANO-01", "DISPONIBILE", "")])
        self.assertEqual(self.mod.events.stato_corrente(self.con), [])   # log vuoto

    def test_flusso_valido_e_proiezione(self):
        self.assertEqual(self.muovi("preleva"), "IN_USO")
        self.assertEqual(self.mod.stato_di(self.con, self.ana, self.trapano, self.sm),
                         "IN_USO")
        st = self.mod.events.stato_corrente(self.con, self.trapano)
        self.assertEqual((st["attore_id"], st["tipo"], st["entita_id"]),
                         ("u-rossi", "presenze.cambio_stato", self.trapano))
        self.muovi("restituisci")
        self.assertEqual(self.mod.stato_di(self.con, self.ana, self.trapano, self.sm),
                         "DISPONIBILE")

    def test_transizione_non_valida_e_attrezzo_non_attivo(self):
        with self.assertRaises(self.mod.statemachine.TransizioneNonValida):
            self.muovi("restituisci")                  # da DISPONIBILE non si puo'
        with self.assertRaises(ValueError):
            self.muovi("preleva", attrezzo="id-sconosciuto")
        self.anagrafica.rendi_obsoleta(self.ana_rw, self.ids["scala-04"], attore="sistema")
        with self.assertRaises(ValueError):
            self.muovi("preleva", attrezzo=self.ids["scala-04"])
        self.assertEqual([r["entita"] for r in
                          self.mod.righe_board(self.con, self.ana, self.sm)], ["TRAPANO-01"])

    def test_rinomina_e_fusione_in_lettura(self):
        self.muovi("preleva")
        self.anagrafica.rinomina(self.ana_rw, self.trapano, "TR-1", attore="sistema")
        righe = {r["entita"]: r["stato"] for r in
                 self.mod.righe_board(self.con, self.ana, self.sm)}
        self.assertEqual(righe["TR-1"], "IN_USO")                  # rinomina: subito
        self.anagrafica.fondi(self.ana_rw, self.trapano, self.ids["scala-04"],
                              attore="sistema")
        righe = {r["entita"]: r["stato"] for r in
                 self.mod.righe_board(self.con, self.ana, self.sm)}
        self.assertEqual(righe, {"SCALA-04": "IN_USO"})           # storia del fuso
        self.assertEqual(self.muovi("restituisci", attrezzo=self.trapano), "DISPONIBILE")
        st = self.mod.events.stato_corrente(self.con, self.ids["scala-04"])
        self.assertEqual(st["stato"], "DISPONIBILE")               # scritto canonico

    def test_righe_prima_dell_anagrafica(self):
        """Log scritto quando la chiave era il nome dell'attrezzo: si risolve."""
        self.mod.events.registra(self.con, self.mod.EVENTO, "Trapano-01", "MANUTENZIONE",
                                 manifest=self.mod.MANIFEST, attore_id="sistema")
        self.assertEqual(self.mod.stato_di(self.con, self.ana, self.trapano, self.sm),
                         "MANUTENZIONE")

    def test_manutenzioni_a_tempo_di_lettura(self):
        m = self.mod.stato_manutenzioni(self.con, self.ana, self.cfg, oggi="2026-07-07")
        self.assertEqual(m["TRAPANO-01"]["stato"], "da_fare")      # mai manutenuto
        self.muovi("invia_manutenzione", attore="u")
        m2 = self.mod.stato_manutenzioni(self.con, self.ana, self.cfg)   # oggi reale
        self.assertEqual(m2["TRAPANO-01"]["stato"], "ok")

    @unittest.skipUnless(HA_FLASK, "Flask non installato")
    def test_http_con_login_della_suite(self):
        from core import auth, db
        auth_db = Path(self.tmp.name) / "auth.sqlite"
        (Path(self.tmp.name) / "argo.toml").write_text(
            "[auth]\ndurata_sessione_ore = 8\n[anagrafica.tipi.attrezzo]\n",
            encoding="utf-8")
        admin = auth.crea_admin(auth_db, "admin", "pw")
        con = db.owned(auth_db)
        vede = auth.definisci_ruolo(con, "Vede", ["presenze.vedi"], attore=admin)
        muove = auth.definisci_ruolo(con, "Muove", ["presenze.registra_movimento"],
                                     attore=admin)
        rossi = auth.crea_utente(con, "rossi", password="pr", attore=admin)
        auth.assegna_ruolo(con, vede, utente_id=rossi, attore=admin)
        token = auth.login(con, "rossi", "pr", durata_ore=1)
        con.close()
        client = self.mod.create_app().test_client()
        self.assertEqual(client.get("/", headers={"Accept": "text/html"}).status_code, 302)
        client.set_cookie(auth.COOKIE, token, domain="localhost")
        pagina = client.get("/")
        self.assertEqual(pagina.status_code, 200)
        self.assertNotIn("Registra movimento", pagina.get_data(as_text=True))  # solo vede
        dati = {"attrezzo": "TRAPANO-01", "azione": "preleva"}
        self.assertEqual(client.post("/movimento", data=dati).status_code, 403)
        con = db.owned(auth_db)
        auth.assegna_ruolo(con, muove, utente_id=rossi, attore=admin)
        con.close()
        self.assertEqual(client.post("/movimento", data=dati).status_code, 302)
        st = self.mod.events.stato_corrente(self.con, self.trapano)
        self.assertEqual((st["stato"], st["attore_id"]), ("IN_USO", rossi))  # chi = sessione
        self.assertIn("rossi", client.get("/").get_data(as_text=True))      # 'chi' sulla board


if __name__ == "__main__":
    unittest.main()
