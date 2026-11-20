#!/bin/sh
# Generate kicad/ and jlc/ for each variant from the current commit, with its board from pcb/<variant>/.
set -e
cd "$(dirname "$0")/.."
. scripts/kicad.sh
if [ -n "$(git status --porcelain)" ]; then
    echo "uncommitted changes or untracked files" >&2
    exit 1
fi
C=$(git rev-parse --short HEAD)
rm -rf ./kicad ./jlc
mkdir -p build
for V in mini pro; do
    P=airfrog-$V
    O=kicad/$V
    mkdir -p "$O"
    cp -R kicad-src/. "$O/"
    mv "$O/airfrog.kicad_pro" "$O/$P.kicad_pro"
    cp pcb/$V/$P.kicad_pro "$O/"
    python3 - "$O/$P.kicad_pro" "$P" "$C" <<'PY'
import json, sys
p, project, commit = sys.argv[1:4]
d = json.load(open(p))
d['meta']['filename'] = project + '.kicad_pro'
d['text_variables']['COMMIT'] = commit
open(p, 'w').write(json.dumps(d, indent=2) + '\n')
PY
    python3 scripts/airfrog.py $V "$O"
    cp pcb/$V/$P.kicad_pcb "$O/"
    $K sch export pdf -o "$O/$P-schematic.pdf" "$O/$P.kicad_sch" >/dev/null 2>&1
    $PY scripts/jlc.py $V 2>build/jlc-$V.err || { sed -n '/^Traceback/,$p' build/jlc-$V.err >&2; exit 1; }
    # KiCad's backups and local settings, and the name's font outlines, which only glyphs.py reads
    rm -rf "$O"/*-backups "$O"/*.kicad_prl "$O/bauhaus93.json"
done
