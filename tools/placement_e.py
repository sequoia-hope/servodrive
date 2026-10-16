#!/usr/bin/env python3
"""placement_e.py — board E, the Ethernet daughter board: a placement SKETCH.

A Ø65 board stacked on board S's expansion header J13, about 6 mm above
board S's outward face (ethernet.html, decided 2026-09-24): a W5500 behind a
HanRun HR911105A RJ45 at the rim over the CPU wedge, its own ME6211 3.3 V
from J13's +5V, a reset button, SWD pads.

Nothing here is captured or routed. It answers "does it fit, and where",
against board S AS ROUTED: the cans, the XT30, BOOTSEL and the bosses are
read from hardware/single_board/servodrive_S.kicad_pcb, and the courtyards
are KiCad's own footprints'. Coordinates are board S's: mm from the shaft
axis, y up, angles counter-clockwise from +x, seen from the outward face.

    python3 tools/placement_e.py        # the report, and img/board_e_plan.svg

Each part is put by the same kind of small search as board S's sketch: the
nearest legal spot to where it is wanted. Legal means inside board E's
outline (edge clearance), clear of every cutout, and clear of every part on
the same face.
"""
import json
from math import cos, sin, radians, degrees, hypot, atan2
from pathlib import Path

import pcbnew
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union
from shapely import affinity

import geometry as G

ROOT = Path(__file__).resolve().parent.parent
PCB_S = ROOT / "hardware/single_board/servodrive_S.kicad_pcb"
FPLIB = Path("/usr/share/kicad/footprints")
CX, CY = 148.0, 105.0                 # gen_boards.P(): the axis in KiCad's frame

R = G.R                               # board E is board S's disc
EDGE = 0.5                            # courtyard to outline (the boards' rule)
CUT_CLR = 0.5                         # a cutout's edge, round what it clears
GAP = 6.0                             # board S's face to board E's underside:
                                      # J13 + the 4.3 mm socket (ethernet.html)
TALL = 5.0                            # anything taller on board S needs a hole
CAN_D = 6.3
STANDOFF_R = 5.0 / 2 / cos(radians(30)) + 0.3   # M2.5 hex, 5 mm A/F, corners
JACK_AT = (G.WEDGE_ANG[1] - G.WEDGE_SPAN / 2,   # the CPU wedge, 256-308 deg:
           G.WEDGE_ANG[1] + G.WEDGE_SPAN / 2)   # the only rim with nothing to keep
JACK_BOSSES = ("H14", "H15")          # the two bosses either side of the jack

FP = dict(
    RJ45=("Connector_RJ", "RJ45_Hanrun_HR911105A_Horizontal"),
    SOCK=("Connector_PinSocket_1.27mm", "PinSocket_2x10_P1.27mm_Vertical_SMD"),
    LQFP48=("Package_QFP", "LQFP-48_7x7mm_P0.5mm"),
    X3225=("Crystal", "Crystal_SMD_3225-4Pin_3.2x2.5mm"),
    SOT235=("Package_TO_SOT_SMD", "SOT-23-5"),
    B3U=("Button_Switch_SMD", "SW_SPST_B3U-1000P"),
    TP=("TestPoint", "TestPoint_Pad_D1.0mm"),
    C0402=("Capacitor_SMD", "C_0402_1005Metric"),
    R0402=("Resistor_SMD", "R_0402_1005Metric"),
    C0603=("Capacitor_SMD", "C_0603_1608Metric"),
    R0603=("Resistor_SMD", "R_0603_1608Metric"),
    L0603=("Inductor_SMD", "L_0603_1608Metric"),
    C1206=("Capacitor_SMD", "C_1206_3216Metric"),
)


# ----------------------------------------------------------- geometry ----
def to_up(v):
    """A KiCad board point (nm, y down) in this sketch's frame (mm, y up)."""
    return pcbnew.ToMM(v.x) - CX, CY - pcbnew.ToMM(v.y)


def _poly_of(ps):
    """A SHAPE_POLY_SET's outlines as one shapely polygon."""
    out = []
    for i in range(ps.OutlineCount()):
        ch = ps.Outline(i)
        pts = [to_up(ch.CPoint(k)) for k in range(ch.PointCount())]
        if len(pts) >= 3:
            out.append(Polygon(pts).buffer(0))
    return unary_union(out) if out else None


