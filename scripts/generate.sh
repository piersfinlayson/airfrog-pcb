#!/bin/sh
# Generate kicad/ and jlc/ for each variant from the current commit.
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
for V in c3 s3; do
    P=airfrog-$V
    O=kicad/$V
    mkdir -p "$O"
    cp -R kicad-src/. "$O/"
    mv "$O/airfrog.kicad_pro" "$O/$P.kicad_pro"
    python3 - "$O/$P.kicad_pro" "$P" "$C" <<'PY'
import json, sys
p, project, commit = sys.argv[1:4]
d = json.load(open(p))
d['meta']['filename'] = project + '.kicad_pro'
d['text_variables']['COMMIT'] = commit
open(p, 'w').write(json.dumps(d, indent=2) + '\n')
PY
    python3 scripts/airfrog.py $V "$O"
    # hide KiCad's messages on stderr but show a Python traceback
    $PY scripts/airfrog_pcb.py $V "$O" > build/airfrog_pcb-$V.log 2>build/airfrog_pcb-$V.err || {
        sed -n '/^Traceback/,$p' build/airfrog_pcb-$V.err >&2; cat build/airfrog_pcb-$V.log >&2; exit 1; }
    $K sch export pdf -o "$O/$P-schematic.pdf" "$O/$P.kicad_sch" >/dev/null 2>&1
    $PY scripts/jlc.py $V 2>build/jlc-$V.err || { sed -n '/^Traceback/,$p' build/jlc-$V.err >&2; exit 1; }
    # KiCad's backups and local settings, and the name's font outlines, which only the generator reads
    rm -rf "$O"/*-backups "$O"/*.kicad_prl "$O/bauhaus93.json"
done
