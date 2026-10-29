"""Generate the Airfrog PCB for one ESP32 module variant, c3 or s3, from its schematic. Run with KiCad's bundled
Python.

Board coordinates are in mm from the board's top left corner, y down, viewed from the top. The board is drawn the
same way round as One ROM: the One ROM header is along the top edge with its 5V pin at the left, and the board
lies over One ROM with the module's antenna at the far end.
"""
import json
import math
import os
import sys
import tempfile

import pcbnew

from kipcb import Board, export_netlist, read_netlist, read_title_block
from router import Router

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # the project folder, above scripts/
SRC = os.path.join(ROOT, 'kicad-src')                                 # the committed KiCad project files

# ------------------------------------------------------------ parameters

# One ROM header, from the Fire boards: row 1 is 1.58 mm from the top edge on fire-24-f and 1.8 mm on the others.
# The header is centred, with at least 4.11 mm from its outer pins to the side edges. fire-24-f is the shortest
# board, at 31.37 mm.
PITCH = 2.54
HDR_Y = 1.58                     # row 1 (5V, RUN, BOOTSEL, SEL_B, SEL_A) from the top edge
ENVELOPE = (18.39, 31.37)        # the largest board that stays inside every Fire board's outline

J2_X = 1.2                       # user and serial header column, from the left edge
J2_PAD = 1.6
CORNER_R = 0.4                   # the largest that keeps the S3 module's corner pad 0.4 mm from the edge
SILK_EDGE = 0.3

VARIANTS = {
    # module footprint body (F.Fab) in footprint coordinates before rotation: x0, y0, x1, y1, and where the antenna
    # area ends. size is the board width and the length to the module's antenna end. module_x is the module's centre.
    # under_antenna is how far the board runs past the start of the antenna. silk_x is the centre of the
    # bottom silkscreen name and lines: clear of J2's pin names on the left, and of the vias beside the module's
    # right-hand pins.
    # The C3 is 0.25 mm right of the board's centre, as far left as a fitted J2's plastic body allows. The board runs
    # 0.1 mm under its antenna, to keep the module's pads there 0.4 mm from the edge.
    # The S3 is 0.05 mm in from the right edge, to keep its pads there 0.4 mm from the edge.
    'c3': {'body': (-6.65, -11.05, 6.65, 5.65), 'antenna_end': -5.6, 'size': (18.0, 31.37),
           'model': 'ESP32-C3-MINI-1', 'module_x': 9.25, 'under_antenna': 0.1, 'epad_pitch': 1.975,
           'epad_center': (0, 0), 'silk_x': 10.4},
    's3': {'body': (-7.75, -10.2, 7.75, 10.3), 'antenna_end': -5.25, 'size': (18.39, 35.2),
           'model': 'ESP32-S3-MINI-1', 'module_x': 10.59, 'epad_pitch': 1.65, 'epad_center': (0, 2.55),
           'silk_x': 9.0},
}

# board minimums, inside JLC's 4-layer capabilities
RULES = {
    'm_MinClearance': 0.127,
    'm_TrackMinWidth': 0.127,
    'm_ViasMinSize': 0.45,
    'm_MinThroughDrill': 0.2,
    'm_ViasMinAnnularWidth': 0.12,
    'm_CopperEdgeClearance': 0.4,   # JLC's minimum from a V-cut edge
    'm_HoleClearance': 0.25,
    'm_HoleToHoleMin': 0.25,
    'm_MinSilkTextHeight': 0.8,
    'm_MinSilkTextThickness': 0.15,
}

