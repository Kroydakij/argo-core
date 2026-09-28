"""Test di core.auth (ADR-001): identita' centrale, eventi, permessi, sessioni.
Il cuore e' stdlib puro; l'integrazione Flask gira solo con Flask installato."""
import io
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import auth, db, manifest, migrazioni  # noqa: E402
from core.auth import SISTEMA, AuthError  # noqa: E402

try:
    import flask  # noqa: F401
    HA_FLASK = True
except ImportError:
    HA_FLASK = False

# hash veloce nei test (il default scrypt costa ~50 ms a password)
VELOCE = mock.patch.object(auth, "_METODO", "pbkdf2:sha256:1000")


def _con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    auth.migra(con)
    return con


class Base(unittest.TestCase):
    def setUp(self):
        VELOCE.start()
        self.addCleanup(VELOCE.stop)
        self.con = _con()
        self.admin = auth.crea_utente(self.con, "admin", password="pw-admin",
                                      attore=SISTEMA)


class TestUtenti(Base):
    def test_crea_e_leggi(self):
        uid = auth.crea_utente(self.con, " mrossi ", password="x", nome="Mario Rossi",
                               attore=self.admin)
        u = auth.utente(self.con, uid)
        self.assertEqual((u["username"], u["nome"], u["tipo"], u["attivo"]),
                         ("mrossi", "Mario Rossi", "persona", 1))
        self.assertEqual(auth.utente_per_nome(self.con, "mrossi")["id"], uid)
        self.assertEqual([x["username"] for x in auth.utenti(self.con)],
                         ["admin", "mrossi"])

    def test_username_unico_e_valido(self):
        with self.assertRaises(AuthError):
            auth.crea_utente(self.con, "admin", attore=SISTEMA)
        with self.assertRaises(AuthError):
            auth.crea_utente(self.con, "  ", attore=SISTEMA)
        with self.assertRaises(AuthError):
            auth.crea_utente(self.con, "x", tipo="robot", attore=SISTEMA)

    def test_rinomina_mantiene_id(self):
        uid = auth.crea_utente(self.con, "mrossi", nome="Mario", attore=self.admin)
        auth.rinomina_utente(self.con, uid, "m.rossi", attore=self.admin)
        u = auth.utente(self.con, uid)
        self.assertEqual((u["username"], u["nome"]), ("m.rossi", "Mario"))
        self.assertIsNone(auth.utente_per_nome(self.con, "mrossi"))
        with self.assertRaises(AuthError):
            auth.rinomina_utente(self.con, uid, "admin", attore=self.admin)

    def test_disattiva_riattiva(self):
        uid = auth.crea_utente(self.con, "b", attore=self.admin)
        auth.disattiva_utente(self.con, uid, attore=self.admin)
        self.assertEqual(auth.utente(self.con, uid)["attivo"], 0)
        auth.riattiva_utente(self.con, uid, attore=self.admin)
        self.assertEqual(auth.utente(self.con, uid)["attivo"], 1)

    def test_attore_obbligatorio_e_esistente(self):
        with self.assertRaises(AuthError):
            auth.crea_utente(self.con, "x", attore="")
        with self.assertRaises(AuthError):
            auth.crea_utente(self.con, "x", attore="non-esiste")

    def test_audit_trail(self):
        uid = auth.crea_utente(self.con, "b", password="x", attore=self.admin)
        auth.disattiva_utente(self.con, uid, attore=self.admin)
        st = auth.storico(self.con, utente_id=uid)
        self.assertEqual([e["tipo"] for e in st],
                         [auth.E_UTENTE_CREATO, auth.E_PASSWORD,
                          auth.E_UTENTE_DISATTIVATO])
        self.assertTrue(all(e["attore_id"] == self.admin for e in st))
        self.assertTrue(all(e["ts_utc"].endswith("Z") and e["uid"] for e in st))
        self.assertTrue(all(e["entita_id"] is None for e in st))

    def test_password_non_finisce_nel_log(self):
        uid = auth.crea_utente(self.con, "b", password="segreta", attore=self.admin)
        auth.imposta_password(self.con, uid, "nuova", attore=self.admin)
        h = self.con.execute("SELECT password_hash FROM credenziali WHERE utente_id=?",
                             (uid,)).fetchone()[0]
        dump = "\n".join(str(dict(r)) for r in self.con.execute(
            "SELECT * FROM auth_eventi"))
        self.assertNotIn(h, dump)
        self.assertNotIn("nuova", dump)
        self.assertEqual(sum(e["tipo"] == auth.E_PASSWORD
                             for e in auth.storico(self.con, utente_id=uid)), 2)


