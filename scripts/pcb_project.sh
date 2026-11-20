#!/bin/sh
# Write a variant's schematic, libraries and 3D models into pcb/<variant>/, beside its committed board and project
# file, so that the folder opens in KiCad as a complete project.
set -e
cd "$(dirname "$0")/.."
V=$1
O=pcb/$V
mkdir -p "$O"
cp -R kicad-src/airfrog.kicad_sym kicad-src/airfrog.pretty kicad-src/airfrog.3dshapes kicad-src/fp-lib-table \
    kicad-src/sym-lib-table "$O/"
python3 scripts/airfrog.py "$V" "$O"
