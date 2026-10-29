#!/bin/sh
# Write the release files for tag $1 to folder $2. The tag must match VERSION and have a dated section in
# CHANGELOG.md.
set -e
cd "$(dirname "$0")/.."
T=$1
D=${2:-build/release}
V=$(cat VERSION)
if [ "$T" != "$V" ]; then
    echo "tag $T doesn't match VERSION ($V)" >&2
    exit 1
fi
python3 scripts/changelog.py "$T" >/dev/null
scripts/generate.sh
mkdir -p "$D"
for M in c3 s3; do
    P=airfrog-$M
    cp jlc/$M/$P-gerbers.zip jlc/$M/$P-bom.csv jlc/$M/$P-cpl.csv jlc/$M/$P-socket-bom.csv jlc/$M/$P-socket-cpl.csv \
        kicad/$M/$P-schematic.pdf "$D/"
    rm -rf "${D:?}/$P"
    mkdir "$D/$P"
    cp -R kicad/$M/. LICENSE NOTICE "$D/$P/"
    rm -f "$D/$P/$P-schematic.pdf"
    (cd "$D" && python3 -m zipfile -c "$P-kicad.zip" "$P")
    rm -r "${D:?}/$P"
done