STACKUP = '''\t\t(stackup
\t\t\t(layer "F.SilkS" (type "Top Silk Screen"))
\t\t\t(layer "F.Paste" (type "Top Solder Paste"))
\t\t\t(layer "F.Mask" (type "Top Solder Mask") (thickness 0.01524) (material "JLCPCB Soldermask") (epsilon_r 3.8) (loss_tangent 0))
\t\t\t(layer "F.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "dielectric 1" (type "prepreg") (thickness 0.2104) (material "NP-155F 7628") (epsilon_r 4.4) (loss_tangent 0.02))
\t\t\t(layer "In1.Cu" (type "copper") (thickness 0.0152))
\t\t\t(layer "dielectric 2" (type "core") (thickness 1.065) (material "NP-155F") (epsilon_r 4.43) (loss_tangent 0.02))
\t\t\t(layer "In2.Cu" (type "copper") (thickness 0.0152))
\t\t\t(layer "dielectric 3" (type "prepreg") (thickness 0.2104) (material "NP-155F 7628") (epsilon_r 4.4) (loss_tangent 0.02))
\t\t\t(layer "B.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01524) (material "JLCPCB Soldermask") (epsilon_r 3.8) (loss_tangent 0))
\t\t\t(layer "B.Paste" (type "Bottom Solder Paste"))
\t\t\t(layer "B.SilkS" (type "Bottom Silk Screen"))
\t\t\t(copper_finish "HAL lead-free")
\t\t\t(dielectric_constraints no)
\t\t)
'''

CLEARANCE = 0.15
W_SIG = 0.2
W_PWR = 0.25
POWER = ('+5V', '+3V3')
W_TRUNK = 0.5                    # the 3.3 V supply from the regulator to the module
# regulator output first, then the module and its decoupling
TRUNK_3V3 = {'c3': (('U2', '5'), ('C3', '1'), ('U1', '3'), ('C5', '1'), ('C4', '1')),
             's3': (('U2', '5'), ('C3', '1'), ('U1', '3'), ('C4', '1'))}
VIA = (0.5, 0.25)                # diameter, drill
ROUTE_LAYERS = ('F', 'In2', 'B')  # In1 is the GND plane
GRID = 0.1                       # router grid
STITCH_PITCH = 1.5               # minimum spacing of the GND vias joining the pours to the In1 plane
UNDER_MODULE_COST = 2.0          # In2 and bottom track cost under the module, to keep its ground whole
UNDER_MODULE_TOP_COST = 15.0     # top track cost under the module body

GLYPHS = json.load(open(os.path.join(SRC, 'bauhaus93.json')))
NAME_SIZE = 2.6
LINE_PITCH = 1.4                 # version, commit and copyright lines
COPYRIGHT = '(c) 2026 piers.rocks'   # piers.rocks is required on products by NOTICE

# top-left corner mark, a triangle over One ROM's L-shaped mark, from the board's top-left corner
CORNER_MARK = ((0.311, 0.304), (2.724, 0.304), (0.311, 2.717))

J2_NAMES = ('GND', 'SEL_D', 'SEL_C', 'DTR', 'RTS', 'BOOT', 'RX', 'TX', 'EN')


# placement of the small parts, (x, y, rotation), between the One ROM header and the module
PLACES = {
    'c3': {
        'Q1': (7.73, 6.5, 0), 'R3': (11.0, 10.4, 90), 'R4': (10.2, 8.2, 90),
        'R6': (6.75, 9.9, 270), 'D2': (6.75, 12.6, 90), 'R5': (8.75, 9.9, 270), 'D1': (8.75, 12.6, 90),
        'Q2': (4.0, 11.3, 90), 'R10': (4.0, 8.3, 270), 'R9': (4.0, 13.65, 180), 'R2': (5.4, 8.4, 90),
        'R12': (11.0, 12.4, 90), 'R13': (16.7, 4.75, 0),
        'U2': (14.6, 7.0, 0), 'C2': (12.0, 7.0, 90), 'C3': (17.2, 7.0, 90),
        # the SEL_A and SEL_B pull-ups have their SEL ends towards J1
        'R7': (16.6, 1.45, 180), 'R8': (16.6, 3.25, 180), 'SW1': (14.6, 11.9, 0),
        # EN's pull-up and delay, IO2's pull-up and the 3V3 decoupling are beside those module pins
        # with their courtyards touching, from SW1 down to C5 clear of the module's pin 1 mark
        'R1': (17.0, 14.61, 90), 'C1': (17.0, 16.49, 90), 'R11': (17.0, 18.37, 90), 'C4': (17.0, 20.25, 90),
        'C5': (17.0, 22.68, 90),
    },
}

