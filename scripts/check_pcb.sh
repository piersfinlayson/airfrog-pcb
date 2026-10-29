#!/bin/sh
# Generate each variant's schematic and PCB in its own folder under the first argument, run DRC with schematic
# parity and render the layers to PNG. Fails on any problem.
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
    echo "== $V"
    # hide KiCad's messages on stderr but show a Python traceback
    $PY scripts/airfrog_pcb.py $V "$O" 2>"$O/airfrog_pcb.err" || status=1
    sed -n '/^Traceback/,$p' "$O/airfrog_pcb.err" >&2
    $K pcb drc --schematic-parity --severity-all --format json -o "$O/drc.json" "$O/$P.kicad_pcb" 2>&1 | grep -v -E 'Fontconfig|Saved|Found' || true
    python3 scripts/drc_summary.py "$O/drc.json" || status=1
    # board top left is at (100, 100) mm on the page
    DPI=${DPI:-600}
    X=$(python3 -c "print(int((100 - 1) / 25.4 * $DPI))")
    S=$(python3 -c "print(int(37 / 25.4 * $DPI))")
    render() {
        $K pcb export pdf --mode-single --drill-shape-opt 1 $3 -l "$2" -o "$O/$1.pdf" "$O/$P.kicad_pcb" >/dev/null 2>&1
        pdftoppm -png -singlefile -r $DPI -x $X -y $X -W $S -H $S "$O/$1.pdf" "$O/$1"
    }
    render top F.Cu,F.SilkS,Edge.Cuts
    render in2 In2.Cu,Edge.Cuts
    render bottom B.Cu,B.SilkS,Edge.Cuts
    # 3D views from above and below. The renderer follows the 3D viewer settings in KiCad's config folder, so it uses
    # its own, with through-hole models shown.
    mkdir -p "$D/kicad-config/9.0"
    echo '{"render": {"show_footprints_normal": true, "show_footprints_insert": true}}' > "$D/kicad-config/9.0/3d_viewer.json"
    KICAD_CONFIG_HOME="$PWD/$D/kicad-config" $K pcb render --quality high --perspective --rotate ' -50,0,25' --zoom 0.7 \
        --width 1200 --height 1200 -o "$O/3d-top.png" "$O/$P.kicad_pcb" >/dev/null 2>&1
    KICAD_CONFIG_HOME="$PWD/$D/kicad-config" $K pcb render --quality high --perspective --rotate '230,0,-25' --zoom 0.7 \
        --width 1200 --height 1200 -o "$O/3d-bottom.png" "$O/$P.kicad_pcb" >/dev/null 2>&1
done
exit $status
