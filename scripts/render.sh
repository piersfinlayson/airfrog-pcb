#!/bin/sh
# Render each variant's generated board in 3D to images/, from above and below.
set -e
cd "$(dirname "$0")/.."
. scripts/kicad.sh
mkdir -p images build/kicad-config/9.0
# the renderer follows the 3D viewer settings in KiCad's config folder, so it uses its own, with through-hole models
# shown
echo '{"render": {"show_footprints_normal": true, "show_footprints_insert": true}}' > build/kicad-config/9.0/3d_viewer.json
for V in c3 s3; do
    P=airfrog-$V
    # The leading space in --rotate stops kicad-cli reading the negative value as an option.
    KICAD_CONFIG_HOME="$PWD/build/kicad-config" $K pcb render --quality high --floor --perspective \
        --rotate ' -50,0,25' --zoom 0.6 --width 2000 --height 2000 -o images/$P-top.png kicad/$V/$P.kicad_pcb \
        >/dev/null 2>&1
    KICAD_CONFIG_HOME="$PWD/build/kicad-config" $K pcb render --quality high --floor --perspective \
        --rotate '130,0,155' --zoom 0.6 --width 2000 --height 2000 -o images/$P-bottom.png kicad/$V/$P.kicad_pcb \
        >/dev/null 2>&1
    python3 scripts/crop_image.py images/$P-top.png images/$P-bottom.png
done
