"""Write the outlines of some characters of a TrueType font to JSON, for silkscreen text drawn as polygons.

The board is generated from the JSON, so the font itself isn't needed to build it, and KiCad, CI and JLC never need
it installed. Usage: glyphs.py <font.ttf> <characters> <out.json>

Each character has its advance and its contours, in units of the font's em, y up. Curves are flattened to lines.
"""
import json
import struct
import sys


class Font:
    def __init__(self, path):
        self.d = open(path, 'rb').read()
        n = struct.unpack('>H', self.d[4:6])[0]
        self.t = {}
        for i in range(n):
            tag, _, off, ln = struct.unpack('>4sIII', self.d[12 + 16 * i:28 + 16 * i])
            self.t[tag.decode('latin-1')] = (off, ln)
        h = self.t['head'][0]
        self.em = struct.unpack('>H', self.d[h + 18:h + 20])[0]
        self.long_loca = struct.unpack('>h', self.d[h + 50:h + 52])[0] == 1
        m = self.t['maxp'][0]
        self.nglyphs = struct.unpack('>H', self.d[m + 4:m + 6])[0]
        hh = self.t['hhea'][0]
        self.nmetrics = struct.unpack('>H', self.d[hh + 34:hh + 36])[0]
        self.cmap = self._cmap()

    def u16(self, o):
        return struct.unpack('>H', self.d[o:o + 2])[0]

    def i16(self, o):
        return struct.unpack('>h', self.d[o:o + 2])[0]

    def _cmap(self):
        c = self.t['cmap'][0]
        best = None
        for i in range(self.u16(c + 2)):
            pid, eid, off = struct.unpack('>HHI', self.d[c + 4 + 8 * i:c + 12 + 8 * i])
            if self.u16(c + off) == 4 and (pid, eid) in ((3, 1), (0, 3), (0, 4), (3, 0)):
                best = c + off
        out = {}
        segx2 = self.u16(best + 6)
        ends, starts = best + 14, best + 16 + segx2
        deltas, ranges = starts + segx2, starts + 2 * segx2
        for s in range(segx2 // 2):
            end, start = self.u16(ends + 2 * s), self.u16(starts + 2 * s)
            delta, ro = self.i16(deltas + 2 * s), self.u16(ranges + 2 * s)
            for ch in range(start, min(end, 0xFFFE) + 1):
                if ro == 0:
                    g = (ch + delta) & 0xFFFF
                else:
                    g = self.u16(ranges + 2 * s + ro + 2 * (ch - start))
                    if g:
                        g = (g + delta) & 0xFFFF
                out[ch] = g
        return out

    def advance(self, g):
        h = self.t['hmtx'][0]
        return self.u16(h + 4 * min(g, self.nmetrics - 1))

    def _glyph_range(self, g):
        lo = self.t['loca'][0]
        if self.long_loca:
            a, b = struct.unpack('>II', self.d[lo + 4 * g:lo + 4 * g + 8])
        else:
            a, b = (2 * v for v in struct.unpack('>HH', self.d[lo + 2 * g:lo + 2 * g + 4]))
        return self.t['glyf'][0] + a, b - a

    def contours(self, g, dx=0, dy=0):
        """Contours of glyph g as lists of (x, y, on_curve)."""
        o, ln = self._glyph_range(g)
        if ln == 0:
            return []
        nc = self.i16(o)
        if nc < 0:
            return self._composite(o + 10, dx, dy)
        ends = [self.u16(o + 10 + 2 * i) for i in range(nc)]
        p = o + 10 + 2 * nc
        p += 2 + self.u16(p)
        npts = ends[-1] + 1 if ends else 0
        flags = []
        while len(flags) < npts:
            f = self.d[p]
            p += 1
            flags.append(f)
            if f & 8:
                r = self.d[p]
                p += 1
                flags += [f] * r
        xs, ys = [], []
        for coords, short, same in ((xs, 2, 16), (ys, 4, 32)):
            v = 0
            for f in flags:
                if f & short:
                    d = self.d[p]
                    p += 1
                    v += d if f & same else -d
                elif not f & same:
                    v += self.i16(p)
                    p += 2
                coords.append(v)
        out, s = [], 0
        for e in ends:
            out.append([(xs[i] + dx, ys[i] + dy, bool(flags[i] & 1)) for i in range(s, e + 1)])
            s = e + 1
        return out

    def _composite(self, p, dx, dy):
        out = []
        while True:
            flags, g = self.u16(p), self.u16(p + 2)
            p += 4
            if flags & 1:
                a, b = self.i16(p), self.i16(p + 2)
                p += 4
            else:
                a, b = struct.unpack('>bb', self.d[p:p + 2])
                p += 2
            if flags & 8:
                p += 2
            elif flags & 0x40:
                p += 4
            elif flags & 0x80:
                p += 8
            out += self.contours(g, dx + a, dy + b)
            if not flags & 0x20:
                return out


def flatten(contour, steps=6):
    """A closed TrueType contour of on- and off-curve points as a polygon."""
    pts = list(contour)
    if not any(on for _, _, on in pts):
        x0, y0, _ = pts[0]
        x1, y1, _ = pts[1]
        pts.insert(0, ((x0 + x1) / 2, (y0 + y1) / 2, True))
    while not pts[0][2]:
        pts.append(pts.pop(0))
    # an implied on-curve point between each pair of off-curve points
    full = []
    for a, b in zip(pts, pts[1:] + pts[:1]):
        full.append(a)
        if not a[2] and not b[2]:
            full.append(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, True))
    out = []
    i = 0
    n = len(full)
    while i < n:
        a = full[i]
        b = full[(i + 1) % n]
        if b[2]:
            out.append((a[0], a[1]))
            i += 1
            continue
        c = full[(i + 2) % n]
        for s in range(steps):
            t = s / steps
            out.append(((1 - t) ** 2 * a[0] + 2 * (1 - t) * t * b[0] + t * t * c[0],
                        (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * b[1] + t * t * c[1]))
        i += 2
    return out


def main(path, chars, out):
    f = Font(path)
    glyphs = {}
    for ch in chars:
        g = f.cmap.get(ord(ch), 0)
        glyphs[ch] = {'advance': f.advance(g) / f.em,
                      'contours': [[(round(x / f.em, 5), round(y / f.em, 5)) for x, y in flatten(c)]
                                   for c in f.contours(g)]}
    json.dump({'source': path.rsplit('/', 1)[-1], 'glyphs': glyphs}, open(out, 'w'), indent=0)


if __name__ == '__main__':
    main(*sys.argv[1:4])