# the S3 board is 0.39 mm wider, so its header, and the parts below it, are 0.195 mm further right. It has no IO2 or
# IO8 pull-ups, and no room beside its module, so the 3V3 decoupling is in the parts area and EN's pull-up and
# delay are below J2, beside the EN pin.
_S3 = {k: v for k, v in PLACES['c3'].items() if k not in ('R1', 'C1', 'R11', 'C4', 'C5', 'R12')}
_S3.update({'R1': (1.5, 26.6, 90), 'C1': (1.5, 28.6, 270), 'C4': (11.0, 12.4, 90)})
PLACES['s3'] = {k: (x + 0.195, y, r) for k, (x, y, r) in _S3.items()}


def layout(v):
    """Board size and the module origin for variant v. The module is rotated 180 degrees so that its antenna is at
    the bottom. The board ends where the antenna starts, or under_antenna past it, so the antenna overhangs the
    bottom edge, as Espressif recommends."""
    y0 = VARIANTS[v]['body'][1]
    w, length = VARIANTS[v]['size']
    # rotated 180, the body runs from origin - y1 to origin - y0
    mx = VARIANTS[v]['module_x']
    my = length + y0
    return w, my - VARIANTS[v]['antenna_end'] + VARIANTS[v].get('under_antenna', 0), (mx, my)


def tight_courtyard(B, ref, margin):
    """Replace a footprint's top courtyard with a rectangle margin outside its pads, and remove its top silkscreen.
    J2 is unfitted, and its library courtyard and outline reach over the parts beside it."""
    fp = B.fps[ref]
    for g in list(fp.GraphicalItems()):
        if g.GetLayer() in (pcbnew.F_CrtYd, pcbnew.F_SilkS):
            fp.Remove(g)
    xs, ys = [], []
    for p in fp.Pads():
        bb = p.GetBoundingBox()
        xs += [bb.GetLeft(), bb.GetRight()]
        ys += [bb.GetTop(), bb.GetBottom()]
    m = pcbnew.FromMM(margin)
    r = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_RECT)
    r.SetLayer(pcbnew.F_CrtYd)
    r.SetStart(pcbnew.VECTOR2I(min(xs) - m, min(ys) - m))
    r.SetEnd(pcbnew.VECTOR2I(max(xs) + m, max(ys) + m))
    r.SetWidth(pcbnew.FromMM(0.05))
    fp.Add(r)


def place_flipped(B, ref, x, y, rot):
    """Place a footprint on the bottom side."""
    fp = B.place(ref, x, y, rot)
    fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    return fp


def one_rom_header(B, w):
    """J1 on the bottom side, each pin under the One ROM pin it plugs onto. Pins 2n-1 and 2n are in column n from
    the left. Mirrored onto the bottom, the footprint can only have its odd pins on row 2."""
    x0 = w / 2 - 2 * PITCH
    want = {}
    for n in range(1, 6):
        want[str(2 * n - 1)] = (x0 + (n - 1) * PITCH, HDR_Y + PITCH)
        want[str(2 * n)] = (x0 + (n - 1) * PITCH, HDR_Y)
    for rot in (0, 90, 180, 270):
        if 'J1' in B.fps:
            B.b.Remove(B.fps.pop('J1'))
        place_flipped(B, 'J1', *want['1'], rot)
        if all(math.hypot(B.pad('J1', p)[0] - q[0], B.pad('J1', p)[1] - q[1]) < 0.01 for p, q in want.items()):
            return x0
    raise ValueError('no rotation of J1 matches the One ROM header')


def board_xy(v):
    return pcbnew.ToMM(v.x) - 100, pcbnew.ToMM(v.y) - 100


def build(v, out_dir):
    with tempfile.TemporaryDirectory() as tmp:
        return _build(v, out_dir, tmp)