def courtyard(fp):
    fp.BuildCourtyardCaches()
    lay = pcbnew.F_CrtYd if fp.GetLayer() == pcbnew.F_Cu else pcbnew.B_CrtYd
    p = _poly_of(fp.GetCourtyard(lay))
    if p is None or p.is_empty:
        bb = fp.GetBoundingBox(False)
        (x0, y0), (x1, y1) = to_up(bb.GetOrigin()), to_up(bb.GetEnd())
        p = box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    return p


# ------------------------------------------------------------ board S ----
_BOARD_S = None


def board_s():
    """What board E has to sit over: board S's outward face as routed."""
    global _BOARD_S
    # kept alive for the process: pcbnew's SWIG objects outlive their owner
    # badly (a collected BOARD or a footprint removed from one leaves later
    # calls returning dead objects, or a segfault)
    b = _BOARD_S = pcbnew.LoadBoard(str(PCB_S))
    out = {}
    for f in b.GetFootprints():
        x, y = to_up(f.GetPosition())
        out[f.GetReference()] = dict(
            ref=f.GetReference(), value=f.GetValue(),
            fp=str(f.GetFPID().GetLibItemName()),
            face="F" if f.GetLayer() == pcbnew.F_Cu else "B",
            x=x, y=y, rot=f.GetOrientationDegrees(), cy=courtyard(f),
            pads={p.GetNumber(): to_up(p.GetPosition()) for p in f.Pads()},
            nets={p.GetNumber(): p.GetNetname() for p in f.Pads()},
            drills=[(to_up(p.GetPosition()), pcbnew.ToMM(p.GetDrillSize().x))
                    for p in f.Pads() if p.GetDrillSize().x > 0])
    return out


# Heights that matter: what stands taller than TALL under board E.
# The cans are 9.5 mm (PA100V22M6X9, D6.3 x 9 + the sleeve); the XT30 and
# its mating plug are cut round regardless. Everything else on the outward
# face is under 3.2 mm (the USB-C), well inside GAP.
def is_can(p):
    return p["fp"].startswith("CP_Radial_D6.3")


# ------------------------------------------------------------ board E ----
def cutouts(S):
    """The holes and the notch board E needs, as (name, polygon, why)."""
    cuts = []
    cans = [p for p in S.values() if p["face"] == "F" and is_can(p)]
    discs = [(p["ref"], Point(p["x"], p["y"]).buffer(CAN_D / 2 + CUT_CLR, 48))
             for p in cans]
    # Two holes closer than 1.5 mm leave a web the fab will not hold: one
    # slot round both instead.
    groups = []
    for ref, d in discs:
        hit = [g for g in groups if g[1].distance(d) < 1.5]
        for g in hit:
            groups.remove(g)
        refs = sum((g[0] for g in hit), [ref])
        groups.append((refs, unary_union([d] + [g[1] for g in hit]).convex_hull))
    for refs, poly in groups:
        cuts.append(("can " + "+".join(sorted(refs)), poly,
                     "bulk can, 9.5 mm tall, through board E"))

    # The XT30 and its plug: the housing's courtyard swept out along its own
    # axis to past the edge -- the plug goes in that way, over board S, and
    # the wires leave the same way.
    j4 = S["J4"]
    ax = atan2(j4["y"], j4["x"])
    sweep = unary_union([affinity.translate(j4["cy"], d * cos(ax), d * sin(ax))
                         for d in [k * 0.5 for k in range(0, 61)]])
    cuts.append(("XT30 notch", sweep.convex_hull.buffer(CUT_CLR + 0.5),
                 "the XT30, its plug's path and the wires"))

    # BOOTSEL: a hole to press it through (B3U actuator, 1.6 mm tall, so a
    # pin through a 4 mm hole); the RGB status LED: a window to see it.
    sw = S["SW1"]
    cuts.append(("BOOTSEL hole", Point(sw["x"], sw["y"]).buffer(2.0, 32),
                 "press BOOTSEL with a pin"))
    led = S["D1103"]
    cuts.append(("LED window", Point(led["x"], led["y"]).buffer(1.6, 32),
                 "see board S's status LED"))

    # The six M2.5 bosses: a clearance hole over each; JACK_BOSSES carry the
    # standoffs that take the RJ45's insertion force.
    for ref in ("H10", "H11", "H12", "H13", "H14", "H15"):
        h = S[ref]
        cuts.append((f"M2.5 {ref}", Point(h["x"], h["y"]).buffer(G.PERIM_D / 2, 32),
                     "standoff screw" + (", carries the jack" if ref in JACK_BOSSES
                                         else ", optional third point")))
    return cuts


