"""Test della shell (ADR-001 parte 2): login unico, cornice, menu,
amministrazione. Richiedono Flask: se assente, skip."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import flask  # noqa: F401
    HA_FLASK = True
except ImportError:
    HA_FLASK = False

from core import auth, config, db, migrazioni, registro, scaffold  # noqa: E402

if HA_FLASK:
    from core import shell  # noqa: E402

VELOCE = mock.patch.object(auth, "_METODO", "pbkdf2:sha256:1000")
ARGO_TOML = '[suite]\ntitolo = "Stabilimento"\n[auth]\ndurata_sessione_ore = 8\n'


class TestConfigSuite(unittest.TestCase):
    """Stdlib: comune/argo.toml, fail-fast."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.comune = Path(self.tmp.name)

    def _scrivi(self, testo):
        (self.comune / "argo.toml").write_text(testo, encoding="utf-8")

    def test_valida(self):
        self._scrivi(ARGO_TOML)
        self.assertEqual(config.carica_suite(self.comune),
                         {"titolo": "Stabilimento", "porta": 4700,
                          "durata_sessione_ore": 8.0, "tipi": {}})

    def test_mancante_o_invalida(self):
        with self.assertRaises(config.ConfigError) as ctx:
            config.carica_suite(self.comune)
        self.assertIn("durata_sessione_ore", str(ctx.exception))   # mostra l'esempio
        (self.comune / "portal.json").write_text("{}")
        with self.assertRaises(config.ConfigError) as ctx:
            config.carica_suite(self.comune)
        self.assertIn("portal.json", str(ctx.exception))
        for testo in ("[suite]\n", "[auth]\ndurata_sessione_ore = 0\n",
                      "[auth]\ndurata_sessione_ore = 8\n[suite]\nporta = 'x'\n"):
            self._scrivi(testo)
            with self.assertRaises(config.ConfigError):
                config.carica_suite(self.comune)