def _build(v, out_dir, tmp):
    project = 'airfrog-' + v
    pcbnew.KIID.SeedGenerator(1)
    net_file = os.path.join(tmp, project + '.net')
    export_netlist(os.path.join(out_dir, project + '.kicad_sch'), net_file)
    comps, nets = read_netlist(net_file)
    B = Board(os.path.join(out_dir, project + '.kicad_pcb'), comps, nets, os.path.join(SRC, 'fp-lib-table'))
    ds = B.b.GetDesignSettings()
    for k, val in RULES.items():
        setattr(ds, k, pcbnew.FromMM(val))

    w, h, (mx, my) = layout(v)
    B.place('U1', mx, my, 180)
    # KiCad has no model of either module. These are boxes of the modules' size, in kicad-src.
    B.set_model('U1', '${KIPRJMOD}/airfrog.3dshapes/%s.wrl' % VARIANTS[v]['model'])
    x0 = one_rom_header(B, w)
    # J2's first pad as high as it can be and stay 0.3 mm clear of J1's row 2 pad in column 1
    dx = x0 - J2_X
    B.place('J2', J2_X, HDR_Y + PITCH + math.sqrt(max(0, 2.0 ** 2 - dx ** 2)), 0)
    # J2's pads are slightly smaller than the library's 1.7 mm, to keep the copper clearance from the left edge
    for p in B.fps['J2'].Pads():
        p.SetSize(pcbnew.VECTOR2I(pcbnew.FromMM(J2_PAD), pcbnew.FromMM(J2_PAD)))
    tight_courtyard(B, 'J2', 0.25)
    body_top = my - VARIANTS[v]['body'][3]
    antenna_top = my - VARIANTS[v]['antenna_end']
    j2_end = B.pad('J2', 9)[1] + 0.85
    if j2_end > antenna_top:
        B.warnings.append('J2 runs %.2f mm beside the antenna' % (j2_end - antenna_top))

    # parts between the header's pads and the module, right of J2
    for ref, (x, y, rot) in PLACES[v].items():
        B.place(ref, x, y, rot)

    missing = B.unplaced()
    if missing:
        print('UNPLACED', ' '.join(missing))
    B.outline(w, h, CORNER_R)
    B.clip_silk(w, h, SILK_EDGE)
    silkscreen(B, v, w, (mx, my))
    corner_mark(B)
    failed, router = autoroute(B, v, w, h, (mx, my), antenna_top)
    if failed:
        print('UNROUTED', ' '.join(failed))
    print('BOARD %s %.2f x %.2f mm, %.2f mm to the module antenna end, envelope %.2f x %.2f'
          % (v, w, h, VARIANTS[v]['size'][1], *ENVELOPE))
    for warn in B.warnings:
        print(warn)
    zones(B, w, antenna_top)
    # the first fill keeps the pieces with no GND connection, so that join_islands can add a via to each
    for z in B.b.Zones():
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_NEVER)
    B.fill(os.path.join(tmp, 'fill-drc.rpt'))
    n = join_islands(B, w, h)
    if n:
        print('STITCH %d vias in pour pieces that had none' % n)
    for z in B.b.Zones():
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    B.fill(os.path.join(tmp, 'fill-drc.rpt'))
    print('SILK cut %d footprint outline lines at vias' % clip_silk_at_vias(B))
    viapads = B.vias_on_pads()
    for warn in viapads:
        print(warn)
    B.save(STACKUP)
    return bool(missing or failed or viapads)


def silkscreen(B, v, w, module):
    """J2's pin names, and the name, version, commit and copyright, all on the bottom. The name, version, commit and
    copyright are under the module, clear of the parts area's vias, with the name above the module's centre pad vias
    and the rest below them."""
    # J1's socket is beside J2's pin 1, so its name reads up the edge above the pin
    for n, name in enumerate(J2_NAMES[1:], 2):
        x, y = B.pad('J2', n)
        B.text(name, (x + 1.2, y), size=0.8, thickness=0.15, layer=pcbnew.B_SilkS, justify='right').SetMirrored(True)
    x, y = B.pad('J2', 1)
    B.text(J2_NAMES[0], (x, (y - 0.85) / 2), size=0.8, thickness=0.15, layer=pcbnew.B_SilkS, rot=90).SetMirrored(True)
    cx = VARIANTS[v]['silk_x']
    mx, my = module
    pitch = VARIANTS[v]['epad_pitch']
    epad_y = my - VARIANTS[v]['epad_center'][1]
    glyph_text(B, 'airfrog', cx, epad_y - pitch / 2 - 1.1, NAME_SIZE)
    lines = ((open(os.path.join(ROOT, 'VERSION')).read().strip(), 1.0), ('${COMMIT}', 1.0), (COPYRIGHT, 0.8))
    for i, (s, size) in enumerate(lines):
        y = epad_y + pitch / 2 + 1.0 + LINE_PITCH * i
        B.text(s, (cx, y), size=size, thickness=0.15, layer=pcbnew.B_SilkS).SetMirrored(True)


