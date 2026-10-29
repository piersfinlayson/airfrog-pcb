"""Minimal KiCad 9 PCB writer. Runs under KiCad's bundled Python, which provides pcbnew.

Footprints come from a kicadsexpr netlist exported from the schematic, so each one
is linked to its symbol. Board coordinates are in mm from the board's top left
corner, y down. Rotations are in degrees, anticlockwise, as in KiCad.
"""
import math
import os
import re
import subprocess

import pcbnew

from kicad_paths import FOOTPRINT_DIR, KICAD_CLI
from kisch import find, parse, unq

PAGE = (100.0, 100.0)   # board top left corner on the KiCad page

LAYERS = {'F': pcbnew.F_Cu, 'In1': pcbnew.In1_Cu, 'In2': pcbnew.In2_Cu, 'B': pcbnew.B_Cu}


def mm(v):
    return pcbnew.FromMM(v)


def v2(p):
    return pcbnew.VECTOR2I(mm(PAGE[0] + p[0]), mm(PAGE[1] + p[1]))


def board_xy(v):
    """Board millimetres of a KiCad position."""
    return pcbnew.ToMM(v.x) - PAGE[0], pcbnew.ToMM(v.y) - PAGE[1]


def rotate(dx, dy, deg):
    """Offset (dx, dy) of an unrotated footprint, after rotating it by deg."""
    a = math.radians(deg)
    return (round(dx * math.cos(a) + dy * math.sin(a), 6),
            round(-dx * math.sin(a) + dy * math.cos(a), 6))


# ---------------------------------------------------------------- netlist