@unittest.skipUnless(HA_FLASK, "Flask non installato")
class TestShell(unittest.TestCase):
    def setUp(self):
        VELOCE.start()
        self.addCleanup(VELOCE.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.comune = Path(self.tmp.name) / "comune"
        self.comune.mkdir()
        (self.comune / "argo.toml").write_text(ARGO_TOML, encoding="utf-8")
        self.radice = Path(self.tmp.name) / "suite"
        self.radice.mkdir()
        scaffold.genera("andon", 4730, self.radice)
        scaffold.genera("board", 4731, self.radice)
        self.auth_db = self.comune / "auth.sqlite"
        self.admin = auth.crea_admin(self.auth_db, "admin", "pw-admin")
        con = db.owned(self.auth_db)
        self.op = auth.crea_utente(con, "op", password="pw-op", nome="Operatore",
                                   attore=self.admin)
        r = auth.definisci_ruolo(con, "Andon", ["andon.vedi"], attore=self.admin)
        auth.assegna_ruolo(con, r, utente_id=self.op, attore=self.admin)
        con.close()
        self.app = shell.create_app(self.comune, radice=self.radice)
        self.c = self.app.test_client()

    def login(self, u="op", p="pw-op", next_=""):
        return self.c.post("/login", data={"username": u, "password": p, "next": next_})

    # --- avvio ---------------------------------------------------------

    def test_avvio_negato_senza_config_o_utenti(self):
        (self.comune / "argo.toml").unlink()
        with self.assertRaises(config.ConfigError):
            shell.create_app(self.comune, radice=self.radice)
        vuoto = Path(self.tmp.name) / "vuoto"
        vuoto.mkdir()
        (vuoto / "argo.toml").write_text(ARGO_TOML, encoding="utf-8")
        with self.assertRaises(auth.AuthError) as ctx:
            shell.create_app(vuoto, radice=self.radice)
        self.assertIn("crea-admin", str(ctx.exception))

    def test_db_del_kernel_versionati(self):
        self.assertEqual(migrazioni.versione(self.comune / "core.sqlite"),
                         len(registro.PASSI_CORE))
        self.assertEqual(migrazioni.versione(self.auth_db), len(auth.PASSI_AUTH))

    # --- login ---------------------------------------------------------

    def test_senza_login_redirect(self):
        r = self.c.get("/", headers={"Accept": "text/html"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.location.startswith("/login?next="))
        self.assertEqual(self.c.get("/login").status_code, 200)

    def test_login_ok_imposta_cookie_condiviso(self):
        r = self.login()
        self.assertEqual((r.status_code, r.location), (302, "/"))
        cookie = r.headers["Set-Cookie"]
        self.assertIn(f"{auth.COOKIE}=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Path=/", cookie)
        self.assertIn("Max-Age=28800", cookie)               # 8 ore da argo.toml
        home = self.c.get("/").get_data(as_text=True)
        self.assertIn("Operatore", home)                     # cornice: utente
        self.assertIn("http://localhost:4730/", home)        # menu: andon si'
        self.assertNotIn("http://localhost:4731/", home)     # board no (permesso)

    def test_login_errato(self):
        r = self.login(p="sbagliata")
        self.assertEqual(r.status_code, 401)
        self.assertIn("Credenziali non valide", r.get_data(as_text=True))
        self.assertNotIn("Set-Cookie", r.headers)

    def test_next_sicuro(self):
        self.assertEqual(self.login(next_="http://localhost:4730/fermate").location,
                         "http://localhost:4730/fermate")        # modulo registrato
        self.assertEqual(self.login(next_="http://evil.example/").location, "/")
        self.assertEqual(self.login(next_="http://localhost:9999/").location, "/")
        self.assertEqual(self.login(next_="//evil.example/").location, "/")
        self.assertEqual(self.login(next_="/password").location, "/password")

    def test_logout(self):
        self.login()
        self.assertEqual(self.c.get("/").status_code, 200)
        r = self.c.post("/logout")
        self.assertEqual((r.status_code, r.location), (302, "/login"))
        self.assertEqual(self.c.get("/", headers={"Accept": "text/html"}).status_code, 302)

    def test_cambio_password(self):
        self.login()
        r = self.c.post("/password", data={"attuale": "no", "nuova": "x", "conferma": "x"})
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/password", data={"attuale": "pw-op", "nuova": "n1",
                                           "conferma": "n2"})
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/password", data={"attuale": "pw-op", "nuova": "n1",
                                           "conferma": "n1"})
        self.assertEqual(r.status_code, 200)
        self.c.post("/logout")
        self.assertEqual(self.login(p="n1").status_code, 302)

    # --- permessi --------------------------------------------------------

    def test_admin_riservato(self):
        self.login()
        for metodo, url in (("get", "/api/db/databases"), ("post", "/api/moduli/rileggi"),
                            ("get", "/utenti"), ("get", "/api/utenti")):
            self.assertEqual(getattr(self.c, metodo)(url).status_code, 403, url)
        home = self.c.get("/").get_data(as_text=True)
        self.assertNotIn("Registro moduli", home)

    def test_admin_vede_tutto(self):
        self.login("admin", "pw-admin")
        home = self.c.get("/").get_data(as_text=True)
        self.assertIn("Registro moduli", home)
        self.assertIn("/utenti", home)
        dbs = {d["alias"] for d in self.c.get("/api/db/databases").get_json()}
        self.assertTrue({"core", "auth"} <= dbs)
        r = self.c.post("/api/moduli/rileggi").get_json()
        self.assertIs(r["ok"], True)
        self.assertEqual(r["moduli"], {"ok": 2, "errore": 0, "assente": 0})

    def test_orologi(self):
        self.login("admin", "pw-admin")
        with mock.patch.object(shell, "sfasamento_orologio",
                               side_effect=lambda p: {4730: 5.0, 4731: -300.0}[p]):
            d = self.c.get("/api/orologi").get_json()
        self.assertEqual(d, {"soglia_s": 120, "sfasati": {"board": -300}})
        self.assertIsNone(shell.sfasamento_orologio(4799, timeout=0.2))   # spento
        self.c.post("/logout")
        self.login()
        self.assertEqual(self.c.get("/api/orologi").status_code, 403)

    def test_api_moduli_pubblica_per_scaffolder(self):
        d = self.c.get("/api/moduli").get_json()
        self.assertEqual([m["nome"] for m in d["moduli"]], ["andon", "board"])
        self.assertEqual(d["prossima_porta"], 4732)

    def test_menu_api(self):
        self.login()
        self.assertEqual([v["modulo"] for v in self.c.get("/api/menu").get_json()],
                         ["andon"])

    # --- amministrazione utenti --------------------------------------------

    def test_gestione_utenti_end_to_end(self):
        self.login("admin", "pw-admin")
        r = self.c.post("/api/utenti", json={"username": "capo", "password": "pc",
                                             "nome": "Capo turno"}).get_json()
        self.assertTrue(r["ok"])
        capo = r["id"]
        g = self.c.post("/api/gruppi", json={"nome": "Turno A"}).get_json()["id"]
        self.assertTrue(self.c.post(f"/api/gruppi/{g}/membri",
                                    json={"utente_id": capo}).get_json()["ok"])
        ruolo = self.c.post("/api/ruoli", json={"nome": "Board",
                                                "permessi": ["board.vedi"]}).get_json()["id"]
        self.assertTrue(self.c.post(f"/api/ruoli/{ruolo}/assegnazioni",
                                    json={"gruppo_id": g}).get_json()["ok"])
        d = self.c.get("/api/utenti").get_json()
        self.assertIn("board.vedi", {p["id"] for p in d["catalogo"]})    # dai manifest
        self.assertIn("core.admin", {p["id"] for p in d["catalogo"]})
        con = db.readonly(self.auth_db)
        self.assertEqual(auth.permessi_effettivi(con, capo), {"board.vedi"})
        # l'audit trail registra chi ha fatto cosa
        st = auth.storico(con, utente_id=capo)
        con.close()
        self.assertTrue(all(e["attore_id"] == self.admin for e in st))
        # errori leggibili, non 500
        r = self.c.post("/api/utenti", json={"username": "capo"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("gia' in uso", r.get_json()["msg"])
        r = self.c.post(f"/api/utenti/{self.admin}/attivo", json={"attivo": False})
        self.assertEqual(r.status_code, 400)                 # non disattivarsi da soli
        self.assertEqual(self.c.get("/utenti").status_code, 200)

    # --- modulo generato dallo scaffolder: stessa sessione, cornice, menu ----

    def test_modulo_scaffold_con_sessione_della_shell(self):
        import importlib.util
        import os
        base = self.radice / "andon"
        os.environ["ARGO_COMUNE"] = str(self.comune)
        try:
            spec = importlib.util.spec_from_file_location("mod_andon", base / "app.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.migrate_db()
            modulo = mod.create_app().test_client()
        finally:
            os.environ.pop("ARGO_COMUNE", None)
        # senza sessione: al login della shell
        r = modulo.get("/", headers={"Accept": "text/html"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.location.startswith("http://localhost:4700/login?next="))
        self.assertEqual(modulo.get("/api/health").status_code, 200)   # pubblica
        # il cookie emesso dalla shell vale anche nel modulo (stesso host)
        token = self.login().headers["Set-Cookie"].split(";")[0].split("=", 1)[1]
        modulo.set_cookie(auth.COOKIE, token, domain="localhost")
        pagina = modulo.get("/").get_data(as_text=True)
        self.assertIn("Ciao Operatore", pagina)
        self.assertIn("Stabilimento", pagina)             # titolo suite in cornice
        self.assertIn('href="http://localhost:4700/logout"'.replace('href', 'action'),
                      pagina)
        # logout dalla shell: il modulo lo vede alla richiesta successiva
        self.c.post("/logout")
        self.assertEqual(modulo.get("/", headers={"Accept": "text/html"}).status_code, 302)

    def test_portal_alias_deprecato(self):
        import importlib
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            import core.portal as p
            importlib.reload(p)
        self.assertTrue(any(issubclass(x.category, DeprecationWarning) for x in w))
        self.assertIs(p.create_app, shell.create_app)


if __name__ == "__main__":
    unittest.main()
