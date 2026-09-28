"""Test di core.anagrafica (ADR-002) e dei suoi appoggi (codes.componi,
config dei tipi, db.attach_readonly). Il nucleo e' stdlib; shell e client
HTTP richiedono Flask (skip se assente)."""
import contextlib
import io
import logging
import socket
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import flask  # noqa: F401
    HA_FLASK = True
except ImportError:
    HA_FLASK = False

from core import anagrafica, auth, codes, config, db, manifest, migrazioni  # noqa: E402
from core.anagrafica import AnagraficaError  # noqa: E402

TIPI = {"macchina": {"descrizione": "Macchine", "normalizzazione": ["strip", "maiuscolo"]},
        "articolo": {"descrizione": "Articoli", "normalizzazione": ["strip", "zfill:9"]}}
ARGO_TOML = """[auth]
durata_sessione_ore = 8
[anagrafica.tipi.macchina]
descrizione = "Macchine"
normalizzazione = ["strip", "maiuscolo"]
[anagrafica.tipi.articolo]
descrizione = "Articoli"
normalizzazione = ["strip", "zfill:9"]
"""
U = "u-1"


class TestCodesComponi(unittest.TestCase):
    def test_regole(self):
        self.assertEqual(codes.componi(["strip", "zfill:9"])(" 252 "), "000000252")
        self.assertEqual(codes.componi(["strip", "zfill:9"])("AB-1"), "AB-1")
        self.assertEqual(codes.componi(["senza_spazi", "maiuscolo"])(" pr 01 "), "PR01")
        self.assertEqual(codes.componi(["minuscolo"])("AbC"), "abc")
        self.assertEqual(codes.componi([])(" x "), " x ")

    def test_regole_non_valide(self):
        for cattiva in (["boh"], ["zfill"], ["zfill:x"], ["strip:1"], "strip", [1]):
            with self.assertRaises(ValueError):
                codes.componi(cattiva)


class TestConfigTipi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.comune = Path(self.tmp.name)

    def carica(self, testo):
        (self.comune / "argo.toml").write_text(testo, encoding="utf-8")
        return config.carica_suite(self.comune)

    def test_tipi(self):
        self.assertEqual(self.carica(ARGO_TOML)["tipi"], TIPI)
        t = self.carica("[auth]\ndurata_sessione_ore = 8\n[anagrafica.tipi.commessa]\n")
        self.assertEqual(t["tipi"], {"commessa": {"descrizione": "commessa",
                                                  "normalizzazione": ["strip"]}})

    def test_tipi_non_validi(self):
        base = "[auth]\ndurata_sessione_ore = 8\n"
        for cattivo in ('[anagrafica.tipi.Macchina]\n',
                        '[anagrafica.tipi.m]\nnormalizzazione = ["boh"]\n',
                        '[anagrafica.tipi.m]\ncolore = "rosso"\n',
                        '[anagrafica]\ntipi = ["m"]\n'):
            with self.assertRaises(config.ConfigError, msg=cattivo):
                self.carica(base + cattivo)


class TestAttachReadonly(unittest.TestCase):
    def test_join_in_sola_lettura(self):
        with tempfile.TemporaryDirectory() as d:
            altro = db.owned(Path(d) / "altro db.sqlite")
            altro.execute("CREATE TABLE t (id TEXT, codice TEXT)")
            altro.execute("INSERT INTO t VALUES ('e1', 'PR-01')")
            altro.commit()
            altro.close()
            con = db.owned(Path(d) / "modulo.sqlite")
            con.execute("CREATE TABLE log (entita_id TEXT)")
            con.execute("INSERT INTO log VALUES ('e1')")
            db.attach_readonly(con, Path(d) / "altro db.sqlite", "ana")
            self.assertEqual(con.execute("SELECT t.codice FROM log JOIN ana.t t "
                                         "ON t.id = log.entita_id").fetchone()[0], "PR-01")
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("INSERT INTO ana.t VALUES ('x', 'y')")
            with self.assertRaises(sqlite3.OperationalError):
                db.attach_readonly(con, Path(d) / "manca.sqlite", "boh")
            with self.assertRaises(ValueError):
                db.attach_readonly(con, Path(d) / "altro db.sqlite", "a; drop")
            con.close()


