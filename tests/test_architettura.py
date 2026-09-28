"""Regole tra i livelli di core/ (ADR-000), verificate sul codice. Solo stdlib."""
import ast
import subprocess
import sys
import unittest
from pathlib import Path

RADICE = Path(__file__).resolve().parents[1]
CORE = RADICE / "core"
sys.path.insert(0, str(RADICE))
import core  # noqa: E402


def _importati(file: Path) -> set[str]:
    """Moduli di core/ importati da un file (anche dentro le funzioni)."""
    out = set()
    for nodo in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
        if isinstance(nodo, ast.ImportFrom):
            if nodo.level == 1 and nodo.module:              # from .x import y
                out.add(nodo.module.split(".")[0])
            elif nodo.level == 1:                            # from . import x, y
                out.update(a.name for a in nodo.names)
            elif nodo.module and nodo.module.split(".")[0] == "core":
                parti = nodo.module.split(".")
                out.update([parti[1]] if len(parti) > 1 else [a.name for a in nodo.names])
    return out - {"__version__"}


def _python(*argomenti: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *argomenti], cwd=RADICE,
                          capture_output=True, text=True, timeout=60)


class TestLivelli(unittest.TestCase):
    def test_ogni_modulo_ha_un_livello(self):
        moduli = {p.stem for p in CORE.glob("*.py")} - {"__init__"}
        livelli = set(core.KERNEL) | set(core.UTILITY) | set(core.APPLICAZIONI)
        self.assertEqual(moduli, livelli)
        self.assertFalse(set(core.KERNEL) & set(core.UTILITY))

    def test_il_kernel_non_importa_utility(self):
        for nome in core.KERNEL:
            usati = _importati(CORE / f"{nome}.py")
            self.assertFalse(usati & set(core.UTILITY),
                             f"core.{nome} importa utility: {usati & set(core.UTILITY)}")
            self.assertFalse(usati & set(core.APPLICAZIONI),
                             f"core.{nome} importa un'applicazione")

    def test_import_core_carica_solo_il_kernel(self):
        r = _python("-W", "error", "-c",
                    "import sys, core; "
                    "print(sorted(m[5:] for m in sys.modules if m.startswith('core.')))")
        self.assertEqual(r.returncode, 0, r.stderr)
        caricati = set(eval(r.stdout))
        self.assertFalse(caricati - set(core.KERNEL), caricati)
        r = _python("-c", "import core; core.auth.ha_permesso; core.board")
        self.assertIn("from core import board", r.stderr)      # utility: per nome

    def test_cli_del_kernel_senza_doppio_import(self):
        for mod in ("core.migrazioni", "core.auth", "core.anagrafica"):
            r = _python("-W", "error::RuntimeWarning", "-m", mod, "--help")
            self.assertEqual(r.returncode, 0, f"{mod}: {r.stderr}")


class TestDeprecazioni(unittest.TestCase):
    def test_ident_0x(self):
        from core import migrate
        with self.assertWarns(DeprecationWarning):
            self.assertEqual(migrate._ident("t"), migrate.ident("t"))


if __name__ == "__main__":
    unittest.main()
