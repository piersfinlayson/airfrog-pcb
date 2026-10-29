"""Minimal KiCad 9 schematic writer and layout checker.

Symbols are copied from KiCad's stock libraries into the schematic's lib_symbols.
Coordinates are in mm on the schematic (y down). Pin positions are computed from
the library pin positions so wires and labels land exactly on pin ends.
"""
import copy
import math
import os
import re
import uuid as _uuid

from kicad_paths import SYMBOL_DIR

U = 2.54
CHAR_W = 1.27 * 0.95
TEXT_H = 1.27


# ---------------------------------------------------------------- S-expressions

def parse(text):
    tokens = re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+', text)
    stack = [[]]
    for t in tokens:
        if t == '(':
            stack.append([])
        elif t == ')':
            last = stack.pop()
            stack[-1].append(last)
        else:
            stack[-1].append(t)
    return stack[0][0]


def dump(node, indent=0):
    if not isinstance(node, list):
        return node
    if all(not isinstance(x, list) for x in node):
        return '(' + ' '.join(node) + ')'
    pad = '\t' * (indent + 1)
    out = '('
    first = True
    for x in node:
        if isinstance(x, list):
            out += '\n' + pad + dump(x, indent + 1)
        else:
            out += ('' if first else ' ') + x
        first = False
    return out + '\n' + '\t' * indent + ')'


def q(s):
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"') + '"'


def unq(s):
    return s[1:-1].replace('\\"', '"').replace('\\\\', '\\') if s.startswith('"') else s


def find(node, key):
    return [x for x in node if isinstance(x, list) and x and x[0] == key]


def f(v):
    v = round(v, 4)
    if v == int(v):
        return str(int(v))
    return ('%.4f' % v).rstrip('0').rstrip('.')


# UUIDs are derived from names so they stay the same on every run. Symbols and
# their pins keep their links to the PCB and to ERC exclusions when regenerated.
_seed = ['']
_count = [0]


def uid(key=None):
    if key is None:
        _count[0] += 1
        key = 'item/%d' % _count[0]
    return str(_uuid.uuid5(_uuid.NAMESPACE_URL, _seed[0] + '/' + key))


# ---------------------------------------------------------------- libraries

_libs = {}


def _lib(name, path=None):
    if name not in _libs:
        _libs[name] = parse(open(path or os.path.join(SYMBOL_DIR, name + '.kicad_sym')).read())
    return _libs[name]


def _symbol(lib, name):
    for s in find(lib, 'symbol'):
        if unq(s[1]) == name:
            return s
    raise KeyError(name)


def load_symbol(lib_id, path=None):
    """Return a flattened copy of a library symbol, named lib_id."""
    lib_name, name = lib_id.split(':')
    lib = _lib(lib_name, path)
    sym = copy.deepcopy(_symbol(lib, name))
    ext = find(sym, 'extends')
    if ext:
        parent = copy.deepcopy(_symbol(lib, unq(ext[0][1])))
        pname = unq(parent[1])
        props = {unq(p[1]): p for p in find(sym, 'property')}
        flat = [parent[0], q(lib_id)]
        for x in parent[2:]:
            if isinstance(x, list) and x[0] == 'property' and unq(x[1]) in props:
                flat.append(props.pop(unq(x[1])))
            elif isinstance(x, list) and x[0] == 'symbol':
                x[1] = q(unq(x[1]).replace(pname, name, 1))
                flat.append(x)
            else:
                flat.append(x)
        i = max(k for k, x in enumerate(flat) if isinstance(x, list) and x[0] == 'property') + 1
        for p in props.values():
            flat.insert(i, p)
            i += 1
        sym = flat
    else:
        sym[1] = q(lib_id)
    return sym


def _units(sym, unit):
    """The unit symbols drawn for unit: those common to all units (unit 0) and unit's own. All of them if
    unit is None."""
    return [u for u in find(sym, 'symbol') if unit is None or int(unq(u[1]).rsplit('_', 2)[1]) in (0, unit)]


