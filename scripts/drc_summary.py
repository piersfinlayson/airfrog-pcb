"""Summarise a kicad-cli DRC JSON report. Exits 1 if it has a violation, unconnected item or parity problem, other than
footprints differing from their library copies: the PCB generator cuts footprint silkscreen at the board edge and at
vias, and changes J2's pads and courtyard."""
import json
import sys
from collections import Counter

def expected(v):
    return v['type'] == 'lib_footprint_mismatch'


d = json.load(open(sys.argv[1]))
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 40
groups = (('DRC', d.get('violations', [])), ('UNCONNECTED', d.get('unconnected_items', [])),
          ('PARITY', d.get('schematic_parity', [])))
failed = False
for name, items in groups:
    items = [v for v in items if not v.get('excluded')]
    counts = Counter('%s/%s' % (v['severity'], v['type']) for v in items)
    print('%s: %d %s' % (name, len(items), dict(counts)))
    for v in items[:limit]:
        where = '; '.join('%s @(%.2f,%.2f)' % (i['description'], i['pos']['x'] - 100, i['pos']['y'] - 100)
                          for i in v.get('items', []))
        print('  %s %s: %s' % (v['type'], v['description'], where))
    failed = failed or not all(expected(v) for v in items)
sys.exit(1 if failed else 0)
