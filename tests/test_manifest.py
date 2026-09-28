"""Test di core.manifest (ADR-003). Solo stdlib."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import manifest  # noqa: E402
from core.manifest import ManifestError  # noqa: E402

RADICE_REPO = Path(__file__).resolve().parents[1]

BASE = {
    "modulo": {"nome": "andon", "versione": "1.2.0", "core": ">=1.0,<2.0",
               "titolo": "Andon", "descrizione": "Fermate"},
    "permessi": [{"id": "andon.vedi", "descrizione": "Vedere"},
                 {"id": "andon.chiudi_fermata", "descrizione": "Chiudere"}],
    "menu": [{"titolo": "Fermate", "percorso": "/", "permesso": "andon.vedi"}],
    "anagrafica": {"tipi": ["macchina"]},
    "eventi": [{"tipo": "andon.fermata_chiusa", "versione": 2,
                "entita": "macchina", "descrizione": "Chiusura"}],
}

TOML_VALIDO = """
[modulo]
nome = "andon"
versione = "1.2.0"
core = ">=1.0,<2.0"
titolo = "Andon"

[[permessi]]
id = "andon.vedi"
descrizione = "Vedere"

[[menu]]
titolo = "Fermate"
percorso = "/"
permesso = "andon.vedi"
"""


def _con(**modifiche):
    d = copy.deepcopy(BASE)
    for percorso, valore in modifiche.items():
        *sezioni, chiave = percorso.split("__")
        nodo = d
        for s in sezioni:
            nodo = nodo[int(s)] if s.isdigit() else nodo[s]
        if valore is ...:
            del nodo[int(chiave) if chiave.isdigit() else chiave]
        else:
            nodo[chiave] = valore
    return d


class TestValidazione(unittest.TestCase):
    def valida(self, d, **kw):
        kw.setdefault("versione_core", "1.3.0")
        return manifest.da_dict(d, **kw)

    def test_manifest_valido(self):
        m = self.valida(BASE, nome_cartella="andon")
        self.assertEqual((m.nome, m.versione, m.titolo), ("andon", "1.2.0", "Andon"))
        self.assertTrue(m.dichiara_permesso("andon.chiudi_fermata"))
        self.assertFalse(m.dichiara_permesso("andon.altro"))
        self.assertEqual(m.evento("andon.fermata_chiusa").versione, 2)
        self.assertIsNone(m.evento("andon.boh"))
        self.assertEqual(m.tipi_anagrafica, ("macchina",))
        self.assertEqual(m.to_dict()["menu"][0]["percorso"], "/")

    def test_sezioni_facoltative(self):
        m = self.valida({"modulo": BASE["modulo"]})
        self.assertEqual((m.permessi, m.menu, m.eventi, m.tipi_anagrafica),
                         ((), (), (), ()))

    def test_chiavi_sconosciute_rifiutate(self):
        for d in (_con(extra={"x": 1}), _con(modulo__porta=4701),
                  _con(permessi__0__ruolo="admin"),
                  _con(eventi__0__payload="json")):
            with self.assertRaises(ManifestError):
                self.valida(d)

    def test_chiavi_obbligatorie(self):
        for d in (_con(modulo=...), _con(modulo__titolo=...),
                  _con(permessi__0__descrizione=...),
                  _con(eventi__0__versione=...), _con(modulo__nome="")):
            with self.assertRaises(ManifestError):
                self.valida(d)

    def test_nome_uguale_alla_cartella(self):
        with self.assertRaises(ManifestError):
            self.valida(BASE, nome_cartella="altro")

    def test_namespace_permessi_ed_eventi(self):
        for d in (_con(permessi__0__id="admin"),
                  _con(permessi__0__id="altro.vedi"),
                  _con(permessi__0__id="andon.Vedi"),
                  _con(permessi__0__id="core.admin"),
                  _con(eventi__0__tipo="board.fermata_chiusa")):
            with self.assertRaises(ManifestError):
                self.valida(d)

    def test_duplicati(self):
        d = _con()
        d["permessi"].append({"id": "andon.vedi", "descrizione": "di nuovo"})
        with self.assertRaises(ManifestError):
            self.valida(d)

    def test_menu_con_permesso_non_dichiarato(self):
        with self.assertRaises(ManifestError):
            self.valida(_con(menu__0__permesso="andon.segreto"))
        with self.assertRaises(ManifestError):
            self.valida(_con(menu__0__percorso="storico"))

    def test_evento(self):
        for d in (_con(eventi__0__versione=0), _con(eventi__0__versione="1"),
                  _con(eventi__0__versione=True),
                  _con(eventi__0__entita="commessa")):   # tipo non dichiarato
            with self.assertRaises(ManifestError):
                self.valida(d)
        m = self.valida(_con(eventi__0__entita=...))       # entita facoltativa
        self.assertEqual(m.eventi[0].entita, "")

    def test_versione_semver(self):
        for v in ("1.2", "v1.2.0", "1.2.0-beta"):
            with self.assertRaises(ManifestError):
                self.valida(_con(modulo__versione=v))

    def test_compatibilita_core(self):
        with self.assertRaises(ManifestError):
            self.valida(BASE, versione_core="2.0.0")
        with self.assertRaises(ManifestError):
            self.valida(BASE, versione_core="0.9.9")
        for cattivo in ("1.0", "<2.0", ">=1.0;<2.0", "~1.0"):
            with self.assertRaises(ManifestError):
                self.valida(_con(modulo__core=cattivo))

    def test_compatibile(self):
        self.assertTrue(manifest.compatibile(">=1.0,<2.0", "1.0.0"))
        self.assertTrue(manifest.compatibile(">=1.0,<2.0", "1.9.9"))
        self.assertFalse(manifest.compatibile(">=1.0,<2.0", "2.0.0"))
        self.assertTrue(manifest.compatibile(">=0.4", "0.4.0"))
        self.assertTrue(manifest.compatibile("==1.2.3", "1.2.3"))

    def test_verifica_tipi(self):
        m = self.valida(BASE)
        manifest.verifica_tipi(m, {"macchina", "articolo"})
        with self.assertRaises(ManifestError) as ctx:
            manifest.verifica_tipi(m, {"articolo"})
        self.assertIn("macchina", str(ctx.exception))


class TestFileEScansione(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.radice = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _modulo(self, nome, manifest_toml=TOML_VALIDO, porta=4701):
        d = self.radice / nome
        d.mkdir()
        if manifest_toml is not None:
            (d / "manifest.toml").write_text(manifest_toml, encoding="utf-8")
        if porta is not None:
            (d / f"{nome}.toml").write_text(f"[app]\nporta = {porta}\n",
                                            encoding="utf-8")
        return d

    def test_carica_da_cartella_e_da_file(self):
        d = self._modulo("andon")
        self.assertEqual(manifest.carica(d, versione_core="1.0.0").nome, "andon")
        self.assertEqual(manifest.carica(d / "manifest.toml",
                                         versione_core="1.0.0").nome, "andon")

    def test_carica_errori(self):
        with self.assertRaises(ManifestError):
            manifest.carica(self.radice / "manca")
        d = self._modulo("rotto", "[modulo\n")
        with self.assertRaises(ManifestError):
            manifest.carica(d)

    def test_scansiona(self):
        self._modulo("andon")                                    # valido
        self._modulo("board", TOML_VALIDO)                       # nome != cartella
        self._modulo("senzaporta", TOML_VALIDO.replace('"andon"', '"senzaporta"'),
                     porta=None)
        self._modulo("legacy", manifest_toml=None)               # niente manifest
        (self.radice / "core").mkdir()                           # ignorata
        (self.radice / "file.txt").write_text("x")
        esiti = {s.nome: s for s in manifest.scansiona(self.radice,
                                                       versione_core="1.0.0")}
        self.assertEqual(set(esiti), {"andon", "board", "senzaporta"})
        self.assertIsNone(esiti["andon"].errore)
        self.assertEqual(esiti["andon"].porta, 4701)
        self.assertIsNone(esiti["board"].manifest)
        self.assertIn("cartella", esiti["board"].errore)
        self.assertEqual(esiti["board"].porta, 4701)             # porta nota comunque
        self.assertIsNone(esiti["senzaporta"].manifest)
        self.assertIn("porta", esiti["senzaporta"].errore)

    def test_manifest_dell_esempio_presenze_valido(self):
        # l'esempio nel repo deve restare valido per la versione corrente di core
        m = manifest.carica(RADICE_REPO / "examples" / "presenze")
        self.assertTrue(m.dichiara_permesso("presenze.registra_movimento"))
        esiti = manifest.scansiona(RADICE_REPO / "examples")
        self.assertEqual([(s.nome, s.porta, s.errore) for s in esiti],
                         [("presenze", 4710, None)])


if __name__ == "__main__":
    unittest.main()
