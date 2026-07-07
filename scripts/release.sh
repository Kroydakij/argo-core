#!/usr/bin/env bash
# Costruisce lo zip di release di argo-core, RIPRODUCIBILE.
#
# Uso:
#   scripts/release.sh                 # archivia HEAD, versione da core/__init__.py
#   scripts/release.sh v0.3.0          # archivia un tag/commit specifico
#
# Riproducibilita': git archive e' deterministico a parita' di albero e di
# riferimento (per un tag/commit usa la data del commit come mtime delle voci).
# Lo stesso commit produce sempre lo stesso zip, byte per byte.
#
# Il contenuto rispetta .gitattributes (export-ignore): nel pacchetto entra il
# framework, non i file di sviluppo (tests/, examples/, scripts/, CI).
set -euo pipefail

cd "$(dirname "$0")/.."

REF="${1:-HEAD}"

# versione: dall'argomento (se e' un tag vX.Y.Z) o da core/__init__.py
if [[ "$REF" =~ ^v[0-9] ]]; then
    VER="${REF#v}"
else
    VER="$(python -c "import core, sys; sys.stdout.write(core.__version__)")"
fi

OUT="dist/argo-core-${VER}.zip"
mkdir -p dist

git archive --format=zip --prefix="argo-core-${VER}/" -o "$OUT" "$REF"

echo "creato $OUT"
python - "$OUT" <<'PY'
import hashlib, sys
h = hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest()
print(f"sha256  {h}")
PY
