# Set K to kicad-cli and PY to a Python with KiCad's pcbnew module.
APP=/Applications/KiCad/KiCad.app/Contents
K=${KICAD_CLI:-$(command -v kicad-cli || echo "$APP/MacOS/kicad-cli")}
PY=${KICAD_PYTHON:-$APP/Frameworks/Python.framework/Versions/Current/bin/python3}
[ -n "$KICAD_PYTHON" ] || [ -x "$PY" ] || PY=python3