def corner_mark(B):
    """The top-left corner mark, where One ROM has its own, so the board lines up with One ROM's corner."""
    poly = pcbnew.SHAPE_POLY_SET()
    poly.NewOutline()
    for x, y in CORNER_MARK:
        poly.Append(pcbnew.FromMM(100 + x), pcbnew.FromMM(100 + y))
    shape = pcbnew.PCB_SHAPE(B.b, pcbnew.SHAPE_T_POLY)
    shape.SetPolyShape(poly)
    shape.SetFilled(True)
    shape.SetWidth(0)
    shape.SetLayer(pcbnew.F_SilkS)
    B.b.Add(shape)


def glyph_text(B, s, cx, baseline, size):
    """s in Bauhaus 93, the font of the name on the earlier Airfrog boards, as filled bottom silkscreen centred on cx
    and read from below. The outlines come from kicad-src/bauhaus93.json, which scripts/glyphs.py writes from the font."""
    glyphs = GLYPHS['glyphs']
    width = sum(glyphs[c]['advance'] for c in s) * size
    x = cx + width / 2        # mirrored: the first character is on the right, seen from the top
    outer, holes = pcbnew.SHAPE_POLY_SET(), pcbnew.SHAPE_POLY_SET()
    for c in s:
        contours = glyphs[c]['contours']
        for n, contour in enumerate(contours):
            # a contour inside another of the same character is a hole, as in the 'o'
            inside = any(m != n and point_in(contour[0], other) for m, other in enumerate(contours))
            target = holes if inside else outer
            target.NewOutline()
            for gx, gy in contour:
                target.Append(pcbnew.FromMM(100 + x - gx * size), pcbnew.FromMM(100 + baseline - gy * size))
        x -= glyphs[c]['advance'] * size
    outer.BooleanSubtract(holes)
    # a board polygon is saved with one outline and no holes, so the holes are cut in to each outline's edge
    outer.Fracture()
    for i in range(outer.OutlineCount()):
        one = pcbnew.SHAPE_POLY_SET()
        one.AddOutline(outer.Outline(i))
        shape = pcbnew.PCB_SHAPE(B.b, pcbnew.SHAPE_T_POLY)
        shape.SetPolyShape(one)
        shape.SetFilled(True)
        shape.SetWidth(0)
        shape.SetLayer(pcbnew.B_SilkS)
        B.b.Add(shape)


def commit_box(t):
    """The box the commit text could take with any 7-character hash, so the routes don't depend on the commit."""
    box = None
    text = t.GetText()
    for c in '0123456789abcdef':
        t.SetText(text.replace('${COMMIT}', c * 7))
        bb = t.GetBoundingBox()
        if box is None:
            box = bb
        else:
            box.Merge(bb)
    t.SetText(text)
    return box


def point_in(p, poly):
    """True if point p is inside polygon poly."""
    x, y = p
    inside = False
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


SILK_GROW = VIA[0] / 2 + 0.1      # how far a via centre stays from silkscreen text


def silk_boxes(B):
    """The boxes of the board's silkscreen text and the name."""
    out = []
    for g in B.b.GetDrawings():
        if g.GetLayer() not in (pcbnew.F_SilkS, pcbnew.B_SilkS):
            continue
        bb = commit_box(g) if isinstance(g, pcbnew.PCB_TEXT) and '${COMMIT}' in g.GetText() else g.GetBoundingBox()
        (x0, y0), (x1, y1) = board_xy(bb.GetOrigin()), board_xy(bb.GetEnd())
        out.append((x0, y0, x1, y1))
    return out


def silk_keepout(B, R):
    """Stop vias going through the board's silkscreen text and the name. Footprint outlines are cut back round vias
    after routing instead, by clip_silk_at_vias."""
    for x0, y0, x1, y1 in silk_boxes(B):
        for k in R.rect(x0, y0, x1, y1, SILK_GROW):
            R.no_via[k] = 1


