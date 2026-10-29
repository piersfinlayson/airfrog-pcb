"""Grid router for a few copper layers. Pure Python, so it runs under KiCad's bundled Python.

Tracks run on a square grid in 0, 45 and 90 degree steps. Each net is routed with A* from the copper it already has
to its nearest unconnected pad, so a net with several pads grows as a tree. Nets may at first come too close to
each other. Each round reroutes the nets in conflict, and the cells they shared cost more, until none are. The same
input always produces the same routes.

Clearance is kept by marking, for each committed track, via and pad, the cells where another net's track centre may
not go. Coordinates are in mm from the board's top left corner, y down. Layers are numbered from the top, and every
via goes through all of them.
"""
import heapq
import math
from array import array

FREE, BLOCKED, SHARED = -1, -2, -3
DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]


class Router:
    def __init__(self, w, h, pitch, clearance, max_width, via, via_cost, turn_cost, min_width=None, layers=2):
        self.p = pitch
        self.nx, self.ny = int(math.ceil(w / pitch)) + 1, int(math.ceil(h / pitch)) + 1
        self.clr, self.wmax = clearance, max_width
        self.wmin = min_width or max_width    # the width marks assume for the other net's track
        self.via_d, self.via_cost, self.turn_cost = via, via_cost, turn_cost
        n = self.nx * self.ny
        self.nl = layers
        self.L = tuple(range(layers))
        self.fixed = [array('h', [FREE]) * n for _ in self.L]      # pads and keepouts
        self.pad = [array('h', [FREE]) * n for _ in self.L]        # pad copper, by net
        self.cost = [array('f', [1.0]) * n for _ in self.L]        # cost of a step into a cell
        self.no_via = bytearray(n)                                 # cells where a via may not go
        self.routes = {}                                           # net -> (segments, vias)
        self.trunk = {}                                            # net -> (pad indices, width)
        self.occ = self.hist = None
        self.pres = 0.0
        self.width = {}
        self.pads = {}                                             # net -> [(layers, cells)]
        self.log = None
        self.names = None
        self._off = {}

    # ---- grid
    def idx(self, i, j):
        return j * self.nx + i

    def cell_xy(self, k):
        return (k % self.nx) * self.p, (k // self.nx) * self.p

    def cell(self, x, y):
        return int(round(x / self.p)), int(round(y / self.p))

    def offsets(self, r):
        """(di, dj) of the cells within r of a cell's centre."""
        key = round(r, 4)
        if key not in self._off:
            m = int(math.ceil(r / self.p))
            self._off[key] = [(di, dj) for dj in range(-m, m + 1) for di in range(-m, m + 1)
                              if (di * self.p) ** 2 + (dj * self.p) ** 2 <= r * r + 1e-9]
        return self._off[key]

    def dilate(self, cells, r):
        """The cells within r of any of cells, a set of cell indices."""
        nx, ny = self.nx, self.ny
        out = set()
        off = self.offsets(r)
        for k in cells:
            i, j = k % nx, k // nx
            for di, dj in off:
                ii, jj = i + di, j + dj
                if 0 <= ii < nx and 0 <= jj < ny:
                    out.add(jj * nx + ii)
        return out

    def line_cells(self, pts):
        """Cells along a polyline, one per grid step."""
        out = set()
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            n = max(1, int(math.ceil(math.hypot(x1 - x0, y1 - y0) / self.p)))
            for t in range(n + 1):
                i, j = self.cell(x0 + (x1 - x0) * t / n, y0 + (y1 - y0) * t / n)
                out.add(self.idx(i, j))
        return out

    def disk(self, x, y, r):
        """Cells whose centres are within r of (x, y)."""
        i0, i1 = int(math.floor((x - r) / self.p)), int(math.ceil((x + r) / self.p))
        j0, j1 = int(math.floor((y - r) / self.p)), int(math.ceil((y + r) / self.p))
        for j in range(max(j0, 0), min(j1, self.ny - 1) + 1):
            for i in range(max(i0, 0), min(i1, self.nx - 1) + 1):
                if (i * self.p - x) ** 2 + (j * self.p - y) ** 2 <= r * r + 1e-9:
                    yield self.idx(i, j)

    def rect(self, x0, y0, x1, y1, grow=0.0):
        """Cells whose centres are inside the rectangle grown by grow, with rounded corners."""
        i0, i1 = int(math.floor((x0 - grow) / self.p)), int(math.ceil((x1 + grow) / self.p))
        j0, j1 = int(math.floor((y0 - grow) / self.p)), int(math.ceil((y1 + grow) / self.p))
        for j in range(max(j0, 0), min(j1, self.ny - 1) + 1):
            for i in range(max(i0, 0), min(i1, self.nx - 1) + 1):
                x, y = i * self.p, j * self.p
                dx = max(x0 - x, 0, x - x1)
                dy = max(y0 - y, 0, y - y1)
                if dx * dx + dy * dy <= grow * grow + 1e-9:
                    yield self.idx(i, j)

    @staticmethod
    def _claim(a, k, net):
        v = a[k]
        if v == FREE:
            a[k] = net
        elif v != net and v != BLOCKED:
            a[k] = SHARED

    # ---- input
    def keepout(self, x0, y0, x1, y1, layers=None):
        for layer in self.L if layers is None else layers:
            for k in self.rect(x0, y0, x1, y1, self.clr + self.wmax / 2):
                self.fixed[layer][k] = BLOCKED

    def edge(self, w, h, margin):
        """Block the cells closer than margin to the board edge."""
        m = margin + self.wmax / 2
        for layer in self.L:
            for j in range(self.ny):
                for i in range(self.nx):
                    x, y = i * self.p, j * self.p
                    if x < m or y < m or x > w - m or y > h - m:
                        self.fixed[layer][self.idx(i, j)] = BLOCKED

    def area_cost(self, x0, y0, x1, y1, layer, factor, no_via=False):
        for k in self.rect(x0, y0, x1, y1):
            self.cost[layer][k] = factor
            if no_via:
                self.no_via[k] = 1

    def add_pad(self, net, layers, x0, y0, x1, y1, round_pad=False):
        """Pad copper rectangle on layers. net is an int, or None for a pad on no net. Tracks end at a round pad's
        centre, so they cross its edge square on."""
        grow = self.clr + self.wmax / 2
        key = -4 if net is None else net
        for layer in layers:
            for k in self.rect(x0, y0, x1, y1, grow):
                self._claim(self.fixed[layer], k, key)
        # no via may touch a pad, of any net: it would draw solder from the joint
        for k in self.rect(x0, y0, x1, y1, self.via_d / 2 + 0.1):
            self.no_via[k] = 1
        if net is not None:
            # tracks end well inside the pad, clear of a rounded corner
            s = min(0.12, (x1 - x0) * 0.3, (y1 - y0) * 0.3)
            if round_pad:
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                inner = list(self.disk(cx, cy, 0.1))
                # over the pad and just outside it, tracks only run on the eight grid lines through its centre
                ci, cj = self.cell(cx, cy)
                for k in self.disk(cx, cy, (x1 - x0) / 2 + 0.25):
                    di, dj = k % self.nx - ci, k // self.nx - cj
                    if di and dj and abs(di) != abs(dj):
                        for layer in layers:
                            self.fixed[layer][k] = BLOCKED
            else:
                inner = list(self.rect(x0 + s, y0 + s, x1 - s, y1 - s))
            if not inner:
                inner = [self.idx(*self.cell((x0 + x1) / 2, (y0 + y1) / 2))]
            for layer in layers:
                for k in inner:
                    self.pad[layer][k] = net
            self.pads.setdefault(net, []).append([(layer, k) for layer in layers for k in inner])

    # ---- occupancy
    # A net's marks are the cells where another net's track centre would be too close to its copper. Other nets
    # may still use them during negotiation, at a cost that rises each round, until no net needs to.
    def marks(self, net, segs, vias):
        out = [set() for _ in self.L]
        for layer, pts, w in segs:
            r = w / 2 + self.clr + self.wmin / 2 + self.p * 0.3
            out[layer].update(self.dilate(self.line_cells(pts), r))
        rv = self.via_d / 2 + self.clr + self.wmin / 2 + self.p * 0.3
        for x, y in vias:
            for layer in self.L:
                out[layer].update(self.disk(x, y, rv))
        return out

    def centre(self, segs, vias):
        """Cells a route's track centres and via copper occupy."""
        out = [set() for _ in self.L]
        for layer, pts, w in segs:
            out[layer].update(self.dilate(self.line_cells(pts), max(0.0, (w - self.wmin) / 2)))
        r = self.via_d / 2 - self.wmax / 2
        for x, y in vias:
            for layer in self.L:
                out[layer].update(self.disk(x, y, r))
        return out

    def _add(self, net, sign):
        for layer in self.L:
            o = self.occ[layer]
            for k in self.mk[net][layer]:
                o[k] += sign

    def via_ok(self, i, j, net):
        """True if a via of net fits at cell (i, j) clear of pads and keepouts."""
        k = self.idx(i, j)
        if self.no_via[k]:
            return False
        r = self.via_d / 2 - self.wmax / 2 + self.p    # a cell more, for the grid
        x, y = i * self.p, j * self.p
        for layer in self.L:
            f = self.fixed[layer]
            for kk in self.disk(x, y, r):
                v = f[kk]
                if not (v == FREE or v == net):
                    return False
        return True

    # ---- search
    def search(self, net, sources, targets, margin=4.0, own_vias=(), width=None):
        """A* from source states to any target state. States are (layer, k). Returns the path of states or None.
        Pads of other nets and keepouts block. Other nets' tracks cost more the more rounds they've been in the
        way."""
        nx = self.nx
        tl = [(layer, k % nx, k // nx) for layer, k in targets]
        tset = set(targets)
        tx = [t[1] for t in tl]
        ty = [t[2] for t in tl]
        bx0, bx1, by0, by1 = min(tx), max(tx), min(ty), max(ty)
        # the search stays inside the sources' and targets' bounding box grown by margin
        sx = [k % nx for _, k in sources] + tx
        sy = [k // nx for _, k in sources] + ty
        m = int(margin / self.p)
        wx0, wx1, wy0, wy1 = min(sx) - m, max(sx) + m, min(sy) - m, max(sy) + m

        def h(i, j):
            dx = max(bx0 - i, 0, i - bx1)
            dy = max(by0 - j, 0, j - by1)
            return (max(dx, dy) + 0.414 * min(dx, dy)) * 0.999

        owner = self.fixed
        pad = self.pad
        cost = self.cost
        occ, hist, pf = self.occ, self.hist, self.pres
        voff = self.offsets(self.via_d / 2 - self.wmax / 2 + self.p)
        sep2 = ((self.via_d + 0.3) / self.p) ** 2     # the net's own vias, apart or in the same cell

        def free1(layer, k):
            v = owner[layer][k]
            return v == FREE or v == net or pad[layer][k] == net

        # a track wider than the pads' and marks' allowance needs the cells around its centre clear too
        width = width or self.width[net]
        woff = self.offsets((width - self.wmin) / 2 + self.p * 0.3) if width > self.wmax else None
        ooff = self.offsets((width - self.wmin) / 2) if width > self.wmin else None

        def free(layer, k):
            if not free1(layer, k):
                return False
            if woff is None or pad[layer][k] == net:
                return True
            i0, j0 = k % nx, k // nx
            for di, dj in woff:
                ii, jj = i0 + di, j0 + dj
                if 0 <= ii < nx and 0 <= jj < self.ny and not free1(layer, jj * nx + ii):
                    return False
            return True

        def occupied(layer, k):
            if ooff is None:
                return occ[layer][k]
            i0, j0 = k % nx, k // nx
            m = 0
            for di, dj in ooff:
                ii, jj = i0 + di, j0 + dj
                if 0 <= ii < nx and 0 <= jj < self.ny:
                    m = max(m, occ[layer][jj * nx + ii])
            return m

        g = {}
        came = {}
        heading = {}
        heap = []
        for s in sources:
            g[s] = 0.0
            heading[s] = 8
            i, j = s[1] % nx, s[1] // nx
            heapq.heappush(heap, (h(i, j), 0.0, s))
        while heap:
            f, gc, s = heapq.heappop(heap)
            if gc > g.get(s, 1e18):
                continue
            layer, k = s
            d = heading[s]
            if s in tset:
                path = [s]
                while path[-1] in came:
                    path.append(came[path[-1]])
                return list(reversed(path))
            i, j = k % nx, k // nx
            for nd, (di, dj) in enumerate(DIRS):
                ii, jj = i + di, j + dj
                if not (wx0 <= ii <= wx1 and wy0 <= jj <= wy1 and 0 <= ii < nx and 0 <= jj < self.ny):
                    continue
                kk = jj * nx + ii
                if not free(layer, kk):
                    continue
                if di and dj and not (free(layer, j * nx + ii) or free(layer, jj * nx + i)):
                    continue
                step = (1.414 if di and dj else 1.0) * cost[layer][kk] * (1 + hist[layer][kk]) * \
                    (1 + pf * occupied(layer, kk))
                if d != 8 and d != nd:
                    step += self.turn_cost
                t = (layer, kk)
                ng = gc + step
                if ng < g.get(t, 1e18):
                    g[t] = ng
                    came[t] = s
                    heading[t] = nd
                    heapq.heappush(heap, (ng + h(ii, jj), ng, t))
            # change layer, with a via unless the cell is in a through-hole pad of the net
            tht = all(pad[l][k] == net for l in self.L)
            if tht or (self.via_ok(i, j, net) and
                       all(k == ov or (i - ov % nx) ** 2 + (j - ov // nx) ** 2 >= sep2 for ov in own_vias)):
                if tht:
                    vc = 0.0
                else:
                    # the via's copper, on every layer, against other nets' marks
                    used = 0
                    for di, dj in voff:
                        ii, jj = i + di, j + dj
                        if 0 <= ii < nx and 0 <= jj < self.ny:
                            kk = jj * nx + ii
                            used = max(used, max(occ[l][kk] for l in self.L))
                    vc = self.via_cost * (1 + pf * 4 * used + hist[0][k])
                for ol in self.L:
                    if ol == layer:
                        continue
                    t = (ol, k)
                    ng = gc + vc
                    if ng < g.get(t, 1e18):
                        g[t] = ng
                        came[t] = s
                        heading[t] = 8
                        heapq.heappush(heap, (ng + h(i, j), ng, t))
        return None

    # ---- nets
    def route_net(self, net):
        """Route all of a net's pads. Returns (segments, vias), or None when a pad can't be reached. A net with a
        trunk joins the trunk's pads first, at the trunk's width, and then the rest at the net's width."""
        pads = self.pads[net]
        trunk, tw = self.trunk.get(net, ([], None))
        groups = [([pads[i] for i in trunk[1:]], tw), ([p for i, p in enumerate(pads) if i not in trunk],
                                                        self.width[net])]
        start = pads[trunk[0]] if trunk else pads[0]
        if not trunk:
            groups = [(pads[1:], self.width[net])]
        tree = set(start)
        paths = []
        for left, w in groups:
            left = [c for c in left if not (set(c) & tree)]
            while left:
                targets = set()
                for cells in left:
                    targets.update(cells)
                own = [a[1] for p, _ in paths for a, b in zip(p, p[1:]) if a[0] != b[0]]
                path = self.search(net, sorted(tree), sorted(targets), own_vias=own, width=w)
                if path is None:
                    return None
                paths.append((path, w))
                tree.update(path)
                end = path[-1]
                left = [c for c in left if end not in set(c)]
                for c in pads:
                    if end in set(c):
                        tree.update(c)
        return self.geometry(paths)

    def geometry(self, paths):
        segs, vias = [], []
        for path, w in paths:
            # several layer changes in one cell are one via, and none if the path comes back to the same layer
            clean = [path[0]]
            for s in path[1:]:
                if s[1] == clean[-1][1] and len(clean) > 1 and clean[-2][1] == s[1]:
                    clean[-1] = s
                else:
                    clean.append(s)
            path = [s for i, s in enumerate(clean)
                    if not (0 < i < len(clean) - 1 and clean[i - 1] == clean[i + 1])]
            run = [path[0]]
            for a, b in zip(path, path[1:]):
                if a[0] != b[0]:
                    segs.append((run[0][0], self.simplify(run), w))
                    k = a[1]
                    if not (self.pad[0][k] >= 0 and all(self.pad[l][k] == self.pad[0][k] for l in self.L)):
                        vias.append(((k % self.nx) * self.p, (k // self.nx) * self.p))
                    run = [b]
                else:
                    run.append(b)
            segs.append((run[0][0], self.simplify(run), w))
        segs, vias = [s for s in segs if len(s[1]) > 1], sorted(set(vias))
        return self.split(segs, vias), vias

    def split(self, segs, vias):
        """Add a corner wherever another track of the net, or a via, meets a track part way along a segment."""
        ends = [set(vias) for _ in self.L]
        for layer, pts, w in segs:
            ends[layer].update((pts[0], pts[-1]))
        out = []
        for layer, pts, w in segs:
            new = [pts[0]]
            for a, b in zip(pts, pts[1:]):
                dx, dy = b[0] - a[0], b[1] - a[1]
                n = dx * dx + dy * dy
                if n < 1e-12:
                    continue
                inside = []
                for p in ends[layer]:
                    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / n
                    if 1e-6 < t < 1 - 1e-6 and abs((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) < 1e-6:
                        inside.append((t, p))
                new += [p for _, p in sorted(inside)] + [b]
            out.append((layer, new, w))
        return out

    def simplify(self, run):
        pts = [((k % self.nx) * self.p, (k // self.nx) * self.p) for _, k in run]
        out = [pts[0]]
        for a, b, c in zip(pts, pts[1:], pts[2:]):
            if abs((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])) > 1e-9:
                out.append(b)
        out.append(pts[-1])
        return [(round(x, 4), round(y, 4)) for x, y in out]

    def stitch(self, net, cells, reach=1.6):
        """A via for net as near as it fits to a top-layer pad, joined to it by a track clear of every route.
        Returns (segments, vias) or None."""
        nx = self.nx
        start = [k for layer, k in cells if layer == 0]
        seen = {k: None for k in start}
        frontier = list(start)
        limit = int(reach / self.p)
        i0, j0 = start[0] % nx, start[0] // nx
        rv = self.via_d / 2 + self.clr + self.wmax / 2
        gv = getattr(self, 'gvias', set())
        while frontier:
            nxt = []
            for k in frontier:
                i, j = k % nx, k // nx
                if k in gv and seen[k] is not None:
                    # an earlier via of the net: join it
                    path = [k]
                    while seen[path[-1]] is not None:
                        path.append(seen[path[-1]])
                    return [(0, self.simplify([(0, c) for c in reversed(path)]), self.width[net])], []
                if self.via_ok(i, j, net) and all(self.occ[l][kk] == 0 for l in self.L
                                                   for kk in self.disk(i * self.p, j * self.p, self.via_d / 2)):
                    path = [k]
                    while seen[path[-1]] is not None:
                        path.append(seen[path[-1]])
                    pts = [self.cell_xy(c) for c in reversed(path)]
                    segs = [(0, self.simplify([(0, c) for c in reversed(path)]), self.width[net])] \
                        if len(path) > 1 else []
                    return segs, [self.cell_xy(k)]
                for di, dj in DIRS[:4]:
                    ii, jj = i + di, j + dj
                    if not (0 <= ii < nx and 0 <= jj < self.ny) or abs(ii - i0) > limit or abs(jj - j0) > limit:
                        continue
                    kk = jj * nx + ii
                    if kk in seen:
                        continue
                    v = self.fixed[0][kk]
                    if not (v == FREE or v == net or self.pad[0][kk] == net) or self.occ[0][kk]:
                        continue
                    seen[kk] = k
                    nxt.append(kk)
            frontier = nxt
        return None

    def hand(self, net, segs, vias, ends):
        """Copper of net drawn by hand, held fixed so the other routes go round it. The pads it touches count as one,
        and the net is routed on from ends, [(layer, x, y)]."""
        for layer, m in enumerate(self.marks(net, segs, vias)):
            for k in m:
                self._claim(self.fixed[layer], k, net)
        for x, y in vias:
            for k in self.disk(x, y, self.via_d + 0.05):
                self.no_via[k] = 1
        c = self.centre(segs, vias)
        rest = [p for p in self.pads[net] if not any(k in c[layer] for layer, k in p)]
        group = [(layer, self.idx(*self.cell(x, y))) for layer, x, y in ends]
        for layer, k in group:
            self.pad[layer][k] = net
        self.pads[net] = [group] + rest

    def prestitch(self, net, width):
        """Before routing: a via beside each top-only pad of net, joined to it, held as fixed copper so the
        routes go round them. Returns [(segments, vias)]."""
        n = len(self.fixed[0])
        self.occ = [array('h', [0]) * n for _ in self.L]
        self.width[net] = width
        self.gvias = set()
        out = []
        for cells in self.pads.get(net, []):
            if any(layer != 0 for layer, _ in cells):
                continue
            r = self.stitch(net, cells)
            if r is None:
                continue
            for layer, m in enumerate(self.marks(net, *r)):
                for k in m:
                    self._claim(self.fixed[layer], k, net)
            for x, y in r[1]:
                # vias of the net keep their holes apart
                for k in self.disk(x, y, self.via_d + 0.05):
                    self.no_via[k] = 1
                self.gvias.add(self.idx(*self.cell(x, y)))
            out.append(r)
        return out

    def via_spots(self, net, area, pitch):
        """Grid points at least pitch apart inside area (x0, y0, x1, y1) where a via of net fits clear of every pad,
        keepout and routed track on every layer."""
        x0, y0, x1, y1 = area
        r = self.via_d / 2 - self.wmax / 2 + self.p
        out = []
        step = max(1, int(round(pitch / 4 / self.p)))
        for j in range(int(y0 / self.p), int(y1 / self.p) + 1, step):
            for i in range(int(x0 / self.p), int(x1 / self.p) + 1, step):
                if not self.via_ok(i, j, net):
                    continue
                x, y = self.cell_xy(self.idx(i, j))
                if any((x - a) ** 2 + (y - b) ** 2 < pitch ** 2 for a, b in out):
                    continue
                if any(self.occ[l][k] for l in self.L for k in self.disk(x, y, r)):
                    continue
                out.append((x, y))
        return out

    def run(self, order, widths, rounds=30):
        """Route nets in order, then reroute all of them each round until no net's track centre is inside another
        net's marks. Returns the nets that are unrouted or still too close to another net."""
        self.width.update(widths)
        n = len(self.fixed[0])
        self.occ = [array('h', [0]) * n for _ in self.L]
        self.hist = [array('f', [0.0]) * n for _ in self.L]
        self.mk = {}
        self.pres = 0.5
        self.last_round = rounds - 1
        bad = set(order)
        todo = list(order)
        for rnd in range(rounds):
            for net in todo:
                if net in self.mk:
                    self._add(net, -1)
                    del self.mk[net]
                    del self.routes[net]
                r = self.route_net(net)
                if r is None:
                    continue
                self.routes[net] = r
                self.mk[net] = self.marks(net, *r)
                self._add(net, 1)
            # a net is in conflict if its centre cells are in another net's marks
            bad = set(n2 for n2 in order if n2 not in self.routes)
            over = [set() for _ in self.L]
            for net, r in self.routes.items():
                c = self.centre(*r)
                for layer in self.L:
                    own = self.mk[net][layer]
                    for k in c[layer]:
                        if self.occ[layer][k] - (1 if k in own else 0) > 0:
                            over[layer].add(k)
                            bad.add(net)
            if self.log:
                self.log('ROUND %d: %d nets in conflict, %d cells' % (rnd, len(bad), sum(len(o) for o in over)))
                if rnd == self.last_round:
                    for layer in self.L:
                        for k in over[layer]:
                            who = [self.names[n2] if self.names and isinstance(n2, int) else n2
                                   for n2, m in self.mk.items() if k in m[layer]]
                            self.log('  conflict layer %d at (%.2f, %.2f): marks of %s' % ((layer,) + self.cell_xy(k) + (who,)))
            if not bad:
                break
            for layer in self.L:
                for k in over[layer]:
                    self.hist[layer][k] += 1.0
            # next round, reroute the nets in conflict and the nets whose marks they ran into
            touch = set(n2 for n2, m in self.mk.items() if any(k in m[layer] for layer in self.L for k in over[layer]))
            todo = [n2 for n2 in order if n2 in bad or n2 in touch]
            self.pres *= 1.3
        return [n2 for n2 in order if n2 in bad]