class TestPermessi(Base):
    def setUp(self):
        super().setUp()
        self.u = auth.crea_utente(self.con, "operatore", attore=self.admin)
        self.capo = auth.definisci_ruolo(
            self.con, "Capoturno", ["andon.vedi", "andon.chiudi_fermata"],
            attore=self.admin)

    def test_assegnazione_diretta_e_revoca(self):
        self.assertEqual(auth.permessi_effettivi(self.con, self.u), frozenset())
        auth.assegna_ruolo(self.con, self.capo, utente_id=self.u, attore=self.admin)
        self.assertEqual(auth.permessi_effettivi(self.con, self.u),
                         {"andon.vedi", "andon.chiudi_fermata"})
        auth.revoca_ruolo(self.con, self.capo, utente_id=self.u, attore=self.admin)
        self.assertEqual(auth.permessi_effettivi(self.con, self.u), frozenset())

    def test_via_gruppo(self):
        g = auth.crea_gruppo(self.con, "Turno A", attore=self.admin)
        auth.assegna_ruolo(self.con, self.capo, gruppo_id=g, attore=self.admin)
        self.assertEqual(auth.permessi_effettivi(self.con, self.u), frozenset())
        auth.aggiungi_membro(self.con, g, self.u, attore=self.admin)
        self.assertIn("andon.vedi", auth.permessi_effettivi(self.con, self.u))
        self.assertEqual(auth.gruppi(self.con)[0]["membri"], [self.u])
        auth.rimuovi_membro(self.con, g, self.u, attore=self.admin)
        self.assertEqual(auth.permessi_effettivi(self.con, self.u), frozenset())

    def test_unione_di_ruoli(self):
        altro = auth.definisci_ruolo(self.con, "Magazzino", ["magazzino.carica"],
                                     attore=self.admin)
        g = auth.crea_gruppo(self.con, "Mag", attore=self.admin)
        auth.aggiungi_membro(self.con, g, self.u, attore=self.admin)
        auth.assegna_ruolo(self.con, altro, gruppo_id=g, attore=self.admin)
        auth.assegna_ruolo(self.con, self.capo, utente_id=self.u, attore=self.admin)
        self.assertEqual(auth.permessi_effettivi(self.con, self.u),
                         {"andon.vedi", "andon.chiudi_fermata", "magazzino.carica"})

    def test_ridefinire_ruolo_cambia_permessi(self):
        auth.assegna_ruolo(self.con, self.capo, utente_id=self.u, attore=self.admin)
        auth.definisci_ruolo(self.con, "Capoturno", ["andon.vedi"],
                             ruolo_id=self.capo, attore=self.admin)
        self.assertEqual(auth.permessi_effettivi(self.con, self.u), {"andon.vedi"})
        self.assertEqual(auth.ruoli(self.con)[0]["permessi"], ["andon.vedi"])

    def test_utente_disattivato_senza_permessi(self):
        auth.assegna_ruolo(self.con, self.capo, utente_id=self.u, attore=self.admin)
        auth.disattiva_utente(self.con, self.u, attore=self.admin)
        self.assertEqual(auth.permessi_effettivi(self.con, self.u), frozenset())

    def test_validazioni(self):
        with self.assertRaises(AuthError):
            auth.definisci_ruolo(self.con, "X", ["senzapunto"], attore=self.admin)
        with self.assertRaises(AuthError):
            auth.definisci_ruolo(self.con, "Capoturno", ["a.b"], attore=self.admin)
        with self.assertRaises(AuthError):
            auth.assegna_ruolo(self.con, self.capo, attore=self.admin)
        with self.assertRaises(AuthError):
            auth.assegna_ruolo(self.con, "boh", utente_id=self.u, attore=self.admin)
        with self.assertRaises(AuthError):
            auth.crea_gruppo(self.con, "", attore=self.admin)

    def test_ha_permesso(self):
        self.assertTrue(auth.ha_permesso({"permessi": {"a.b"}}, "a.b"))
        self.assertFalse(auth.ha_permesso({"permessi": {"a.b"}}, "a.c"))
        self.assertFalse(auth.ha_permesso(None, "a.b"))


