"""finish.py -- the last few connections, by maze rather than by freerouting.

After the fan-out and the router's rounds a handful of connections are left,
and they are all the same shape: two escape vias on either side of the CPU's
via field, or a pad on the back with nothing on it at all. Freerouting will
not make them. Its own earlier wires box them in; told it may move those
wires (route.py's loose rounds) it reports "board state has not changed" and
stops, because every route it can see costs more than the one it has.

So they are made here, by a maze router over the board's actual free space:
a 0.05 mm grid per routable layer, every foreign shape rasterised and grown
by its clearance, A* between the two pieces of the net with a via as a move
between layers, and the result simplified back into as few 45 degree
segments as it takes -- each one checked against the exact clearance test in
fanout.py, not against the grid that proposed it.

It is a small router and it is allowed to be: it runs on what is left after
everything else, it knows the whole board rather than a net at a time, and
what it emits is locked, like the rest of the tools' copper.
"""
import heapq
import math
import sys

import numpy as np
import pcbnew
from PIL import Image, ImageDraw
from scipy import ndimage

import fanout
from fanout import MM, V, mm, CLR, H2H, VIA_D, VIA_K, WIDE_CLR, WIDE_NETS

PX = 0.05                       # grid pitch, mm
HOLE_CLR = 0.25                 # min_hole_clearance: copper to any drill
EDGE_CLR = 0.30                 # min_copper_edge_clearance
MARGIN = 0.03                   # mm added to every clearance on the grid:
                                # a rasterised boundary is only good to about
                                # half a cell, and the exact check that has
                                # the last word rejected a step 0.7 um short
LAYERS = (pcbnew.F_Cu, pcbnew.In3_Cu, pcbnew.B_Cu)
VIA_COST = 24.0                 # in cells: a via is worth 1.2 mm of track
ROOM = 0.55                     # mm: room enough that a route costs nothing extra
CROWD = 0.0                     # what a route pays for taking the last of it.
                                # Measured at 2.0: nineteen connections left
                                # rather than twelve. Keeping to the open
                                # board makes each route longer, and length
                                # costs more here than crowding does.
STEP = 1.0
DIAG = math.sqrt(2.0)
NEIGHBOURS = [(-1, 0, STEP), (1, 0, STEP), (0, -1, STEP), (0, 1, STEP),
              (-1, -1, DIAG), (-1, 1, DIAG), (1, -1, DIAG), (1, 1, DIAG)]

# ------------------------------------------------------------ the pieces ----
def pieces(board, code):
    """The net's copper, grouped into what is actually joined to what.

    KiCad's own connectivity would answer this, but it answers it for the
    whole board at once and it is the thing being repaired; this walks the
    geometry directly -- items that touch, plus anything sitting on one of
    the net's filled zones."""
    items = [t for t in board.GetTracks() if t.GetNetCode() == code]
    items += [p for f in board.GetFootprints() for p in f.Pads()
              if p.GetNetCode() == code]
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def join(i, j):
        parent[find(i)] = find(j)

    def touch(a, b):
        ta, tb = a.Type(), b.Type()
        if ta == pcbnew.PCB_TRACE_T and tb == pcbnew.PCB_TRACE_T:
            if a.GetLayer() != b.GetLayer():
                return False
            return any(abs(p.x - q.x) <= 1000 and abs(p.y - q.y) <= 1000
                       for p in (a.GetStart(), a.GetEnd())
                       for q in (b.GetStart(), b.GetEnd()))
        if ta == pcbnew.PCB_TRACE_T or tb == pcbnew.PCB_TRACE_T:
            tr, other = (a, b) if ta == pcbnew.PCB_TRACE_T else (b, a)
            if other.Type() == pcbnew.PCB_PAD_T and not other.IsOnLayer(tr.GetLayer()):
                return False
            return any(other.HitTest(p) for p in (tr.GetStart(), tr.GetEnd()))
        return a.HitTest(b.GetPosition()) or b.HitTest(a.GetPosition())

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if touch(items[i], items[j]):
                join(i, j)
    for z in board.Zones():
        if z.GetNetCode() != code or z.GetIsRuleArea():
            continue
        for l in z.GetLayerSet().CuStack():
            poly = z.GetFilledPolysList(l)
            if not poly.OutlineCount():
                continue
            on = [i for i, it in enumerate(items)
                  if (it.Type() == pcbnew.PCB_VIA_T and poly.Contains(it.GetPosition()))
                  or (it.Type() == pcbnew.PCB_PAD_T and it.IsOnLayer(l)
                      and poly.Contains(it.GetPosition()))
                  or (it.Type() == pcbnew.PCB_TRACE_T and it.GetLayer() == l
                      and (poly.Contains(it.GetStart()) or poly.Contains(it.GetEnd())))]
            for i in on[1:]:
                join(on[0], i)
    out = {}
    for i, it in enumerate(items):
        out.setdefault(find(i), []).append(it)
    return list(out.values())