def clip_silk_at_vias(B, gap=0.1):
    """Cut footprint silkscreen lines back to gap clear of every via. Returns how many lines were cut."""
    vias = [(board_xy(t.GetPosition()), pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu)) / 2 + gap)
            for t in B.b.GetTracks() if isinstance(t, pcbnew.PCB_VIA)]
    cut = 0
    for fp in B.b.GetFootprints():
        for s in list(fp.GraphicalItems()):
            if s.GetLayer() not in (pcbnew.F_SilkS, pcbnew.B_SilkS) or not isinstance(s, pcbnew.PCB_SHAPE) \
                    or s.GetShape() != pcbnew.SHAPE_T_SEGMENT:
                continue
            a, b = board_xy(s.GetStart()), board_xy(s.GetEnd())
            half = pcbnew.ToMM(s.GetWidth()) / 2
            pieces = [(0.0, 1.0)]
            dx, dy = b[0] - a[0], b[1] - a[1]
            ln2 = dx * dx + dy * dy
            if ln2 == 0:
                continue
            for (vx, vy), r in vias:
                r += half
                # the stretch of the line, as fractions of its length, within r of the via centre
                t0 = ((vx - a[0]) * dx + (vy - a[1]) * dy) / ln2
                d2 = (a[0] + t0 * dx - vx) ** 2 + (a[1] + t0 * dy - vy) ** 2
                if d2 >= r * r:
                    continue
                h = math.sqrt((r * r - d2) / ln2)
                lo, hi = t0 - h, t0 + h
                pieces = [q for p in pieces for q in ((p[0], min(p[1], lo)), (max(p[0], hi), p[1])) if q[1] - q[0] > 1e-3]
            if pieces == [(0.0, 1.0)]:
                continue
            cut += 1
            for p0, p1 in pieces:
                n = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_SEGMENT)
                n.SetLayer(s.GetLayer())
                n.SetWidth(s.GetWidth())
                n.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(100 + a[0] + p0 * dx), pcbnew.FromMM(100 + a[1] + p0 * dy)))
                n.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(100 + a[0] + p1 * dx), pcbnew.FromMM(100 + a[1] + p1 * dy)))
                fp.Add(n)
            fp.Remove(s)
    return cut


def module_rects(v, mx, my):
    """The module's body and the area inside its ring of pads, on the board."""
    x0, y0, x1, y1 = VARIANTS[v]['body']
    body = (mx - x1, my - y1, mx - x0, my - y0)
    # rotated 180, the centre pad's offset from the module origin changes sign
    ex, ey = VARIANTS[v]['epad_center']
    half = VARIANTS[v]['epad_pitch'] + 0.75
    return body, (mx - ex - half, my - ey - half, mx - ex + half, my - ey + half)


def hand_routes(B):
    """SEL_A and SEL_B from J1 to their pull-ups and on to a via each, drawn by hand: [(net, segments, vias, ends)].
    The router routes on from the ends to the module. Points off the pad centres are on the router's grid, so that
    its tracks join them end to end."""
    def g(v):
        return round(round(v / GRID) * GRID, 6)
    j8, j10 = B.pad('J1', '8'), B.pad('J1', '10')
    r7, r8 = B.pad('R7', '2'), B.pad('R8', '2')
    a = (g(r7[0]), g(r7[1] + 0.95))                            # SEL_A's via, below R7
    a_end = (g(a[0] - 0.8), g(a[1] + 0.8))
    # SEL_B's via, between J1 pins 7 to 10
    b = (g((j8[0] + j10[0]) / 2), round(math.floor((j8[1] + PITCH / 2) / GRID) * GRID, 6))
    d = g(b[1] - j8[1])
    r8g = (g(r8[0]), g(r8[1]))
    sel_a = [(0, [j10, (a[0], j10[1]), a], W_SIG), (1, [a, a_end], W_SIG)]
    sel_b = [(0, [r8g, (g(r8g[0] - (r8g[1] - b[1])), b[1]), b, (g(b[0] - d), g(b[1] - d))], W_SIG)]
    return [('SEL_A', sel_a, [a], [(1,) + a_end]),
            ('SEL_B', sel_b, [b], [(layer,) + b for layer in range(len(ROUTE_LAYERS))])]