# Each footprint kind is loaded from KiCad's library once, and its courtyard
# and pads kept in its own frame; every candidate spot is then plain
# geometry. (Loading a footprint per candidate ran pcbnew out of the road:
# a segfault some thousands of loads in.)
_TEMPL = {}


def templ(kind):
    if kind not in _TEMPL:
        lib, name = FP[kind]
        f = pcbnew.FootprintLoad(str(FPLIB / f"{lib}.pretty"), name)
        f.BuildCourtyardCaches()

        def up(v):
            return pcbnew.ToMM(v.x), -pcbnew.ToMM(v.y)
        ps = f.GetCourtyard(pcbnew.F_CrtYd)
        polys = []
        for i in range(ps.OutlineCount()):
            ch = ps.Outline(i)
            polys.append(Polygon([up(ch.CPoint(k)) for k in range(ch.PointCount())]).buffer(0))
        pads = [(p.GetNumber(), *up(p.GetPosition()), pcbnew.ToMM(p.GetDrillSize().x))
                for p in f.Pads()]
        _TEMPL[kind] = (unary_union(polys), pads, f)
    return _TEMPL[kind]


def _xf(kind, x, y, rot, face):
    """Kind's courtyard and pads at (x, y), turned rot CCW; on the bottom
    face mirrored first, as seen from above."""
    cy, pads, _ = templ(kind)
    m = -1.0 if face == "B" else 1.0
    th = radians(rot)
    c, s_ = cos(th), sin(th)

    def T(px, py):
        px *= m
        return x + px * c - py * s_, y + px * s_ + py * c
    g = affinity.scale(cy, xfact=m, yfact=1.0, origin=(0, 0))
    g = affinity.rotate(g, rot, origin=(0, 0))
    g = affinity.translate(g, x, y)
    return dict(kind=kind, x=x, y=y, rot=rot % 360, face=face, cy=g,
                padlist=[(n, *T(px, py), d) for n, px, py, d in pads])


class Sketch:
    def __init__(self, S):
        self.S = S
        self.cuts = cutouts(S)
        disc = Point(0, 0).buffer(R, 256)
        self.outline = disc.difference(unary_union([c[1] for c in self.cuts]))
        self.usable = disc.buffer(-EDGE).difference(
            unary_union([c[1].buffer(EDGE) for c in self.cuts]))
        # the standoffs' bodies are under board E, but their hex is round the
        # screw on the top face too (a nut or the screw head): keep it clear
        self.standoff_keep = unary_union(
            [Point(S[r]["x"], S[r]["y"]).buffer(STANDOFF_R, 32)
             for r in ("H10", "H11", "H12", "H13", "H14", "H15")])
        self.parts = []
        self.missed = []
        self.nose = None

    # one footprint, placed in this sketch's frame
    def make(self, kind, x, y, rot, face="F"):
        return _xf(kind, x, y, rot, face)

    def drop(self, f):
        pass

    def legal(self, cy, face, edge_ok=False):
        if not edge_ok and not self.usable.contains(cy):
            return False
        if cy.intersects(self.standoff_keep):
            return False
        return not any(p["face"] == face and p["cy"].intersects(cy)
                       for p in self.parts)

    def record(self, ref, value, kind, f, note, face="F", **extra):
        d = dict(ref=ref, value=value, kind=kind, fp=":".join(FP[kind]),
                 face=face, x=round(f["x"], 3), y=round(f["y"], 3),
                 rot=round(f["rot"], 1) % 360, cy=f["cy"], note=note,
                 pads={n: (x, y) for n, x, y, _ in f["padlist"]},
                 padlist=f["padlist"])
        d.update(extra)
        self.parts.append(d)
        return d

    def put(self, ref, value, kind, x, y, rots=(0,), note="", face="F",
            reach=12.0, step=0.25):
        """The nearest legal spot to (x, y), in rings outward."""
        n = int(reach / step)
        cands = sorted(((i * step, j * step) for i in range(-n, n + 1)
                        for j in range(-n, n + 1)), key=lambda d: hypot(*d))
        for dx, dy in cands:
            if hypot(dx, dy) > reach:
                break
            for rot in rots:
                f = self.make(kind, x + dx, y + dy, rot, face)
                cy = f["cy"]
                if self.legal(cy, face):
                    return self.record(ref, value, kind, f, note, face,
                                       moved=round(hypot(dx, dy), 2))
                self.drop(f)
        self.missed.append((ref, value, note))
        return None


