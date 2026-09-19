#!/usr/bin/env bash
set -euo pipefail

TOOLKIT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

mkdir -p "$WORK_DIR/.git" "$WORK_DIR/components"
cat > "$WORK_DIR/components/example.py" <<'PY'
import Part


def make_shape():
    return Part.makeBox(2, 3, 4)
PY

cat > "$WORK_DIR/geometry_success.py" <<'PY'
from components.example import make_shape
from freecad_toolkit import PreviewAssembly


def create_geometry(doc):
    assembly = PreviewAssembly()
    assembly.group("Test").add(make_shape())
    return assembly.build(doc)
PY

cat > "$WORK_DIR/geometry_failure.py" <<'PY'
raise RuntimeError("expected integration failure")
PY

"$TOOLKIT_ROOT/freecad_headless.py" test "$WORK_DIR/geometry_success.py" \
    >"$WORK_DIR/success.log" 2>&1
grep -q "Bounding Box: 2.00 x 3.00 x 4.00 mm" "$WORK_DIR/success.log"
grep -q "Test erfolgreich" "$WORK_DIR/success.log"

if "$TOOLKIT_ROOT/freecad_headless.py" test "$WORK_DIR/geometry_failure.py" \
    >"$WORK_DIR/failure.log" 2>&1; then
    echo "Expected invalid geometry to return a non-zero exit code" >&2
    exit 1
fi
grep -q "expected integration failure" "$WORK_DIR/failure.log"

echo "External project integration test passed."
