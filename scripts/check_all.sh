#!/bin/sh
# Run the schematic and PCB checks. Exits 1 on any problem.
cd "$(dirname "$0")/.."
D=${1:-build}
status=0
scripts/check.sh "$D" || status=1
scripts/check_pcb.sh "$D" || status=1
exit $status