# --------------------------------------------------------------- the jack
# HR911105A in KiCad's footprint: body x -3.58..12.46, y -4.04..17.26 (KiCad's
# y down), the mouth at +y, pins 1-8 at the back. Its axis points radially
# out at angle a when the footprint is turned a + 90; the mouth's midpoint
# goes on the circle R + overhang.
#
# It is 21.3 mm deep, and the rim it has to use is the CPU wedge's, where
# board S's cans C1015 (R 11, 305 deg) and C1002 (R 18, 325 deg) stand in
# the way of its back half: there is no spot in 256-308 with its mouth on
# the edge. So the jack goes out until it clears them, and board E grows a
# NOSE under it to the mouth -- its LED pins, 6 mm behind the mouth, would
# otherwise be inside the 1 mm copper pull-back or off the board. The nose is
# a decision for the user (ethernet.html lists the other ways out).
JACK_MOUTH = (4.44, 17.26)
JACK_BODY = box(-3.58, -17.26, 12.46, 4.04)        # local, y up
JACK_CUT = 0.25         # courtyard to a cutout: KiCad's courtyard already
                        # stands 0.5 mm off the body
HEAD_R = 2.8            # an M2.5 pan head on board E's top face, and room


def place_jack(K):
    best = None
    cutbuf = unary_union([c[1].buffer(JACK_CUT) for c in K.cuts])
    disc = Point(0, 0).buffer(R - JACK_CUT, 256)
    heads = unary_union([Point(K.S[r]["x"], K.S[r]["y"]).buffer(HEAD_R, 32)
                         for r in ("H10", "H11", "H12", "H13", "H14", "H15")])
    bosses = [(K.S[r]["x"], K.S[r]["y"]) for r in ("H10", "H11", "H12", "H13", "H14", "H15")]
    a0, a1 = JACK_AT
    for k in range(int((a1 - a0) / 0.5) + 1):
        a = a0 + 0.5 * k
        for j in range(0, 25):
            over = 0.25 * j
            th = radians(a + 90)
            mx, my = JACK_MOUTH[0], -JACK_MOUTH[1]          # local, y up
            ox = mx * cos(th) - my * sin(th)
            oy = mx * sin(th) + my * cos(th)
            tx, ty = (R + over) * cos(radians(a)), (R + over) * sin(radians(a))
            f = K.make("RJ45", tx - ox, ty - oy, (a + 90) % 360)
            cy = f["cy"]
            # the mouth stands at (or over) the edge by design; the rest of it
            # must be on the board and clear of every cutout and standoff
            inside = cy.intersection(disc)
            # on top, clear of the cutouts and the screw heads; underneath,
            # its pins and pegs (about 3 mm proud) clear of the standoffs' hex
            ok = (not inside.intersects(cutbuf) and not cy.intersects(heads)
                  and all(hypot(px - bx, py - by) > STANDOFF_R + dr / 2 + 0.3
                          for _, px, py, dr in f["padlist"] if dr > 0
                          for bx, by in bosses))
            if ok:
                cost = (over, abs(a - G.WEDGE_ANG[1]))
                if best is None or cost < best[0]:
                    if best:
                        K.drop(best[1])
                    best = (cost, f, a, over)
                    continue
            K.drop(f)
        if best and best[0][0] == 0.0:
            pass
    if best is None:
        K.missed.append(("J1", "RJ45", "no angle in the CPU wedge clears the cans"))
        return None
    (_, f, a, over) = best
    if over > 0:
        body = affinity.translate(affinity.rotate(JACK_BODY, (a + 90) % 360, origin=(0, 0)),
                                  f["x"], f["y"])
        nose = body.difference(Point(0, 0).buffer(R - 2.0, 256)).convex_hull
        K.nose = nose
        K.outline = K.outline.union(nose)
        K.usable = K.usable.union(nose.buffer(-EDGE))
    return K.record("J1", "HR911105A", "RJ45", f,
                    "RJ45 with magnetics and LEDs; mouth radial, past the rim on a nose"
                    if over else "RJ45 with magnetics and LEDs; mouth radial at the rim",
                    axis_deg=a, overhang_mm=over)