def anchors(item):
    """Where a track may join this item, and on which layers: a centre, not
    an edge, so the connection is unambiguous to KiCad and to freerouting."""
    if item.Type() == pcbnew.PCB_VIA_T:
        return [(item.GetPosition(), None)]
    if item.Type() == pcbnew.PCB_PAD_T:
        if item.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
            return [(item.GetPosition(), None)]
        l = pcbnew.F_Cu if item.IsOnLayer(pcbnew.F_Cu) else pcbnew.B_Cu
        return [(item.GetPosition(), l)]
    return [(item.GetStart(), item.GetLayer()), (item.GetEnd(), item.GetLayer())]

# --------------------------------------------------------------- the grid ----
class Grid:
    """The board's free space for one net, one bitmap per routable layer.

    Foreign copper is drawn as it is and then grown by the clearance it
    wants -- 0.15 mm, or 0.4 for the 60 V nets -- by a distance transform,
    which is exact to the grid and costs one pass per layer rather than one
    test per candidate."""

    def __init__(self, board, code, width=0.15):
        bb = board.GetBoardEdgesBoundingBox()
        self.px = int(round(PX * MM))
        self.x0, self.y0 = bb.GetX(), bb.GetY()
        self.w = bb.GetWidth() // self.px + 2
        self.h = bb.GetHeight() // self.px + 2
        self.code = code
        self.board = board
        wide = {board.FindNet(n).GetNetCode() for n in WIDE_NETS if board.FindNet(n)}
        near = {}                       # layer -> image, normal clearance
        far = {}                        # layer -> image, the 60 V clearance
        for l in LAYERS:
            near[l] = Image.new("1", (self.w, self.h), 0)
            far[l] = Image.new("1", (self.w, self.h), 0)
        draw = {l: (ImageDraw.Draw(near[l]), ImageDraw.Draw(far[l])) for l in LAYERS}

        def poly_of(item, layer):
            ps = pcbnew.SHAPE_POLY_SET()
            item.TransformShapeToPolygon(ps, layer, 0, 5000, pcbnew.ERROR_OUTSIDE)
            return ps

        def paint(ps, layers, ocode):
            for i in range(ps.OutlineCount()):
                ch = ps.Outline(i)
                pts = [self.px_of(ch.CPoint(k)) for k in range(ch.PointCount())]
                if len(pts) < 3:
                    continue
                for l in layers:
                    draw[l][1 if ocode in wide else 0].polygon(pts, fill=1)

        for f in board.GetFootprints():
            for p in f.Pads():
                if p.GetNetCode() == code:
                    continue
                ls = [l for l in p.GetLayerSet().CuStack() if l in LAYERS]
                if not ls:
                    continue
                paint(poly_of(p, ls[0]), ls, p.GetNetCode())
        for t in board.GetTracks():
            if t.GetNetCode() == code:
                continue
            if t.Type() == pcbnew.PCB_VIA_T:
                paint(poly_of(t, pcbnew.F_Cu), LAYERS, t.GetNetCode())
            elif t.GetLayer() in LAYERS:
                paint(poly_of(t, t.GetLayer()), [t.GetLayer()], t.GetNetCode())
        for z in board.Zones():
            if z.GetNetCode() == code:
                continue
            if z.GetIsRuleArea():
                # a keepout binds every layer it names
                ls = [l for l in z.GetLayerSet().CuStack() if l in LAYERS]
                ps = pcbnew.SHAPE_POLY_SET(z.Outline())
                if ls:
                    paint(ps, ls, 0)
                continue
            if z.GetZoneName().startswith("ground pour"):
                continue                # refills round new copper; not an obstacle
            for l in z.GetLayerSet().CuStack():
                if l not in LAYERS:
                    continue            # a plane refills round a via by itself
                fp = z.GetFilledPolysList(l)
                if fp.OutlineCount():
                    paint(fp, [l], z.GetNetCode())

        # the board edge, from the outline itself rather than from the
        # bounding box -- which is bigger by half the outline's stroke, and
        # 0.05 mm of that is the difference between a legal track and a
        # copper-to-edge violation
        import geometry as G
        cx, cy = (bb.GetX() + bb.GetWidth() / 2), (bb.GetY() + bb.GetHeight() / 2)
        r_out = min(bb.GetWidth(), bb.GetHeight()) / 2
        for dr in board.GetDrawings():
            if dr.GetLayer() == pcbnew.Edge_Cuts and dr.GetShape() == pcbnew.SHAPE_T_CIRCLE:
                r_out = min(r_out, dr.GetRadius())
                cx, cy = dr.GetStart().x, dr.GetStart().y
        r_edge = r_out / MM - EDGE_CLR - width / 2 - MARGIN
        outside = Image.new("1", (self.w, self.h), 1)
        d = ImageDraw.Draw(outside)
        d.ellipse([self.px_of(pcbnew.VECTOR2I(int(cx - r_edge * MM), int(cy - r_edge * MM))),
                   self.px_of(pcbnew.VECTOR2I(int(cx + r_edge * MM), int(cy + r_edge * MM)))],
                  fill=0)
        # every drill on the board keeps min_hole_clearance from copper --
        # and a mounting hole is NPTH, so it has no copper of its own to be
        # caught by a clearance rule. Nothing else was keeping tracks off
        # the four M3.
        drills = Image.new("1", (self.w, self.h), 0)
        dd = ImageDraw.Draw(drills)
        self.drills = []
        for f in board.GetFootprints():
            for pad in f.Pads():
                if pad.GetAttribute() not in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
                    continue
                if pad.GetNetCode() == code:
                    continue
                self.drills.append((pad.GetPosition(),
                                    max(pad.GetDrillSize().x, pad.GetDrillSize().y) / 2))
        for v in board.GetTracks():
            if v.Type() == pcbnew.PCB_VIA_T and v.GetNetCode() != code:
                self.drills.append((v.GetPosition(), v.GetDrill() / 2))
        for c, hr in self.drills:
            r = int(hr + (HOLE_CLR + width / 2 + MARGIN) * MM)
            dd.ellipse([self.px_of(pcbnew.VECTOR2I(c.x - r, c.y - r)),
                        self.px_of(pcbnew.VECTOR2I(c.x + r, c.y + r))], fill=1)
        # ... and no copper under a screw head, or under the heatsink ring
        # where it clamps: R_RING_ID..R_RING_OD inside the three phase
        # sectors, which is the 20 A contact of the spec's thermal section.
        import placement as PL
        heads = Image.new("1", (self.w, self.h), 0)
        dh = ImageDraw.Draw(heads)
        for hx, hy in ((G.MOUNT_X, 0), (-G.MOUNT_X, 0), (0, G.MOUNT_Y), (0, -G.MOUNT_Y)):
            c = pcbnew.VECTOR2I(int(cx + hx * MM), int(cy - hy * MM))
            r = int(G.MOUNT_HEAD / 2 * MM)
            dh.ellipse([self.px_of(pcbnew.VECTOR2I(c.x - r, c.y - r)),
                        self.px_of(pcbnew.VECTOR2I(c.x + r, c.y + r))], fill=1)
        land = Image.new("1", (self.w, self.h), 0)
        dl = ImageDraw.Draw(land)

        def ring(r):
            rr = int(r * MM)
            return [self.px_of(pcbnew.VECTOR2I(int(cx - rr), int(cy - rr))),
                    self.px_of(pcbnew.VECTOR2I(int(cx + rr), int(cy + rr)))]

        for th in G.PHASE_ANG:
            dl.pieslice(ring(PL.R_RING_OD), -th - PL.RING_HALF, -th + PL.RING_HALF, fill=1)
        dl.ellipse(ring(PL.R_RING_ID), fill=0)

        self.free = {}
        self.dist = {}
        keep = width / 2 + CLR + MARGIN
        keep_wide = width / 2 + WIDE_CLR + MARGIN
        for l in LAYERS:
            dn = ndimage.distance_transform_edt(~np.array(near[l]).T) * PX
            dw = ndimage.distance_transform_edt(~np.array(far[l]).T) * PX
            ok = (dn >= keep) & (dw >= keep_wide)
            ok &= ~np.array(outside).T & ~np.array(drills).T
            if l == pcbnew.F_Cu:
                ok &= ~np.array(heads).T & ~np.array(land).T
            self.free[l] = ok
            self.dist[l] = np.minimum(dn, dw)
        # where a via may go: its pad clear on every layer, its hole clear of
        # every other hole
        via_ok = np.ones((self.w, self.h), bool)
        for l in LAYERS:
            dn = ndimage.distance_transform_edt(~np.array(near[l]).T) * PX
            dw = ndimage.distance_transform_edt(~np.array(far[l]).T) * PX
            via_ok &= (dn >= VIA_D / 2 + CLR + MARGIN) & (dw >= VIA_D / 2 + WIDE_CLR + MARGIN)
        vd = Image.new("1", (self.w, self.h), 0)
        dv = ImageDraw.Draw(vd)
        for c, hr in self.drills:
            r = int(hr + (HOLE_CLR + VIA_D / 2 + MARGIN) * MM)
            dv.ellipse([self.px_of(pcbnew.VECTOR2I(c.x - r, c.y - r)),
                        self.px_of(pcbnew.VECTOR2I(c.x + r, c.y + r))], fill=1)
        via_ok &= ~np.array(outside).T & ~np.array(vd).T
        via_ok &= ~np.array(heads).T & ~np.array(land).T
        holes = Image.new("1", (self.w, self.h), 0)
        dhole = ImageDraw.Draw(holes)
        for hx, hy, hr in fanout.Obstacles(board).holes:
            r = int(hr + (H2H + VIA_K / 2 + MARGIN) * MM)
            dhole.ellipse([self.px_of(pcbnew.VECTOR2I(hx - r, hy - r)),
                           self.px_of(pcbnew.VECTOR2I(hx + r, hy + r))], fill=1)
        self.via_ok = via_ok & ~np.array(holes).T

    def on_free(self, layer, a, b):
        """Is every cell the segment passes over free? The A* path is by
        construction; a simplified corner is not, and the exact clearance
        test knows nothing of the board edge or of a bare NPTH drill."""
        ax, ay = self.px_of(pcbnew.VECTOR2I(int(a[0]), int(a[1])))
        bx, by = self.px_of(pcbnew.VECTOR2I(int(b[0]), int(b[1])))
        n = max(abs(bx - ax), abs(by - ay))
        free = self.free[layer]
        for k in range(n + 1):
            x = ax + (bx - ax) * k // max(n, 1)
            y = ay + (by - ay) * k // max(n, 1)
            if not (0 <= x < self.w and 0 <= y < self.h and free[x, y]):
                return False
        return True

    def punch(self, layer, a, b):
        """Take a segment (or a via spot) out of the free space: the exact
        clearance test refused it."""
        img = Image.new("1", (self.w, self.h), 0)
        d = ImageDraw.Draw(img)
        pa = self.px_of(pcbnew.VECTOR2I(int(a[0]), int(a[1])))
        pb = self.px_of(pcbnew.VECTOR2I(int(b[0]), int(b[1])))
        d.line([pa, pb], fill=1, width=3)
        cut = np.array(img).T
        if layer is None:
            self.via_ok &= ~cut
        else:
            self.free[layer] &= ~cut

    def px_of(self, p):
        return ((p.x - self.x0) // self.px, (p.y - self.y0) // self.px)

    def cell(self, p):
        return ((p.x - self.x0) // self.px, (p.y - self.y0) // self.px)

    def point(self, ix, iy):
        return pcbnew.VECTOR2I(int(self.x0 + ix * self.px + self.px // 2),
                               int(self.y0 + iy * self.px + self.px // 2))

# ------------------------------------------------------------------ A* ------
def route(grid, starts, goals, allow_vias=True):
    """A* from any of `starts` to any of `goals`, each (layer, ix, iy).

    Returns [(layer, ix, iy), ...] or None. The heuristic is the straight
    distance to the nearest goal, computed once for the whole board by a
    distance transform, which is admissible because no path is shorter than
    the line."""
    W, H = grid.w, grid.h
    li = {l: k for k, l in enumerate(LAYERS)}
    free = np.stack([grid.free[l] for l in LAYERS])          # (L, W, H)
    # A route through a channel that is only just wide enough spends the
    # channel: the next net that needs it has nowhere else to go. So a step
    # costs more the less room there is around it, and the search prefers
    # the open board -- which is what a person does by eye.
    pen = np.stack([1.0 + CROWD * np.clip((ROOM - grid.dist[l]) / ROOM, 0, 1)
                    for l in LAYERS]).astype(np.float32)
    via_ok = grid.via_ok
    tgt = np.ones((W, H), bool)
    for _, ix, iy in goals:
        if 0 <= ix < W and 0 <= iy < H:
            tgt[ix, iy] = False
    if tgt.all():
        return None
    hf = ndimage.distance_transform_edt(tgt).astype(np.float32)

    goalset = {(li[l], ix, iy) for l, ix, iy in goals}
    INF = np.float32(np.inf)
    g = np.full((len(LAYERS), W, H), INF, np.float32)
    came = np.full((len(LAYERS), W, H), -1, np.int64)
    heap = []
    for l, ix, iy in starts:
        k = li[l]
        if not (0 <= ix < W and 0 <= iy < H):
            continue
        if g[k, ix, iy] == 0:
            continue
        g[k, ix, iy] = 0
        heapq.heappush(heap, (float(hf[ix, iy]), k, ix, iy))
    seen = 0
    while heap:
        f, k, ix, iy = heapq.heappop(heap)
        seen += 1
        if seen > 4_000_000:
            return None
        gc = g[k, ix, iy]
        if f > gc + hf[ix, iy] + 1e-6:
            continue
        if (k, ix, iy) in goalset:
            path = []
            cur = (k, ix, iy)
            while cur is not None:
                path.append((LAYERS[cur[0]], cur[1], cur[2]))
                p = came[cur[0], cur[1], cur[2]]
                cur = None if p < 0 else (int(p // (W * H)), int(p // H % W), int(p % H))
            return path[::-1]
        for dx, dy, cost in NEIGHBOURS:
            jx, jy = ix + dx, iy + dy
            if not (0 <= jx < W and 0 <= jy < H) or not free[k, jx, jy]:
                continue
            if dx and dy and not (free[k, ix, jy] or free[k, jx, iy]):
                continue                        # do not cut a diagonal corner
            ng = gc + cost * pen[k, jx, jy]
            if ng < g[k, jx, jy]:
                g[k, jx, jy] = ng
                came[k, jx, jy] = (k * W + ix) * H + iy
                heapq.heappush(heap, (float(ng + hf[jx, jy]), k, jx, jy))
        if allow_vias and via_ok[ix, iy]:
            for k2 in range(len(LAYERS)):
                if k2 == k or not free[k2, ix, iy]:
                    continue
                ng = gc + VIA_COST
                if ng < g[k2, ix, iy]:
                    g[k2, ix, iy] = ng
                    came[k2, ix, iy] = (k * W + ix) * H + iy
                    heapq.heappush(heap, (float(ng + hf[ix, iy]), k2, ix, iy))
    return None

# ------------------------------------------------------------ simplify ------
def _legal(obs, code, layer, pts, w, grid=None):
    segs = list(zip(pts, pts[1:]))
    if grid is not None and not all(grid.on_free(layer, a, b) for a, b in segs):
        return False
    return fanout.legal(obs, code, layer, segs, w=w)

def _corners(a, b):
    """The 45 degree ways from a to b: straight, and the two L-shapes made of
    one axis-aligned run and one diagonal."""
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    out = [[a, b]] if (dx == 0 or dy == 0 or abs(dx) == abs(dy)) else []
    m = min(abs(dx), abs(dy))
    sx = 1 if dx > 0 else -1
    sy = 1 if dy > 0 else -1
    out.append([a, (ax + sx * m, ay + sy * m), b])          # diagonal first
    out.append([a, (bx - sx * m, by - sy * m), b])          # straight first
    return out

def simplify(obs, code, layer, pts, w, grid=None):
    """As few 45 degree segments as the free space allows: from each point,
    the farthest one it can be joined to by one straight run and one
    diagonal, checked exactly."""
    out = [pts[0]]
    i = 0
    while i < len(pts) - 1:
        best = None
        lo, hi = i + 1, len(pts) - 1
        # farthest reachable, by bisection then a short linear walk back
        while lo <= hi:
            mid = (lo + hi) // 2
            cand = None
            for path in _corners(pts[i], pts[mid]):
                if _legal(obs, code, layer, path, w, grid):
                    cand = path
                    break
            if cand:
                best = (mid, cand)
                lo = mid + 1
            else:
                hi = mid - 1
        if best is None:
            j = i + 1
            best = (j, [pts[i], pts[j]])
        j, path = best
        out.extend(path[1:])
        i = j
    return out

# ---------------------------------------------------------------- finish ----
def join_net(board, code, verbose=False):
    """Make the net one piece, one connection at a time. True if it is."""
    # what the net is already made of, then narrower: a 0.5 mm Power-class
    # run does not get through the CPU's via field, and the connection
    # matters more than the width on a rail that is a plane everywhere else
    have = [t.GetWidth() / MM for t in board.GetTracks()
            if t.GetNetCode() == code and t.Type() == pcbnew.PCB_TRACE_T]
    widths = []
    for w in ((max(have) if have else 0.15), 0.25, 0.15):
        w = min(max(w, 0.15), 0.5)
        if w not in widths:
            widths.append(w)
    for _ in range(12):
        groups = pieces(board, code)
        if len(groups) < 2:
            return True
        a, b = _closest(groups)
        done = False
        for w in widths:
            grid = Grid(board, code, width=w)
            starts = [(l, *grid.cell(p)) for it in a for p, l in anchors(it)
                      for l in (LAYERS if l is None else [l]) if l in LAYERS]
            goals = [(l, *grid.cell(p)) for it in b for p, l in anchors(it)
                     for l in (LAYERS if l is None else [l]) if l in LAYERS]
            if not starts or not goals:
                return False
            # The grid proposes and the exact test disposes: what the exact
            # test rejects is punched out of the grid and the route planned
            # again, so a rasterised boundary half a cell out costs a
            # detour rather than the connection.
            for _ in range(6):
                path = route(grid, starts, goals)
                if path is None:
                    if verbose > 1:
                        print(f"    {board.FindNet(code).GetNetname()}: no path at {w} mm")
                    break
                if emit(board, grid, code, path, w, verbose=verbose):
                    done = True
                    break
            if done:
                break
        if not done:
            return False
    return len(pieces(board, code)) < 2

def _centre(items):
    xs = [p.x for it in items for p, _ in anchors(it)]
    ys = [p.y for it in items for p, _ in anchors(it)]
    return sum(xs) / len(xs), sum(ys) / len(ys)

def _closest(groups):
    """The two pieces with the least between them: joining those first is
    what keeps a four-piece net from being crossed twice."""
    best, pair = None, (groups[0], groups[1])
    for i in range(len(groups)):
        for j in range(i + 1, len(groups)):
            (ax, ay), (bx, by) = _centre(groups[i]), _centre(groups[j])
            d = math.hypot(ax - bx, ay - by)
            if best is None or d < best:
                best, pair = d, (groups[i], groups[j])
    return pair

def _span(board, code):
    groups = pieces(board, code)
    if len(groups) < 2:
        return 0.0
    a, b = _closest(groups)
    (ax, ay), (bx, by) = _centre(a), _centre(b)
    return math.hypot(ax - bx, ay - by)

def _state(board):
    """Every track and via as it stands, copied.

    Not a list of uuids and a list of what was removed: an attempt that
    rips up may itself rip up, and the inner attempt puts its copper back
    as fresh items with fresh uuids. The outer undo then deleted those --
    "everything that was not here before" -- and did not have them in its
    own backup, so a failed pair of attempts quietly cost the board whole
    routed nets. Forty-one connections, on a board whose log said it had
    made four. So the undo is by value, and it is the whole board."""
    return [t.Duplicate() for t in board.GetTracks()]

def _rollback(board, snap):
    """Put the copper back exactly as `_state` found it."""
    for t in list(board.GetTracks()):
        board.Delete(t)
    for t in snap:
        board.Add(t)

def rip_and_join(board, code, verbose=True, depth=0):
    """Move the router's own copper out of the way, make the connection, and
    put the copper it displaced back by making those connections too.

    Only unlocked items are candidates: everything the tools laid is theirs,
    and everything on a net that is one piece already stays that way or the
    attempt is rolled back whole."""
    name = board.FindNet(code).GetNetname()
    groups = pieces(board, code)
    if len(groups) < 2:
        return True
    board.BuildConnectivity()
    was = board.GetConnectivity().GetUnconnectedCount(True)
    groups.sort(key=len, reverse=True)
    xs, ys = [], []
    for it in groups[0] + groups[1]:
        bb = it.GetBoundingBox()
        xs += [bb.GetLeft(), bb.GetRight()]
        ys += [bb.GetTop(), bb.GetBottom()]
    for margin in ((1.5,) if depth else (1.5, 4.0, 9.0)):
        m = int(margin * MM)
        work = pcbnew.BOX2I(V(min(xs) - m, min(ys) - m),
                            V(max(xs) - min(xs) + 2 * m, max(ys) - min(ys) + 2 * m))
        # whole nets, not the segments that happen to fall in the box: half
        # a net left behind is a harder problem than the one being solved,
        # and the re-make then fails on a fragment rather than on the route.
        hurt = {t.GetNetCode() for t in board.GetTracks()
                if not t.IsLocked() and t.GetNetCode() not in (0, code)
                and work.Intersects(t.GetBoundingBox())}
        doomed = [t for t in board.GetTracks()
                  if not t.IsLocked() and t.GetNetCode() in hurt]
        if not doomed:
            continue
        snap = _state(board)
        for t in doomed:
            board.Delete(t)
        ok = join_net(board, code)
        missed = []
        if ok:
            # the longest way round first: a short net can take a detour, a
            # long one across the via field cannot
            for c in sorted(hurt, key=lambda c: -_span(board, c)):
                # a net that will not go back the way it came may move
                # something of its own out of the way in turn, once
                if not (join_net(board, c)
                        or (depth < 2 and rip_and_join(board, c, False, depth + 1))):
                    missed.append(board.FindNet(c).GetNetname())
        # The test is not "did everything go back", it is "is the board
        # better". Displacing a net that was already open costs nothing,
        # and a trade that closes two connections and opens one is still
        # worth taking -- KiCad's own count decides.
        board.BuildConnectivity()
        now = board.GetConnectivity().GetUnconnectedCount(True)
        if verbose:
            print(f"  finish: {name} at {margin} mm -- {len(doomed)} segments out of the "
                  f"way, {len(hurt)} nets to re-make, {len(missed)} not"
                  + (f" ({', '.join(missed[:4])})" if missed else "")
                  + f"; {was} unconnected before, {now} after")
        if ok and now < was:
            return True
        _rollback(board, snap)
    return False

def finish(board, open_nets, verbose=True):
    """Join what is still in pieces, the longest way round first: a short
    net can take a detour and a long one across the via field cannot.
    Returns (made, left)."""
    made, left = [], []
    # A guard, because the undo above is the second attempt at getting the
    # undo right: KiCad's own count of what is unconnected, before and
    # after. The maze is allowed to leave the board no worse than it found
    # it, and if it does the whole pass is thrown away.
    board.BuildConnectivity()
    was = board.GetConnectivity().GetUnconnectedCount(True)
    snap = _state(board)
    order = []
    for name in open_nets:
        net = board.FindNet(name)
        if net is not None:
            order.append((-_span(board, net.GetNetCode()), name))
    for _, name in sorted(order):
        net = board.FindNet(name)
        if net is None:
            continue
        code = net.GetNetCode()
        if join_net(board, code, verbose) or rip_and_join(board, code, verbose):
            made.append(name)
        else:
            left.append(name)
    board.BuildConnectivity()
    now = board.GetConnectivity().GetUnconnectedCount(True)
    if now > was:
        _rollback(board, snap)
        print(f"finish: left {now} unconnected against {was} before -- rolled back, "
              f"the board stands as the router left it")
        return [], list(open_nets)
    if verbose:
        print(f"finish: {len(made)} nets joined, {len(left)} left"
              + ("" if not left else ": " + ", ".join(left))
              + f"; {was} unconnected before, {now} after")
    return made, left

def emit(board, grid, code, path, w, verbose=True):
    """Turn a cell path into locked tracks, and a via wherever it changes
    layer -- unless the net already has one there, which is the usual case:
    the path starts and ends on the escape vias the fan-out placed."""
    obs = fanout.Obstacles(board)
    runs, cur = [], [path[0]]
    for step in path[1:]:
        if step[0] != cur[-1][0]:
            runs.append(cur)
            cur = [step]
        else:
            cur.append(step)
    runs.append(cur)
    # the junctions between runs, and the two far ends, snap onto whatever
    # of this net is already there, so every join is a centre
    joints = []
    for k in range(len(runs) - 1):
        _, ix, iy = runs[k][-1]
        p = grid.point(ix, iy)
        hit = drill_centre(board, code, p)
        joints.append((hit if hit is not None else p, hit is None))
    snap = _state(board)
    laid = []
    for k, run in enumerate(runs):
        layer = run[0][0]
        pts = [(grid.point(ix, iy).x, grid.point(ix, iy).y) for _, ix, iy in run]
        if k == 0:
            hit = nearest_centre(board, code, pcbnew.VECTOR2I(*pts[0]), layer)
            if hit is not None:
                pts[0] = (hit.x, hit.y)
        else:
            pts[0] = (joints[k - 1][0].x, joints[k - 1][0].y)
        if k == len(runs) - 1:
            hit = nearest_centre(board, code, pcbnew.VECTOR2I(*pts[-1]), layer)
            if hit is not None:
                pts[-1] = (hit.x, hit.y)
        else:
            pts[-1] = (joints[k][0].x, joints[k][0].y)
        pts = [p for n, p in enumerate(pts) if n == 0 or p != pts[n - 1]]
        if len(pts) < 2:
            continue
        pts = simplify(obs, code, layer, pts, w, grid)
        for a, b in zip(pts, pts[1:]):
            if not _legal(obs, code, layer, [a, b], w, grid):
                grid.punch(layer, a, b)
                _rollback(board, snap)
                return False
            laid.append(fanout.add_track(board, obs, a, b, layer, code, w=w))
    for p, need in joints:
        if need:
            if not fanout.legal(obs, code, None, [], via=(p.x, p.y)):
                grid.punch(None, (p.x, p.y), (p.x, p.y))
                _rollback(board, snap)
                return False
            fanout.add_via(board, obs, (p.x, p.y), code)
    return bool(laid)

def drill_centre(board, code, p):
    """The centre of the net's via or through-hole pad this point falls in."""
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T and t.GetNetCode() == code and t.HitTest(p):
            return t.GetPosition()
    for f in board.GetFootprints():
        for pad in f.Pads():
            if (pad.GetNetCode() == code
                    and pad.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH)
                    and pad.HitTest(p)):
                return pad.GetPosition()
    return None

def nearest_centre(board, code, p, layer):
    """The centre of the net's via or pad this point falls in, if any."""
    hit = drill_centre(board, code, p)
    if hit is not None:
        return hit
    for f in board.GetFootprints():
        for pad in f.Pads():
            if pad.GetNetCode() == code and pad.IsOnLayer(layer) and pad.HitTest(p):
                return pad.GetPosition()
    return None

def open_nets(path):
    """The nets KiCad's own DRC says are still in pieces."""
    import json
    import re
    import subprocess
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".json") as f:
        subprocess.run(["kicad-cli", "pcb", "drc", "--format", "json",
                        "--severity-all", "-o", f.name, str(path)],
                       capture_output=True, text=True)
        d = json.loads(open(f.name).read())
    out = []
    for it in d.get("unconnected_items", ()):
        m = re.search(r"\[([^\]]*)\]", it["items"][0]["description"])
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out

def run(path, nets=None, verbose=True):
    board = pcbnew.LoadBoard(str(path))
    nets = nets or open_nets(path)
    if not nets:
        if verbose:
            print("finish: nothing left in pieces")
        return [], []
    made, left = finish(board, nets, verbose=verbose)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(str(path), board)
    return made, left

if __name__ == "__main__":
    import os
    made, left = run(sys.argv[1], sys.argv[2:] or None)
    sys.stdout.flush()
    os._exit(1 if left else 0)