def symbol_pins(sym, unit=None):
    """{number: (x, y, angle, name, type, length)} in library coordinates (y up)."""
    pins = {}
    for unit in _units(sym, unit):
        for p in find(unit, 'pin'):
            at = find(p, 'at')[0]
            num = unq(find(p, 'number')[0][1])
            name = unq(find(p, 'name')[0][1])
            length = float(find(p, 'length')[0][1])
            pins.setdefault(num, (float(at[1]), float(at[2]), int(float(at[3])) if len(at) > 3 else 0,
                                  name, p[1], length))
    return pins


def symbol_graphics_points(sym, unit=None):
    """Points outlining the symbol's drawn body, library coordinates."""
    pts = []
    for unit in _units(sym, unit):
        for g in unit[2:]:
            if not isinstance(g, list):
                continue
            if g[0] == 'rectangle':
                s, e = find(g, 'start')[0], find(g, 'end')[0]
                pts += [(float(s[1]), float(s[2])), (float(e[1]), float(e[2]))]
            elif g[0] in ('polyline', 'bezier'):
                for xy in find(find(g, 'pts')[0], 'xy'):
                    pts.append((float(xy[1]), float(xy[2])))
            elif g[0] == 'circle':
                c, r = find(g, 'center')[0], float(find(g, 'radius')[0][1])
                cx, cy = float(c[1]), float(c[2])
                pts += [(cx - r, cy - r), (cx + r, cy + r)]
            elif g[0] == 'arc':
                for k in ('start', 'mid', 'end'):
                    a = find(g, k)[0]
                    pts.append((float(a[1]), float(a[2])))
    return pts


# ---------------------------------------------------------------- geometry

class Box:
    def __init__(self, x0, y0, x1, y1, what):
        self.x0, self.y0 = min(x0, x1), min(y0, y1)
        self.x1, self.y1 = max(x0, x1), max(y0, y1)
        self.what = what

    def overlaps(self, o, tol=0.15):
        return (self.x0 + tol < o.x1 and o.x0 + tol < self.x1 and
                self.y0 + tol < o.y1 and o.y0 + tol < self.y1)

    def hits_segment(self, a, b, tol=0.15):
        x0, y0, x1, y1 = self.x0 + tol, self.y0 + tol, self.x1 - tol, self.y1 - tol
        if x0 >= x1 or y0 >= y1:
            return False
        if a[0] == b[0]:
            lo, hi = sorted((a[1], b[1]))
            return x0 < a[0] < x1 and lo < y1 and hi > y0
        if a[1] == b[1]:
            lo, hi = sorted((a[0], b[0]))
            return y0 < a[1] < y1 and lo < x1 and hi > x0
        return False


def text_box(s, x, y, just_h, just_v='center', angle=0, size=1.27, what=''):
    w = len(s) * size * 0.95 + 0.2
    h = size * 1.1
    if angle in (90, 270):
        # vertical text: swap axes, text runs upward from anchor for 'left'
        if just_h == 'left':
            y0, y1 = y - w, y
        elif just_h == 'right':
            y0, y1 = y, y + w
        else:
            y0, y1 = y - w / 2, y + w / 2
        return Box(x - h / 2, y0, x + h / 2, y1, what)
    if just_h == 'left':
        x0, x1 = x, x + w
    elif just_h == 'right':
        x0, x1 = x - w, x
    else:
        x0, x1 = x - w / 2, x + w / 2
    if just_v == 'bottom':
        y0, y1 = y - h, y
    else:
        y0, y1 = y - h / 2, y + h / 2
    return Box(x0, y0, x1, y1, what)


# ---------------------------------------------------------------- schematic

PASSIVE_LIBS = ('Device:R_Small', 'Device:C_Small', 'Device:C_Polarized_Small')