class TestSessioni(Base):
    def setUp(self):
        super().setUp()
        self.u = auth.crea_utente(self.con, "op", password="pw", attore=self.admin)
        r = auth.definisci_ruolo(self.con, "R", ["andon.vedi"], attore=self.admin)
        auth.assegna_ruolo(self.con, r, utente_id=self.u, attore=self.admin)

    def test_login_e_sessione(self):
        self.assertIsNone(auth.login(self.con, "op", "sbagliata", durata_ore=8))
        self.assertIsNone(auth.login(self.con, "nessuno", "pw", durata_ore=8))
        token = auth.login(self.con, "op", "pw", durata_ore=8)
        s = auth.sessione(self.con, token)
        self.assertEqual((s["id"], s["username"]), (self.u, "op"))
        self.assertEqual(s["permessi"], {"andon.vedi"})
        # nel DB c'e' l'hash del token, non il token
        dump = str([dict(r) for r in self.con.execute("SELECT * FROM auth_eventi")])
        self.assertNotIn(token, dump)
        self.assertIsNone(auth.sessione(self.con, "token-inventato"))
        self.assertIsNone(auth.sessione(self.con, None))

    def test_scadenza_a_tempo_di_lettura(self):
        t0 = datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc)
        token = auth.apri_sessione(self.con, self.u, durata_ore=8, ora=t0)
        self.assertIsNotNone(auth.sessione(self.con, token, ora=t0 + timedelta(hours=7)))
        self.assertIsNone(auth.sessione(self.con, token, ora=t0 + timedelta(hours=8)))

    def test_logout_e_disattivazione_immediati(self):
        t1 = auth.login(self.con, "op", "pw", durata_ore=8)
        t2 = auth.login(self.con, "op", "pw", durata_ore=8)
        self.assertTrue(auth.chiudi_sessione(self.con, t1))
        self.assertFalse(auth.chiudi_sessione(self.con, t1))
        self.assertIsNone(auth.sessione(self.con, t1))
        self.assertIsNotNone(auth.sessione(self.con, t2))
        auth.disattiva_utente(self.con, self.u, attore=self.admin)
        self.assertIsNone(auth.sessione(self.con, t2))
        self.assertIsNone(auth.login(self.con, "op", "pw", durata_ore=8))
        with self.assertRaises(AuthError):
            auth.apri_sessione(self.con, self.u, durata_ore=8)

    def test_revoca_permesso_immediata(self):
        token = auth.login(self.con, "op", "pw", durata_ore=8)
        r = auth.ruoli(self.con)[0]["id"]
        auth.revoca_ruolo(self.con, r, utente_id=self.u, attore=self.admin)
        self.assertEqual(auth.sessione(self.con, token)["permessi"], frozenset())

    def test_servizio_non_apre_sessioni(self):
        auth.crea_utente(self.con, "sensore", password="pw", tipo="servizio",
                         attore=self.admin)
        self.assertIsNone(auth.login(self.con, "sensore", "pw", durata_ore=8))
        self.assertIsNotNone(auth.BackendLocale().autentica(self.con, "sensore", "pw"))


