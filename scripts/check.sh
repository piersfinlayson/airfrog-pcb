#!/bin/sh
# Generate each variant's schematic, run ERC, compare its netlist and render PNGs. Fails on any problem.
# Each variant is written to its own folder under the first argument.
set -e
cd "$(dirname "$0")/.."
D=${1:-build}
. scripts/kicad.sh
status=0
for V in ${VARIANTS:-c3 s3}; do
    P=airfrog-$V
    O=$D/$V
    mkdir -p "$O"
    cp -R kicad-src/. "$O/"
    mv "$O/airfrog.kicad_pro" "$O/$P.kicad_pro"
    python3 scripts/airfrog.py $V "$O" || status=1
    $K sch erc --severity-all --format json -o "$O/erc.json" "$O/$P.kicad_sch" 2>&1 | grep -v Fontconfig || true
    $K sch export netlist -o "$O/$P.net" "$O/$P.kicad_sch" 2>&1 | grep -v Fontconfig | grep -v Done || true
    $K sch export pdf -o "$O/$P.pdf" "$O/$P.kicad_sch" 2>&1 | grep -v Fontconfig | grep -v -E 'Done|Plotted' || true
    echo "== $V"
    python3 scripts/compare.py "$O" $V || status=1
    pdftoppm -png -r ${DPI:-100} "$O/$P.pdf" "$O/sheet"
done
exit $status