# ------------------------------------------------------------- the socket
def place_socket(K):
    """Under board E, contact for contact over J13."""
    j13 = K.S["J13"]
    hc = (j13["x"], j13["y"])
    hp = j13["pads"]

    def unit(v):
        n = hypot(*v)
        return (v[0] / n, v[1] / n)
    for rot in (0, 90, 180, 270):
        f = K.make("SOCK", hc[0], hc[1], rot, face="B")
        sp = {n: (x, y) for n, x, y, _ in f["padlist"]}
        good = all(
            sum(a * b for a, b in zip(
                unit((sp[n][0] - hc[0], sp[n][1] - hc[1])),
                unit((hp[n][0] - hc[0], hp[n][1] - hc[1])))) > 0.98
            for n in hp if n in sp)
        if good:
            return K.record("J2", "HC-PM127-4.3H-2x10PS", "SOCK", f,
                            "mates with board S's J13, contact over contact",
                            face="B", nets={n: j13["nets"][n] for n in j13["nets"]})
        K.drop(f)
    K.missed.append(("J2", "socket", "no orientation matches J13"))
    return None


# ------------------------------------------------------------ the rest
def local(d, dx, dy):
    """A point dx, dy from part d, in d's own frame (y up, as placed)."""
    th = radians(d["rot"])
    return (d["x"] + dx * cos(th) - dy * sin(th),
            d["y"] + dx * sin(th) + dy * cos(th))