class Part:
    def __init__(self, sch, lib_id, ref, value, at, rot=0, mirror=None, props=None, fields=None,
                 hide_value=False, dnp=False, unit=1):
        self.sch, self.lib_id, self.ref, self.value = sch, lib_id, ref, value
        self.x, self.y = at
        self.rot, self.mirror = rot, mirror
        self.props = props or {}
        self.sym = sch.need(lib_id)
        self.unit = unit
        self.pins = symbol_pins(self.sym, unit)
        self.is_power = bool(find(self.sym, 'power'))
        self.hide_value = hide_value
        self.dnp = dnp
        self.fields = fields
        self.uuid = uid('symbol/' + ref + ('/%d' % unit if unit > 1 else ''))

    # library (y up) -> schematic offset (y down)
    def _xf(self, x, y):
        a = math.radians(self.rot)
        rx = x * math.cos(a) - y * math.sin(a)
        ry = x * math.sin(a) + y * math.cos(a)
        sx, sy = rx, -ry
        if self.mirror == 'y':
            sx = -sx
        elif self.mirror == 'x':
            sy = -sy
        return sx, sy

    def pin(self, num):
        px, py = self.pins[str(num)][:2]
        dx, dy = self._xf(px, py)
        return (round(self.x + dx, 4), round(self.y + dy, 4))

    def pin_dir(self, num):
        """Unit vector pointing away from the symbol body at the pin end."""
        ang = self.pins[str(num)][2]
        a = math.radians(ang)
        bx, by = self._xf(math.cos(a), math.sin(a))
        return (-round(bx), -round(by))

    def body_box(self):
        pts = [self._xf(x, y) for x, y in symbol_graphics_points(self.sym, self.unit)]
        if not pts:
            return None
        xs = [self.x + p[0] for p in pts]
        ys = [self.y + p[1] for p in pts]
        return Box(min(xs), min(ys), max(xs), max(ys), 'body ' + self.ref)

    def pin_boxes(self):
        out = []
        for num, (px, py, ang, name, typ, length) in self.pins.items():
            a = math.radians(ang)
            ex, ey = px + length * math.cos(a), py + length * math.sin(a)
            p0, p1 = self._xf(px, py), self._xf(ex, ey)
            out.append(Box(self.x + p0[0], self.y + p0[1], self.x + p1[0], self.y + p1[1],
                           'pin %s.%s' % (self.ref, num)))
        return out

    # ---- fields
    def _lib_field(self, name):
        for p in find(self.sym, 'property'):
            if unq(p[1]) == name:
                at = find(p, 'at')[0]
                eff = find(p, 'effects')
                just = find(eff[0], 'justify') if eff else []
                return float(at[1]), float(at[2]), int(float(at[3])), (just[0][1:] if just else [])
        return 0, 0, 0, []

    def field_layout(self):
        """{field: (x, y, angle, justify_h_visual, justify_v, hidden)} for Reference and Value."""
        x, y, rot = self.x, self.y, self.rot
        horiz = rot in (90, 270)
        out = {}
        if self.is_power:
            val = self.value
            name = self.lib_id.split(':')[1]
            if name == 'GND':
                d = {0: (0, 3.81, 'center'), 270: (-3.3, 0, 'right'), 90: (3.3, 0, 'left'),
                     180: (0, -3.81, 'center')}[rot]
            else:
                d = {0: (0, -3.556, 'center'), 90: (-3.3, 0, 'right'), 270: (3.3, 0, 'left'),
                     180: (0, 3.556, 'center')}[rot]
            out['Value'] = (x + d[0], y + d[1], d[2], 'center', False)
            out['Reference'] = (x, y, 'center', 'center', True)
            return out
        mode = self.fields
        if self.lib_id in PASSIVE_LIBS and mode is None:
            mode = 'above' if horiz else 'right'
        if isinstance(mode, dict):
            return mode
        if mode in ('right', 'left'):
            dx = 2.032 if mode == 'right' else -2.032
            j = 'left' if mode == 'right' else 'right'
            out['Reference'] = (x + dx, y - 1.016, j, 'center', False)
            out['Value'] = (x + dx, y + 1.016, j, 'center', self.hide_value)
        elif mode in ('above', 'below'):
            s = -1 if mode == 'above' else 1
            out['Reference'] = (x, y + s * (3.81 if s < 0 else 2.286), 'center', 'center', False)
            out['Value'] = (x, y + s * (2.286 if s < 0 else 3.81), 'center', 'center', self.hide_value)
        elif mode in ('above1', 'below1'):
            s = -1 if mode == 'above1' else 1
            out['Reference'] = (x - 0.4, y + s * 2.286, 'right', 'center', False)
            out['Value'] = (x + 0.4, y + s * 2.286, 'left', 'center', self.hide_value)
        else:
            # library positions
            for k in ('Reference', 'Value'):
                lx, ly, la, lj = self._lib_field(k)
                dx, dy = self._xf(lx, ly)
                j = 'center'
                if 'left' in lj:
                    j = 'left'
                elif 'right' in lj:
                    j = 'right'
                if self.mirror == 'y' and j != 'center':
                    j = 'right' if j == 'left' else 'left'
                out[k] = (x + dx, y + dy, j, 'center', k == 'Value' and self.hide_value)
        return out

    def field_boxes(self):
        out = []
        texts = {'Reference': self.ref, 'Value': self.value}
        for k, (fx, fy, j, jv, hidden) in self.field_layout().items():
            if not hidden:
                out.append(text_box(texts[k], fx, fy, j, jv, what='%s %s' % (k, self.ref)))
        return out

    def sexp(self, project, root_uuid):
        defaults = {unq(p[1]): unq(p[2]) for p in find(self.sym, 'property')}
        props = [('Reference', self.ref), ('Value', self.value)]
        for k in ('Footprint', 'Datasheet', 'Description'):
            props.append((k, self.props.get(k, defaults.get(k, ''))))
        for k, v in self.props.items():
            if k not in ('Footprint', 'Datasheet', 'Description'):
                props.append((k, v))
        node = ['symbol', ['lib_id', q(self.lib_id)], ['at', f(self.x), f(self.y), str(self.rot)]]
        if self.mirror:
            node.append(['mirror', self.mirror])
        node += [['unit', str(self.unit)], ['exclude_from_sim', 'no'], ['in_bom', 'no' if self.is_power else 'yes'],
                 ['on_board', 'no' if self.is_power else 'yes'], ['dnp', 'yes' if self.dnp else 'no'], ['uuid', q(self.uuid)]]
        layout = self.field_layout()
        # field angle that keeps text horizontal, and whether KiCad mirrors justification for it
        fang = 90 if self.rot in (90, 270) else 0
        flip = self.rot in (90, 180)
        for k, v in props:
            if k in layout:
                fx, fy, j, jv, hidden = layout[k]
                if flip and j in ('left', 'right'):
                    j = 'right' if j == 'left' else 'left'
                eff = ['effects', ['font', ['size', '1.27', '1.27']]]
                js = ([j] if j != 'center' else []) + ([jv] if jv != 'center' else [])
                if js:
                    eff.append(['justify'] + js)
                if hidden:
                    eff.append(['hide', 'yes'])
                node.append(['property', q(k), q(v), ['at', f(fx), f(fy), str(fang)], eff])
            else:
                node.append(['property', q(k), q(v), ['at', f(self.x), f(self.y), '0'],
                             ['effects', ['font', ['size', '1.27', '1.27']], ['hide', 'yes']]])
        for num in sorted(self.pins):
            node.append(['pin', q(num), ['uuid', q(uid('pin/%s/%s' % (self.ref, num)))]])
        node.append(['instances', ['project', q(project), ['path', q('/' + root_uuid),
                                                            ['reference', q(self.ref)], ['unit', str(self.unit)]]]])
        return node


