#!/usr/bin/env python3
"""fanout.py — escape vias for the RP2350, its DVDD ring stubs, and plane taps.

    python3 tools/fanout.py hardware/motor_board/servodrive_A.kicad_pcb

Three things a maze router does badly on this board, done first and left in
its way as protected copper:

  - THE QFN. Sixty pads at 0.4 mm pitch, each 0.2 mm wide. A via is 0.5 mm
    and needs 0.15 mm around it, so it cannot sit in the pad and it cannot
    sit at the end of the pad either without taking its neighbours' room.
    Each pin gets a 0.15 mm stub out of its pad and a via at the first
    legal spot -- straight out, angled, or off into the corner beside the
    chip -- searched nearest-first against everything already on the board.
    That builds the staggered rows a fan-out is: the arithmetic says a
    15-pin side needs about four of them, and the search finds that on its
    own.
  - THE DVDD RING. Raspberry Pi's reference design joins the four DVDD pins
    with a ring of 1V1 copper under the chip, inside the pin rows. Those
    four pins get a short stub inward into it instead of a via, and VREG_FB
    gets one outward into the regulator's output pour as well.
  - PLANE TAPS. +3V3 is a plane on In2 -- the CPU wedge, and the whole
    centre disc so the three phase cells can reach it -- and a pad that is
    not over it needs a track to a via that is. Straight in, then a via.

Everything placed here obeys the board's rules by construction: the via pad
clears foreign copper on every layer by the clearance, its hole keeps
0.25 mm from foreign copper and 0.5 mm from every other hole, and the stubs
are checked the same way. What it cannot place, it reports and leaves to
the router.
"""
import argparse
import math
import re
from collections import Counter
from pathlib import Path

import pcbnew

import stitch

MM = 1e6
STUB_W = 0.15          # the board's minimum, and the width the reference uses
VIA_D, VIA_K = 0.46, 0.20   # the smallest via the rules allow: 0.13 mm of ring
CLR = 0.1505           # copper clearance: the 0.15 rule, plus half a micron.
                       # The lane between two vias four pins apart is 0.84 mm
                       # and three stubs with their gaps need 0.75; the margin
                       # is hundredths, so this cannot be generous.
WIDE_NETS = ("VBUS", "SW_A", "SW_B", "SW_C", "PHASE_A", "PHASE_B", "PHASE_C",
             "SNUB_A", "SNUB_B", "SNUB_C",
             "SW_12V", "SW_5V")     # board S's buck switch nodes: the Phase class too
WIDE_CLR = 0.4005      # the Phase class's clearance, to those nets' copper
H2H = 0.505            # hole edge to hole edge: the 0.5 rule and a rounding
HOLE_CU = stitch.HOLE_CU
RING_R = 2.0           # the 1V1 ring is 1.9 .. 2.3 mm from the chip centre

def mm(v): return int(round(v * MM))
def V(x, y): return pcbnew.VECTOR2I(int(x), int(y))