def layout():
    S = board_s()
    K = Sketch(S)
    jack = place_jack(K)
    sock = place_socket(K)
    a = jack["axis_deg"]
    ux, uy = cos(radians(a)), sin(radians(a))          # radially out, the jack
    vx, vy = -uy, ux                                    # across it, CCW

    # The jack's back: pins 1-8 across it at local y 0..-2.54 (KiCad's)
    back = local(jack, 4.44, 1.27)
    # W5500: behind the jack, its network side (pins 1-12) toward it, its SPI
    # and crystal side (pins 25-36) toward the socket
    w = K.put("U1", "W5500", "LQFP48", back[0] - ux * 7.5, back[1] - uy * 7.5,
              rots=[(a + 180) % 360], note="Ethernet controller, LQFP-48 7x7",
              reach=10)

    def wl(dx, dy):
        """In the W5500's frame: -x faces the jack, +x the socket."""
        return local(w, dx, dy)

    K.put("Y1", "25MHz", "X3225", *wl(7.0, -2.0), rots=[(a + 180) % 360, (a + 270) % 360],
          note="25 MHz, 20 pF load; at XI/XO")
    for i, (dx, dy) in enumerate([(4.6, -5.0), (6.3, -4.6)]):
        K.put(f"C{20 + i}", "33p", "C0402", *wl(dx, dy), rots=[(a + 90) % 360],
              note="crystal load", reach=6)
    # the network side: terminations and the centre-tap caps between the
    # W5500 and the jack's pins
    for i, dy in enumerate([3.0, 1.8, -1.8, -3.0]):
        K.put(f"R{1 + i}", "49R9", "R0402", *wl(-6.6, dy), rots=[(a + 90) % 360, a % 360],
              note="TX/RX termination, 1 %", reach=6)
    K.put("C1", "6n8", "C0402", *wl(-6.6, 0.0), rots=[(a + 90) % 360],
          note="RX termination midpoint", reach=6)
    K.put("C2", "10n", "C0402", *wl(-7.8, 0.6), rots=[(a + 90) % 360],
          note="transformer centre tap", reach=6)
    K.put("R5", "12k4", "R0402", *wl(-5.0, -5.6), rots=[a % 360, (a + 90) % 360],
          note="EXRES1, 1 %", reach=6)
    # supply decoupling round the package, one per AVDD/VDD pin
    for i, (dx, dy) in enumerate([(-5.0, 5.6), (-2.0, 5.8), (1.0, 5.8),
                                  (-2.0, -5.8), (1.0, -5.8), (4.0, -5.8),
                                  (5.8, 2.0)]):
        K.put(f"C{3 + i}", "100n", "C0402", *wl(dx, dy),
              rots=[a % 360, (a + 90) % 360], note="AVDD/VDD decoupling", reach=6)
    K.put("C10", "4u7", "C0603", *wl(4.0, -6.6), rots=[a % 360, (a + 90) % 360],
          note="TOCAP", reach=6)
    K.put("C11", "10n", "C0402", *wl(-0.5, -7.0), rots=[a % 360, (a + 90) % 360],
          note="1V2O", reach=6)
    K.put("C12", "10u", "C0603", *wl(4.5, 6.2), rots=[a % 360, (a + 90) % 360],
          note="3.3 V bulk", reach=6)
    K.put("FB1", "600R@100MHz", "L0603", *wl(-4.5, 7.4), rots=[a % 360, (a + 90) % 360],
          note="3.3 V to AVDD", reach=10)

    # 3.3 V: the LDO by the socket's +5V contact (J13 pin 12)
    p12 = sock["pads"]["12"]
    u2 = K.put("U2", "ME6211C33M5G-N", "SOT235", p12[0], p12[1] + 3.0,
               rots=[0, 90, 180, 270], note="3.3 V, 500 mA, from J13's +5V", reach=8)
    K.put("C13", "1u", "C0402", *local(u2, -2.6, 0), rots=[90, 0], note="LDO in", reach=4)
    K.put("C14", "1u", "C0402", *local(u2, 2.6, 0), rots=[90, 0], note="LDO out", reach=4)
    K.put("C15", "10u", "C0603", *local(u2, 0, 3.2), rots=[0, 90], note="3.3 V bulk", reach=5)

    # pull-ups: SCSn, INTn, RSTn, at the socket's GPIO21, 19, 24 contacts
    for i, (pin, what) in enumerate([("16", "SCSn"), ("13", "INTn"), ("11", "RSTn")]):
        x, y = sock["pads"][pin]
        K.put(f"R{6 + i}", "10k", "R0402", x, y + 3.0, rots=[0, 90],
              note=f"{what} pull-up (J13 pin {pin})", reach=6)

    # the jack's two LEDs, their resistors beside its LED pins (9-12)
    for i, (num, side) in enumerate([("9", -1), ("12", 1)]):
        lx, ly = jack["pads"][num]
        K.put(f"R{9 + i}", "330R", "R0402", lx - ux * 2.0 + side * vx * 1.5,
              ly - uy * 2.0 + side * vy * 1.5, rots=[a % 360, (a + 90) % 360],
              note="jack LED", reach=8)

    # the shield: 1 nF / 2 kV and 1 M to GND, by a shield peg
    sx, sy = jack["pads"]["SH"]
    K.put("C16", "1n/2kV", "C1206", sx - ux * 4.0, sy - uy * 4.0,
          rots=[a % 360, (a + 90) % 360], note="shield to GND", reach=8)
    K.put("R11", "1M", "R0603", sx - ux * 6.5, sy - uy * 6.5,
          rots=[a % 360, (a + 90) % 360], note="shield to GND, bleed", reach=8)

    # reset, SWD: where a finger and a probe can get at them, away from the jack
    opp = (G.WEDGE_ANG[1] + 180) % 360
    K.put("SW1", "RESET", "B3U", 20 * cos(radians(opp)), 20 * sin(radians(opp)),
          rots=[opp - 90], note="board S's RUN (J13 pin 9)", reach=10)
    for i, (name, pin) in enumerate([("SWCLK", "19"), ("SWDIO", "20"),
                                     ("RUN", "9"), ("GND", "10")]):
        x, y = 12 * cos(radians(opp - 40)), 12 * sin(radians(opp - 40))
        K.put(f"TP{1 + i}", name, "TP", x + 2.0 * i, y, note=f"J13 pin {pin}", reach=8)
    return K


# ------------------------------------------------------------- report ----
def report(K):
    j = next(p for p in K.parts if p["ref"] == "J1")
    print(f"board E: {len(K.parts)} parts placed, {len(K.missed)} missed")
    print(f"  RJ45 axis {j['axis_deg']:.1f} deg, mouth {j['overhang_mm']:.2f} mm "
          f"past the edge" + (f", on a nose of {K.nose.area:.0f} mm2" if K.nose else ""))
    for p in K.parts:
        print(f"  {p['ref']:5s} {p['value']:22s} {p['face']} ({p['x']:6.2f}, {p['y']:6.2f}) "
              f"rot {p['rot']:5.1f}  {p['note']}")
    for m in K.missed:
        print("  MISSED", m)
    area = K.outline.area
    print(f"  outline {area:.0f} mm2 of {3.14159 * R * R:.0f}; "
          f"cutouts: {', '.join(c[0] for c in K.cuts)}")