def export_netlist(sch, out):
    subprocess.run([KICAD_CLI, 'sch', 'export', 'netlist', '-o', out, sch], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def read_netlist(path):
    """({ref: component}, {net: [(ref, pin)]}) from a kicadsexpr netlist."""
    root = parse(open(path).read())
    comps = {}
    for c in find(find(root, 'components')[0], 'comp'):
        ref = unq(find(c, 'ref')[0][1])
        fields = {}
        for fl in find(find(c, 'fields')[0], 'field') if find(c, 'fields') else []:
            fields[unq(find(fl, 'name')[0][1])] = unq(fl[2]) if len(fl) > 2 else ''
        props = {}
        for p in find(c, 'property'):
            v = find(p, 'value')
            props[unq(find(p, 'name')[0][1])] = unq(v[0][1]) if v else ''
        sheet = unq(find(find(c, 'sheetpath')[0], 'tstamps')[0][1])
        comps[ref] = {
            'value': unq(find(c, 'value')[0][1]),
            'footprint': unq(find(c, 'footprint')[0][1]) if find(c, 'footprint') else '',
            'fields': fields,
            'props': props,
            'path': sheet + unq(find(c, 'tstamps')[0][1]),
        }
    nets = {}
    for n in find(find(root, 'nets')[0], 'net'):
        name = unq(find(n, 'name')[0][1])
        nets[name] = [(unq(find(x, 'ref')[0][1]), unq(find(x, 'pin')[0][1])) for x in find(n, 'node')]
    return comps, nets


def read_title_block(path):
    """(title, revision, comment 1) of the schematic's title block in a kicadsexpr netlist."""
    tb = find(find(find(parse(open(path).read()), 'design')[0], 'sheet')[0], 'title_block')[0]
    comments = {unq(find(c, 'number')[0][1]): unq(find(c, 'value')[0][1]) for c in find(tb, 'comment')}
    return unq(find(tb, 'title')[0][1]), unq(find(tb, 'rev')[0][1]), comments['1']


def fp_libs(table):
    """{nickname: .pretty path} for KiCad's stock footprint libraries and those in a project's footprint
    library table."""
    libs = {n[:-len('.pretty')]: os.path.join(FOOTPRINT_DIR, n) for n in os.listdir(FOOTPRINT_DIR) if n.endswith('.pretty')}
    for m in re.finditer(r'\(lib \(name "([^"]+)"\)\(type "KiCad"\)\(uri "([^"]+)"\)', open(table).read()):
        libs[m.group(1)] = m.group(2).replace('${KIPRJMOD}', os.path.dirname(table))
    return libs


# ---------------------------------------------------------------- board

class Board:
    def __init__(self, path, comps, nets, fp_table):
        self.path = path
        self.b = pcbnew.NewBoard(path)
        self.b.SetFileName(path)    # NewBoard leaves it empty. The DRC rules file is looked up by it.
        self.b.SetCopperLayerCount(4)
        self.comps, self.netlist = comps, nets
        self.pin_net = {(r, p): n for n, nodes in nets.items() for r, p in nodes}
        self.libs = fp_libs(fp_table)
        self.fps = {}
        self.warnings = []
        self._nets = {}
        for name in nets:
            self.net(name)

    def net(self, name):
        if name not in self._nets:
            n = pcbnew.NETINFO_ITEM(self.b, name)
            self.b.Add(n)
            self._nets[name] = n
        return self._nets[name]

    # ---- footprints
    def load(self, lib_id):
        nick, name = lib_id.split(':')
        fp = pcbnew.FootprintLoad(self.libs[nick], name)
        if fp is None:
            raise ValueError('footprint %s not found' % lib_id)
        fp.SetFPID(pcbnew.LIB_ID(nick, name))
        return fp

    def place(self, ref, x, y, rot=0):
        """Place the footprint of netlist component ref with its origin at (x, y)."""
        c = self.comps[ref]
        fp = self.load(c['footprint'])
        fp.SetReference(ref)
        fp.SetValue(c['value'])
        fp.SetPath(pcbnew.KIID_PATH(c['path']))
        fp.SetSheetname(c['props'].get('Sheetname', ''))
        fp.SetSheetfile(c['props'].get('Sheetfile', ''))
        for k, v in c['fields'].items():
            if k == 'Footprint':
                continue
            if fp.HasFieldByName(k):
                fp.GetFieldByName(k).SetText(v)
            else:
                fp.SetField(k, v)
                fld = fp.GetFieldByName(k)
                fld.SetVisible(False)
                fld.SetLayer(pcbnew.F_Fab)
        if 'dnp' in c['props']:
            fp.SetDNP(True)
        if 'exclude_from_bom' in c['props']:
            fp.SetExcludedFromBOM(True)
        fp.Reference().SetVisible(False)
        fp.SetPosition(v2((x, y)))
        fp.SetOrientationDegrees(rot)
        for pad in fp.Pads():
            n = self.pin_net.get((ref, pad.GetNumber()))
            if n:
                pad.SetNet(self.net(n))
        self.b.Add(fp)
        self.fps[ref] = fp
        return fp

    def board_only(self, ref, lib_id, x, y, rot=0, value=''):
        """Footprint without a schematic symbol, for example a mounting hole."""
        fp = self.load(lib_id)
        fp.SetReference(ref)
        fp.SetValue(value or lib_id.split(':')[1])
        fp.SetBoardOnly(True)
        fp.SetExcludedFromBOM(True)
        fp.SetExcludedFromPosFiles(True)
        fp.Reference().SetVisible(False)
        fp.SetPosition(v2((x, y)))
        fp.SetOrientationDegrees(rot)
        self.b.Add(fp)
        self.fps[ref] = fp
        return fp

    def set_model(self, ref, path):
        """Replace a placed footprint's 3D models with one model at path."""
        models = self.b.FindFootprintByReference(ref).Models()
        models.clear()
        m = pcbnew.FP_3DMODEL()
        m.m_Filename = path
        models.push_back(m)

    def vias_on_pads(self):
        """A VIAPAD line for each via that overlaps a pad without a hole, whatever the pad's net."""
        out = []
        pads = [(p, fp.GetReference()) for fp in self.b.GetFootprints() for p in fp.Pads()
                if p.GetDrillSize().x == 0]
        for v in self.b.GetTracks():
            if not isinstance(v, pcbnew.PCB_VIA):
                continue
            r = v.GetWidth(pcbnew.F_Cu) // 2
            for p, ref in pads:
                for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
                    if not p.IsOnLayer(layer):
                        continue
                    poly = pcbnew.SHAPE_POLY_SET()
                    p.TransformShapeToPolygon(poly, layer, r, mm(0.005), pcbnew.ERROR_INSIDE)
                    if poly.Contains(v.GetPosition()):
                        x, y = board_xy(v.GetPosition())
                        out.append('VIAPAD %s via at (%.2f,%.2f) on %s pad %s' % (v.GetNetname(), x, y, ref, p.GetNumber()))
        return out

    def unplaced(self):
        return sorted(set(self.comps) - set(self.fps))

    def pad(self, ref, num):
        """Board coordinates of a pad's centre. Footprints may have several pads with one number."""
        for p in self.fps[ref].Pads():
            if p.GetNumber() == str(num):
                q = p.GetPosition()
                return (round(pcbnew.ToMM(q.x) - PAGE[0], 6), round(pcbnew.ToMM(q.y) - PAGE[1], 6))
        raise KeyError('%s has no pad %s' % (ref, num))

    def move_pad_to(self, ref, num, at):
        """Move a placed footprint so that its pad num is at board point at."""
        px, py = self.pad(ref, num)
        fp = self.fps[ref]
        q = fp.GetPosition()
        fp.SetPosition(pcbnew.VECTOR2I(q.x + mm(at[0] - px), q.y + mm(at[1] - py)))

    def pad_net(self, ref, num):
        return self.pin_net[(ref, str(num))]

    # ---- copper
    def track(self, net, layer, width, pts, chamfer=0.25):
        """Track through pts. Square corners are cut at 45 degrees unless chamfer is 0.
        Segments that aren't horizontal, vertical or diagonal are reported."""
        pts = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
        for a, b in zip(pts, pts[1:]):
            if odd_angle(a, b):
                self.warnings.append('ANGLE %s %s (%.3f,%.3f)-(%.3f,%.3f)' % (net, layer, a[0], a[1], b[0], b[1]))
        for a, p, b in zip(pts, pts[1:], pts[2:]):
            if turn_cos(a, p, b) < -0.01:
                self.warnings.append('ACUTE %s %s at (%.3f,%.3f)' % (net, layer, p[0], p[1]))
        if chamfer:
            pts = chamfered(pts, max(chamfer, width / 2))
        n = self.net(net)
        for a, b in zip(pts, pts[1:]):
            t = pcbnew.PCB_TRACK(self.b)
            t.SetStart(v2(a))
            t.SetEnd(v2(b))
            t.SetWidth(mm(width))
            t.SetLayer(LAYERS[layer])
            t.SetNet(n)
            self.b.Add(t)

    def via(self, net, at, size=0.6, drill=0.3):
        v = pcbnew.PCB_VIA(self.b)
        v.SetPosition(v2(at))
        v.SetViaType(pcbnew.VIATYPE_THROUGH)
        v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        v.SetWidth(mm(size))
        v.SetDrill(mm(drill))
        v.SetNet(self.net(net))
        self.b.Add(v)
        return v

    def zone(self, net, layer, pts, priority=0, clearance=0.2, min_width=0.2, solid=False,
             thermal_gap=0.3, spoke=0.3, name=''):
        z = pcbnew.ZONE(self.b)
        z.SetLayer(LAYERS[layer])
        if net:
            z.SetNet(self.net(net))
        ol = z.Outline()
        ol.NewOutline()
        for p in pts:
            q = v2(p)
            ol.Append(q.x, q.y)
        z.SetAssignedPriority(priority)
        z.SetLocalClearance(mm(clearance))
        z.SetMinThickness(mm(min_width))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL if solid else pcbnew.ZONE_CONNECTION_THERMAL)
        z.SetThermalReliefGap(mm(thermal_gap))
        z.SetThermalReliefSpokeWidth(mm(spoke))
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        if name:
            z.SetZoneName(name)
        self.b.Add(z)
        return z

    def stitch(self, net, layers, area, pitch, size, drill, clearance):
        """Stitching vias on net over area (x0, y0, x1, y1), at least pitch from each other
        and from the net's existing vias. A via is placed where the net's filled pours cover it with
        clearance on every one of layers, clear of courtyards and board text. Candidate points are a
        quarter of pitch apart. Fill the zones before and after."""
        x0, y0, x1, y1 = area
        def shrunk(z, layer):
            p = pcbnew.SHAPE_POLY_SET(z.GetFilledPolysList(layer))
            p.Deflate(mm(size / 2 + clearance), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, mm(0.005))
            return p
        by_layer = [[shrunk(z, LAYERS[l]) for z in self.b.Zones()
                     if z.GetNetname() == net and z.IsOnLayer(LAYERS[l])] for l in layers]
        yards = [fp.GetCourtyard(pcbnew.F_CrtYd) for fp in self.b.GetFootprints()]
        texts = [d.GetBoundingBox() for d in self.b.GetDrawings() if isinstance(d, pcbnew.PCB_TEXT)]
        for bb in texts:
            bb.Inflate(mm(size / 2))
        vias = [v for v in self.b.GetTracks() if isinstance(v, pcbnew.PCB_VIA)]
        others = [board_xy(v.GetPosition()) for v in vias if v.GetNetname() != net]
        mine = [board_xy(v.GetPosition()) for v in vias if v.GetNetname() == net]
        step = pitch / 4
        n = 0
        y = y0 + step
        while y < y1:
            x = x0 + step
            while x < x1:
                q = v2((x, y))
                if (all(math.hypot(x - ox, y - oy) >= pitch for ox, oy in mine)
                        and all(any(p.Contains(q) for p in ps) for ps in by_layer)
                        and not any(c.Contains(q) or c.SquaredDistance(q, True) < mm(size / 2) ** 2 for c in yards)
                        and not any(bb.Contains(q) for bb in texts)
                        and all(math.hypot(x - ox, y - oy) >= size + clearance for ox, oy in others)):
                    self.via(net, (x, y), size, drill)
                    mine.append((x, y))
                    n += 1
                x += step
            y += step
        return n

    def keepout(self, layers, pts, tracks=False, vias=False, pour=True, name=''):
        z = pcbnew.ZONE(self.b)
        z.SetIsRuleArea(True)
        ls = pcbnew.LSET()
        for l in layers:
            ls.AddLayer(LAYERS[l])
        z.SetLayerSet(ls)
        ol = z.Outline()
        ol.NewOutline()
        for p in pts:
            q = v2(p)
            ol.Append(q.x, q.y)
        z.SetDoNotAllowTracks(tracks)
        z.SetDoNotAllowVias(vias)
        z.SetDoNotAllowZoneFills(pour)
        z.SetDoNotAllowPads(False)
        z.SetDoNotAllowFootprints(False)
        if name:
            z.SetZoneName(name)
        self.b.Add(z)
        return z

    def fill(self, scratch):
        """Fill all zones. The filler's clearances come from net classes and the DRC rules, so both
        are loaded first."""
        self.b.SynchronizeNetsAndNetClasses(False)
        self.b.BuildConnectivity()
        pcbnew.WriteDRCReport(self.b, scratch, pcbnew.EDA_UNITS_MM, False)
        pcbnew.ZONE_FILLER(self.b).Fill(self.b.Zones())

    # ---- graphics
    def line(self, layer, a, b, width=0.1):
        s = pcbnew.PCB_SHAPE(self.b, pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(v2(a))
        s.SetEnd(v2(b))
        s.SetLayer(layer)
        s.SetWidth(mm(width))
        self.b.Add(s)

    def arc(self, layer, start, mid, end, width=0.1):
        s = pcbnew.PCB_SHAPE(self.b, pcbnew.SHAPE_T_ARC)
        s.SetArcGeometry(v2(start), v2(mid), v2(end))
        s.SetLayer(layer)
        s.SetWidth(mm(width))
        self.b.Add(s)

    def outline(self, w, h, r):
        """Rectangular board edge with corner radius r."""
        e = pcbnew.Edge_Cuts
        k = r * (1 - math.sqrt(0.5))
        self.line(e, (r, 0), (w - r, 0))
        self.line(e, (w, r), (w, h - r))
        self.line(e, (w - r, h), (r, h))
        self.line(e, (0, h - r), (0, r))
        self.arc(e, (w - r, 0), (w - k, k), (w, r))
        self.arc(e, (w, h - r), (w - k, h - k), (w - r, h))
        self.arc(e, (r, h), (k, h - k), (0, h - r))
        self.arc(e, (0, r), (k, k), (r, 0))

    def clip_silk(self, w, h, margin):
        """Cut footprint silk lines back to margin inside a w x h board. Silk on a footprint at the
        edge, for example a connector, otherwise runs off the board."""
        x0, y0, x1, y1 = margin, margin, w - margin, h - margin
        for fp in self.b.GetFootprints():
            for s in list(fp.GraphicalItems()):
                if s.GetLayer() != pcbnew.F_SilkS or not isinstance(s, pcbnew.PCB_SHAPE):
                    continue
                if s.GetShape() != pcbnew.SHAPE_T_SEGMENT:
                    bb = s.GetBoundingBox()
                    (l, t), (r, b) = board_xy(bb.GetOrigin()), board_xy(bb.GetEnd())
                    if l < x0 or t < y0 or r > x1 or b > y1:
                        self.warnings.append('SILK %s: shape past the board edge' % fp.GetReference())
                    continue
                a, b = board_xy(s.GetStart()), board_xy(s.GetEnd())
                c = clip_segment(a, b, x0, y0, x1, y1)
                if c is None:
                    fp.Remove(s)
                elif c != (a, b):
                    s.SetStart(v2(c[0]))
                    s.SetEnd(v2(c[1]))

    def text(self, s, at, size=1.0, thickness=0.15, layer=pcbnew.F_SilkS, rot=0, justify='center', bold=False):
        t = pcbnew.PCB_TEXT(self.b)
        t.SetText(s)
        t.SetPosition(v2(at))
        t.SetLayer(layer)
        t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size)))
        t.SetTextThickness(mm(thickness))
        t.SetBold(bold)
        t.SetTextAngleDegrees(rot)
        t.SetHorizJustify({'left': pcbnew.GR_TEXT_H_ALIGN_LEFT, 'center': pcbnew.GR_TEXT_H_ALIGN_CENTER,
                           'right': pcbnew.GR_TEXT_H_ALIGN_RIGHT}[justify])
        self.b.Add(t)
        return t

    # ---- output
    def save(self, stackup=None):
        pcbnew.SaveBoard(self.path, self.b)
        if stackup:
            text = open(self.path).read()
            text = text.replace('\t(setup\n', '\t(setup\n' + stackup, 1)
            open(self.path, 'w').write(text)


