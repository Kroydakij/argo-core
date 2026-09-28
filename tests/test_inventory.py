"""Test di core.inventory 1.0 (articoli in anagrafica, movimenti con busta).
Solo stdlib: il test fa da kernel (scrive l'anagrafica) e da modulo."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import anagrafica, busta, db, inventory, manifest  # noqa: E402

CAUSALI = {"CARICO": "+", "CONSUMO": "-", "RETTIFICA_PIU": "+", "RETTIFICA_MENO": "-"}
TIPI = {"articolo": {"descrizione": "Articoli", "normalizzazione": ["strip", "zfill:9"]},
        "macchina": {"descrizione": "Macchine", "normalizzazione": ["strip"]}}
M = manifest.da_dict({
    "modulo": {"nome": "magazzino", "versione": "1.0.0", "core": ">=0.4", "titolo": "M"},
    "anagrafica": {"tipi": ["articolo"]},
    "eventi": [{"tipo": "magazzino.movimento", "versione": 1, "entita": "articolo",
                "descrizione": "movimento"},
               {"tipo": "magazzino.soglia_impostata", "versione": 1,
                "entita": "articolo", "descrizione": "soglia"}],
})
K = "kernel"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ana_path = Path(self.tmp.name) / "anagrafica.sqlite"
        anagrafica.prepara_db(self.ana_path, TIPI)
        self.inv = inventory.Inventario(CAUSALI, manifest=M, anagrafica=self.ana_path)
        self.con = db.owned(Path(self.tmp.name) / "magazzino.sqlite")
        self.addCleanup(self.con.close)
        self.inv.migra(self.con)
        self.con.commit()
        self.guanti = self.articolo("A1", "Guanti", unita="paia")

    def articolo(self, codice, descrizione="", tipo="articolo", **attributi):
        con = db.owned(self.ana_path)
        try:
            return anagrafica.crea(con, tipo, codice, attore=K, descrizione=descrizione,
                                   attributi=attributi)
        finally:
            con.close()

    def kernel(self, f, *a, **kw):
        con = db.owned(self.ana_path)
        try:
            return f(con, *a, attore=K, **kw)
        finally:
            con.close()


class TestCostruzione(Base):
    def test_fail_fast(self):
        with self.assertRaises(ValueError):
            inventory.Inventario({}, manifest=M, anagrafica=self.ana_path)
        with self.assertRaises(ValueError):
            inventory.Inventario({"X": "su"}, manifest=M, anagrafica=self.ana_path)
        with self.assertRaises(ValueError):
            inventory.Inventario(CAUSALI, manifest=M, anagrafica=self.ana_path,
                                 tabella_movimenti="m; DROP TABLE m")
        with self.assertRaises(ValueError):                   # tipo non nel manifest
            inventory.Inventario(CAUSALI, manifest=M, anagrafica=self.ana_path,
                                 tipo="macchina")
        with self.assertRaises(ValueError):                   # evento non dichiarato
            inventory.Inventario(CAUSALI, manifest=M, anagrafica=self.ana_path,
                                 evento_movimento="magazzino.boh")
        with self.assertRaises(ValueError):
            inventory.Inventario.da_config({"consenti_negativo": True}, manifest=M,
                                           anagrafica=self.ana_path)

    def test_da_config(self):
        inv = inventory.Inventario.da_config(
            {"causali": CAUSALI, "consenti_negativo": True, "tipo": "articolo"},
            manifest=M, anagrafica=self.ana_path)
        self.assertTrue(inv.consenti_negativo)
        self.assertEqual((inv.evento_movimento, inv.evento_soglia),
                         ("magazzino.movimento", "magazzino.soglia_impostata"))

    def test_migra_idempotente(self):
        self.inv.migra(self.con)
        vis = {r[0] for r in self.con.execute(
            "SELECT name FROM sqlite_master WHERE type='view'")}
        self.assertIn("inventario_saldi", vis)


class TestInventario(Base):
    def test_giacenza_come_proiezione_con_busta(self):
        g = self.inv.giacenza(self.con, self.guanti)
        self.assertEqual((g["codice"], g["descrizione"], g["unita"], g["giacenza"]),
                         ("A1", "Guanti", "paia", 0))
        self.inv.movimenta(self.con, self.guanti, 50, "CARICO", attore_id="u-rossi")
        self.inv.movimenta(self.con, self.guanti, 2, "CONSUMO", attore_id="u-bianchi")
        self.assertEqual(self.inv.giacenza(self.con, self.guanti)["giacenza"], 48)
        sto = self.inv.storico(self.con, self.guanti)
        self.assertEqual([(m["quantita"], m["attore_id"], m["tipo"], m["versione"])
                          for m in sto],
                         [(50.0, "u-rossi", "magazzino.movimento", 1),
                          (-2.0, "u-bianchi", "magazzino.movimento", 1)])
        self.assertTrue(sto[0]["ts_utc"].endswith("Z"))

    def test_movimenti_rifiutati(self):
        m = lambda *a, **kw: self.inv.movimenta(self.con, *a, attore_id="u", **kw)  # noqa: E731
        with self.assertRaises(ValueError):
            m(self.guanti, 1, "INVENTATA")                     # causale ignota
        with self.assertRaises(ValueError):
            m(self.guanti, -5, "CARICO")                       # quantita negativa
        with self.assertRaises(ValueError):
            m(self.guanti, 0, "CARICO")
        with self.assertRaises(ValueError):
            m(self.guanti, 1, "CARICO", sorgente="BOH")
        with self.assertRaises(ValueError):
            m("id-mai-visto", 1, "CARICO")                     # non in anagrafica
        with self.assertRaises(ValueError):
            m(self.articolo("PR-1", tipo="macchina"), 1, "CARICO")   # altro tipo
        with self.assertRaises(ValueError):                    # attore mai dedotto
            self.inv.movimenta(self.con, self.guanti, 1, "CARICO")
        self.kernel(anagrafica.rendi_obsoleta, self.guanti)
        with self.assertRaises(ValueError):
            m(self.guanti, 1, "CARICO")                        # obsoleto
        self.assertIsNone(self.inv.giacenza(self.con, self.guanti))

    def test_giacenza_insufficiente(self):
        self.inv.movimenta(self.con, self.guanti, 5, "CARICO", attore_id="u")
        with self.assertRaises(inventory.GiacenzaInsufficiente):
            self.inv.movimenta(self.con, self.guanti, 6, "CONSUMO", attore_id="u")
        inv2 = inventory.Inventario(CAUSALI, manifest=M, anagrafica=self.ana_path,
                                    consenti_negativo=True, tabella_movimenti="m2",
                                    tabella_soglie="s2", vista_saldi="v2")
        inv2.migra(self.con)
        inv2.movimenta(self.con, self.guanti, 6, "CONSUMO", attore_id="u")
        self.assertEqual(inv2.giacenza(self.con, self.guanti)["giacenza"], -6)

    def test_soglie_e_sotto_scorta(self):
        senza = self.articolo("A2", "Senza soglia")            # mai segnalato
        self.inv.imposta_soglia(self.con, self.guanti, 10, attore_id="u")
        self.inv.movimenta(self.con, self.guanti, 11, "CARICO", attore_id="u")
        self.assertEqual(self.inv.sotto_scorta(self.con), [])      # 11 > 10
        self.inv.movimenta(self.con, self.guanti, 1, "CONSUMO", attore_id="u")
        self.assertEqual([r["entita_id"] for r in self.inv.sotto_scorta(self.con)],
                         [self.guanti])
        self.inv.imposta_soglia(self.con, self.guanti, None, attore_id="u")   # tolta
        self.assertEqual(self.inv.sotto_scorta(self.con), [])
        self.assertEqual({r["entita_id"] for r in self.inv.giacenza(self.con)},
                         {self.guanti, senza})
        with self.assertRaises(ValueError):
            self.inv.imposta_soglia(self.con, self.guanti, -1, attore_id="u")

    def test_rinomina_e_fusione_senza_riscrivere_i_movimenti(self):
        doppione = self.articolo("A9", "Guanti (doppione)")
        self.inv.movimenta(self.con, self.guanti, 10, "CARICO", attore_id="u")
        self.inv.movimenta(self.con, doppione, 5, "CARICO", attore_id="u")
        self.kernel(anagrafica.rinomina, self.guanti, "B1")
        self.assertEqual(self.inv.giacenza(self.con, self.guanti)["codice"], "B1")
        self.kernel(anagrafica.fondi, doppione, self.guanti)
        self.assertEqual(self.inv.giacenza(self.con, self.guanti)["giacenza"], 15)
        self.assertEqual(self.inv.giacenza(self.con, doppione)["entita_id"], self.guanti)
        self.assertEqual(len(self.inv.giacenza(self.con)), 1)
        self.inv.movimenta(self.con, doppione, 1, "CONSUMO", attore_id="u")  # ID fuso
        sto = self.inv.storico(self.con, self.guanti)
        self.assertEqual([m["entita_id"] for m in sto],
                         [self.guanti, doppione, self.guanti])   # log intatto, ultimo canonico

    def test_colonne_extra(self):
        inv = inventory.Inventario(CAUSALI, manifest=M, anagrafica=self.ana_path,
                                   tabella_movimenti="mx", tabella_soglie="sx",
                                   vista_saldi="vx")
        inv.migra(self.con, extra_movimenti={"commessa": "TEXT"})
        inv.movimenta(self.con, self.guanti, 2, "CARICO", attore_id="u",
                      extra={"commessa": "K42"})
        self.assertEqual(inv.storico(self.con, self.guanti)[0]["commessa"], "K42")
        with self.assertRaises(ValueError):
            inv.movimenta(self.con, self.guanti, 1, "CARICO", attore_id="u",
                          extra={"bad; drop": 1})

    def test_check_sorgente_a_livello_db(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute(
                "INSERT INTO inventario_movimenti (uid, tipo, versione, ts_utc, offset_min,"
                " attore_id, quantita, causale, sorgente) "
                "VALUES ('x', 't', 1, 'x', 0, 'u', 1, 'CARICO', 'BOH')")


class TestAdozione0x(Base):
    """Un inventario 0.4: si esportano gli articoli per la CLI dell'anagrafica,
    poi si adottano i movimenti. Le tabelle 0.x restano intatte."""

    def setUp(self):
        super().setUp()
        c = self.con
        c.execute("""CREATE TABLE articoli (id INTEGER PRIMARY KEY AUTOINCREMENT,
            codice TEXT NOT NULL UNIQUE, descrizione TEXT NOT NULL DEFAULT '',
            unita TEXT NOT NULL DEFAULT 'pz', soglia_minima REAL,
            attivo INTEGER NOT NULL DEFAULT 1, creato_il TEXT)""")
        c.execute("""CREATE TABLE movimenti (id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, codice TEXT NOT NULL REFERENCES articoli(codice),
            quantita REAL NOT NULL, causale TEXT NOT NULL,
            sorgente TEXT NOT NULL DEFAULT 'MANUALE', operatore TEXT, note TEXT)""")
        c.executemany("INSERT INTO articoli (codice, descrizione, unita, soglia_minima, "
                      "attivo) VALUES (?,?,?,?,?)",
                      [("000000252", "Occhiali", "pz", 5, 1),
                       ("000000300", "Vecchio", "pz", None, 0),
                       ("000000999", "Mai importato", "pz", None, 1)])
        c.executemany("INSERT INTO movimenti (ts, codice, quantita, causale, operatore) "
                      "VALUES (?,?,?,?,?)",
                      [("2026-03-01 08:00:00", "000000252", 20, "CARICO", "rossi"),
                       ("2026-03-02 09:00:00", "000000252", -3, "CONSUMO", None),
                       ("2026-03-02 10:00:00", "000000999", 1, "CARICO", None)])
        c.commit()

    def test_esporta_importa_adotta(self):
        csv_path = Path(self.tmp.name) / "articoli.csv"
        self.assertEqual(self.inv.esporta_articoli_0x(self.con, csv_path), 3)
        con = db.owned(self.ana_path)
        righe = csv_path.read_text(encoding="utf-8").splitlines()
        csv_path.write_text("\n".join(r for r in righe if "999" not in r) + "\n",
                            encoding="utf-8")                 # uno non importato
        anagrafica.importa_csv(con, "articolo", csv_path)
        con.close()
        prima = [dict(r) for r in self.con.execute("SELECT * FROM movimenti")]
        esito = self.inv.adotta_0x(self.con)
        self.assertEqual((esito["movimenti"], esito["soglie"], esito["codici_non_trovati"],
                          esito["disattivati"]),
                         (2, 1, ["000000999"], ["000000300"]))
        self.assertEqual(self.inv.adotta_0x(self.con)["gia_adottati"], 2)   # idempotente
        ana = anagrafica.apri(Path(self.tmp.name))
        eid = anagrafica.risolvi(ana, "articolo", "252").id
        ana.close()
        g = self.inv.giacenza(self.con, eid)
        self.assertEqual((g["giacenza"], g["soglia_minima"]), (17, 5))
        sto = self.inv.storico(self.con, eid)
        self.assertEqual((sto[0]["note"], sto[0]["id_0x"], sto[0]["attore_id"]),
                         ("operatore: rossi", 1, "sistema"))
        self.assertEqual(busta.ora_locale(sto[0]).strftime("%Y-%m-%d %H:%M"),
                         "2026-03-01 08:00")                  # ora locale di allora
        self.assertEqual([dict(r) for r in self.con.execute("SELECT * FROM movimenti")],
                         prima)                               # 0.x intatta


if __name__ == "__main__":
    unittest.main()