# ---------------------------------------------------------------- svg ----
SVG_STYLE = """
  :root { --bg:#ffffff; --panel:#f4f2ec; --ink:#1e2126; --muted:#6b7280; --line:#c9c4b8;
          --accent:#2f6fb0; --newfill:#d6e6f7; --warn:#a05a00; --s:#9aa0a8; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#1e2128; --panel:#262a33; --ink:#e8e6e1; --muted:#9aa3af; --line:#3d434f;
            --accent:#7db4ea; --newfill:#23384f; --warn:#e0a44c; --s:#6b7280; } }
  .bg    { fill: var(--bg); }
  .board { fill: var(--panel); stroke: var(--ink); stroke-width: .3; fill-rule: evenodd; }
  .sdisc { fill: none; stroke: var(--s); stroke-width: .15; stroke-dasharray: 1 .6; }
  .spart { fill: var(--s); fill-opacity: .18; stroke: var(--s); stroke-width: .08; }
  .can   { fill: var(--warn); fill-opacity: .25; stroke: var(--warn); stroke-width: .15; }
  .part  { fill: var(--newfill); stroke: var(--accent); stroke-width: .14; }
  .big   { fill: var(--newfill); stroke: var(--accent); stroke-width: .25; }
  .under { fill: none; stroke: var(--accent); stroke-width: .18; stroke-dasharray: .7 .4; }
  .pad   { fill: var(--accent); fill-opacity: .55; }
  .keep  { fill: none; stroke: var(--warn); stroke-width: .12; stroke-dasharray: .5 .4; }
  .wedge { fill: none; stroke: var(--accent); stroke-width: .1; stroke-dasharray: .8 .6; }
  text   { font-family: ui-sans-serif, system-ui, sans-serif; fill: var(--ink); }
  .h     { font-size: 2.1px; font-weight: 600; }
  .sub   { font-size: 1.3px; fill: var(--muted); }
  .lb    { font-size: 1.15px; font-weight: 600; }
  .ls    { font-size: 0.8px; fill: var(--muted); }
  .lc    { font-size: 0.9px; fill: var(--warn); }
"""


def _path(poly):
    def ring(cs):
        pts = list(cs)
        return "M" + " L".join(f"{x:.3f},{-y:.3f}" for x, y in pts) + " Z"
    polys = getattr(poly, "geoms", [poly])
    out = []
    for p in polys:
        out.append(ring(p.exterior.coords))
        out += [ring(i.coords) for i in p.interiors]
    return " ".join(out)


def _t(x, y, s, cls, anchor="middle"):
    s = s.replace("&", "&amp;").replace("<", "&lt;")
    return f'<text x="{x:.2f}" y="{-y:.2f}" class="{cls}" text-anchor="{anchor}">{s}</text>'