class TestHash(unittest.TestCase):
    def test_formato_e_verifica(self):
        h = auth.hash_password("segreta", metodo="pbkdf2:sha256:1000")
        self.assertTrue(h.startswith("pbkdf2:sha256:1000$"))
        self.assertTrue(auth._verifica_hash(h, "segreta"))
        self.assertFalse(auth._verifica_hash(h, "altra"))
        self.assertFalse(auth._verifica_hash("rotto", "x"))
        with self.assertRaises(AuthError):
            auth.hash_password("")

    def test_scrypt_default(self):
        h = auth.hash_password("pw")
        self.assertTrue(auth._verifica_hash(h, "pw"))

    @unittest.skipUnless(HA_FLASK, "werkzeug non installato")
    def test_compatibile_con_werkzeug(self):
        from werkzeug.security import check_password_hash, generate_password_hash
        for metodo in ("scrypt", "pbkdf2:sha256:1000"):
            w = generate_password_hash("pw", method=metodo)
            self.assertTrue(auth._verifica_hash(w, "pw"), metodo)
        self.assertTrue(check_password_hash(auth.hash_password("pw"), "pw"))
        self.assertTrue(check_password_hash(
            auth.hash_password("pw", metodo="pbkdf2:sha256:1000"), "pw"))


class TestFile(unittest.TestCase):
    def setUp(self):
        VELOCE.start()
        self.addCleanup(VELOCE.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "auth.sqlite"

    def test_crea_admin_e_versione(self):
        uid = auth.crea_admin(self.path, "capo", "pw")
        self.assertEqual(migrazioni.versione(self.path), len(auth.PASSI_AUTH))
        ro = db.readonly(self.path)
        try:
            self.assertEqual(auth.permessi_effettivi(ro, uid), set(auth.PERMESSI_KERNEL))
            self.assertEqual(auth.utente(ro, uid)["username"], "capo")
        finally:
            ro.close()
        auth.crea_admin(self.path, "vice", "pw")        # riusa il ruolo esistente
        con = db.owned(self.path)
        self.assertEqual(len(auth.ruoli(con)), 1)
        con.close()

    def test_cli_crea_admin(self):
        comune = Path(self.tmp.name) / "comune"
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("pw\n")), redirect_stdout(out):
            self.assertEqual(auth.main(["--comune", str(comune), "crea-admin",
                                        "capo", "--password-stdin"]), 0)
        con = db.owned(comune / "auth.sqlite")
        self.assertIsNotNone(auth.login(con, "capo", "pw", durata_ore=1))
        con.close()
        with mock.patch("sys.stdin", io.StringIO("pw\n")), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(auth.main(["--comune", str(comune), "crea-admin",
                                        "capo", "--password-stdin"]), 1)

    def test_importa_0x_senza_reset_password(self):
        vecchio = Path(self.tmp.name) / "modulo.db"
        c = sqlite3.connect(vecchio)
        c.execute("CREATE TABLE utenti (id INTEGER PRIMARY KEY, username TEXT, "
                  "password_hash TEXT, ruolo TEXT, attivo INTEGER)")
        c.executemany("INSERT INTO utenti (username, password_hash, ruolo, attivo) "
                      "VALUES (?,?,?,?)",
                      [("mario", auth.hash_password("pm"), "operatore", 1),
                       ("luca", auth.hash_password("pl"), "admin", 0)])
        c.commit(); c.close()
        esito = auth.importa_0x(self.path, vecchio)
        self.assertEqual(sorted(esito["importati"]), ["luca", "mario"])
        self.assertEqual(esito["ruoli_da_mappare"], {"admin": ["luca"],
                                                      "operatore": ["mario"]})
        con = db.owned(self.path)
        self.assertIsNotNone(auth.login(con, "mario", "pm", durata_ore=1))
        self.assertIsNone(auth.login(con, "luca", "pl", durata_ore=1))  # era disattivo
        con.close()
        self.assertEqual(auth.importa_0x(self.path, vecchio)["gia_presenti"],
                         ["luca", "mario"])


MANIFEST = manifest.da_dict({
    "modulo": {"nome": "andon", "versione": "1.0.0", "core": ">=1.0",
               "titolo": "Andon"},
    "permessi": [{"id": "andon.vedi", "descrizione": "v"},
                 {"id": "andon.chiudi", "descrizione": "c"}],
})


