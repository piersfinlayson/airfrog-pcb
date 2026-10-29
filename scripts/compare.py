"""Compare KiCad's exported netlist with expected_nets and summarise ERC. Exits 1 on any difference or ERC
violation."""
import json
import os
import sys
from collections import Counter, defaultdict

import expected_nets as E
from kisch import read_netlist

POLARISED = set()

d, variant = sys.argv[1], sys.argv[2]
actual = read_netlist(os.path.join(d, 'airfrog-%s.net' % variant))
act = {}
actual = {n.lstrip('/'): v for n, v in actual.items()}
for name, nodes in actual.items():
    for x in nodes:
        act[x] = name

expected = [(k, v) for k, v in E.named(variant).items()] + [(None, s) for s in E.unnamed(variant)]
exp = {}
for i, (k, nodes) in enumerate(expected):
    for x in nodes:
        if x in exp:
            print('expected netlist lists %s twice' % x)
        exp[x] = i

problems = []
swappable = lambda ref: ref[0] in 'RC' and ref not in POLARISED

# expected net -> actual net, decided by IC and connector pins
image = defaultdict(set)
for x, i in exp.items():
    if not swappable(x.split('.')[0]):
        image[i].add(act.get(x, '(missing %s)' % x))
for i, names in image.items():
    if len(names) > 1:
        problems.append('expected net %s is split across %s' % (expected[i][0] or sorted(expected[i][1]), sorted(names)))
m = {i: next(iter(n)) for i, n in image.items()}
# nets made only of two-pin passives: find the actual net with the same pins, allowing pin swaps
import itertools
other = {'1': '2', '2': '1'}
for i, (k, nodes) in enumerate(expected):
    if i in m:
        continue
    nodes = sorted(nodes)
    for flips in itertools.product((False, True), repeat=len(nodes)):
        cand = {x.split('.')[0] + '.' + (other[x.split('.')[1]] if f else x.split('.')[1])
                for x, f in zip(nodes, flips)}
        hits = {act.get(x) for x in cand}
        if len(hits) == 1 and None not in hits and actual[next(iter(hits))] == cand:
            m[i] = next(iter(hits))
            break
    else:
        problems.append('expected net %s not found' % (k or sorted(nodes)))
for i, (k, nodes) in enumerate(expected):
    if k and m.get(i) != k:
        problems.append('net %s is named %s' % (k, m.get(i)))
inv = defaultdict(list)
for i, name in m.items():
    inv[name].append(i)
for name, idx in inv.items():
    if len(idx) > 1:
        problems.append('actual net %s merges expected %s' % (name, [expected[i][0] or sorted(expected[i][1]) for i in idx]))

# passive pins
refs = defaultdict(dict)
for x, i in exp.items():
    ref, pin = x.split('.')
    if swappable(ref):
        refs[ref][pin] = m.get(i)
for ref, pins in refs.items():
    want = (pins.get('1'), pins.get('2'))
    got = (act.get(ref + '.1'), act.get(ref + '.2'))
    if got != want and got[::-1] != want:
        problems.append('%s pins on %s, expected %s' % (ref, got, want))
for x, i in exp.items():
    ref, pin = x.split('.')
    if not swappable(ref) and x not in act:
        problems.append('missing pin %s' % x)

# every actual pin is expected or a deliberate no-connect
nc = set(E.no_connect(variant))
for x, name in act.items():
    if x not in exp and x not in nc:
        problems.append('unexpected pin %s on %s' % (x, name))
for x in nc:
    if x in act and len(actual[act[x]]) > 1:
        problems.append('no-connect pin %s is on net %s' % (x, act[x]))

print('NETLIST: %d nets, %d problems' % (len(actual), len(problems)))
for p in problems:
    print('  ', p)

erc = json.load(open(os.path.join(d, 'erc.json')))
viol = [v for sh in erc['sheets'] for v in sh['violations']]
print('ERC: %d violations' % len(viol), dict(Counter((v['severity'], v['type']) for v in viol)))
for v in viol:
    print('  ', v['severity'], v['type'], '|', '; '.join(i['description'] for i in v['items']))
sys.exit(1 if problems or viol else 0)