class TestAnagrafica(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        anagrafica.migra(self.con)
        anagrafica.configura_tipi(self.con, TIPI)

    def crea(self, codice, tipo="macchina", **kw):
        return anagrafica.crea(self.con, tipo, codice, attore=U, **kw)

    def test_crea_normalizza_e_legge(self):
        eid = self.crea(" pr-01 ", descrizione=" Pressa 1 ", attributi={"reparto": "A", "kw": 7.5})
        e = anagrafica.entita(self.con, eid)
        self.assertEqual((e["tipo"], e["codice"], e["descrizione"], e["stato"], e["fusa_in"]),
                         ("macchina", "PR-01", "Pressa 1", "ATTIVO", None))
        self.assertEqual(e["attributi"], {"reparto": "A", "kw": 7.5})
        self.assertEqual(len(eid), 36)
        self.assertEqual(anagrafica.elenco(self.con, "macchina")[0]["id"], eid)
        a = anagrafica.crea(self.con, "articolo", " 252 ", attore=U)
        self.assertEqual(anagrafica.entita(self.con, a)["codice"], "000000252")
        self.assertEqual(anagrafica.risolvi(self.con, "articolo", "252").id, a)

    def test_evento_con_busta(self):
        eid = self.crea("PR-01")
        ev = anagrafica.storico(self.con, eid)[0]
        self.assertEqual((ev["tipo"], ev["versione"], ev["attore_id"], ev["entita_id"]),
                         (anagrafica.E_CREATA, 1, U, eid))
        self.assertTrue(ev["ts_utc"].endswith("Z"))
        with self.assertRaises(ValueError):                      # attore obbligatorio
            anagrafica.crea(self.con, "macchina", "X", attore="")

    def test_codice_unico_anche_se_obsoleto(self):
        eid = self.crea("PR-01")
        with self.assertRaises(AnagraficaError):
            self.crea("pr-01")
        anagrafica.rendi_obsoleta(self.con, eid, attore=U)
        with self.assertRaises(AnagraficaError):
            self.crea("PR-01")
        self.crea("PR-01", tipo="articolo")                    # altro tipo: libero
        with self.assertRaises(AnagraficaError):
            self.crea("   ")                                    # vuoto
        with self.assertRaises(AnagraficaError):
            self.crea("X", tipo="commessa")                     # tipo non configurato

    def test_rinomina_codice_storico_e_riuso(self):
        vecchia = self.crea("PR-01")
        self.assertEqual(anagrafica.rinomina(self.con, vecchia, "pr-001", attore=U), "PR-001")
        self.assertEqual(anagrafica.entita(self.con, vecchia)["codice"], "PR-001")
        r = anagrafica.risolvi(self.con, "macchina", "PR-01")
        self.assertEqual((r.id, r.come, r.codice), (vecchia, "STORICO", "PR-001"))
        nuova = self.crea("PR-01")                              # liberato dalla rinomina
        self.assertEqual(anagrafica.risolvi(self.con, "macchina", "PR-01").id, nuova)
        self.assertEqual(anagrafica.risolvi(self.con, "macchina", "PR-01").come, "CORRENTE")
        with self.assertRaises(AnagraficaError):
            anagrafica.rinomina(self.con, vecchia, "PR-01", attore=U)   # occupato
        with self.assertRaises(AnagraficaError):
            anagrafica.rinomina(self.con, vecchia, "PR-001", attore=U)  # uguale

    def test_alias_e_ordine_di_risoluzione(self):
        eid = self.crea("PR-01")
        altra = self.crea("PR-02")
        self.assertEqual(anagrafica.aggiungi_alias(self.con, eid, "gestionale", " m0001 ",
                                                   attore=U), "M0001")
        r = anagrafica.risolvi(self.con, "macchina", "m0001")
        self.assertEqual((r.id, r.come), (eid, "ALIAS"))
        with self.assertRaises(AnagraficaError):                # (sistema, codice) unico
            anagrafica.aggiungi_alias(self.con, altra, "gestionale", "M0001", attore=U)
        anagrafica.aggiungi_alias(self.con, altra, "fornitore", "M0001", attore=U)
        with self.assertRaises(AnagraficaError):                # ambiguo senza sistema
            anagrafica.risolvi(self.con, "macchina", "M0001")
        self.assertEqual(anagrafica.risolvi(self.con, "macchina", "M0001",
                                            sistema="fornitore").id, altra)
        anagrafica.aggiungi_alias(self.con, altra, "gestionale", "PR-01", attore=U)
        self.assertEqual(anagrafica.risolvi(self.con, "macchina", "PR-01").id, eid)  # corrente vince
        anagrafica.rimuovi_alias(self.con, eid, "gestionale", "M0001", attore=U)
        self.assertEqual([a["codice"] for a in anagrafica.alias(self.con, eid)], [])
        self.assertEqual(anagrafica.risolvi(self.con, "macchina", "M0001").id, altra)
        with self.assertRaises(AnagraficaError):
            anagrafica.rimuovi_alias(self.con, eid, "gestionale", "M0001", attore=U)
        with self.assertRaises(AnagraficaError):
            anagrafica.aggiungi_alias(self.con, eid, "ge stionale", "X", attore=U)
        self.assertIsNone(anagrafica.risolvi(self.con, "macchina", "MAI-VISTO"))

    def test_descrivi_sostituisce_attributi(self):
        eid = self.crea("PR-01", descrizione="Pressa", attributi={"a": 1, "b": 2})
        anagrafica.descrivi(self.con, eid, attributi={"c": True}, attore=U)
        e = anagrafica.entita(self.con, eid)
        self.assertEqual((e["descrizione"], e["attributi"]), ("Pressa", {"c": True}))
        anagrafica.descrivi(self.con, eid, descrizione="Pressa grande", attore=U)
        e = anagrafica.entita(self.con, eid)
        self.assertEqual((e["descrizione"], e["attributi"]), ("Pressa grande", {"c": True}))
        for cattivi in ({"x": {"annidato": 1}}, {"x": [1]}, {"1x": 1}, {"a-b": 1}, ["a"]):
            with self.assertRaises(AnagraficaError, msg=cattivi):
                anagrafica.descrivi(self.con, eid, attributi=cattivi, attore=U)

    def test_stati(self):
        eid = self.crea("PR-01")
        with self.assertRaises(AnagraficaError):
            anagrafica.riattiva(self.con, eid, attore=U)
        anagrafica.rendi_obsoleta(self.con, eid, attore=U)
        self.assertEqual(anagrafica.elenco(self.con, "macchina"), [])
        self.assertEqual(len(anagrafica.elenco(self.con, "macchina", stati=("OBSOLETO",))), 1)
        anagrafica.riattiva(self.con, eid, attore=U)
        self.assertEqual(anagrafica.entita(self.con, eid)["stato"], "ATTIVO")
        with self.assertRaises(AnagraficaError):
            anagrafica.elenco(self.con, "macchina", stati=("BOH",))

    def test_fusioni(self):
        a, b, c = self.crea("A"), self.crea("B"), self.crea("C")
        anagrafica.aggiungi_alias(self.con, a, "gestionale", "VECCHIO-A", attore=U)
        anagrafica.fondi(self.con, a, b, attore=U)
        anagrafica.fondi(self.con, b, c, attore=U)
        self.assertEqual(anagrafica.entita(self.con, a)["stato"], "FUSO")
        self.assertEqual(anagrafica.entita(self.con, a)["fusa_in"], b)
        self.assertEqual(anagrafica.canonico(self.con, a), c)
        self.assertEqual(anagrafica.canonico(self.con, "id-sconosciuto"), "id-sconosciuto")
        self.assertEqual(sorted(anagrafica.equivalenti(self.con, a)), sorted([a, b, c]))
        self.assertEqual(anagrafica.equivalenti(self.con, "boh"), ["boh"])
        self.assertEqual(anagrafica.mappa_canonici(self.con), {a: c, b: c, c: c})
        vista = dict(self.con.execute("SELECT id, id_canonico FROM anagrafica_canonico"))
        self.assertEqual(vista, {a: c, b: c, c: c})
        for codice in ("A", "B", "VECCHIO-A"):                 # tutto risolve su C
            r = anagrafica.risolvi(self.con, "macchina", codice)
            self.assertEqual((r.id, r.codice), (c, "C"), codice)
        self.assertEqual(anagrafica.risolvi(self.con, "macchina", "A").id_trovato, a)
        with self.assertRaises(AnagraficaError):
            self.crea("A")                                      # il codice resta riservato
        with self.assertRaises(AnagraficaError):
            anagrafica.rinomina(self.con, a, "Z", attore=U)     # una fusa non si modifica
        with self.assertRaises(AnagraficaError):
            anagrafica.fondi(self.con, c, a, attore=U)          # ne' riceve fusioni
        with self.assertRaises(AnagraficaError):
            anagrafica.fondi(self.con, c, c, attore=U)
        art = anagrafica.crea(self.con, "articolo", "1", attore=U)
        with self.assertRaises(AnagraficaError):
            anagrafica.fondi(self.con, art, c, attore=U)        # tipi diversi
        self.assertIn(anagrafica.E_FUSA, [e["tipo"] for e in anagrafica.storico(self.con, c)])

    def test_tipi_da_config(self):
        self.assertEqual(anagrafica.tipi(self.con), TIPI)
        self.assertEqual(anagrafica.configura_tipi(self.con, TIPI), [])        # idempotente
        nuovi = {**TIPI, "macchina": {"descrizione": "Impianti",
                                      "normalizzazione": ["strip"]}}
        self.assertEqual(anagrafica.configura_tipi(self.con, nuovi), ["macchina"])
        self.assertEqual(anagrafica.normalizza(self.con, "macchina", " pr "), "pr")
        with self.assertRaises(AnagraficaError):                # un tipo non si toglie
            anagrafica.configura_tipi(self.con, {"macchina": TIPI["macchina"]})
        self.assertEqual(anagrafica.permessi(TIPI), {
            "core.anagrafica.modifica.articolo": "Anagrafica: creare e modificare Articoli",
            "core.anagrafica.modifica.macchina": "Anagrafica: creare e modificare Macchine"})

    def test_transazione_aperta_rifiutata(self):
        self.con.execute("CREATE TABLE x (a)")
        self.con.execute("INSERT INTO x VALUES (1)")
        with self.assertRaises(AnagraficaError):
            self.crea("PR-01")


class TestFileECli(unittest.TestCase):
    def setUp(self):
        for flusso in (contextlib.redirect_stdout, contextlib.redirect_stderr):
            cm = flusso(io.StringIO())
            cm.__enter__()
            self.addCleanup(cm.__exit__, None, None, None)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.comune = Path(self.tmp.name)
        (self.comune / "argo.toml").write_text(ARGO_TOML, encoding="utf-8")
        self.path = anagrafica.percorso_db(self.comune)

    def test_prepara_e_apri(self):
        with self.assertRaises(AnagraficaError):
            anagrafica.apri(self.comune)                        # shell mai avviata
        esito = anagrafica.prepara_db(self.path, TIPI)
        self.assertEqual(sorted(esito["tipi"]), ["articolo", "macchina"])
        self.assertEqual(migrazioni.versione(self.path), len(anagrafica.PASSI))
        self.assertEqual(anagrafica.prepara_db(self.path, TIPI)["tipi"], [])
        con = anagrafica.apri(self.comune)
        self.assertEqual(anagrafica.tipi(con), TIPI)
        with self.assertRaises(sqlite3.OperationalError):
            anagrafica.crea(con, "macchina", "X", attore=U)     # sola lettura
        con.close()

    def test_importa_csv_idempotente(self):
        f = self.comune / "articoli.csv"
        f.write_text("﻿codice;descrizione;unita\n252;Guanti;paia\n 0253 ;Occhiali;\n"
                     ";vuoto;\n", encoding="utf-8")
        self.assertEqual(anagrafica.main(["--comune", str(self.comune), "importa",
                                          "--tipo", "articolo", str(f)]), 1)   # riga vuota
        con = anagrafica.apri(self.comune)
        r = anagrafica.risolvi(con, "articolo", "252")
        e = anagrafica.entita(con, r.id)
        self.assertEqual((e["codice"], e["descrizione"], e["attributi"]),
                         ("000000252", "Guanti", {"unita": "paia"}))
        self.assertEqual(anagrafica.entita(con, anagrafica.risolvi(
            con, "articolo", "253").id)["attributi"], {})
        self.assertEqual(anagrafica.storico(con, r.id)[0]["attore_id"], anagrafica.SISTEMA)
        con.close()
        con = db.owned(self.path)
        esito = anagrafica.importa_csv(con, "articolo", f)
        con.close()
        self.assertEqual((esito["create"], esito["gia_presenti"]), (0, 2))

    def test_cli_come_utente_e_errori(self):
        auth.crea_admin(auth.percorso_db(self.comune), "admin", "pw")
        f = self.comune / "m.csv"
        f.write_text("codice\npr-1\n", encoding="utf-8")
        self.assertEqual(anagrafica.main(["--comune", str(self.comune), "importa",
                                          "--tipo", "macchina", "--come", "admin",
                                          str(f)]), 0)
        con = anagrafica.apri(self.comune)
        ev = anagrafica.storico(con, anagrafica.risolvi(con, "macchina", "PR-1").id)[0]
        con.close()
        self.assertNotEqual(ev["attore_id"], anagrafica.SISTEMA)
        for argomenti in (["--tipo", "macchina", "--come", "nessuno", str(f)],
                          ["--tipo", "commessa", str(f)],
                          ["--tipo", "macchina", str(self.comune / "manca.csv")]):
            self.assertEqual(anagrafica.main(["--comune", str(self.comune), "importa",
                                              *argomenti]), 1, argomenti)


# --- shell (Flask) ---------------------------------------------------------------------

VELOCE = mock.patch.object(auth, "_METODO", "pbkdf2:sha256:1000")


@unittest.skipUnless(HA_FLASK, "Flask non installato")
class TestShellAnagrafica(unittest.TestCase):
    PORTA = None

    def setUp(self):
        from core import shell
        VELOCE.start()
        self.addCleanup(VELOCE.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.comune = Path(self.tmp.name) / "comune"
        self.comune.mkdir()
        porta = f"[suite]\nporta = {self.PORTA}\n" if self.PORTA else ""
        (self.comune / "argo.toml").write_text(porta + ARGO_TOML, encoding="utf-8")
        radice = Path(self.tmp.name) / "suite"
        radice.mkdir()
        self.auth_db = self.comune / "auth.sqlite"
        self.admin = auth.crea_admin(self.auth_db, "admin", "pw-admin")
        con = db.owned(self.auth_db)
        self.mag = auth.crea_utente(con, "mag", password="pw", attore=self.admin)
        r = auth.definisci_ruolo(con, "Magazzino", ["core.anagrafica.modifica.articolo"],
                                 attore=self.admin)
        auth.assegna_ruolo(con, r, utente_id=self.mag, attore=self.admin)
        self.op = auth.crea_utente(con, "op", password="pw", attore=self.admin)
        self.token = {u: auth.login(con, u, p, durata_ore=1)
                      for u, p in (("mag", "pw"), ("op", "pw"), ("admin", "pw-admin"))}
        con.close()
        self.app = shell.create_app(self.comune, radice=radice)
        self.c = self.app.test_client()

    def come(self, username):
        self.c.set_cookie(auth.COOKIE, self.token[username], domain="localhost")

    def test_tipi_permessi_e_catalogo(self):
        self.come("mag")
        tipi = {t["tipo"]: t["modificabile"] for t in
                self.c.get("/api/anagrafica/tipi").get_json()["tipi"]}
        self.assertEqual(tipi, {"articolo": True, "macchina": False})
        self.assertEqual(self.c.get("/anagrafica").status_code, 200)
        self.assertIn("/anagrafica", self.c.get("/").get_data(as_text=True))
        self.come("admin")
        catalogo = {p["id"] for p in self.c.get("/api/utenti").get_json()["catalogo"]}
        self.assertTrue({"core.anagrafica.modifica.articolo",
                         "core.anagrafica.modifica.macchina", "core.admin"} <= catalogo)

    def test_scrittura_solo_col_permesso_del_tipo(self):
        self.come("mag")
        r = self.c.post("/api/anagrafica/entita", json={"tipo": "articolo", "codice": "252",
                                                        "descrizione": "Guanti"})
        self.assertEqual(r.status_code, 200, r.get_json())
        eid = r.get_json()["id"]
        self.assertEqual(self.c.post("/api/anagrafica/entita", json={
            "tipo": "macchina", "codice": "PR-1"}).status_code, 403)
        self.assertEqual(self.c.post("/api/anagrafica/entita", json={
            "tipo": "articolo", "codice": "0252"}).status_code, 400)     # duplicato
        self.come("op")                                                   # solo lettura
        self.assertEqual(self.c.post(f"/api/anagrafica/entita/{eid}/rinomina",
                                     json={"codice": "253"}).status_code, 403)
        ris = self.c.get("/api/anagrafica/risolvi?tipo=articolo&codice=252").get_json()
        self.assertEqual(ris["risoluzione"]["id"], eid)
        elenco = self.c.get("/api/anagrafica/entita?tipo=articolo").get_json()["entita"]
        self.assertEqual([e["codice"] for e in elenco], ["000000252"])
        self.come("mag")
        for url, dati in (("rinomina", {"codice": "253"}),
                          ("descrivi", {"attributi": {"unita": "paia"}}),
                          ("alias", {"sistema": "gestionale", "codice": "G-1"}),
                          ("stato", {"stato": "OBSOLETO"}),
                          ("stato", {"stato": "ATTIVO"})):
            r = self.c.post(f"/api/anagrafica/entita/{eid}/{url}", json=dati)
            self.assertEqual(r.status_code, 200, (url, r.get_json()))
        d = self.c.get(f"/api/anagrafica/entita/{eid}").get_json()
        self.assertEqual((d["entita"]["codice"], d["entita"]["attributi"]),
                         ("000000253", {"unita": "paia"}))
        self.assertEqual([a["codice"] for a in d["alias"]], ["G-1"])
        self.assertEqual(d["attori"], {self.mag: "mag"})
        self.assertEqual(len(d["storico"]), 6)
        altro = self.c.post("/api/anagrafica/entita", json={
            "tipo": "articolo", "codice": "9"}).get_json()["id"]
        self.assertEqual(self.c.post(f"/api/anagrafica/entita/{altro}/fondi",
                                     json={"destinazione": eid}).status_code, 200)
        d = self.c.get(f"/api/anagrafica/entita/{altro}").get_json()
        self.assertEqual((d["canonico"], d["codici"]), (eid, {eid: "000000253"}))
        d = self.c.get(f"/api/anagrafica/entita/{eid}").get_json()       # ricevuta
        self.assertEqual(d["codici"], {eid: "000000253", altro: "000000009"})
        self.assertEqual(self.c.post(f"/api/anagrafica/entita/{eid}/stato",
                                     json={"stato": "FUSO"}).status_code, 400)
        self.assertEqual(self.c.post("/api/anagrafica/entita/nessuno/rinomina",
                                     json={"codice": "1"}).status_code, 400)


def _porta_libera() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(HA_FLASK, "Flask non installato")
class TestClientHTTP(TestShellAnagrafica):
    """Un modulo scrive in anagrafica con anagrafica.client: la shell vera gira
    su una porta locale, l'attore e' l'utente della richiesta del modulo."""

    def setUp(self):
        from werkzeug.serving import make_server
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        TestClientHTTP.PORTA = _porta_libera()
        super().setUp()
        self.server = make_server("127.0.0.1", self.PORTA, self.app)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.modulo = self._modulo().test_client()

    def _modulo(self):
        from flask import Flask, jsonify, request
        m = manifest.da_dict({
            "modulo": {"nome": "magazzino", "versione": "1.0.0", "core": ">=1.0",
                       "titolo": "Magazzino"},
            "permessi": [{"id": "magazzino.vedi", "descrizione": "vedere"}],
            "anagrafica": {"tipi": ["articolo"]}})
        app = Flask("magazzino")

        @app.post("/nuovo")
        @auth.richiede_permesso("magazzino.vedi")
        def nuovo():
            try:
                eid = anagrafica.client.crea(tipo="articolo", codice=request.json["codice"])
            except PermissionError as e:
                return jsonify({"msg": str(e)}), 403
            except AnagraficaError as e:
                return jsonify({"msg": str(e)}), 400
            con = anagrafica.apri(self.comune)
            try:
                ev = anagrafica.storico(con, eid)[0]
            finally:
                con.close()
            return jsonify({"id": eid, "attore": ev["attore_id"]})
        return auth.inizializza(app, manifest=m, auth_db=self.auth_db)

    def test_client_scrive_come_utente(self):
        con = db.owned(self.auth_db)
        r = auth.definisci_ruolo(con, "Vede", ["magazzino.vedi"], attore=self.admin)
        for u in (self.mag, self.op):
            auth.assegna_ruolo(con, r, utente_id=u, attore=self.admin)
        con.close()
        self.modulo.set_cookie(auth.COOKIE, self.token["mag"], domain="localhost")
        r = self.modulo.post("/nuovo", json={"codice": "252"})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["attore"], self.mag)
        r = self.modulo.post("/nuovo", json={"codice": "252"})
        self.assertEqual(r.status_code, 400)                            # duplicato
        self.assertIn("gia' portato", r.get_json()["msg"])
        self.modulo.set_cookie(auth.COOKIE, self.token["op"], domain="localhost")
        self.assertEqual(self.modulo.post("/nuovo", json={"codice": "300"}).status_code, 403)

    def test_modulo_con_tipo_non_configurato_non_parte(self):
        from flask import Flask
        m = manifest.da_dict({
            "modulo": {"nome": "officina", "versione": "1.0.0", "core": ">=1.0",
                       "titolo": "Officina"},
            "anagrafica": {"tipi": ["commessa"]}})
        with self.assertRaises(manifest.ManifestError) as ctx:
            auth.inizializza(Flask("officina"), manifest=m, auth_db=self.auth_db)
        self.assertIn("commessa", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