@unittest.skipUnless(HA_FLASK, "Flask non installato")
class TestFlask(unittest.TestCase):
    def setUp(self):
        VELOCE.start()
        self.addCleanup(VELOCE.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "auth.sqlite"
        self.admin = auth.crea_admin(self.path, "admin", "pw")
        con = db.owned(self.path)
        self.op = auth.crea_utente(con, "op", password="pw", attore=self.admin)
        r = auth.definisci_ruolo(con, "Vede", ["andon.vedi"], attore=self.admin)
        auth.assegna_ruolo(con, r, utente_id=self.op, attore=self.admin)
        self.token = auth.login(con, "op", "pw", durata_ore=1)
        auth.crea_utente(con, "sensore", password="spw", tipo="servizio",
                         attore=self.admin)
        auth.assegna_ruolo(con, r, utente_id=auth.utente_per_nome(con, "sensore")["id"],
                           attore=self.admin)
        con.close()
        self.client = self._app().test_client()

    def _app(self, extra_perm=None):
        from flask import Flask, g
        app = Flask(__name__)

        @app.get("/")
        @auth.richiede_permesso("andon.vedi")
        def home():
            return g.utente["username"]

        @app.post("/chiudi")
        @auth.richiede_permesso("andon.chiudi")
        def chiudi():
            return "ok"

        @app.get("/health")
        @auth.pubblica
        def health():
            return "vivo"

        @app.get("/libera")
        def libera():                           # sessione si', permesso no
            return "dentro"

        if extra_perm:
            @app.get("/extra")
            @auth.richiede_permesso(extra_perm)
            def extra():
                return "x"

        return auth.inizializza(app, manifest=MANIFEST, auth_db=self.path)

    def _con_sessione(self, token=None):
        self.client.set_cookie(auth.COOKIE, token or self.token, domain="localhost")

    def test_senza_sessione_redirect_al_login(self):
        r = self.client.get("/", headers={"Accept": "text/html"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.location.startswith(
            f"http://localhost:{auth.PORTA_SHELL}/login?next=http%3A%2F%2Flocalhost%2F"))
        self.assertEqual(self.client.get("/", headers={"Accept": "application/json"})
                         .status_code, 401)

    def test_con_sessione_e_permesso(self):
        self._con_sessione()
        r = self.client.get("/")
        self.assertEqual((r.status_code, r.get_data(as_text=True)), (200, "op"))
        self.assertEqual(self.client.get("/libera").status_code, 200)
        self.assertEqual(self.client.post("/chiudi").status_code, 403)

    def test_pubblica(self):
        self.assertEqual(self.client.get("/health").get_data(as_text=True), "vivo")

    def test_logout_immediato_nel_modulo(self):
        self._con_sessione()
        self.assertEqual(self.client.get("/").status_code, 200)
        con = db.owned(self.path)
        auth.chiudi_sessione(con, self.token)
        con.close()
        self.assertEqual(self.client.get("/", headers={"Accept": "text/html"})
                         .status_code, 302)

    def test_origine_estranea_rifiutata(self):
        self._con_sessione()
        r = self.client.post("/chiudi", headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)
        self.assertIn("Origine", r.get_data(as_text=True))

    def test_basic_auth_solo_per_servizi(self):
        import base64

        def basic(u, p):
            return {"Authorization": "Basic " +
                    base64.b64encode(f"{u}:{p}".encode()).decode(),
                    "Accept": "application/json"}
        self.assertEqual(self.client.get("/", headers=basic("sensore", "spw"))
                         .status_code, 200)
        self.assertEqual(self.client.get("/", headers=basic("sensore", "no"))
                         .status_code, 401)
        self.assertEqual(self.client.get("/", headers=basic("op", "pw"))
                         .status_code, 401)            # persona: solo sessione

    def test_permesso_non_dichiarato_blocca_avvio(self):
        with self.assertRaises(manifest.ManifestError):
            self._app(extra_perm="andon.segreto")

    def test_auth_db_mancante_blocca_avvio(self):
        self.path = Path(self.tmp.name) / "manca.sqlite"
        with self.assertRaises(AuthError):
            self._app()

    def test_cookie_sessione_flask_del_modulo(self):
        self.assertEqual(self._app().config["SESSION_COOKIE_NAME"], "argo_andon")


if __name__ == "__main__":
    unittest.main()