# ------------------------------------------------------------ obstacles ----
class Obstacles:
    """Everything the new copper has to keep away from, with a bounding box
    each so a candidate only pays for the neighbours it could touch."""

    def __init__(self, board):
        self.board = board
        self.copper = []     # (bbox, shape, netcode, layer or None for all, own clearance)
        self.holes = []      # (x, y, drill radius)
        self.zones = []      # obstacles: (bbox, polyset, netcode, layer)
        self.planes = []     # In2 fills, for the containment test only
        # The 60 V nets' class asks 0.4 mm of everything else, and a via or
        # a stub beside a switch-node pad at the board's 0.15 is a
        # clearance violation (a ground tap found that, 0.19 mm from a
        # shunt's SW pad).
        self.wide = {board.FindNet(n).GetNetCode() for n in WIDE_NETS if board.FindNet(n)}
        for f in board.GetFootprints():
            for p in f.Pads():
                self.add_pad(p)
        for t in board.GetTracks():
            self.add_track(t)
        for z in board.Zones():
            if z.GetIsRuleArea() or z.GetZoneName().startswith("ground pour"):
                continue
            for l in z.GetLayerSet().CuStack():
                poly = z.GetFilledPolysList(l)
                if not poly.OutlineCount():
                    continue
                if l in (pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.In4_Cu):
                    # planes refill around a foreign via; not an obstacle
                    self.planes.append((poly.BBox(), poly, z.GetNetCode(), l))
                else:
                    self.zones.append((poly.BBox(), poly, z.GetNetCode(), l))

    def add_pad(self, p):
        code = p.GetNetCode()
        # A footprint or a pad can ask more than its netclass: TI's PowerPAD
        # footprint carries (clearance 0.2), and DRC holds everything to it.
        own = max(p.GetLocalClearance() or 0, p.GetParentFootprint().GetLocalClearance() or 0)
        for l in p.GetLayerSet().CuStack():
            sh = p.GetEffectiveShape(l)
            self.copper.append((sh.BBox(), sh, code, l, own))
        if p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
            d = max(p.GetDrillSize().x, p.GetDrillSize().y)
            self.holes.append((p.GetPosition().x, p.GetPosition().y, d // 2))

    def _add_track(self, t):
        code = t.GetNetCode()
        if t.Type() == pcbnew.PCB_VIA_T:
            sh = t.GetEffectiveShape(pcbnew.F_Cu)
            self.copper.append((sh.BBox(), sh, code, None, 0))
            self.holes.append((t.GetPosition().x, t.GetPosition().y, t.GetDrill() // 2))
        else:
            sh = t.GetEffectiveShape()
            self.copper.append((sh.BBox(), sh, code, t.GetLayer(), 0))

    def near(self, x, y, reach=mm(7.0)):
        """A view of the obstacles within `reach` of a point: the search for
        one pin's via tests thousands of candidates against the same few
        dozen neighbours, so it should not walk the whole board each time."""
        bb = pcbnew.BOX2I(V(x - reach, y - reach), V(2 * reach, 2 * reach))
        sub = Obstacles.__new__(Obstacles)
        sub.board = self.board
        sub.copper = [c for c in self.copper if c[0].Intersects(bb)]
        sub.holes = [h for h in self.holes if abs(h[0] - x) < reach and abs(h[1] - y) < reach]
        sub.zones = [z for z in self.zones if z[0].Intersects(bb)]
        sub.planes = self.planes
        sub.wide = self.wide
        sub.parent = self
        return sub

    def add_track(self, t):
        if hasattr(self, "parent"):
            self.parent.add_track(t)
        self._add_track(t)

    def clear(self, shape, code, layer, clr=mm(CLR), zones=True):
        """Does `shape` on `layer` (None = every layer) keep `clr` from all
        foreign copper? A pour's outline counts unless `zones` is off: its
        fill retreats from a track by itself, so a track that only crosses a
        pour's outline is legal, and one that must (the regulator's neck) is
        checked against the pads alone."""
        wide = max(clr, mm(WIDE_CLR))
        # The 60 V class's 0.4 mm holds whichever side of the pair is in it:
        # a switch-node track laid by the tools (board S's bucks) keeps it
        # from a ground via as much as a ground track keeps it from the node.
        mine = code in self.wide
        bb = shape.BBox(); bb.Inflate(wide + 10)
        for obb, osh, ocode, ol, own in self.copper:
            if ocode == code:
                continue
            if layer is not None and ol is not None and ol != layer:
                continue
            if not bb.Intersects(obb):
                continue
            need = wide if (mine or ocode in self.wide) else clr
            if osh.Collide(shape, max(need, own + 500)):
                return False
        for zbb, poly, zcode, zl in (self.zones if zones else ()):
            if zcode == code or (layer is not None and zl != layer):
                continue
            if not bb.Intersects(zbb):
                continue
            if poly.Collide(shape, wide if (mine or zcode in self.wide) else clr):
                return False
        return True

    def hole_ok(self, x, y, r):
        """Hole-to-hole, and the hole to foreign copper.
        Foreign copper vs the hole is covered by the via pad's own clearance
        on every layer, which reaches further than the drill does."""
        for hx, hy, hr in self.holes:
            if math.hypot(hx - x, hy - y) < hr + r + mm(H2H):
                return False
        return True

    def inside(self, x, y, code, layer):
        """Is the point on a filled zone of `code` on `layer`?"""
        v = V(x, y)
        return any(zcode == code and zl == layer and poly.Contains(v)
                   for _, poly, zcode, zl in self.planes + self.zones)

# ------------------------------------------------------------- placing ----
def seg(a, b, w=STUB_W):
    return pcbnew.SHAPE_SEGMENT(V(*a), V(*b), mm(w))

def circle(c, d=VIA_D):
    return pcbnew.SHAPE_CIRCLE(V(*c), mm(d) // 2)

def legal(obs, code, layer, segs, via=None, w=STUB_W, zones=True):
    for a, b in segs:
        if not obs.clear(seg(a, b, w), code, layer, zones=zones):
            return False
    if via is not None:
        if not obs.clear(circle(via), code, None):
            return False
        if not obs.hole_ok(via[0], via[1], mm(VIA_K) // 2):
            return False
    return True

def add_track(board, obs, a, b, layer, code, w=STUB_W):
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(V(*a)); t.SetEnd(V(*b))
    t.SetWidth(mm(w)); t.SetLayer(layer); t.SetNetCode(code)
    t.SetLocked(True)              # KiCad exports a locked track as (type fix):
    board.Add(t)                   # it is how route.py tells this copper from the router's
    obs.add_track(t)
    return t

def add_via(board, obs, c, code, d=VIA_D, k=VIA_K):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(V(*c))
    v.SetWidth(mm(d)); v.SetDrill(mm(k))
    v.SetViaType(pcbnew.VIATYPE_THROUGH)
    v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    v.SetNetCode(code)
    v.SetLocked(True)
    board.Add(v)
    obs.add_track(v)
    return v

def already(board, pad):
    """Has this pad got a track of its own net starting on it already, on
    its own layer? Then a previous run did it, and this one leaves it
    alone. (HitTest knows nothing of layers: a 3V3 tap on the front, under
    a 3V3 pad on the back at the same angle, counted as that pad's.)"""
    layers = set(pad.GetLayerSet().CuStack())
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_TRACE_T and t.GetNetCode() == pad.GetNetCode() \
                and t.GetLayer() in layers:
            if pad.HitTest(t.GetStart()) or pad.HitTest(t.GetEnd()):
                return True
    return False

def pad_axes(fp, pad):
    """(centre, outward unit, lateral unit, half length) of a QFN pad."""
    c = pad.GetPosition()
    o = fp.GetPosition()
    a = math.radians(pad.GetOrientationDegrees())
    sx, sy = pad.GetSize().x, pad.GetSize().y
    if sx >= sy:
        ux, uy, half = math.cos(a), -math.sin(a), sx / 2      # KiCad: y down
    else:
        ux, uy, half = math.sin(a), math.cos(a), sy / 2
    if ux * (c.x - o.x) + uy * (c.y - o.y) < 0:
        ux, uy = -ux, -uy
    return (c.x, c.y), (ux, uy), (-uy, ux), half

# F.Cu tracks of the reference design (RP-006440), chip frame, mm, KiCad
# y-down: pin 50 (DVDD) to the regulator's output pour, pin 46 (VREG_AVDD)
# to its capacitor and the 33R. Read off the reference board.
REF_TRACKS = {
    "50": [(1.2, -3.44), (1.18, -4.12), (0.93, -4.38), (0.93, -5.12)],
    "46": [(2.8, -3.44), (2.8, -3.6), (3.64, -4.44), (3.64, -4.97),
           (4.2, -5.53), (4.2, -6.39)],
}

ROW0, ROW_PITCH = 0.43, 0.71     # via centre beyond the pad end, per row
ROWS = 4                         # planned rows per side, before the search
LANE = 0.20                      # extra depth for the outermost row.
# At 0.71 mm there is no radial lane anywhere in the field: a 0.15 mm track
# passing between two rows needs 0.23 to clear a via pad, 0.15 of clearance
# and half its own width on each side -- 0.91 mm between row centres. So
# every escaped signal has to leave through the gaps within a row instead,
# and the last few connections on the board are the ones that run out of
# them. Pushing the outermost row alone out by 0.20 buys one lane the whole
# way round the chip for 0.2 mm of depth; a lane between every pair of rows
# would cost 0.6 mm on every side and a rebuild of the wedge to match.

def row_depth(k):
    """How far beyond the pad end row `k`'s via centres sit."""
    return ROW0 + ROW_PITCH * k + (LANE if k >= ROWS - 1 else 0.0)
                                 # is allowed anywhere; a side with room --
                                 # the one facing the encoder keepout, where
                                 # only tracks may go -- gets more

def candidates(end, u, n, d_min=0.45, d_max=3.2, l_max=2.4, step=0.05,
               d_pref=None):
    """Via positions beyond a pad end, each with the stub that reaches it:
    straight, or straight then a bend -- the bend as early as 0.1 mm, which
    is what lets a stub step 0.075 mm sideways to pass the via in front of
    its neighbour's pad. Nearest first, unless a row is preferred, in which
    case that row's depth first and straightest first within it."""
    out = []
    d = d_min
    while d <= d_max + 1e-9:
        l = -l_max
        while l <= l_max + 1e-9:
            if abs(l) <= 0.8 + 0.7 * d:
                cost = (abs(d - d_pref) if d_pref is not None else d) + 0.6 * abs(l)
                out.append((cost, d, l))
            l += step
        d += step
    out.sort()
    for _, d, l in out:
        via = (end[0] + mm(d) * u[0] + mm(l) * n[0],
               end[1] + mm(d) * u[1] + mm(l) * n[1])
        if abs(l) < 1e-6:
            yield via, [(end, via)]
        else:
            for s0 in sorted({0.1, 0.3, max(0.1, d - abs(l))}):
                if s0 >= d:
                    continue
                bend = (end[0] + mm(s0) * u[0], end[1] + mm(s0) * u[1])
                yield via, [(end, bend), (bend, via)]

LATS = tuple(sorted({0.0} | {s * k for k in range(1, 8) for s in (0.03, -0.03)}, key=abs))

def threaded(obs, code, end, u, n, row, d_via, lat_via, lats=LATS):
    """A stub from the pad end to a via at (d_via, lat_via) beyond it, with a
    waypoint at 0.1 mm and one just before and just after every row of vias
    it has to pass, each free to step sideways. The path chosen is the one
    that strays least from the pad's axis, summed over the waypoints -- a
    stub steps aside only where a via forces it to, and comes back. A
    first-found depth-first search put the whole jog wherever it happened
    to fit, which was usually in the next pin's lane.

    Dynamic programming over (waypoint, lateral): a hundred nodes, each
    segment checked once."""
    def pt(d, l):
        return (end[0] + mm(d) * u[0] + mm(l) * n[0], end[1] + mm(d) * u[1] + mm(l) * n[1])
    # A stub at lateral 0 is 0.455 mm from a neighbour's via 0.4 mm to the
    # side only when it is 0.22 mm short of the via's depth or 0.22 mm past
    # it, so the jog has to hold across just that band; between two rows
    # there is a point where it comes back to the axis, and the neighbour
    # that has to jog the OTHER way for the next row does so only after
    # that. Held across a whole row-to-row interval, two such jogs met.
    depths = [0.1]
    for k in range(row):
        dk = row_depth(k)
        for d in (dk - 0.22, dk + 0.22, dk + ROW_PITCH / 2):
            if d - depths[-1] >= 0.08 and d < d_via - 0.1:
                depths.append(d)
    via = pt(d_via, lat_via)
    corner = abs(lat_via) > 0.5
    INF = float("inf")
    # cost[i][j]: best cost reaching waypoint i at lats[j]; back[i][j]: j'
    cost = [[INF] * len(lats) for _ in depths]
    back = [[-1] * len(lats) for _ in depths]
    for i, d in enumerate(depths):
        span = 0.06 if i == 0 else 0.21
        for j, l in enumerate(lats):
            if abs(l) > span + 1e-9:
                continue
            if not corner and i == len(depths) - 1 and abs(l - lat_via) > 0.45:
                continue
            here = pt(d, l)
            if i == 0:
                if obs.clear(seg(end, here), code, pcbnew.F_Cu):
                    cost[0][j] = abs(l)
                continue
            for jp, lp in enumerate(lats):
                c = cost[i - 1][jp]
                step = abs(l) + 0.5 * abs(l - lp)     # stray little, and smoothly
                if c == INF or c + step >= cost[i][j]:
                    continue
                if obs.clear(seg(pt(depths[i - 1], lp), here), code, pcbnew.F_Cu):
                    cost[i][j] = c + step
                    back[i][j] = jp
    last = len(depths) - 1
    best = None
    for j, l in sorted(enumerate(lats), key=lambda jl: cost[last][jl[0]]):
        if cost[last][j] == INF:
            break
        if obs.clear(seg(pt(depths[last], l), via), code, pcbnew.F_Cu):
            best = j
            break
    if best is None:
        return None
    pts = []
    j = best
    for i in range(last, -1, -1):
        pts.append(pt(depths[i], lats[j]))
        j = back[i][j]
    pts.reverse()
    chain = [end] + pts + [via]
    return list(zip(chain, chain[1:]))

# --------------------------------------------------------------- the QFN ----
def qfn(board, obs, ref="U7", verbose=True):
    fp = next(f for f in board.GetFootprints() if f.GetReference() == ref)
    o = fp.GetPosition()
    tht = [p for p in fp.Pads() if p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD]
    # what an earlier run did, decided before this one adds anything: a
    # bridge from pin 54 lands on pin 53, and must not make 53 look done
    done = {p.GetNumber() for p in fp.Pads() if already(board, p)}
    pads = [p for p in fp.Pads() if p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD
            and p.GetNetCode() > 0
            and not any(q.GetNetCode() == p.GetNetCode() and p.HitTest(q.GetPosition())
                        for q in tht)]
    # The ring, and the pins the regulator's own pours already cover.
    ring = {z.GetNetCode() for z in board.Zones() if z.GetZoneName().startswith("1V1 ring")}
    # ... judged by the zone's OUTLINE, not its fill: between two neighbouring
    # pads the fill of a pour is 0.3 mm wide and breaks at their corners, so
    # the fill under VREG_LX's pad is an island. The stub these pins get
    # reaches past that into the body of the pour.
    covered = set()
    for p in pads:
        c = p.GetPosition()
        for z in board.Zones():
            if z.GetNetCode() == p.GetNetCode() and pcbnew.F_Cu in z.GetLayerSet().CuStack() \
                    and not z.GetIsRuleArea() and z.Outline().Contains(c):
                covered.add(p.GetNumber())

    # The rows are planned, not discovered. Along each side the pins that
    # need a via take rows 1, 2, 3, 4, 1, 2, ... in turn: between two row-1
    # vias 1.6 mm apart there is then 1.1 mm of lane, which is what three
    # 0.15 mm stubs with 0.15 mm between them need, and so on up the rows.
    # Found nearest-first, the search put a via on every other pin in the
    # first row and left the ones between with nowhere to go.
    stubs = 0
    made, left = [], []
    ring_pins, done_pins = [], set()
    rows, bridged = {}, {}
    for side in range(4):
        col = []
        for p in pads:
            c, u, n, half = pad_axes(fp, p)
            if round(math.degrees(math.atan2(u[1], u[0])) / 90) % 4 != side:
                continue
            lat = (c[0] - o.x) * n[0] + (c[1] - o.y) * n[1]
            col.append((lat, p))
        col.sort()
        # Two neighbours on one net (53 and 54, both IOVDD) share a via: the
        # second is bridged to the first along the pad ends.
        prev = None
        for lat, p in col:
            if prev is not None and prev.GetNetCode() == p.GetNetCode() \
                    and p.GetNetCode() not in ring and prev.GetNumber() not in bridged:
                bridged[p.GetNumber()] = prev
            prev = p
        # how deep this side may go: as far as the first foreign copper
        # straight out from the side's middle, less a via
        # ... tested at both ends of the side as well as its middle, because
        # what limits a side is usually a part beside one corner
        ends = [pad_axes(fp, col[i][1]) for i in (0, len(col) // 2, -1)]
        depth = ROWS
        for k in range(ROWS, 8):
            d = row_depth(k)
            ok = True
            for c0, u0, n0, h0 in ends:
                v = (c0[0] + u0[0] * (h0 + mm(d)), c0[1] + u0[1] * (h0 + mm(d)))
                if not (obs.clear(circle(v), -1, None) and obs.hole_ok(v[0], v[1], mm(VIA_K) // 2)):
                    ok = False
            if ok:
                depth = k + 1
            else:
                break
        k = 0
        for lat, p in col:
            if p.GetNetCode() in ring or p.GetNumber() in covered \
                    or p.GetNumber() in done or p.GetNumber() in bridged:
                continue
            rows[p.GetNumber()] = k % depth
            k += 1
    order = sorted(pads, key=lambda p: (rows.get(p.GetNumber(), -1), p.GetNumber()))
    for p in order:
        num, code = p.GetNumber(), p.GetNetCode()
        c, u, n, half = pad_axes(fp, p)
        if num in done:
            continue
        # DVDD: a stub inward into the ring (and, for VREG_FB, outward too)
        if code in ring:
            inner = (c[0] - u[0] * (half - mm(0.05)), c[1] - u[1] * (half - mm(0.05)))
            r_in = mm(RING_R)
            to = (o.x + (inner[0] - o.x) * r_in / mm(3.0),
                  o.y + (inner[1] - o.y) * r_in / mm(3.0))
            to = (o.x + u[0] * r_in, o.y + u[1] * r_in)
            # the pad's own lateral offset, kept
            lat = (c[0] - o.x) * n[0] + (c[1] - o.y) * n[1]
            to = (to[0] + lat * n[0], to[1] + lat * n[1])
            if legal(obs, code, pcbnew.F_Cu, [(inner, to)]):
                add_track(board, obs, c, to, pcbnew.F_Cu, code)
                stubs += 1
            outer = (c[0] + u[0] * (half - mm(0.05)), c[1] + u[1] * (half - mm(0.05)))
            for L in (2.0, 1.8, 1.6, 1.45):
                to = (outer[0] + u[0] * mm(L), outer[1] + u[1] * mm(L))
                if obs.inside(to[0], to[1], code, pcbnew.F_Cu) and \
                        legal(obs, code, pcbnew.F_Cu, [(outer, to)]):
                    add_track(board, obs, c, to, pcbnew.F_Cu, code)
                    stubs += 1
                    break
            continue
        end = (c[0] + u[0] * (half - mm(0.05)), c[1] + u[1] * (half - mm(0.05)))
        if num in covered:
            # a pin one of the regulator's pours reaches: a stub out along its
            # axis into the pour, because the pour's own fill between two
            # neighbouring pads is 0.3 mm wide and breaks at their corners.
            # VREG_LX gets the whole way to the inductor as a track, 0.2 mm,
            # since its pour is that neck for most of its length.
            for L in (1.0, 0.8, 0.6):
                to = (end[0] + u[0] * mm(L), end[1] + u[1] * mm(L))
                if legal(obs, code, pcbnew.F_Cu, [(end, to)], zones=False):
                    add_track(board, obs, c, to, pcbnew.F_Cu, code)
                    stubs += 1
                    break
            tgt = next((q for f2 in board.GetFootprints() for q in f2.Pads()
                        if q.GetNetCode() == code and f2.GetReference() != ref), None)
            if tgt is not None:
                # 0.15 wide, like the reference's pour, because it passes
                # between the VIN capacitor's pads; and checked against pads
                # and tracks only -- the neighbouring pours' fill gives way.
                tc = (tgt.GetPosition().x, tgt.GetPosition().y)
                for reach in (2.4, 2.0, 2.8, 1.6):
                    far = (end[0] + u[0] * mm(reach), end[1] + u[1] * mm(reach))
                    if legal(obs, code, pcbnew.F_Cu, [(end, far), (far, tc)], zones=False):
                        add_track(board, obs, c, far, pcbnew.F_Cu, code)
                        add_track(board, obs, far, tc, pcbnew.F_Cu, code)
                        stubs += 1
                        break
            continue
        if num in bridged:
            q = bridged[num]
            qc, qu, qn, qhalf = pad_axes(fp, q)
            b = (qc[0] + qu[0] * (qhalf - mm(0.05)), qc[1] + qu[1] * (qhalf - mm(0.05)))
            if legal(obs, code, pcbnew.F_Cu, [(end, b)]):
                add_track(board, obs, c, qc, pcbnew.F_Cu, code)
                stubs += 1
                continue
        net = p.GetNet()
        plane = obs.plane_layer(code) if hasattr(obs, "plane_layer") else None
        got = None
        local = obs.near(end[0], end[1])
        row = rows.get(num, 0)
        d_pref = row_depth(row)
        # Its own row first: the via as near its axis as it can be, and the
        # stub threaded through the rows in front of it. Then anywhere the
        # simple one-bend search can reach, nearest first.
        # The via stays on its pin's axis, to 0.05 mm: the lane between two
        # same-row vias has 0.09 mm to spare for the stubs that pass through
        # it, and a via that drifts sideways takes that from its neighbour.
        # The STUB may wander, and comes back to the axis at the via.
        for dd in (0.0, 0.05, 0.1, -0.05, 0.15, 0.2):
            for lv in (0.0, 0.025, -0.025, 0.05, -0.05):
                d_via = d_pref + dd
                via = (end[0] + mm(d_via) * u[0] + mm(lv) * n[0],
                       end[1] + mm(d_via) * u[1] + mm(lv) * n[1])
                if plane is not None and not local.inside(via[0], via[1], code, plane):
                    continue
                if not (local.clear(circle(via), code, None)
                        and local.hole_ok(via[0], via[1], mm(VIA_K) // 2)):
                    continue
                segs = threaded(local, code, end, u, n, row, d_via, lv)
                if segs is not None:
                    got = (via, segs)
                    break
            if got:
                break
        if got is None:
            # No room in its row. Thread out past the last row and sweep to
            # a via off to the side, in the corner beside the chip where the
            # neighbouring side's rows do not reach.
            # thread past every row on this side, then sweep sideways beyond
            # the last one: the sweep has to clear the last row's vias.
            side_rows = [v for k, v in rows.items()
                         if round(math.degrees(math.atan2(*reversed(pad_axes(fp, next(q for q in pads if q.GetNumber() == k))[1]))) / 90) % 4
                         == round(math.degrees(math.atan2(u[1], u[0])) / 90) % 4]
            depth = (max(side_rows) + 1) if side_rows else ROWS
            beyond = row_depth(depth - 1) + ROW_PITCH / 2
            for r in (depth,):
                for lv in [x / 10 for x in range(-32, 33)]:
                    if abs(lv) < 0.6:
                        continue
                    for dd in (0.1, 0.2, 0.3, 0.45, 0.6):
                        d_via = beyond + dd
                        via = (end[0] + mm(d_via) * u[0] + mm(lv) * n[0],
                               end[1] + mm(d_via) * u[1] + mm(lv) * n[1])
                        if plane is not None and not local.inside(via[0], via[1], code, plane):
                            continue
                        if not (local.clear(circle(via), code, None)
                                and local.hole_ok(via[0], via[1], mm(VIA_K) // 2)):
                            continue
                        segs = threaded(local, code, end, u, n, r, d_via, lv)
                        if segs is not None:
                            got = (via, segs)
                            break
                    if got:
                        break
                if got:
                    break
        if got is None:
            for via, segs in candidates(end, u, n, d_min=0.43, d_max=3.6, l_max=2.8):
                if plane is not None and not local.inside(via[0], via[1], code, plane):
                    continue
                if legal(local, code, pcbnew.F_Cu, segs, via):
                    got = (via, segs)
                    break
        if got is None:
            left.append((num, net.GetNetname()))
            continue
        via, segs = got
        # Freerouting connects a trace to a pad only if the trace ends at
        # the pad's CENTRE (Trace.get_normal_contacts: DrillItem.get_center()
        # .equals(point)); a stub that begins 0.05 mm inside the pad's end is
        # a separate island to it, which it then tries to route to and
        # cannot. So every stub begins at the centre and runs the length of
        # its pad.
        for a, b in [(c, end)] + list(segs):
            add_track(board, obs, a, b, pcbnew.F_Cu, code)
        add_via(board, obs, via, code)
        made.append((num, net.GetNetname(),
                     math.hypot(via[0] - end[0], via[1] - end[1]) / MM))
    (x0, y0), a0 = (o.x, o.y), math.radians(fp.GetOrientationDegrees())
    def chip(dx, dy):
        return (x0 + mm(dx * math.cos(a0) + dy * math.sin(a0)),
                y0 + mm(-dx * math.sin(a0) + dy * math.cos(a0)))
    # Two F.Cu tracks copied from the reference design (RP-006440), in the
    # chip's own frame: DVDD pin 50 out past the VIN capacitor into the
    # regulator's output pour -- which is how the 1V1 ring under the chip
    # meets the 1V1 the regulator makes -- and VREG_AVDD pin 46 out to its
    # 4u7 and the 33R. Each begins at its pin's centre.
    ref_tracks = 0
    for num, pts in REF_TRACKS.items():
        pad = next((q for q in fp.Pads() if q.GetNumber() == num), None)
        if pad is None or pad.GetNetCode() <= 0:
            continue
        code = pad.GetNetCode()
        c = pad.GetPosition()
        segs = [(chip(*a), chip(*b)) for a, b in zip(pts, pts[1:])]
        if math.hypot(segs[0][0][0] - c.x, segs[0][0][1] - c.y) > mm(0.02):
            print(f"  reference track for pin {num}: frame mismatch, skipped")
            continue
        segs[0] = ((c.x, c.y), segs[0][1])
        tgt = next((q for f2 in board.GetFootprints() for q in f2.Pads()
                    if q.GetNetCode() == code and f2.GetReference() != ref
                    and q.HitTest(V(*segs[-1][1]))), None)
        if not legal(obs, code, pcbnew.F_Cu, segs, w=0.15):
            print(f"  reference track for pin {num}: not clear, left to the router")
            continue
        for a, b in segs:
            add_track(board, obs, a, b, pcbnew.F_Cu, code, w=0.15)
        ref_tracks += 1
    # The ring and the regulator's output pour are both 1V1 and neither
    # reaches the other: VREG_FB's pad cannot get a track out between its
    # neighbours to the pour. A via in each, and the router joins them on
    # In3. The ring via sits between two of the exposed pad's thermal vias.
    ring_vias = 0
    # ... and the regulator's ground pour, PGND, gets the reference design's
    # own three ground vias, or as many of them as fit beside the fan-out.
    for zname, spots in (("1V1 ring", [(2.1, 0.69), (2.1, -0.69), (-2.1, 0.69), (0.69, 2.1)]),
                         ("DVDD", [(1.3, -7.0), (1.3, -7.4), (1.3, -6.7)]),
                         ("VREG_PGND", [(3.1, -5.5), (3.1, -4.9), (3.85, -3.85)])):
        z = next((z for z in board.Zones() if z.GetZoneName().startswith(zname)), None)
        if z is None:
            continue
        for dx, dy in spots:
            via = chip(dx, dy)
            if any(v.GetPosition() == V(*via) for v in board.GetTracks() if v.Type() == pcbnew.PCB_VIA_T):
                break                                   # an earlier run's
            if z.Outline().Contains(V(*via)) and obs.clear(circle(via), z.GetNetCode(), None) \
                    and obs.hole_ok(via[0], via[1], mm(VIA_K) // 2):
                add_via(board, obs, via, z.GetNetCode())
                ring_vias += 1
                if zname != "VREG_PGND":
                    break
    if verbose:
        print(f"fanout {ref}: {len(made)} vias, {stubs} ring stubs, {ref_tracks} reference tracks, {ring_vias} 1V1 vias, "
              f"{len(covered)} pins on the regulator's pours, {len(left)} left")
        if left:
            print("  left to the router: " + ", ".join(f"{n} {nm}" for n, nm in left))
    return made, left

# ------------------------------------------------- any fine-pitch part ----
ESCAPE_SKIP = set(WIDE_NETS) | {"GND", "+3V3", "SNSP_A", "SNSP_B", "SNSP_C"}   # stitched, tapped, or an arc

def escape_refs(board):
    """The parts whose pads get a via each: the flash, the three sense
    amplifiers, and each cell's sense filter and sense tap. On board S also
    the parts in the link wedges whose fine-pitch pads sit off the 45-degree
    grid: the USB-C's 0.3 mm pads at 0.5 mm, the RS-485 ports' 0.6 mm at
    1.0 mm, the USB ESD part's SOT-23-6 -- and the four SIT3088."""
    out = []
    for f in board.GetFootprints():
        ref, val = f.GetReference(), f.GetValue()
        if ref in ("U8", "R901", "J9") or "INA241" in val or re.fullmatch(r"[CR][123]08", ref):
            out.append(ref)
        elif ref in ("J12", "J14", "J15", "U13") and val in (
                "USB-C", "RS485 IN", "RS485 OUT", "USBLC6-2SC6"):
            out.append(ref)
        elif ref == "J13" and val == "EXPANSION":
            # board S's expansion header, on the axis: on the grid, but its
            # north row faces away from the CPU, and the header's GPIO were
            # the nets left over when it had no escapes
            out.append(ref)
        elif val.startswith("SIT3088"):
            # 0.28 mm pads at 0.65 mm: the INA241's trap again. On board S's
            # first routes the router never reached pins 1, 4, 6 or 7 of any
            # of the four, on or off the grid.
            out.append(ref)
    return sorted(out)

# ---------------------------------------------------------------- modules ----
POWER_W = 0.5          # the Power and Phase classes' tracks, as route.py's DSN has them

def near_links(board, obs, ic_value="LMR38010", reach=4.5, verbose=True):
    """The short connections inside a buck module, as copper.

    placement_s lays each LMR38010's passives against the pins they serve
    (MOD_LMR38010), so every one of these is a millimetre or two -- and the
    module turns in 15-degree steps, off the 45-degree grid a router needs to
    leave a pad. So they are made here, pad centre to pad centre, straight or
    with one corner, checked like every other track the tools lay: per net, a
    minimum spanning tree over the module's pads of that net and face, each
    edge shorter than `reach`. Ground only the input cap's return to pin 1,
    the hot loop; every other ground pad is on its plane by a via.

    Wide where the current is (VIN, SW, the output), narrow elsewhere, and
    narrower again if the wide one does not fit."""
    made, left = [], []
    wide_nets = set(WIDE_NETS) | {"+12V", "+5V_BUCK", "+5V"}
    for ic in [f for f in board.GetFootprints() if f.GetValue().startswith(ic_value)]:
        layer = pcbnew.F_Cu if ic.GetLayer() == pcbnew.F_Cu else pcbnew.B_Cu
        c0 = ic.GetPosition()
        ic_nets = {p.GetNetCode() for p in ic.Pads() if p.GetNetCode() > 0}
        gnd = board.FindNet("GND").GetNetCode()
        # the module's own pads: within 8 mm for the power nets, 12 for the
        # rest, which reaches the EN divider's switch beside the module
        power = set(WIDE_NETS) | {"+12V", "+5V_BUCK", "+5V", "GND"}
        pads = [(f, p) for f in board.GetFootprints() for p in f.Pads()
                if p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD and p.GetNetCode() in ic_nets
                and layer in p.GetLayerSet().CuStack()
                and math.hypot(p.GetPosition().x - c0.x, p.GetPosition().y - c0.y)
                < mm(8.0 if p.GetNetname() in power else 12.0)]
        by_net = {}
        for f, p in pads:
            by_net.setdefault(p.GetNetCode(), []).append((f, p))
        # the ground link: pin 1 to the nearest ground pad of a capacitor on VIN
        vin = next((p.GetNetCode() for p in ic.Pads() if p.GetNumber() == "3"), None)
        cin = [f for f, p in pads if p.GetNetCode() == vin and f.GetReference().startswith("C")]
        g1 = next((p for p in ic.Pads() if p.GetNumber() == "1"), None)
        edges = []
        if cin and g1 is not None:
            cg = min(((q, math.hypot(q.GetPosition().x - g1.GetPosition().x,
                                     q.GetPosition().y - g1.GetPosition().y))
                      for f in cin for q in f.Pads() if q.GetNetCode() == gnd), key=lambda e: e[1])
            if cg[1] < mm(reach):
                edges.append((cg[1], gnd, g1, cg[0]))
        for code, lst in by_net.items():
            if code == gnd:
                continue
            # Prim's tree over the pad centres; pads of one number (the
            # exposed pad's pieces) are one node
            nodes = []
            for f, p in lst:
                if not any(q.GetParentFootprint() is f and q.GetNumber() == p.GetNumber() for _, q in nodes):
                    nodes.append((f, p))
            if len(nodes) < 2:
                continue
            inside, rest = [nodes[0]], nodes[1:]
            while rest:
                best = min(((math.hypot(a.GetPosition().x - b.GetPosition().x,
                                        a.GetPosition().y - b.GetPosition().y), a, (g, b))
                            for _, a in inside for g, b in rest), key=lambda e: e[0])
                d, a, (g, b) = best
                inside.append((g, b)); rest.remove((g, b))
                if d < mm(reach):
                    edges.append((d, code, a, b))
        # power first, and shortest first within each
        name = {p.GetNetCode(): p.GetNetname() for _, p in pads}
        edges.sort(key=lambda e: (name.get(e[1], "") not in wide_nets, e[0]))
        for d, code, a, b in edges:
            pa, pb = a.GetPosition(), b.GetPosition()
            if _joined(board, code, layer, a, b):
                continue
            net = a.GetNetname()
            widths = (POWER_W, 0.3, STUB_W) if net in wide_nets or net == "GND" else (0.25, STUB_W)
            got = None
            for w in widths:
                for pts in _link_options((pa.x, pa.y), (pb.x, pb.y)):
                    if len(pts) > 3:
                        break
                    segs = list(zip(pts, pts[1:]))
                    if all(math.hypot(q[0] - p[0], q[1] - p[1]) > mm(0.02) for p, q in segs) \
                            and legal(obs, code, layer, segs, w=w):
                        got = (w, segs)
                        break
                if got:
                    break
            if got is None:
                left.append((ic.GetReference(), net, a.GetParentFootprint().GetReference(),
                             b.GetParentFootprint().GetReference()))
                continue
            w, segs = got
            for p, q in segs:
                add_track(board, obs, p, q, layer, code, w=w)
            made.append((ic.GetReference(), net))
    if verbose:
        print(f"modules: {len(made)} short links laid, {len(left)} left"
              + ("" if not left else ": " + ", ".join(f"{i} {n} {a}-{b}" for i, n, a, b in left)))
    return made, left

BUS_VIA = (0.8, 0.4)        # the Phase class's via; 0.2 mm of ring on a 0.4 drill

def bus_field(board, obs, name="BusPad_Arc", pitch=0.95, verbose=True):
    """As many barrels as fit in each of board S's bus lead pads.

    The pad carries the whole bus current into the planes, and a fixed array
    in the footprint came through onto the RS-485 port on the other face. So
    the array is a lattice at `pitch` (0.55 mm hole to hole) aligned with the
    pad's own axis, at whichever offset lets the most of it land: inside the
    pad, clear of foreign copper on every layer, clear of every hole.

    The VMOT pad shares the rim with RS-485 port IN's mounting tab on the
    other face, which leaves room for four; so the field carries on into the
    VBUS spine on the outward face (gen_boards.bus_spine) within `spill` mm
    of the pad, where the far face is clear."""
    made = {}
    spill = 4.5
    for f in board.GetFootprints():
        if str(f.GetFPID().GetLibItemName()) != name:
            continue
        pad = f.Pads()[0]
        code = pad.GetNetCode()
        # the stitcher's via-in-pad is already there: the field fills round it
        c = f.GetPosition()
        a = math.radians(f.GetOrientationDegrees())
        ux, uy = math.cos(a), -math.sin(a)                  # the pad's radial axis, y down
        vx, vy = -uy, ux
        d, k = BUS_VIA
        r = mm(d) // 2
        local = obs.near(c.x, c.y, reach=mm(6.0 + spill))
        spines = [z.Outline() for z in board.Zones()
                  if z.GetNetCode() == code and z.IsOnLayer(pcbnew.F_Cu) and "spine" in z.GetZoneName()]
        def inside(x, y):
            p = V(x, y)
            return pad.HitTest(p) or (math.hypot(x - c.x, y - c.y) < mm(spill)
                                      and any(s.Contains(p) for s in spines))
        def fits(x, y):
            if not inside(x, y):
                return False
            return all(inside(int(x + (r + mm(0.1)) * math.cos(t)), int(y + (r + mm(0.1)) * math.sin(t)))
                       for t in [i * math.pi / 8 for i in range(16)])
        def ok(x, y, taken):
            if not fits(x, y):
                return False
            if not local.clear(pcbnew.SHAPE_CIRCLE(V(x, y), r), code, None):
                return False
            if not local.hole_ok(x, y, mm(k) // 2):
                return False
            return all(math.hypot(x - px, y - py) >= mm(k + H2H) for px, py in taken)
        best = []
        n = 4 + int(spill / pitch) + 1
        for oi in range(10):
            for oj in range(10):
                pts = []
                for i in range(-n, n + 1):
                    for j in range(-n, n + 1):
                        s, t = (i + oi / 10) * mm(pitch), (j + oj / 10) * mm(pitch)
                        x, y = int(c.x + s * ux + t * vx), int(c.y + s * uy + t * vy)
                        if ok(x, y, pts):
                            pts.append((x, y))
                if len(pts) > len(best):
                    best = pts
        for x, y in best:
            add_via(board, obs, (x, y), code, d=d, k=k)
        made[f.GetReference()] = len(best)
    if verbose and made:
        print("bus pads: " + ", ".join(f"{r} {n} barrels" for r, n in sorted(made.items())))
    return made

def pin_field(board, obs, name="AMASS_XT30", pitch=0.95, spill=3.4, verbose=True):
    """Barrels round board S's XT30 pins (2026-09-24, when it took the bus
    pads' place). A pin is a plated hole and joins every plane it passes, but
    the whole bus goes in at one pin and In2 is 1 oz: a field of vias round
    the VMOT pin, inside both the F.Cu spine and In2's VBUS tab, gives the
    spine its own ways down; the ground pin gets the same into In1 and In4.
    A lattice at `pitch`, at whichever offset lands the most within `spill`
    of the pin, clear of foreign copper on every layer and of every hole."""
    made = {}
    d, k = BUS_VIA
    r = mm(d) // 2
    for f in board.GetFootprints():
        if not str(f.GetFPID().GetLibItemName()).startswith(name):
            continue
        vcode = board.FindNet("VBUS").GetNetCode()
        # VMOT first; the ground pin's may not perforate In2's VBUS tab
        for pad in sorted((q for q in f.Pads() if q.GetNumber() in ("1", "2")),
                          key=lambda q: q.GetNetname() != "VBUS"):
            code, c = pad.GetNetCode(), pad.GetPosition()
            rp = max(pad.GetSize().x, pad.GetSize().y) // 2
            local = obs.near(c.x, c.y, reach=mm(spill + 2.0))
            vbus = pad.GetNetname() == "VBUS"
            ring = [i * math.pi / 4 for i in range(8)]
            def ok(x, y, taken):
                dist = math.hypot(x - c.x, y - c.y)
                if dist > mm(spill) or dist < rp + r + mm(0.15):
                    return False
                if vbus and not all(local.inside(int(x + (r + mm(0.1)) * math.cos(t)),
                                                 int(y + (r + mm(0.1)) * math.sin(t)), code, l)
                                    for l in (pcbnew.F_Cu, pcbnew.In2_Cu) for t in ring):
                    return False
                if not vbus and any(local.inside(int(x + (r + mm(0.5)) * math.cos(t)),
                                                 int(y + (r + mm(0.5)) * math.sin(t)), vcode,
                                                 pcbnew.In2_Cu) for t in ring):
                    return False
                if not local.clear(pcbnew.SHAPE_CIRCLE(V(x, y), r), code, None):
                    return False
                if not local.hole_ok(x, y, mm(k) // 2):
                    return False
                return all(math.hypot(x - px, y - py) >= mm(k + H2H) for px, py in taken)
            best = []
            n = int(spill / pitch) + 1
            for oi in range(10):
                for oj in range(10):
                    pts = []
                    for i in range(-n, n + 1):
                        for j in range(-n, n + 1):
                            x = int(c.x + (i + oi / 10) * mm(pitch))
                            y = int(c.y + (j + oj / 10) * mm(pitch))
                            if ok(x, y, pts):
                                pts.append((x, y))
                    if len(pts) > len(best):
                        best = pts
            for x, y in best:
                add_via(board, obs, (x, y), code, d=d, k=k)
            made[f"{f.GetReference()}-{pad.GetNumber()} {pad.GetNetname()}"] = len(best)
    if verbose and made:
        print("XT30 pins: " + ", ".join(f"{r} {n} barrels" for r, n in sorted(made.items())))
    return made

def _joined(board, code, layer, a, b):
    """Is there already a track of the net on the layer from pad a to pad b?"""
    for t in board.GetTracks():
        if t.Type() != pcbnew.PCB_TRACE_T or t.GetNetCode() != code or t.GetLayer() != layer:
            continue
        ends = (t.GetStart(), t.GetEnd())
        if any(a.HitTest(e) for e in ends) and any(b.HitTest(e) for e in ends):
            return True
    return False

def escaped(board, pad, depth=4):
    """Does a track of the pad's net lead from this pad to a via of its net
    within `depth` segments? Then an earlier run gave it its escape. (A
    track that merely starts on the pad -- the sense arcs' spurs do -- is
    not that.)"""
    code = pad.GetNetCode()
    layers = set(pad.GetLayerSet().CuStack())
    tracks = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_TRACE_T and t.GetNetCode() == code]
    vias = [v.GetPosition() for v in board.GetTracks() if v.Type() == pcbnew.PCB_VIA_T and v.GetNetCode() == code]
    front = [t for t in tracks if t.GetLayer() in layers
             and (pad.HitTest(t.GetStart()) or pad.HitTest(t.GetEnd()))]
    others = [q for f in board.GetFootprints() for q in f.Pads()
              if q.GetNetCode() == code and q is not pad and q.GetPosition() != pad.GetPosition()]
    seen = set()
    for _ in range(depth):
        nxt = []
        for t in front:
            if id(t) in seen:
                continue
            seen.add(id(t))
            for e in (t.GetStart(), t.GetEnd()):
                if any(abs(e.x - v.x) <= mm(0.05) and abs(e.y - v.y) <= mm(0.05) for v in vias):
                    return True                      # nudged vias count
                # ... or into a neighbouring pad of the net that has its own
                # way out (a bridged QFN pin)
                if any(v in [x.GetPosition() for x in others] for v in [e]) or \
                        any(q.HitTest(e) and t.GetLayer() in q.GetLayerSet().CuStack()
                            and any(q.HitTest(v) for v in vias) for q in others):
                    return True
                nxt += [u for u in tracks if id(u) not in seen and (u.GetStart() == e or u.GetEnd() == e)]
        front = nxt
    return False

def escape(board, obs, ref, rows=2, verbose=True, only=None):
    """A straight stub and a via for every netted SMD pad of `ref`.

    For a part whose pads freerouting cannot leave: the 0.5 mm-pitch flash,
    whose pads sit 26 degrees off the grid. In 45-degree mode the router's
    search tree wraps a tilted pad in its bounding octagon, which overlaps
    the neighbours at this pitch and leaves the pin no way out; in free-angle
    mode it found the way out of two pads in four. So the way out is made
    here: along the pad's own axis, away from the body, to a via in one of
    `rows` staggered rows -- and the router only ever meets the via."""
    fp = next(f for f in board.GetFootprints() if f.GetReference() == ref)
    layer = pcbnew.F_Cu if fp.GetLayer() == pcbnew.F_Cu else pcbnew.B_Cu
    have = stitch.already(board)
    pads = [p for p in fp.Pads()
            if p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD and p.GetNetCode() > 0
            and p.GetNetname() not in ESCAPE_SKIP
            and (only is None or p.GetNumber() in only)
            and not any(p.HitTest(V(x, y)) for x, y in have.get(p.GetNetCode(), ()))]
    sides = {}
    for p in pads:
        c, u, n, half = pad_axes(fp, p)
        key = (round(u[0], 3), round(u[1], 3))
        sides.setdefault(key, []).append((c[0] * n[0] + c[1] * n[1], p.GetNumber(), p))
    made, left = [], []
    for key, lst in sides.items():
        lst.sort()
        for k, (_, num, p) in enumerate(lst):
            if escaped(board, p):
                continue
            c, u0, n0, half = pad_axes(fp, p)
            code = p.GetNetCode()
            local = obs.near(c[0], c[1])
            got = None
            row0 = k % rows
            # Away from the body first; then through it, for a pad whose
            # outward side is a pour (the amplifier's outer row sits in the
            # switch-node pour's notch): between the two rows there is room
            # for a via. The amplifier's inner row goes through the body
            # FIRST: inward of it are the +3V3 taps, and the search went
            # round them to R 14.8, which the router then could not reach.
            dirs = [(u0, n0), ((-u0[0], -u0[1]), (-n0[0], -n0[1]))]
            if "INA241" in fp.GetValue() and num in ("5", "8"):
                dirs.reverse()
            for u, n in dirs:
                end = (c[0] + u[0] * half, c[1] + u[1] * half)
                for r in [row0] + [x for x in range(rows + 2) if x != row0]:
                    for dd in (0.0, 0.05, 0.1, 0.15, 0.2, -0.05):
                        d = ROW0 + ROW_PITCH * r + dd
                        for lv in (0.0, 0.05, -0.05, 0.1, -0.1, 0.15, -0.15):
                            via = (end[0] + mm(d) * u[0] + mm(lv) * n[0],
                                   end[1] + mm(d) * u[1] + mm(lv) * n[1])
                            if lv == 0.0:
                                segs = [(c, end), (end, via)]
                            else:
                                bend = (end[0] + mm(d - 0.25) * u[0], end[1] + mm(d - 0.25) * u[1])
                                segs = [(c, end), (end, bend), (bend, via)]
                            if legal(local, code, layer, segs, via):
                                got = (via, segs)
                                break
                        if got:
                            break
                    if got:
                        break
                if got is None:
                    for via, segs in candidates(end, u, n, d_min=0.43, d_max=3.0, l_max=2.0):
                        if legal(local, code, layer, [(c, end)] + segs, via):
                            got = (via, [(c, end)] + segs)
                            break
                if got:
                    break
            if got is None and min(p.GetSize().x, p.GetSize().y) >= mm(VIA_D + 0.1):
                # ... or in the pad itself, where the pad is big enough
                # (a 0603's is; the amplifier's 0.5 mm pads are not) --
                # anywhere in it the via fits, since the sense tap's pad
                # centre is 0.07 mm too close to the low-side gate.
                ha = max(p.GetSize().x, p.GetSize().y) / 2 - mm(VIA_D) / 2 - mm(0.02)
                hb = min(p.GetSize().x, p.GetSize().y) / 2 - mm(VIA_D) / 2 - mm(0.02)
                for fa, fb in sorted({(a / 4, bb / 4) for a in range(-4, 5) for bb in range(-4, 5)},
                                     key=lambda v: abs(v[0]) + abs(v[1])):
                    spot = (c[0] + u0[0] * ha * fa + n0[0] * hb * fb,
                            c[1] + u0[1] * ha * fa + n0[1] * hb * fb)
                    if legal(local, code, layer, [], spot):
                        # off the centre, a stub from the centre to it:
                        # freerouting joins a via to a pad only at the
                        # pad's centre (route.py, 8d)
                        got = (spot, [] if spot == c else [(c, spot)])
                        break
            if got is None:
                left.append((num, p.GetNetname()))
                continue
            via, segs = got
            for a, b in segs:
                add_track(board, obs, a, b, layer, code)
            add_via(board, obs, via, code)
            made.append((num, p.GetNetname()))
    if verbose:
        print(f"escape {ref}: {len(made)} vias, {len(left)} left"
              + ("" if not left else ": " + ", ".join(f"{n} {nm}" for n, nm in left)))
    return made, left

# ---------------------------------------------------------------- gates ----
GATE_W = 0.25                          # the Gate class
GATE_ARC_R = (23.2, 23.0, 23.4, 22.8, 23.6, 22.6)   # under the low-side drain tab's 0.4 mm, above the driver pins'

def gates(board, obs, verbose=True):
    """Each FET's gate pad to its gate resistor, as copper.

    The gate is the innermost pad of the source row, at R 24.4 in every
    cell; the resistor is in the driver row at R 19.9, and for the
    high-side FET it is eight millimetres round the cell from the gate, on
    the driver's output side. Between the driver row's outer ends and the
    FETs there is a clear band on the front, R 22.5 to 24, and the
    connection is an arc along it: radially in from the gate, round to the
    resistor's angle, radially in to its pad. A 45-degree router cannot
    follow a 0.7 mm-wide arc and did not, in five cells out of six, in
    every run."""
    c0 = board.GetBoardEdgesBoundingBox().GetCenter()
    def polar(x, y):
        return math.hypot(x - c0.x, y - c0.y), math.atan2(y - c0.y, x - c0.x)
    def at(r, a):
        return (c0.x + r * math.cos(a), c0.y + r * math.sin(a))
    made, left = [], []
    for fp in board.GetFootprints():
        if not re.fullmatch(r"Q\d", fp.GetReference()):
            continue
        gate = next((p for p in fp.Pads() if p.GetNumber() == "4"), None)
        if gate is None or gate.GetNetCode() <= 0 or already(board, gate):
            continue
        code = gate.GetNetCode()
        tgt = next((q for f2 in board.GetFootprints() for q in f2.Pads()
                    if q.GetNetCode() == code and f2.GetReference()[0] == "R"
                    and f2.GetLayer() == pcbnew.F_Cu), None)
        if tgt is None:
            left.append((fp.GetReference(), gate.GetNetname()))
            continue
        g, r = gate.GetPosition(), tgt.GetPosition()
        rg, ag = polar(g.x, g.y)
        rr, ar = polar(r.x, r.y)
        da = (ar - ag + math.pi) % (2 * math.pi) - math.pi
        got = None
        for R in GATE_ARC_R:
            R = mm(R)
            n = max(1, int(abs(da) * R / mm(1.0)))      # 1 mm chords: 0.005 mm of sagitta
            pts = [(g.x, g.y)] + [at(R, ag + da * i / n) for i in range(n + 1)] + [(r.x, r.y)]
            segs = list(zip(pts, pts[1:]))
            if legal(obs, code, pcbnew.F_Cu, segs, w=GATE_W):
                got = segs
                break
        if got is None:
            left.append((fp.GetReference(), gate.GetNetname()))
            continue
        for a, b in got:
            add_track(board, obs, a, b, pcbnew.F_Cu, code, w=GATE_W)
        # ... and the bleed on the back: its gate pad lies under the route
        # (under the resistor's pad for the high side, under the gate's
        # radial for the low side), and one via through both is the whole
        # connection. On the route's line, at the bleed pad's centre or,
        # if that is off it, where the route crosses the pad.
        bleed = next((q for f2 in board.GetFootprints() for q in f2.Pads()
                      if q.GetNetCode() == code and f2.GetLayer() == pcbnew.B_Cu
                      and q.GetAttribute() == pcbnew.PAD_ATTRIB_SMD), None)
        if bleed is not None and not any(bleed.HitTest(v.GetPosition()) for v in board.GetTracks()
                                         if v.Type() == pcbnew.PCB_VIA_T and v.GetNetCode() == code):
            bc = bleed.GetPosition()
            spots = []
            for a, b in got:
                seg_shape = pcbnew.SHAPE_SEGMENT(V(*a), V(*b), 0)
                if seg_shape.Collide(V(bc.x, bc.y), mm(0.05)):
                    spots.append((bc.x, bc.y))
                # the nearest point of the segment to the pad centre
                ax, ay, bx, by = a[0], a[1], b[0], b[1]
                L2 = (bx - ax) ** 2 + (by - ay) ** 2
                if L2:
                    u = max(0.0, min(1.0, ((bc.x - ax) * (bx - ax) + (bc.y - ay) * (by - ay)) / L2))
                    spots.append((ax + u * (bx - ax), ay + u * (by - ay)))
            placed = False
            for sx, sy in spots:
                if bleed.HitTest(V(int(sx), int(sy))) and legal(obs, code, None, [], (sx, sy)):
                    add_via(board, obs, (sx, sy), code)
                    placed = True
                    break
            if not placed:
                left.append((fp.GetReference(), gate.GetNetname() + " (bleed via)"))
        made.append((fp.GetReference(), gate.GetNetname()))
    if verbose:
        print(f"gates: {len(made)} gate pads wired to their resistors, {len(left)} left"
              + ("" if not left else ": " + ", ".join(f"{a} {b}" for a, b in left)))
    return made, left

# ---------------------------------------------------------------- sense ----
SNSP_R = (19.1, 19.0, 19.2, 18.95)     # between the amplifier's pad rows
SNSN_R = (19.5, 19.55, 19.45)          # ... and SNSN's, outside it, under the outer row's ends

def sense(board, obs, verbose=True):
    """SNSP, in each cell, as copper: the shunt's switch-node-side tap, the
    filter cap and the amplifier's SNSP pin are all on the back within
    0.4 mm of R 18.4, and an arc between the amplifier's two pad rows
    joins them, with a radial spur down to each pad. The router could not
    leave the amplifier's 0.65 mm-pitch pads at all in 45-degree mode."""
    c0 = board.GetBoardEdgesBoundingBox().GetCenter()
    def polar(x, y):
        return math.hypot(x - c0.x, y - c0.y), math.atan2(y - c0.y, x - c0.x)
    def at(r, a):
        return (c0.x + r * math.cos(a), c0.y + r * math.sin(a))
    made, left = [], []
    for fp in board.GetFootprints():
      if "INA241" not in fp.GetValue():
        continue
      # SNSP: pin 8, the SW-side tap and the filter cap's pin 1, all of them.
      # SNSN: pin 1 and the filter cap's pin 2 -- the tap at the shunt is
      # across the switch-node pour and joins by a via and In3 (links).
      for num, radii, only in (("8", SNSP_R, None), ("1", SNSN_R, ("C",))):
        pin = next((p for p in fp.Pads() if p.GetNumber() == num), None)
        if pin is None or pin.GetNetCode() <= 0 or already(board, pin):
            continue
        code = pin.GetNetCode()
        pads = [q for f2 in board.GetFootprints() for q in f2.Pads()
                if q.GetNetCode() == code and pcbnew.B_Cu in q.GetLayerSet().CuStack()
                and (f2.GetReference() == fp.GetReference() or only is None
                     or f2.GetReference()[0] in only)]
        pol = sorted((polar(q.GetPosition().x, q.GetPosition().y), q) for q in pads)
        angs = [a for (_, a), _ in pol]
        a0, a1 = min(angs), max(angs)
        got = None
        for R in radii:
            Rm = mm(R)
            n = max(1, int((a1 - a0) * Rm / mm(1.0)))
            arc = [at(Rm, a0 + (a1 - a0) * i / n) for i in range(n + 1)]
            segs = list(zip(arc, arc[1:]))
            for (r, a), q in pol:
                c = q.GetPosition()
                segs.append(((c.x, c.y), at(Rm, a)))
            if legal(obs, code, pcbnew.B_Cu, segs, w=STUB_W):
                got = segs
                break
        if got is None:
            left.append((fp.GetReference(), pin.GetNetname()))
            continue
        for a, b in got:
            add_track(board, obs, a, b, pcbnew.B_Cu, code)
        made.append((fp.GetReference(), pin.GetNetname()))
    if verbose:
        print(f"sense: {len(made)} sense nets wired on the back, {len(left)} left"
              + ("" if not left else ": " + ", ".join(f"{a} {b}" for a, b in left)))
    return made, left

def _link_options(a, b):
    """Paths from a to b: straight; one corner at 1/3, 1/2 and 2/3 of the way,
    stepped off the line by up to 3 mm either side; and two corners, the
    middle third stepped off the line."""
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    L = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / L, dx / L
    yield [(ax, ay), (bx, by)]
    steps = [k * 0.25 for k in range(1, 17)]
    for d in steps:
        for sgn in (1, -1):
            for f in (0.5, 0.33, 0.67, 0.25, 0.75):
                m = (ax + dx * f + nx * mm(d) * sgn, ay + dy * f + ny * mm(d) * sgn)
                yield [(ax, ay), m, (bx, by)]
    for d1 in steps:
        for d2 in steps:
            for s1 in (1, -1):
                for s2 in (1, -1):
                    m1 = (ax + dx * 0.33 + nx * mm(d1) * s1, ay + dy * 0.33 + ny * mm(d1) * s1)
                    m2 = (ax + dx * 0.67 + nx * mm(d2) * s2, ay + dy * 0.67 + ny * mm(d2) * s2)
                    yield [(ax, ay), m1, m2, (bx, by)]

def links(board, obs, nets=("SNSN_A", "SNSN_B", "SNSN_C", "+1V1"), w=0.25, verbose=True):
    """Join a net's vias on In3, in order round the board: straight, or
    with one corner. For SNSN, whose three ends each got a via (the
    amplifier's pin, the filter cap, the tap at the shunt) and whose path
    crosses the switch-node pour on the back."""
    c0 = board.GetBoardEdgesBoundingBox().GetCenter()
    def polar(x, y):
        return math.hypot(x - c0.x, y - c0.y), math.atan2(y - c0.y, x - c0.x)
    def at(r, a):
        return (c0.x + r * math.cos(a), c0.y + r * math.sin(a))
    made, left = [], []
    for name in nets:
        net = board.FindNet(name)
        if net is None:
            continue
        code = net.GetNetCode()
        vias = [v for v in board.GetTracks() if v.Type() == pcbnew.PCB_VIA_T and v.GetNetCode() == code]
        if len(vias) < 2:
            continue
        if any(t.GetLayer() == pcbnew.In3_Cu for t in board.GetTracks()
               if t.Type() == pcbnew.PCB_TRACE_T and t.GetNetCode() == code):
            continue                                      # done on an earlier run
        vias.sort(key=lambda v: polar(v.GetPosition().x, v.GetPosition().y)[1])
        ok = True
        for va, vb in zip(vias, vias[1:]):
            a, b = va.GetPosition(), vb.GetPosition()
            (ra, aa), (rb, ab) = polar(a.x, a.y), polar(b.x, b.y)
            options = list(_link_options((a.x, a.y), (b.x, b.y)))
            for r_mid in (ra, rb, (ra + rb) / 2):
                for a_mid in (aa, ab, (aa + ab) / 2):
                    m = at(r_mid, a_mid)
                    options.append([(a.x, a.y), m, (b.x, b.y)])
            got = None
            for pts in options:
                segs = list(zip(pts, pts[1:]))
                if all(math.hypot(q[0] - p[0], q[1] - p[1]) > mm(0.05) for p, q in segs) \
                        and legal(obs, code, pcbnew.In3_Cu, segs, w=w):
                    got = segs
                    break
            if got is None:
                ok = False
                break
            for p, q in got:
                add_track(board, obs, p, q, pcbnew.In3_Cu, code, w=w)
        (made if ok else left).append(name)
    if verbose:
        print(f"links: {len(made)} nets joined on In3, {len(left)} left"
              + ("" if not left else ": " + ", ".join(left)))
    return made, left

# ------------------------------------------------------------- plane taps ----
def dogleg(local, c, code, layer, planes, reach, grid=0.2):
    """The last resort for a two-terminal part's plane pad: a first leg at any
    of the eight 45-degree headings, then straight to the nearest via that
    lands on the plane -- the shortest such path that is legal. Only for the
    pads the straight and near-straight taps above could not make, so a pad
    that tapped before taps the same way now. The thermistor's 3V3 pad needs
    it: its own FET_TEMP pad is straight inward of it, and the 3V3 via beside
    it that it used to share was the LIN pull-up's, which is a pull-down now."""
    n = int(reach / grid)
    vias = []
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            v = (c.x + mm(i * grid), c.y + mm(j * grid))
            d = math.hypot(v[0] - c.x, v[1] - c.y)
            if d > mm(reach) or not any(local.inside(v[0], v[1], code, pl) for pl in planes):
                continue
            vias.append((d, v))
    vias.sort()
    best = None
    for d, v in vias[:40]:
        if best and d >= best[0]:
            break
        if not legal(local, code, layer, [], v):
            continue
        for k in range(8):
            ux, uy = math.cos(k * math.pi / 4), math.sin(k * math.pi / 4)
            for L1 in [x / 10 for x in range(3, 21)]:
                m = (c.x + ux * mm(L1), c.y + uy * mm(L1))
                segs = [((c.x, c.y), m), (m, v)]
                total = mm(L1) + math.hypot(v[0] - m[0], v[1] - m[1])
                if best and total >= best[0]:
                    continue
                if legal(local, code, layer, segs, v, w=0.2):
                    best = (total, v, segs)
    return (best[1], best[2]) if best else None

def taps(board, obs, verbose=True, reach=5.0):
    """A stub and a via for every pad on a plane net that is not on the plane.

    Straight toward the board centre first -- the +3V3 disc is there -- then
    the other three ways, the shortest that lands on the plane and clears
    everything. The stub is on the pad's own layer."""
    planes = {}
    for z in board.Zones():
        for l in z.GetLayerSet().CuStack():
            if l in (pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.In4_Cu) and z.GetNetCode() > 0:
                planes.setdefault(z.GetNetCode(), set()).add(l)
    gnd = board.FindNet("GND").GetNetCode() if board.FindNet("GND") else -1
    # Ground: a pad the stitcher could not drill (a 0402's ground pad with
    # a shunt's via beneath it, the regulator's PGND pin) sits on the
    # ground pour of its own face, which the filler calls connected -- and
    # that pour piece, cut off by routing, may have no via of its own. Such
    # a pad gets a tap like any other; a pad on a piece that has a ground
    # via or through-hole pad does not.
    gnd_vias = [(v.GetPosition().x, v.GetPosition().y) for v in board.GetTracks()
                if v.Type() == pcbnew.PCB_VIA_T and v.GetNetCode() == gnd]
    gnd_vias += [(q.GetPosition().x, q.GetPosition().y)
                 for f in board.GetFootprints() for q in f.Pads()
                 if q.GetNetCode() == gnd and q.GetAttribute() == pcbnew.PAD_ATTRIB_PTH]
    pours = [(l, z.GetFilledPolysList(l)) for z in board.Zones()
             if z.GetNetCode() == gnd and z.GetZoneName().startswith("ground pour")
             for l in z.GetLayerSet().CuStack()]
    def on_tied_pour(p):
        """A ground pad on a pour piece that already has a hole to the planes."""
        for zl, poly in pours:
            if zl not in p.GetLayerSet().CuStack():
                continue
            for i in range(poly.OutlineCount()):
                one = pcbnew.SHAPE_POLY_SET(poly.Outline(i))
                if one.Collide(p.GetEffectiveShape(zl), 0):
                    return any(one.Contains(V(x, y)) for x, y in gnd_vias)
        return False
    def on_own_pour(p):
        """Connected already by a zone of its net on its own layer, or by a
        through-hole pad of its net inside it (the FET drains)."""
        if p.GetNetCode() == gnd:
            # A ground pad on the ground pour of its face is connected only
            # as long as routing leaves that piece of pour touching a hole;
            # a pull-down's ground pad and a decoupling cap's on the back
            # both ended up on islands. Every one without a hole of its
            # own gets a tap while the board is still empty.
            return False
        c = p.GetPosition()
        for _, poly, zcode, zl in obs.zones:
            if zcode == p.GetNetCode() and zl in p.GetLayerSet().CuStack() \
                    and poly.Collide(p.GetEffectiveShape(zl), 0):
                return True
        for f in board.GetFootprints():
            for q in f.Pads():
                if q.GetNetCode() == p.GetNetCode() and q is not p and \
                        q.GetAttribute() == pcbnew.PAD_ATTRIB_PTH and p.HitTest(q.GetPosition()):
                    return True
        return False
    have = stitch.already(board)
    c0 = board.GetBoardEdgesBoundingBox().GetCenter()
    made, left = [], []
    # Supply pins first, ground last: ground has two planes and a pour on
    # every face, a supply pin has one plane and a few places a via can land
    # on it -- and on board S a ground tap laid first crossed the only way
    # from a transceiver's VCC pin to its capacitor.
    order = sorted(((f, p) for f in board.GetFootprints() for p in f.Pads()),
                   key=lambda fp: fp[1].GetNetCode() == gnd)
    for f, p in order:
            code = p.GetNetCode()
            if code not in planes or p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
                continue
            if any(p.HitTest(v) for v in have.get(code, ())) or escaped(board, p) \
                    or on_own_pour(p):
                continue
            layer = next(iter(p.GetLayerSet().CuStack()))
            c = p.GetPosition()
            dx, dy = c0.x - c.x, c0.y - c.y
            n = math.hypot(dx, dy) or 1.0
            inward = (dx / n, dy / n)
            dirs = [inward, (-inward[0], -inward[1]), (-inward[1], inward[0]),
                    (inward[1], -inward[0])]
            got = None
            local = obs.near(c.x, c.y, reach=mm(8.0))
            # A via of the net already within reach: a track to it, on
            # the pad's layer.
            near_vias = sorted((math.hypot(v.GetPosition().x - c.x, v.GetPosition().y - c.y), v)
                               for v in board.GetTracks()
                               if v.Type() == pcbnew.PCB_VIA_T and v.GetNetCode() == code
                               and math.hypot(v.GetPosition().x - c.x, v.GetPosition().y - c.y) < mm(reach))
            for _, v in near_vias:
                vp = (v.GetPosition().x, v.GetPosition().y)
                for pts in _link_options((c.x, c.y), vp):
                    segs = list(zip(pts, pts[1:]))
                    if all(math.hypot(q[0] - p[0], q[1] - p[1]) > mm(0.05) for p, q in segs) \
                            and legal(local, code, layer, segs, w=0.2):
                        got = (None, segs)
                        break
                if got:
                    break
            for ux, uy in ([] if got else dirs):
                L = 0.6
                while L <= reach and got is None:
                    for lat in (0.0, 0.3, -0.3, 0.6, -0.6):
                        via = (c.x + ux * mm(L) - uy * mm(lat), c.y + uy * mm(L) + ux * mm(lat))
                        if not any(local.inside(via[0], via[1], code, pl) for pl in planes[code]):
                            continue
                        start = (c.x, c.y)
                        segs = [(start, via)] if lat == 0.0 else \
                               [(start, (c.x + ux * mm(0.4), c.y + uy * mm(0.4))),
                                ((c.x + ux * mm(0.4), c.y + uy * mm(0.4)), via)]
                        if legal(local, code, layer, segs, via, w=0.2):
                            got = (via, segs)
                            break
                    L += 0.1
                if got:
                    break
            if got is None and len(f.Pads()) <= 3:
                got = dogleg(local, c, code, layer, planes[code], reach)
            if got is None:
                left.append((f.GetReference(), p.GetNumber(), p.GetNetname()))
                continue
            via, segs = got
            for a, b in segs:
                add_track(board, obs, a, b, layer, code, w=0.2)
            if via is not None:
                add_via(board, obs, via, code)
            made.append((f.GetReference(), p.GetNumber()))
    # A pad that could not reach its plane may reach a neighbour that did:
    # a transceiver's VCC pin under the VBUS spine on the other face, with
    # its own 100 n a millimetre away. A track to the nearest such pad of
    # the net on the same face, straight or with one corner.
    still = []
    lpads = {(r, n) for r, n, _ in left}
    for r, n, name in left:
        f = next(f for f in board.GetFootprints() if f.GetReference() == r)
        p = next(q for q in f.Pads() if q.GetNumber() == n)
        code, c = p.GetNetCode(), p.GetPosition()
        layer = next(iter(p.GetLayerSet().CuStack()))
        local = obs.near(c.x, c.y, reach=mm(6.0))
        near = sorted((math.hypot(q.GetPosition().x - c.x, q.GetPosition().y - c.y), q)
                      for g in board.GetFootprints() for q in g.Pads()
                      if q.GetNetCode() == code and q is not p and layer in q.GetLayerSet().CuStack()
                      and (g.GetReference(), q.GetNumber()) not in lpads
                      and math.hypot(q.GetPosition().x - c.x, q.GetPosition().y - c.y) < mm(4.0))
        got = None
        for _, q in near:
            qc = q.GetPosition()
            for w in (0.2, STUB_W):
                for pts in _link_options((c.x, c.y), (qc.x, qc.y)):
                    if len(pts) > 3:
                        break
                    segs = list(zip(pts, pts[1:]))
                    if all(math.hypot(b2[0] - a2[0], b2[1] - a2[1]) > mm(0.05) for a2, b2 in segs) \
                            and legal(local, code, layer, segs, w=w):
                        got = (segs, w)
                        break
                if got:
                    break
            if got:
                break
        if got is None:
            still.append((r, n, name))
            continue
        for a2, b2 in got[0]:
            add_track(board, obs, a2, b2, layer, code, w=got[1])
        made.append((r, n))
    left = still
    if verbose:
        print(f"plane taps: {len(made)} pads tapped to a plane, {len(left)} left")
        if left:
            print("  left to the router: " + ", ".join(f"{r}-{n} {nm}" for r, n, nm in left))
    return made, left

def run(path, verbose=True):
    board = pcbnew.LoadBoard(str(path))
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    obs = Obstacles(board)
    # 3V3 pins of the QFN must land on the In2 plane; other nets anywhere
    plane_nets = {z.GetNetCode() for z in board.Zones()
                  if pcbnew.In2_Cu in z.GetLayerSet().CuStack() and z.GetNetCode() > 0}
    obs.plane_layer = lambda code: pcbnew.In2_Cu if code in plane_nets else None
    bus_field(board, obs, verbose=verbose)     # board S's bus pads; nothing on A
    pin_field(board, obs, verbose=verbose)     # ... or its XT30
    q = qfn(board, obs, verbose=verbose)
    # ... and a last try, by the generic escape, at any QFN pin the planned
    # rows could not place: it is not fussy about which row a via lands in,
    # and a pin with no via is a pin no router can leave -- the pads are
    # 0.4 mm apart and a track with its clearance needs 0.45.
    escape(board, obs, "U7", rows=3, verbose=verbose,
           only={num for num, _ in q[1]})
    gates(board, obs, verbose=verbose)         # before the escapes: they dodge it
    sense(board, obs, verbose=verbose)
    near_links(board, obs, verbose=verbose)    # board S's bucks; nothing on A
    for ref in escape_refs(board):
        escape(board, obs, ref, verbose=verbose)
    links(board, obs, verbose=verbose)
    t = taps(board, obs, verbose=verbose)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(str(path), board)
    return q, t

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("board")
    a = ap.parse_args()
    run(Path(a.board))

if __name__ == "__main__":
    main()