# ---------------------------------------------------------------- geometry

def offset_polyline(pts, d):
    """pts offset by d to the left of the direction of travel, with mitred corners."""
    def normal(a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = math.hypot(dx, dy)
        return (dy / n, -dx / n)    # left of travel on a y-down page
    segs = list(zip(pts, pts[1:]))
    out = []
    for i, p in enumerate(pts):
        if i == 0:
            nx, ny = normal(*segs[0])
            out.append((p[0] + d * nx, p[1] + d * ny))
        elif i == len(pts) - 1:
            nx, ny = normal(*segs[-1])
            out.append((p[0] + d * nx, p[1] + d * ny))
        else:
            n1, n2 = normal(*segs[i - 1]), normal(*segs[i])
            bx, by = n1[0] + n2[0], n1[1] + n2[1]
            dot = n1[0] * n2[0] + n1[1] * n2[1]
            k = d / (1 + dot)
            out.append((p[0] + k * bx, p[1] + k * by))
    return [(round(x, 6), round(y, 6)) for x, y in out]


def odd_angle(a, b, tol=0.01):
    """True when segment ab is more than tol off horizontal, vertical or 45 degrees."""
    dx, dy = abs(b[0] - a[0]), abs(b[1] - a[1])
    return min(dx, dy, abs(dx - dy) / math.sqrt(2)) > tol


def turn_cos(a, p, b):
    ux, uy = p[0] - a[0], p[1] - a[1]
    vx, vy = b[0] - p[0], b[1] - p[1]
    return (ux * vx + uy * vy) / (math.hypot(ux, uy) * math.hypot(vx, vy))


def chamfered(pts, c):
    """pts with each square corner replaced by a 45 degree cut of up to c along each side."""
    out = [pts[0]]
    for a, p, b in zip(pts, pts[1:], pts[2:]):
        la, lb = math.hypot(p[0] - a[0], p[1] - a[1]), math.hypot(b[0] - p[0], b[1] - p[1])
        if abs(turn_cos(a, p, b)) > 0.01:
            out.append(p)
            continue
        k = min(c, 0.45 * la, 0.45 * lb)
        out.append((round(p[0] - (p[0] - a[0]) / la * k, 6), round(p[1] - (p[1] - a[1]) / la * k, 6)))
        out.append((round(p[0] + (b[0] - p[0]) / lb * k, 6), round(p[1] + (b[1] - p[1]) / lb * k, 6)))
    out.append(pts[-1])
    return out


def elbow(a, b, straight_first=True):
    """a to b as one horizontal or vertical segment and one 45 degree segment."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    sx, sy = (1 if dx > 0 else -1), (1 if dy > 0 else -1)
    if abs(dx) >= abs(dy):
        m = (b[0] - sx * abs(dy), a[1]) if straight_first else (a[0] + sx * abs(dy), b[1])
    else:
        m = (a[0], b[1] - sy * abs(dx)) if straight_first else (b[0], a[1] + sy * abs(dx))
    return [a, (round(m[0], 6), round(m[1], 6)), b]


def clip_segment(a, b, x0, y0, x1, y1):
    """The part of segment a-b inside the rectangle, or None if none of it is."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if p == 0:
            if q < 0:
                return None
            continue
        r = q / p
        if p < 0:
            t0 = max(t0, r)
        else:
            t1 = min(t1, r)
        if t0 > t1:
            return None
    if t0 == 0 and t1 == 1:
        return a, b
    return (a[0] + t0 * dx, a[1] + t0 * dy), (a[0] + t1 * dx, a[1] + t1 * dy)


def length(pts):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