def render(K, path):
    S = K.S
    s = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="-38 -43 118 82" '
         'width="1180" height="820" role="img">',
         '<title>Board E placement sketch</title>',
         f'<style>{SVG_STYLE}</style>',
         '<rect class="bg" x="-38" y="-43" width="118" height="82"/>']
    # board S underneath: its outward face, faint, and the cans
    s.append(f'<circle class="sdisc" cx="0" cy="0" r="{R}"/>')
    for p in S.values():
        if p["face"] != "F" or p["cy"] is None:
            continue
        cls = "can" if is_can(p) else "spart"
        s.append(f'<path class="{cls}" d="{_path(p["cy"])}"/>')
    # board E: outline with its cutouts
    s.append(f'<path class="board" d="{_path(K.outline)}"/>')
    # the jack's wedge, 256-308
    for ang in JACK_AT:
        s.append(f'<line class="wedge" x1="0" y1="0" x2="{R * cos(radians(ang)):.2f}" '
                 f'y2="{-R * sin(radians(ang)):.2f}"/>')
    # through the cutouts board S shows again: redraw S's parts inside them
    holes = unary_union([c[1] for c in K.cuts]).intersection(Point(0, 0).buffer(R, 256))
    for p in S.values():
        if p["face"] == "F" and p["cy"] is not None:
            q = p["cy"].intersection(holes)
            if not q.is_empty and q.area > 0.01:
                cls = "can" if is_can(p) else "spart"
                s.append(f'<path class="{cls}" d="{_path(q)}"/>')
    s.append(f'<path class="keep" d="{_path(K.standoff_keep)}"/>')
    # board E's parts
    for p in K.parts:
        cls = "under" if p["face"] == "B" else ("big" if p["kind"] in ("RJ45", "LQFP48") else "part")
        tip = f'{p["ref"]} {p["value"]}: {p["note"]}'.replace("&", "&amp;").replace("<", "&lt;")
        s.append(f'<g><title>{tip}</title><path class="{cls}" d="{_path(p["cy"])}"/></g>')
    if K.nose is not None:
        c = K.nose.centroid
        s.append(_t(c.x * 1.07, c.y * 1.07 - 0.4, "nose", "lc"))
    for p in K.parts:
        if p["kind"] in ("RJ45", "LQFP48", "SOCK", "X3225", "SOT235", "B3U"):
            for (_, x, y, _) in p["padlist"]:
                s.append(f'<circle class="pad" cx="{x:.2f}" cy="{-y:.2f}" r=".22"/>')
    # labels
    lab = {"J1": "RJ45", "U1": "W5500", "J2": "socket (underneath)", "Y1": "25 MHz",
           "U2": "LDO", "SW1": "reset"}
    for p in K.parts:
        c = p["cy"].centroid
        if p["ref"] in lab:
            s.append(_t(c.x, c.y + 0.4, lab[p["ref"]], "lb"))
            s.append(_t(c.x, c.y - 0.9, p["ref"], "ls"))
    tps = [p for p in K.parts if p["kind"] == "TP"]
    if tps:
        c = unary_union([p["cy"] for p in tps]).centroid
        s.append(_t(c.x, c.y - 1.7, "SWD / RUN pads", "ls"))
    for name, poly, why in K.cuts:
        c = poly.intersection(Point(0, 0).buffer(R - 1, 256)).centroid
        if name.startswith("M2.5"):
            continue
        txt = {"XT30 notch": "XT30", "BOOTSEL hole": "BOOTSEL", "LED window": "LED"}.get(
            name, "can")
        s.append(_t(c.x, c.y - 0.3, txt, "lc"))
    # the key, right
    j = next(p for p in K.parts if p["ref"] == "J1")
    kx = 36
    s += [_t(kx, 36, "Board E — placement sketch", "h", "start"),
          _t(kx, 33.6, "seen from above, board S's frame; Ø65, ~6 mm over board S", "sub", "start")]
    rows = [
        ("big", "board E's parts, top face"),
        ("under", "socket J2 on the underside, over board S's J13"),
        ("spart", "board S's outward face, below"),
        ("can", "board S's bulk cans, 9.5 mm: through board E"),
        ("keep", "M2.5 standoff hex, kept clear"),
    ]
    for i, (cls, txt) in enumerate(rows):
        y = 30 - i * 2.6
        s.append(f'<rect class="{cls}" x="{kx}" y="{-y - 1.1:.2f}" width="2.4" height="1.5"/>')
        s.append(_t(kx + 3.2, y - 0.9, txt, "sub", "start"))
    facts = [
        f"{len(K.parts)} parts placed, {len(K.missed)} not placed",
        f"RJ45 axis {j['axis_deg']:.1f}°, mouth "
        + (f"{j['overhang_mm']:.2f} mm past the edge" if j["overhang_mm"] else "on the edge"),
        f"cutouts: {sum(1 for c in K.cuts if c[0].startswith('can'))} can holes/slots, "
        "XT30 notch, BOOTSEL, LED",
        "standoffs at H14 (256°) and H15 (308°); holes at all six bosses",
        "board S's M3 heads stay under board E (3 mm < 6 mm)",
    ]
    for i, t in enumerate(facts):
        s.append(_t(kx, 14 - i * 2.2, t, "sub", "start"))
    s.append('</svg>')
    Path(path).write_text("\n".join(s) + "\n")


def save_json(K, path):
    out = dict(parts=[{k: v for k, v in p.items() if k not in ("cy", "pads", "padlist")}
                      for p in K.parts],
               missed=K.missed,
               cutouts=[dict(name=n, why=w, bounds=[round(v, 2) for v in p.bounds])
                        for n, p, w in K.cuts],
               outline_mm2=round(K.outline.area, 1))
    Path(path).write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    K = layout()
    report(K)
    render(K, ROOT / "img/board_e_plan.svg")
    save_json(K, ROOT / "img/board_e_plan.json")