def autoroute(B, v, w, h, module, antenna_top):
    """Route every net except GND. Returns the nets left unrouted."""
    R = Router(w, h, GRID, CLEARANCE, W_PWR, VIA[0], via_cost=4.0, turn_cost=1.0, min_width=W_SIG,
               layers=len(ROUTE_LAYERS))
    R.edge(w, h, RULES['m_CopperEdgeClearance'])
    R.keepout(-1, antenna_top, w + 1, h + 1)
    body, inner = module_rects(v, *module)
    R.keepout(*inner, layers=(0,))
    for layer in (1, 2):
        R.area_cost(*body, layer, UNDER_MODULE_COST)
    # on the top, under the module, only short escapes from pads that have no other way out
    R.area_cost(*body, 0, UNDER_MODULE_TOP_COST)
    silk_keepout(B, R)
    ids, names = {}, []
    pad_ids = {}                         # net -> [(ref, pad number)], in the router's pad order
    for fp in B.b.GetFootprints():
        for p in fp.Pads():
            name = p.GetNetname()
            net = None
            if name and not name.startswith('unconnected'):
                if name not in ids:
                    ids[name] = len(names)
                    names.append(name)
                net = ids[name]
                pad_ids.setdefault(net, []).append((fp.GetReference(), p.GetNumber()))
            bb = p.GetBoundingBox()
            (x0, y0), (x1, y1) = board_xy(bb.GetOrigin()), board_xy(bb.GetEnd())
            if p.GetDrillSize().x > 0:
                layers = R.L
            else:
                layers = (R.nl - 1,) if p.IsOnLayer(pcbnew.B_Cu) else (0,)
            R.add_pad(net, layers, x0, y0, x1, y1, round_pad=p.GetShape() == pcbnew.PAD_SHAPE_CIRCLE)
    widths = {ids[n]: (W_PWR if n in POWER else W_SIG) for n in names}
    n3 = ids['+3V3']
    R.trunk[n3] = ([pad_ids[n3].index(rp) for rp in TRUNK_3V3[v]], W_TRUNK)
    def hpwl(n):
        xs = [R.cell_xy(k)[0] for cells in R.pads[ids[n]] for _, k in cells[:1]]
        ys = [R.cell_xy(k)[1] for cells in R.pads[ids[n]] for _, k in cells[:1]]
        return max(xs) - min(xs) + max(ys) - min(ys)
    order = sorted((n for n in names if n != 'GND' and len(R.pads[ids[n]]) > 1), key=lambda n: (hpwl(n), n))
    hand = hand_routes(B)
    for name, segs, vias, ends in hand:
        R.hand(ids[name], segs, vias, ends)
        for layer, pts, wd in segs:
            B.track(name, ROUTE_LAYERS[layer], wd, pts, chamfer=0)
        for at in vias:
            B.via(name, at, *VIA)
    # each top-side GND pad gets a via to the planes before the signals are routed
    gnd = ids['GND']
    stitched = R.prestitch(gnd, W_SIG)
    for segs, vias in stitched:
        for layer, pts, wd in segs:
            B.track('GND', ROUTE_LAYERS[layer], wd, pts, chamfer=0)
        for at in vias:
            B.via('GND', at, *VIA)
    print('STITCH %d GND pad vias' % len(stitched))
    R.log = lambda s: print(s, flush=True)
    R.names = names
    failed = R.run([ids[n] for n in order], widths, rounds=40)
    for net, (segs, vias) in sorted(R.routes.items()):
        for layer, pts, wd in segs:
            B.track(names[net], ROUTE_LAYERS[layer], wd, pts, chamfer=0)
        for at in vias:
            B.via(names[net], at, *VIA)
    print('ROUTED %d nets, %d vias' % (len(R.routes), sum(len(r[1]) for r in R.routes.values())))
    R.width[gnd] = W_SIG
    mx, my = module
    pitch = VARIANTS[v]['epad_pitch']
    ex, ey = VARIANTS[v]['epad_center']
    for dx in (-pitch / 2, pitch / 2):
        for dy in (-pitch / 2, pitch / 2):
            B.via('GND', (mx - ex + dx, my - ey + dy), *VIA)
    # vias joining every piece of the outer and In2 pours to the In1 plane
    spots = R.via_spots(gnd, (0, 0, w, antenna_top), STITCH_PITCH)
    for at in spots:
        B.via('GND', at, *VIA)
    print('STITCH %d GND pour vias' % len(spots))
    R.gnd = gnd
    return [names[n] for n in failed], R