class Schematic:
    def __init__(self, project, paper='A3', title='', date='', rev='', company='', comments=()):
        self.project, self.paper = project, paper
        self.title, self.date, self.rev, self.company, self.comments = title, date, rev, company, comments
        _seed[0], _count[0] = project, 0
        self.uuid = uid('root')
        self.libsyms = {}
        self.parts = []
        self.wires = []
        self.junctions = []
        self.noconnects = []
        self.labels = []
        self.texts = []
        self.rects = []
        self.npwr = 0
        self.nflg = 0

    def need(self, lib_id, path=None):
        if lib_id not in self.libsyms:
            self.libsyms[lib_id] = load_symbol(lib_id, path)
        return self.libsyms[lib_id]

    def add(self, lib_id, ref, value, at, **kw):
        p = Part(self, lib_id, ref, value, at, **kw)
        self.parts.append(p)
        return p

    def power(self, kind, at, rot=0):
        """kind: GND, +3V3, PWR_FLAG, or any rail name (drawn with the +5V symbol)."""
        if kind == 'PWR_FLAG':
            self.nflg += 1
            return self.add('power:PWR_FLAG', '#FLG%02d' % self.nflg, 'PWR_FLAG', at, rot=rot)
        self.npwr += 1
        lib = {'GND': 'power:GND', '+3V3': 'power:+3V3'}.get(kind, 'power:+5V')
        return self.add(lib, '#PWR%03d' % self.npwr, kind, at, rot=rot)

    def wire(self, *pts):
        for a, b in zip(pts, pts[1:]):
            a, b = (round(a[0], 4), round(a[1], 4)), (round(b[0], 4), round(b[1], 4))
            if a != b:
                if a[0] != b[0] and a[1] != b[1]:
                    raise ValueError('diagonal wire %s %s' % (a, b))
                self.wires.append((a, b))

    def junction(self, at):
        self.junctions.append((round(at[0], 4), round(at[1], 4)))

    def no_connect(self, at):
        self.noconnects.append(at)

    def label(self, name, at, angle=0, kind='global', shape='passive'):
        """angle: direction the label text runs from its anchor (0 right, 90 up, 180 left, 270 down)."""
        self.labels.append((name, (round(at[0], 4), round(at[1], 4)), angle, kind, shape))

    def text(self, s, at, size=1.27, bold=False, justify='left'):
        self.texts.append((s, at, size, bold, justify))

    def rect(self, a, b):
        self.rects.append((a, b))

    # ---- connectivity helpers
    def stub(self, part, pin, length=U):
        p = part.pin(pin)
        d = part.pin_dir(pin)
        e = (round(p[0] + d[0] * length, 4), round(p[1] + d[1] * length, 4))
        self.wire(p, e)
        return e, d

    def pin_label(self, part, pin, name, length=U, kind='global', shape='passive'):
        e, d = self.stub(part, pin, length) if length else (part.pin(pin), part.pin_dir(pin))
        ang = {(1, 0): 0, (-1, 0): 180, (0, -1): 90, (0, 1): 270}[d]
        self.label(name, e, ang, kind, shape)
        return e

    def pin_power(self, part, pin, kind, length=0, rot=None):
        """Power symbol at a pin end, pointing away from the part unless rot is set."""
        e, d = self.stub(part, pin, length) if length else (part.pin(pin), part.pin_dir(pin))
        if rot is not None:
            return self.power(kind, e, rot)
        if kind == 'GND':
            rot = {(0, 1): 0, (-1, 0): 270, (1, 0): 90, (0, -1): 180}[d]
        else:
            rot = {(0, -1): 0, (-1, 0): 90, (1, 0): 270, (0, 1): 180}[d]
        return self.power(kind, e, rot)

    def pin_nc(self, part, pin):
        self.no_connect(part.pin(pin))

    # ---- checks
    def auto_junctions(self):
        pts = {}
        for a, b in self.wires:
            for p in (a, b):
                pts[p] = pts.get(p, 0) + 1
        pinpts = set()
        for p in self.parts:
            for n in p.pins:
                pinpts.add(p.pin(n))
        found = set(self.junctions)
        for p, n in pts.items():
            if n + (1 if p in pinpts else 0) >= 3:
                found.add(p)
        for a, b in self.wires:
            for p in list(pts) + list(pinpts):
                if p in (a, b):
                    continue
                if a[0] == b[0] == p[0] and min(a[1], b[1]) < p[1] < max(a[1], b[1]):
                    found.add(p)
                elif a[1] == b[1] == p[1] and min(a[0], b[0]) < p[0] < max(a[0], b[0]):
                    found.add(p)
        self.junctions = sorted(found)

    def label_box(self, name, at, angle, kind):
        x, y = at
        w = len(name) * CHAR_W + (3.0 if kind == 'global' else 0.6)
        h = 2.4 if kind == 'global' else 1.5
        if kind == 'global':
            if angle == 0:
                return Box(x, y - h / 2, x + w, y + h / 2, 'label ' + name)
            if angle == 180:
                return Box(x - w, y - h / 2, x, y + h / 2, 'label ' + name)
            if angle == 90:
                return Box(x - h / 2, y - w, x + h / 2, y, 'label ' + name)
            return Box(x - h / 2, y, x + h / 2, y + w, 'label ' + name)
        if angle == 0:
            return Box(x, y - h, x + w, y - 0.1, 'label ' + name)
        if angle == 180:
            return Box(x - w, y - h, x, y - 0.1, 'label ' + name)
        if angle == 90:
            return Box(x - h, y - w, x - 0.1, y, 'label ' + name)
        return Box(x - h, y, x - 0.1, y + w, 'label ' + name)

    def check(self):
        issues = []
        bodies, texts = [], []
        for p in self.parts:
            b = p.body_box()
            if b:
                bodies.append((p, b))
            for t in p.field_boxes():
                texts.append((p, t))
        for name, at, ang, kind, shape in self.labels:
            texts.append((None, self.label_box(name, at, ang, kind)))
        for s, at, size, bold, just in self.texts:
            texts.append((None, text_box(s, at[0], at[1], just, size=size, what='text ' + s)))
        items = [(p, b) for p, b in bodies] + texts
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                (p1, b1), (p2, b2) = items[i], items[j]
                if p1 is not None and p1 is p2 and (b1.what.startswith('body') or b2.what.startswith('body')):
                    continue
                if b1.overlaps(b2):
                    issues.append('overlap: %s / %s' % (b1.what, b2.what))
        # wires through bodies or text
        for a, b in self.wires:
            for p, bx in bodies:
                if bx.hits_segment(a, b):
                    issues.append('wire %s-%s crosses %s' % (a, b, bx.what))
            for p, bx in texts:
                if bx.hits_segment(a, b, tol=0.25):
                    issues.append('wire %s-%s crosses %s' % (a, b, bx.what))
        # wire crossings and collinear overlaps
        ws = self.wires
        for i in range(len(ws)):
            for j in range(i + 1, len(ws)):
                (a, b), (c, d) = ws[i], ws[j]
                h1, h2 = a[1] == b[1], c[1] == d[1]
                if h1 != h2:
                    hseg, vseg = ((a, b), (c, d)) if h1 else ((c, d), (a, b))
                    y, x = hseg[0][1], vseg[0][0]
                    if (min(hseg[0][0], hseg[1][0]) < x < max(hseg[0][0], hseg[1][0]) and
                            min(vseg[0][1], vseg[1][1]) < y < max(vseg[0][1], vseg[1][1])):
                        issues.append('wires cross at (%s, %s)' % (f(x), f(y)))
                elif h1 and a[1] == c[1]:
                    lo1, hi1 = sorted((a[0], b[0]))
                    lo2, hi2 = sorted((c[0], d[0]))
                    if min(hi1, hi2) > max(lo1, lo2):
                        issues.append('collinear wires overlap at y=%s' % f(a[1]))
                elif not h1 and a[0] == c[0]:
                    lo1, hi1 = sorted((a[1], b[1]))
                    lo2, hi2 = sorted((c[1], d[1]))
                    if min(hi1, hi2) > max(lo1, lo2):
                        issues.append('collinear wires overlap at x=%s' % f(a[0]))
        # off-grid points
        for a, b in self.wires:
            for p in (a, b):
                for v in p:
                    if abs(v / 1.27 - round(v / 1.27)) > 1e-3:
                        issues.append('off grid %s' % (p,))
        # wires crossing block frames
        for (r0, r1) in self.rects:
            x0, y0, x1, y1 = min(r0[0], r1[0]), min(r0[1], r1[1]), max(r0[0], r1[0]), max(r0[1], r1[1])
            for a, b in self.wires:
                ins = [x0 < p[0] < x1 and y0 < p[1] < y1 for p in (a, b)]
                if ins[0] != ins[1]:
                    issues.append('wire %s-%s crosses block frame' % (a, b))
        return issues

    # ---- output
    def render(self):
        self.auto_junctions()
        tb = ['title_block', ['title', q(self.title)], ['date', q(self.date)], ['rev', q(self.rev)],
              ['company', q(self.company)]]
        for i, c in enumerate(self.comments, 1):
            tb.append(['comment', str(i), q(c)])
        root = ['kicad_sch', ['version', '20250114'], ['generator', q('eeschema')], ['generator_version', q('9.0')],
                ['uuid', q(self.uuid)], ['paper', q(self.paper)], tb, ['lib_symbols'] + list(self.libsyms.values())]
        for j in self.junctions:
            root.append(['junction', ['at', f(j[0]), f(j[1])], ['diameter', '0'], ['color', '0', '0', '0', '0'],
                         ['uuid', q(uid())]])
        for n in self.noconnects:
            root.append(['no_connect', ['at', f(n[0]), f(n[1])], ['uuid', q(uid())]])
        # split wires at junctions and labels on their interior, as eeschema does
        for a, b in self.wires:
            cuts = [a, b]
            for j in self.junctions + [lab[1] for lab in self.labels]:
                if a[0] == b[0] == j[0] and min(a[1], b[1]) < j[1] < max(a[1], b[1]):
                    cuts.append(j)
                elif a[1] == b[1] == j[1] and min(a[0], b[0]) < j[0] < max(a[0], b[0]):
                    cuts.append(j)
            cuts.sort()
            for p, r in zip(cuts, cuts[1:]):
                root.append(['wire', ['pts', ['xy', f(p[0]), f(p[1])], ['xy', f(r[0]), f(r[1])]],
                             ['stroke', ['width', '0'], ['type', 'default']], ['uuid', q(uid())]])
        for name, at, angle, kind, shape in self.labels:
            if kind == 'global':
                just = 'left' if angle in (0, 90) else 'right'
                root.append(['global_label', q(name), ['shape', shape], ['at', f(at[0]), f(at[1]), str(angle)],
                             ['fields_autoplaced', 'yes'],
                             ['effects', ['font', ['size', '1.27', '1.27']], ['justify', just]],
                             ['uuid', q(uid())],
                             ['property', q('Intersheetrefs'), q('${INTERSHEET_REFS}'),
                              ['at', f(at[0]), f(at[1]), '0'],
                              ['effects', ['font', ['size', '1.27', '1.27']], ['justify', just], ['hide', 'yes']]]])
            else:
                just = 'left' if angle in (0, 90) else 'right'
                root.append(['label', q(name), ['at', f(at[0]), f(at[1]), str(angle)], ['fields_autoplaced', 'yes'],
                             ['effects', ['font', ['size', '1.27', '1.27']], ['justify', just, 'bottom']],
                             ['uuid', q(uid())]])
        for a, b in self.rects:
            root.append(['rectangle', ['start', f(a[0]), f(a[1])], ['end', f(b[0]), f(b[1])],
                         ['stroke', ['width', '0'], ['type', 'default']], ['fill', ['type', 'none']],
                         ['uuid', q(uid())]])
        for s, at, size, bold, just in self.texts:
            fnt = ['font', ['size', f(size), f(size)]] + ([['bold', 'yes']] if bold else [])
            eff = ['effects', fnt]
            if just != 'center':
                eff.append(['justify', just])
            root.append(['text', q(s), ['exclude_from_sim', 'no'], ['at', f(at[0]), f(at[1]), '0'], eff,
                         ['uuid', q(uid())]])
        for p in self.parts:
            root.append(p.sexp(self.project, self.uuid))
        root.append(['sheet_instances', ['path', q('/'), ['page', q('1')]]])
        root.append(['embedded_fonts', 'no'])
        return dump(root) + '\n'


# ---------------------------------------------------------------- netlist

def read_netlist(path):
    """{net name: set('REF.pin')} from a kicadsexpr netlist, unconnected single pins dropped."""
    root = parse(open(path).read())
    nets = {}
    for n in find(find(root, 'nets')[0], 'net'):
        name = unq(find(n, 'name')[0][1])
        nodes = {unq(find(x, 'ref')[0][1]) + '.' + unq(find(x, 'pin')[0][1]) for x in find(n, 'node')}
        nets[name] = nodes
    return nets