def join_islands(B, w, h):
    """A GND via in each filled piece of a GND pour that has none, where one fits. Returns how many were added."""
    vias = [t.GetPosition() for t in B.b.GetTracks() if isinstance(t, pcbnew.PCB_VIA) and t.GetNetname() == 'GND']
    keep = silk_boxes(B)
    added = 0
    for z in B.b.Zones():
        if z.GetNetname() != 'GND':
            continue
        for layer in z.GetLayerSet().Seq():
            polys = z.GetFilledPolysList(layer)
            for i in range(polys.OutlineCount()):
                if any(polys.Contains(v, i) for v in vias):
                    continue
                bb = polys.Outline(i).BBox()
                (x0, y0), (x1, y1) = board_xy(bb.GetOrigin()), board_xy(bb.GetEnd())
                spots = [(x0 + 0.05 * a, y0 + 0.05 * b) for a in range(int((x1 - x0) / 0.05) + 1)
                         for b in range(int((y1 - y0) / 0.05) + 1)]
                # nearest the middle of the piece first
                spots.sort(key=lambda s: (s[0] - (x0 + x1) / 2) ** 2 + (s[1] - (y0 + y1) / 2) ** 2)
                for at in spots:
                    q = pcbnew.VECTOR2I(pcbnew.FromMM(at[0] + 100), pcbnew.FromMM(at[1] + 100))
                    if polys.Contains(q, i) and via_fits(B, at, w, h, keep):
                        vias.append(B.via('GND', at, *VIA).GetPosition())
                        added += 1
                        break
    return added


def via_fits(B, at, w, h, keep):
    """True if a GND via at fits by the board's own rules: clear of other nets' copper, 0.1 mm from every pad, clear
    of every hole, the board edge and the silkscreen boxes in keep."""
    x, y = at
    r = VIA[0] / 2
    e = RULES['m_CopperEdgeClearance'] + r
    if x < e or y < e or x > w - e or y > h - e:
        return False
    g = SILK_GROW
    if any(x0 - g <= x <= x1 + g and y0 - g <= y <= y1 + g for x0, y0, x1, y1 in keep):
        return False
    c = pcbnew.VECTOR2I(pcbnew.FromMM(x + 100), pcbnew.FromMM(y + 100))
    copper = pcbnew.SHAPE_CIRCLE(c, pcbnew.FromMM(r))
    hole = pcbnew.SHAPE_CIRCLE(c, pcbnew.FromMM(VIA[1] / 2))
    layers = (pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.B_Cu)
    for fp in B.b.GetFootprints():
        for p in fp.Pads():
            gap = pcbnew.FromMM(0.1 if p.GetNetname() == 'GND' else CLEARANCE)
            if any(p.IsOnLayer(l) and p.GetEffectiveShape(l).Collide(copper, gap) for l in layers):
                return False
            if p.HasHole() and p.GetEffectiveHoleShape().Collide(hole, pcbnew.FromMM(RULES['m_HoleClearance'])):
                return False
    for t in B.b.GetTracks():
        gnd = t.GetNetname() == 'GND'
        if isinstance(t, pcbnew.PCB_VIA):
            if t.GetEffectiveHoleShape().Collide(hole, pcbnew.FromMM(RULES['m_HoleToHoleMin'])):
                return False
            if not gnd and t.GetEffectiveShape(pcbnew.F_Cu).Collide(copper, pcbnew.FromMM(CLEARANCE)):
                return False
        elif not gnd and t.GetEffectiveShape(t.GetLayer()).Collide(copper, pcbnew.FromMM(CLEARANCE)):
            return False
    return True


def zones(B, w, antenna_top):
    """The In1 GND plane and GND pours on the other layers, all stopping where the antenna starts."""
    area = [(0, 0), (w, 0), (w, antenna_top), (0, antenna_top)]
    for layer, name in (('In1', 'GND plane'), ('F', 'GND top'), ('In2', 'GND In2'), ('B', 'GND bottom')):
        B.zone('GND', layer, area, clearance=CLEARANCE, min_width=0.2, solid=True, name=name)


if __name__ == '__main__':
    variant = sys.argv[1]
    out = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, 'build', variant))
    sys.exit(1 if build(variant, out) else 0)
