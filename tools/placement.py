#!/usr/bin/env python3
"""placement.py — where every part on board A sits, and the checks that say so.

The floorplan is polar, because the board is. A part is placed at (r, s, rot):

    r    radius of the part's COURTYARD CENTRE, mm from the shaft axis
    s    tangential offset from its block's centre line, mm of arc at that r
    rot  degrees added to the block angle. rot = 0 points the footprint's +x
         axis radially outward, +y counter-clockwise; rot = 90 turns the part
         so its +x runs tangentially.

Parts are packed into ROWS. A row is a radius and everything in it is placed
outward from the centre line in order, each part clearing the last by CLEAR.
Rows whose radial extents do not overlap cannot collide, which removes most of
the ways a hand-placed round board goes wrong; the rest is checked explicitly.

    python3 tools/placement.py        # the floorplan report

Nothing here knows about KiCad. gen_boards.py turns these coordinates into
placed footprints.
"""
import re
from math import cos, sin, atan2, radians, degrees, hypot
from pathlib import Path

import geometry as G

ROOT  = Path(__file__).resolve().parent.parent
KFP   = Path("/usr/share/kicad/footprints")
SIS   = Path("/home/sequoia/pcb/rp2350-motor-controller/hardware/parts")
LOCAL = ROOT / "hardware/parts/servodrive.pretty"

CLEAR = 0.30          # mm between courtyards of parts in the same row

# --------------------------------------------------------------- library ----
LIBS = {"servodrive": LOCAL, "parts_motor": SIS}

def fp_path(spec):
    lib, name = spec.split(":", 1)
    base = LIBS.get(lib)
    p = (base / f"{name}.kicad_mod") if base else (KFP / f"{lib}.pretty" / f"{name}.kicad_mod")
    if not p.exists():
        raise FileNotFoundError(f"{spec} -> {p}")
    return p

_CY = {}

def courtyard(spec):
    """(width, height, cx, cy) of the courtyard in footprint coordinates.

    Header footprints put their origin on pin 1 rather than in the middle, so
    the offset matters: this module places courtyard centres, not origins.
    """
    if spec in _CY:
        return _CY[spec]
    t = fp_path(spec).read_text()
    xs, ys = [], []
    for m in re.finditer(r'\(fp_(?:line|rect|poly|circle|arc)\b'
                         r'((?:[^()]|\([^()]*(?:\([^()]*\)[^()]*)*\))*)\)', t, re.S):
        if "CrtYd" not in m.group(1):
            continue
        blk = m.group(1)
        pts = [(float(c.group(1)), -float(c.group(2)))       # y-down -> y-up
               for c in re.finditer(r'\((?:start|end|mid|center|xy) '
                                    r'([-\d.]+) ([-\d.]+)\)', blk)]
        if m.group(0).lstrip().startswith("(fp_circle") and len(pts) >= 2:
            # centre + a point on the rim: the extent is the bounding square,
            # not those two points. A test point read 1.00 x 0.00 before this.
            (cx0, cy0), (ex, ey) = pts[0], pts[1]
            r = ((ex - cx0) ** 2 + (ey - cy0) ** 2) ** 0.5
            pts = [(cx0 - r, cy0 - r), (cx0 + r, cy0 + r)]
        for x, y in pts:
            xs.append(x); ys.append(y)
    if not xs:
        raise ValueError(f"{spec}: no courtyard layer")
    _CY[spec] = (max(xs) - min(xs), max(ys) - min(ys),
                 (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2)
    return _CY[spec]

_THT = {}

def tht_pads(spec):
    """(x, y, w, h) of every through-hole pad in a footprint."""
    if spec in _THT:
        return _THT[spec]
    t = fp_path(spec).read_text()
    out = []
    for m in re.finditer(r'\(pad\s+"[^"]*"\s+(?:thru_hole|np_thru_hole)\s+\w+\s*'
                         r'\(at ([-\d.]+) ([-\d.]+)[^)]*\)\s*\(size ([\d.]+) ([\d.]+)\)',
                         t, re.S):
        out.append((float(m.group(1)), float(m.group(2)),
                    float(m.group(3)), float(m.group(4))))
    _THT[spec] = out
    return out

def extents(spec, rot):
    """(radial half-height, tangential half-width) of a courtyard at rot."""
    w, h, _, _ = courtyard(spec)
    a = radians(rot)
    rad = (abs(w * cos(a)) + abs(h * sin(a))) / 2
    tan = (abs(w * sin(a)) + abs(h * cos(a))) / 2
    return rad, tan

# ----------------------------------------------------------------- parts ----
class Part:
    __slots__ = ("ref", "value", "fp", "layer", "r", "s", "rot", "block",
                 "note", "dnp", "x", "y", "ang")

    def __init__(self, ref, value, fp, r, s, rot, block, block_ang,
                 layer="F.Cu", note="", dnp=False):
        self.ref, self.value, self.fp = ref, value, fp
        self.layer, self.block, self.note, self.dnp = layer, block, note, dnp
        self.r, self.s, self.rot = r, s, rot
        da = degrees(s / r) if r > 1e-6 else 0.0
        phi = block_ang + da
        self.ang = (phi + rot) % 360.0
        w, h, cx, cy = self._cy()
        a = radians(self.ang)
        ox, oy = polar_xy(phi, r)
        self.x = ox - (cx * cos(a) - cy * sin(a))
        self.y = oy - (cx * sin(a) + cy * cos(a))

    @classmethod
    def at_xy(cls, ref, value, fp, x, y, ang, block, layer="F.Cu", note="",
              dnp=False):
        """A part at explicit board coordinates: courtyard centre at (x, y),
        footprint +x pointing along `ang`. For the parts that are laid out in
        another part's frame -- the RP2350's regulator cluster is copied from
        Raspberry Pi's minimal design in the chip's own coordinates."""
        p = cls.__new__(cls)
        p.ref, p.value, p.fp = ref, value, fp
        p.layer, p.block, p.note, p.dnp = layer, block, note, dnp
        p.ang = ang % 360.0
        p.r, p.s, p.rot = hypot(x, y), 0.0, 0.0
        w, h, cx, cy = p._cy()
        a = radians(p.ang)
        p.x = x - (cx * cos(a) - cy * sin(a))
        p.y = y - (cx * sin(a) + cy * cos(a))
        return p

    def _cy(self):
        """Courtyard in the part's own frame. A back-side footprint is stored
        mirrored about its local x axis, so its offset centre mirrors too."""
        w, h, cx, cy = courtyard(self.fp)
        return (w, h, cx, -cy if self.layer.startswith("B.") else cy)

    @property
    def rect(self):
        w, h, cx, cy = self._cy()
        a = radians(self.ang)
        return ((self.x + cx * cos(a) - cy * sin(a),
                 self.y + cx * sin(a) + cy * cos(a)), w / 2, h / 2, a)

    def corners(self):
        (gx, gy), hw, hh, a = self.rect
        ca, sa = cos(a), sin(a)
        return [(gx + dx * ca - dy * sa, gy + dx * sa + dy * ca)
                for dx, dy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))]

    def tht(self):
        """Through-hole pads, in board coordinates, as (x, y, radius).

        These cost real estate on BOTH faces. Sixteen of them sit inside every
        FET drain pad, which is exactly the collision a same-layer courtyard
        check cannot see.
        """
        out = []
        a = radians(self.ang)
        ca, sa = cos(a), sin(a)
        mirror = -1 if self.layer.startswith("B.") else 1
        for px, py, w, h in tht_pads(self.fp):
            ly = mirror * -py            # file is y-down; this module is y-up
            out.append((self.x + px * ca - ly * sa,
                        self.y + px * sa + ly * ca, max(w, h) / 2))
        return out

def polar_xy(ang_deg, r):
    a = radians(ang_deg)
    return (r * cos(a), r * sin(a))

class Row:
    """A band at one radius. Parts pack outward from the centre line."""

    def __init__(self, block, block_ang, r, layer="F.Cu", s0=0.0, avoid=()):
        self.block, self.block_ang, self.r, self.layer = block, block_ang, r, layer
        self.edge = {+1: s0, -1: -s0}     # next free |s| on each side
        self.parts = []
        # Parts in OTHER rows this one has to dodge. Rows at different radii
        # usually cannot touch, but two rows that overlap radially and sit at
        # different angles can, and a packer that only looks at its own row
        # walks straight into them.
        self.avoid = [a for a in avoid if a.layer == layer]

    def centre(self, ref, value, fp, rot=0, note="", dnp=False):
        """Place one part straddling the centre line."""
        _, tan = extents(fp, rot)
        assert self.edge[+1] == 0.0 and self.edge[-1] == 0.0, "centre must come first"
        self.edge[+1], self.edge[-1] = tan + CLEAR, -(tan + CLEAR)
        return self._emit(ref, value, fp, 0.0, rot, note, dnp)

    def add(self, side, ref, value, fp, rot=0, note="", dnp=False):
        """Place one part on the given side (+1 ccw, -1 cw), next in line.

        Arc spacing alone is not enough. Neighbours in a row sit at different
        polar angles, so they are also rotated relative to each other, and two
        rectangles 0.3 mm apart along the arc can still have corners inside one
        another. So: propose a position, then walk it outward until the
        courtyards genuinely clear.
        """
        _, tan = extents(fp, rot)
        s = self.edge[side] + side * tan
        obst = bosses()
        for _ in range(400):
            cand = self._make(ref, value, fp, s, rot, note, dnp)
            if (not any(_overlap(cand, q) for q in self.parts + self.avoid)
                    and not any(_rect_circle(cand, *o) for o in obst)):
                break
            s += side * 0.05
        else:
            raise RuntimeError(f"{ref}: cannot clear row r={self.r} in {self.block}")
        self.edge[side] = s + side * (tan + CLEAR)
        self.parts.append(cand)
        return cand

    def _make(self, ref, value, fp, s, rot, note, dnp):
        return Part(ref, value, fp, self.r, s, rot, self.block, self.block_ang,
                    self.layer, note, dnp)

    def _emit(self, ref, value, fp, s, rot, note, dnp):
        p = self._make(ref, value, fp, s, rot, note, dnp)
        self.parts.append(p)
        return p

# ------------------------------------------------------------- floorplan ----
# Radial bands on board A's outward face. The thermal ring land is what makes
# them tight: every part has to finish inside R_RING_ID so the aluminium ring
# has an unbroken annulus of bare copper to clamp. That land is the 20 A
# rating -- without it the stack is a 15 K/W sealed sandwich (spec sec.4).
R_RING_ID = 29.2
R_RING_OD = G.R_USABLE                # 31.5

R_DRV     = 19.9                      # gate-drive row      r 17.2 .. 22.6
R_FET     = 26.1                      # half-bridge row     r 23.3 .. 28.9
R_DCLINK  = 24.8                      # DC-link ceramic, beside the FET's drain pad.
                                      # Any further out and the bulk cap's outer
                                      # corner is inside the perimeter boss keepout
FET_S     = 5.65                      # tangential half-spacing: 4.7 mm SW island,
                                      # wide enough for both shunts on the back

R_SW_B0   = 20.0                      # B.Cu: inner edge of the switch-node pour
R_SENSE_B = 19.3                      # B.Cu: amplifier, bleeds, bootstrap diode
R_SHUNT  = 24.85                     # B.Cu: the inline shunt pair, radial, side
S_SHUNT  = (-2.75, 0.65)             #       by side under the switch-node island,
                                     #       between the two FETs' via clusters
R_TAP_PH = 25.0                      # B.Cu: the PHASE-side sense tap, its outer
S_TAP_PH = 7.65                      #       pad over the phase-output pour, 0.63 mm
                                     #       clear of the low-side drain pad above
R_CLAMP_B = 29.8                     # B.Cu: the clamp, beside the lead pad, on the
S_CLAMP_B = 6.5                      #       pour's reach into the channel
S_SENSE_1 = 3.8                      # B.Cu: the sense row's counter-clockwise side
                                     #       starts past the driver's VS via above
S_BOOT_D  = 10.5                     # B.Cu: the bootstrap diode, past the bootstrap
                                     #       cap's SW via coming through at s 9.05
R_SNUB    = 22.0                     # B.Cu: the RC snubber, SW -> GND, a tangential
S_SNUB_C  = -9.26                    #       pair under the high-side FET, in the one
S_SNUB_R  = -6.0                     #       strip of switch-node pour nothing wants
# The switch-node island on F.Cu: from just past the high-side source row,
# clockwise of the centre line, to just short of the low-side source row.
S_SW_F0 = -(FET_S - 2.9) - 0.6
S_SW_F1 = (FET_S + 2.9) - 0.85
S_SW_B    = 10.0                     # B.Cu: the switch-node pour's half-width
# Its inner edge is R 20.0 only where a pad has to sit over it -- the
# high-side bleed and SW tap (s -5.5..-2.0), the driver's VS pin (1.6..3.4)
# and the bootstrap cap (8.4..10) -- and R 21.6 elsewhere, so the sense
# amplifier's and filter's outer pads are not walled in by it. Its outer
# edge steps in to R 23.3 beside the PHASE tap for the same reason.
SW_B_NOTCH_IN  = [(-2.0, 1.6, 22.3), (3.4, 8.4, 21.6)]   # (s0, s1, r0)
R_BLEED   = 20.85                    # B.Cu: the high-side bleed, radial, directly under
                                     #       the gate resistor: one via joins both
R_BLEED_L = 23.9                     # B.Cu: the low-side bleed, radial, its gate pad
                                     #       under the gate's radial track just inside the
                                     #       arc's corner and its ground pad beyond the corner
SW_B_NOTCH_OUT = [(6.9, 8.4, 23.3)]                        # (s0, s1, r1)
R_PH0, R_PH1, R_PH2 = 25.7, 28.9, 30.6   # phase-output pour: band, and its reach
S_PH_NOTCH = 4.5                     #   into the channel either side of the lead pad

# The motor lead lands on an ARC pad, not a rectangle: a straight pad wide
# enough for 14 AWG at this radius puts its corners outside the board. Same
# reason the heatsink land is an arc.
PAD_R0, PAD_R1, PAD_HALF = 28.5, 31.3, 7.0      # mm, mm, degrees
BOSS_PAD = 4.8                                   # ThermalBoss_M2.5 copper

def bosses():
    """The six perimeter bosses, as (x, y, keepout radius)."""
    return [(*polar_xy(a, G.PERIM_BC / 2), BOSS_PAD / 2 + 0.25)
            for a in G.PERIM_ANG]

def sectors():
    """Arc copper that is not a Part: (layer, r0, r1, centre angle, half span)."""
    out = []
    for th in G.PHASE_ANG:
        out.append(("F.Cu", R_RING_ID, R_RING_OD, th, RING_HALF))
        out.append(("B.Cu", PAD_R0, PAD_R1, th, PAD_HALF))
    return out

RING_HALF = G.PHASE_SPAN / 2 - 6.0    # heatsink land half-span: up to the
                                      # cell edge, less the boss pad and margin

PHASE_NAMES = ["A", "B", "C"]
FET_FP = "servodrive:TDSON-8-1_ThermalVias"          # low side: 4 x 4 barrels
FET_FP_HS = "servodrive:TDSON-8-1_ThermalVias_HS"    # high side: 4 x 5

def phase_cell(i):
    """One phase cell: half-bridge, driver, gate network, DC link, sense.

    Laid out so that every connection the router has to make is a short one
    with a clear approach, and every pad on a 20 A net sits over its own
    pour and takes a via instead of a track. The two things that decided the
    shape of it, both found by routing the previous arrangement:

      - the FET's gate pad is at one END of its source-pad row. With the row
        pointing outward the gate sat at R 27.9, walled in by the switch-node
        pour on three sides and the heatsink land on the fourth. Both FETs are
        turned 180 deg, which swaps their sides and puts the gate pads at the
        INNER end, R 24.4, facing the driver row across an empty 1.5 mm.
      - the driver's outputs (LO, VS, HO, VB) are a radial column at its
        counter-clockwise end, so what they feed sits on that side, in the
        order the pins come: the gate resistors tangential so their pads sit
        at the height of the pins, then the bootstrap cap. The logic side
        gets the same treatment with the two pulls and the VCC 100 n.
    """
    n, th, P = i + 1, G.PHASE_ANG[i], PHASE_NAMES[i]
    blk, out = f"phase {P}", []

    # -- the half-bridge. Both FETs at rot = -90, so the footprint +x (drain)
    # points clockwise. Q_H sits clockwise of the centre line with its drain
    # (VBUS) at the far end and its source row (SW, then the gate) facing the
    # centre; Q_L sits counter-clockwise with its drain (SW) facing the centre
    # and its source row (GND, then the gate) at the far end. The switch node
    # is the island between Q_H's sources and Q_L's drain.
    fet = Row(blk, th, R_FET)
    tan = extents(FET_FP, -90)[1]
    fet.edge = {+1: FET_S - tan, -1: -(FET_S - tan)}
    fet.add(-1, f"Q{2*n-1}", "BSC030N08NS5", FET_FP_HS, -90, f"phase {P} high side")
    fet.add(+1, f"Q{2*n}",   "BSC030N08NS5", FET_FP, -90, f"phase {P} low side")
    out += fet.parts

    # -- DC link, flanking the pair inside the commutation loop and pushed as
    # far out as the heatsink land allows. The 100 n goes in FIRST, because a
    # Row packs outward from the centre line and the high-frequency capacitor
    # is the one that has to be nearest the drain; the 2.2 u sits behind it.
    # VBUS is the high-side drain, clockwise; GND is the low-side source row,
    # counter-clockwise.
    dcl = Row(blk, th, R_DCLINK, avoid=fet.parts)
    dcl.edge = {+1: FET_S + tan + CLEAR, -1: -(FET_S + tan + CLEAR)}
    dcl.add(-1, f"C{n}03", "100n/100V", "Capacitor_SMD:C_0603_1608Metric", 0,
            "DC link, high frequency, at the high-side drain")
    dcl.add(+1, f"C{n}04", "100n/100V", "Capacitor_SMD:C_0603_1608Metric", 0,
            "DC link, high frequency, at the low-side source")
    # 1206, not 1210: with the FETs 1.4 mm further apart to make room for the
    # shunts on the back, a 1210 here runs into the perimeter boss.
    dcl.add(-1, f"C{n}01", "2.2u/100V", "Capacitor_SMD:C_1206_3216Metric", 0,
            "DC link bulk, behind the 100 n")
    dcl.add(+1, f"C{n}02", "2.2u/100V", "Capacitor_SMD:C_1206_3216Metric", 0,
            "DC link bulk, behind the 100 n")
    out += dcl.parts

    # -- gate driver at rot = 90: VCC, HIN, LIN, COM are a radial column at
    # the clockwise end (R 18.2, 19.4, 20.7, 21.9), LO, VS, HO, VB the same
    # column at the counter-clockwise end, VB innermost. Each side of the row
    # holds what that column feeds.
    drv = Row(blk, th, R_DRV)
    drv.centre(f"U{n}", "EG2103", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", 90,
               "600 V half bridge, interlock + 560 ns dead time")
    # Output side. The gate resistors lie TANGENTIALLY, so HO and LO reach a
    # pad at their own radius and the far pad points at the gate: GH sets off
    # from R 20.4 to the high-side gate at (24.4, -1.3), across the gap between
    # the rows; GL goes straight out to the low-side gate at (24.4, +7.1).
    # The bootstrap cap is radial, its SW pad over the B.Cu switch-node pour
    # so it stitches, its VB pad reached under the resistors from pin 8.
    drv.add(+1, f"R{n}01", "2R2", "Resistor_SMD:R_0402_1005Metric", 90,
            "gate, high side - damping, not edge shaping")
    drv.add(+1, f"R{n}02", "2R2", "Resistor_SMD:R_0402_1005Metric", 90, "gate, low side")
    drv.add(+1, f"C{n}05", "1u/25V", "Capacitor_SMD:C_0603_1608Metric", 0,
            "bootstrap cap; SW pad over the pour")
    # Logic side, same idea: the pulls tangential at the height of HIN and
    # LIN, the VCC 100 n radial at the end with pin 1 reached underneath.
    drv.add(-1, f"R{n}11", "4k7", "Resistor_SMD:R_0402_1005Metric", 90,
            "HIN pull-down to GND; also clears RP2350-E9")
    drv.add(-1, f"R{n}12", "4k7", "Resistor_SMD:R_0402_1005Metric", 90,
            "LIN pull-down to GND; brake at reset")
    drv.add(-1, f"C{n}06", "100n", "Capacitor_SMD:C_0603_1608Metric", 0,
            "VCC decoupling, at the driver")
    out += drv.parts

    # -- phase output, on the MOTOR-FACING side. The leads come off the motor on
    # that side, so they land there: no wrap-around, and the outward perimeter
    # stays free for the heatsink ring. The pad itself is an arc, placed by
    # gen_boards; what lives here is the shunt pair feeding it.
    # The shunts sit side by side under the switch-node island, between the
    # two FETs' via clusters, radial: SW pad at R 22.4 over the switch-node
    # pour, PHASE pad at R 27.0 in the phase-output pour, and the lead pad
    # directly beyond. Nowhere else on the back is clear of the DC-link
    # caps' vias coming through from the other face.
    two_m = "1m6" if i < 2 else "0R"
    for s, ref in zip(S_SHUNT, (f"R{n}06", f"R{n}05")):
        # rot = -da: both parallel to the cell axis rather than each pointing
        # at the shaft, so 0.3 mm apart really is 0.3 mm apart
        out.append(Part(ref, two_m, "Resistor_SMD:R_2010_5025Metric",
                        R_SHUNT, s, -degrees(s / R_SHUNT), blk, th, "B.Cu",
                        "inline shunt, parallel pair - desolder one for the "
                        "18 A mode" if i < 2 else
                        "0 R link, so phase C has the same series resistance"))

    # -- the PHASE-side sense tap: its outer pad over the phase-output pour,
    # in the one slot on the back the DC-link vias leave, past the low-side
    # via cluster. The SW-side tap is in the sense row below.
    dnp = (i == 2)
    # rot 180: pin 1, the PHASE end, is then the outer pad, over the pour
    out.append(Part(f"R{n}08", "10R", "Resistor_SMD:R_0603_1608Metric",
                    R_TAP_PH, S_TAP_PH, 180, blk, th, "B.Cu",
                    "sense tap, phase-output side", dnp=dnp))
    # -- the clamp, in the channel beside the lead pad, where the
    # phase-output pour reaches out to meet it.
    out.append(Part(f"D{n}02", "TPSMF4L64A", "Diode_SMD:D_SOD-123F",
                    R_CLAMP_B, S_CLAMP_B, 90, blk, th, "B.Cu",
                    f"phase {P} clamp to GND, beside the lead pad"))

    # -- current sense, the gate bleeds and the bootstrap diode, on the back
    # under the driver. Phase C is reconstructed, so its amplifier is a
    # footprint and nothing else.
    sb = Row(blk, th, R_SENSE_B, layer="B.Cu", avoid=out)
    # Turned 180: pins 5-8 (ISENSE, +3V3, +3V3, SNSP) face the centre, so the
    # two 3V3 pins are 1.3 mm from the +3V3 disc on In2 and tap it with a
    # straight stub; SNSN faces outward, toward its tap at the shunt.
    sb.centre(f"U{3+n}", "INA241A3", "Package_TO_SOT_SMD:TSOT-23-8", 180,
              "inline sense, 50 V/V - A3 is the 50 V/V grade"
              + (" - DNP, phase C is reconstructed" if dnp else ""),
              dnp=dnp)
    # The counter-clockwise side starts past s = 3.8: the driver's VS pin on
    # the other face is at s = 2.5 and its via to the switch-node pour needs
    # 0.63 mm of clear copper under it.
    sb.edge[+1] = S_SENSE_1
    # The gate bleeds sit directly beneath the gate resistors on the other
    # face, radial, their gate pad under the resistor's gate pad: one via
    # through both pads joins them, and the whole gate net is copper before
    # the router sees it (tools/fanout.py, gates). The SW / GND pad is the
    # outer one, over the switch-node pour or, for the low side, a via away
    # from the ground planes.
    # The resistor's gate pad is at s = h.s + 0.48 on R_DRV; the same
    # ANGLE at another radius is a different s, and the via and the gate's
    # radial track are at that angle.
    h = next(q for q in drv.parts if q.ref == f"R{n}01")
    out.append(Part(f"R{n}09", "10k", "Resistor_SMD:R_0603_1608Metric",
                    R_BLEED, (h.s + 0.48) * R_BLEED / R_DRV, 0, blk, th, "B.Cu",
                    "G-S bleed, high side; gate pad under the resistor's, SW pad over the pour"))
    # The low side's bleed goes to ground, and a ground pad wants a via,
    # which must not sit on the gate's radial track: so this one is radial
    # too, further out, its gate pad under the track just inside the arc's
    # corner at R 23.2 and its ground pad beyond the corner. (Lying across
    # the track at R 22 it cut the switch-node pour's finger to the
    # bootstrap cap.)
    h = next(q for q in drv.parts if q.ref == f"R{n}02")
    out.append(Part(f"R{n}10", "10k", "Resistor_SMD:R_0603_1608Metric",
                    R_BLEED_L, (h.s + 0.48) * R_BLEED_L / R_DRV, 0, blk, th, "B.Cu",
                    "G-S bleed, low side; gate pad under the gate track, via through both"))
    # Radial, a little further out than the row so it clears the centre
    # keepout: the cathode (VB) is the inner pad, on free copper, a via away
    # from the bootstrap cap's VB pad on the other face; the anode (+12V)
    # sits in the switch-node pour's reach and leaves by a via too.
    # SOD-323, not SOD-123: at 1.7 mA average and a few hundred mA of peak
    # charging current a 1N4148WS is ample, and the smaller body is what
    # keeps it inside the cell past the bootstrap cap's via.
    out.append(Part(f"D{n}01", "100V fast", "Diode_SMD:D_SOD-323", R_SENSE_B + 0.25,
                    S_BOOT_D, 0, blk, th, "B.Cu",
                    "bootstrap diode, beside the cap it charges"))
    # The sense filter and the switch-node-side tap on the clockwise side,
    # where SNSP is an arc between the amplifier's pad rows (fanout.sense).
    sb.add(-1, f"C{n}08", "1n", "Capacitor_SMD:C_0603_1608Metric", 0,
           "sense filter", dnp=dnp)
    sb.add(-1, f"R{n}07", "10R", "Resistor_SMD:R_0603_1608Metric", 0,
           "sense tap, switch-node side; SW pad over the pour", dnp=dnp)
    if i == 0:
        # Inside the sense row, radial, FET_TEMP the inner pad: in the row
        # it sat under the driver's logic-side pulls on the other face and
        # no via could reach it. At R 17.7 its pad takes a via straight
        # down; the ground pad is on the ground pour.
        out.append(Part("R901", "10k NTC", "Resistor_SMD:R_0603_1608Metric",
                        18.5, -6.1, 180, blk, th, "B.Cu",
                        "FET temperature -> ADC3; FET_TEMP pad inner, via in it"))
    out += sb.parts

    # -- the RC snubber, fitted only if the EG2103 turns out to be the strong
    # driver (sim P2, Q2/Q3: 73.9 V of V_ds in that corner, 62.7 V with this
    # across the switch node). On the back under the high-side FET, in a
    # line along the arc between the sense row and the FET's barrels: the
    # capacitor's clockwise pad is GND and takes a via, the resistor's
    # counter-clockwise pad sits on the switch-node pour, and the two facing
    # pads are joined by a small pour of their own (gen_boards.zones).
    # Tangential because radial they would take the room the high side's
    # fifth row of barrels needs, and 0603 rather than the 0805 the
    # simulation proposed because an 0805 pair does not fit between the sense
    # tap and the shunt. The ratings do not need 0805: 470 pF C0G at 100 V,
    # and the resistor takes C.V^2.f = 0.034 W with edge transients inside a
    # 0603's 75 V.
    out.append(Part(f"C{n}09", "470p/100V C0G", "Capacitor_SMD:C_0603_1608Metric",
                    R_SNUB, S_SNUB_C, 90, blk, th, "B.Cu",
                    "snubber C, GND pad clockwise with a via; DNP unless the driver is strong",
                    dnp=True))
    out.append(Part(f"R{n}13", "2R2", "Resistor_SMD:R_0603_1608Metric",
                    R_SNUB, S_SNUB_R, 90, blk, th, "B.Cu",
                    "snubber R, SW pad on the pour; DNP unless the driver is strong",
                    dnp=True))
    return out

def power_wedge():
    """230 deg: the board A <-> board B power link, and the bus divider.

    Two 2x5 rather than one 2x8. geometry.interconnect() puts 23.2 A RMS
    through this link at the design point; 8+8 pins at 3 A is 24 A, and 0.8 A
    of margin is not a continuous rating. 10+10 is 30 A.
    """
    th, out = G.WEDGE_ANG[0], []
    HDR = "Connector_PinHeader_2.54mm:PinHeader_2x05_P2.54mm_Vertical"
    for ref, r in (("J7", 20.2), ("J8", 26.6)):
        out.append(Part(ref, "PWR_LINK", HDR, r, 0, 0, "power link", th,
                        note="5 x VBUS, 5 x GND, ganged"))
    # The bus divider lives here because this is where VBUS is a plane under
    # the parts: the 56 k's VBUS pad sits over In2 and takes a via. In the
    # signal wedge it needed a 60 V track to reach any VBUS copper at all.
    # A radial column beside the headers, toward the CPU.
    # Beside J8, above J7's courtyard, in two short radial columns: the
    # wedge is only 15.4 mm wide at R 17 and the header takes 13.7 of it.
    for ref, val, r, s, note in (
            ("R806", "56k 0.1%", 25.0, 8.6, "bus divider, high leg, second half"),
            ("R801", "56k 0.1%", 28.3, 8.6,
             "bus divider, high leg -> VBUS over the plane; two in series for "
             "the working voltage"),
            ("R802", "4k7 0.1%", 25.0, 10.4,
             "bus divider, low leg: 79 V clamp -> 3.18 V, 60 V -> 2.42 V"),
            ("C801", "100n", 25.0, -8.6, "ADC2 filter")):
        fp = "Capacitor_SMD:C_0603_1608Metric" if ref[0] == "C" else \
             "Resistor_SMD:R_0603_1608Metric"
        out.append(Part(ref, val, fp, r, s, 0, "power link", th, note=note))
    return out

# The RP2350A. Its regulator cluster and decoupling are Raspberry Pi's own,
# copied from the RP2350A minimal design (RP-006440) in the chip's frame and
# rotated into place: the 3.3 uH inductor 3.8 mm from VREG_LX with the VIN,
# 1V1 and AVDD 4.7 u stacked beside it, a 33 R into VREG_AVDD, 27 R in series
# with USB, a 100 n at each IOVDD pin, and the DVDD pins joined by a ring of
# 1V1 copper under the chip. What is not copied is what a 6-layer board does
# better: IOVDD's plane is In2, 0.1 mm under In1's ground, and every 3V3 pad
# is one via into it.
#
# Everything else keeps out of a band around the chip so the 60 pins can fan
# out: 0.4 mm pitch cannot take a via per pin in place, the stubs have to
# spread first, and that takes about 2 mm on every side. The inner side faces
# the encoder keepout, where nothing but tracks may go, so it carries the
# pins that have no parts of their own: RS-485, the encoder, HIN/LIN A and B.
R_CPU     = 21.15
R_CPU_OUT = 29.9       # the row beyond the fan-out: crystal, flash

FP0402C = "Capacitor_SMD:C_0402_1005Metric"
# the reference design's 0402 with 0.47 x 0.53 pads: the VREG_LX neck
# passes between this capacitor's pads, and needs the 0.56 mm gap
FP0402CS = "servodrive:C_0402_1005Metric_small_pads"
FP0402R = "Resistor_SMD:R_0402_1005Metric"

# (ref, value, footprint, dx, dy, rot, note) in the CHIP's frame, KiCad
# convention: +x is the footprint's +x (here: radially outward), +y is down on
# an unrotated sheet (here: the clockwise side, pins 16-30). Positions of the
# regulator cluster are the reference design's, to the 0.05 mm.
CPU_CLUSTER = [
    # -- the core regulator, pins 46-50, counter-clockwise side
    ("L701", "3u3",  "servodrive:L_pol_2016", 2.0, -7.2, 0,
     "core buck: VREG_LX -> DVDD. 3.3 uH 2016, >= 0.6 A, as the reference"),
    ("C714", "4u7",  FP0402CS, 2.0, -4.6,  0,   "VREG_VIN, at pin 49"),
    ("C715", "4u7",  FP0402CS, 2.0, -5.55, 0,   "DVDD, at the inductor"),
    ("C718", "4u7",  FP0402C, 4.2, -5.05, -90, "VREG_AVDD"),
    ("R706", "33R",  FP0402R, 4.2, -6.9,  -90, "+3V3 -> VREG_AVDD"),
    ("R707", "27R",  FP0402R, -1.1, -8.2, -90, "USB_DP series"),
    ("R708", "27R",  FP0402R, -0.1, -8.2, -90, "USB_DM series"),
    # -- the ADC supply filter, on the BACK under the flash: the corner in
    # front of pin 44 is full, and the back there is empty copper
    ("R703", "10R",  FP0402R, 6.0, -4.6,  -90, "ADC_AVDD filter, with C716", "B.Cu"),
    ("C716", "1u",   FP0402C, 6.0, -6.6,  -90, "ADC_AVDD", "B.Cu"),
    # -- clockwise side: the second DVDD bulk and its 100 n, at pin 23, and
    # the crystal's series resistor at XOUT, all beyond five rows of vias
    ("C719", "4u7",  FP0402C, 0.8, 9.0,   -90, "DVDD, at pin 23"),
    ("C707", "100n", FP0402C, 2.0, 9.0,   -90, "DVDD, at pin 23"),
    ("R702", "1k",   FP0402R, 3.2, 9.0,   -90, "crystal series, on XOUT"),
]

# The six IOVDD 100 n go on the back, in two columns beside the chip. On the
# reference's two-layer board they had to be at the pins; here IOVDD is the
# In2 plane 0.1 mm under In1's ground, and every one of these is a via into
# each -- the plane pair is the high-frequency capacitor, these are the bulk.
CPU_BACK = [("C701", 18.2, -1), ("C702", 20.3, -1), ("C703", 22.4, -1),
            ("C704", 18.2, +1), ("C705", 20.3, +1), ("C706", 22.4, +1)]
S_CPU_BACK = 7.5


def cpu_wedge():
    """282 deg: RP2350A and the things that have to be beside it."""
    th, out = G.WEDGE_ANG[1], []
    mid = Row("CPU", th, R_CPU)
    u7 = mid.centre("U7", "RP2350A",
                    "servodrive:RP2350-QFN-60-1EP_7x7_P0.4mm_EP3.4x3.4mm_ThermalVias", 0,
                    "QFN-60, 1EP; three PWM slices, four ADC")
    out += mid.parts
    for ref, val, fp, dx, dy, rot, note, *layer in CPU_CLUSTER:
        out.append(in_frame(u7, ref, val, fp, dx, dy, rot, "CPU", note,
                            layer[0] if layer else "F.Cu"))
    for ref, r, side in CPU_BACK:
        out.append(Part(ref, "100n", FP0402C, r, side * S_CPU_BACK, 0, "CPU", th,
                        "B.Cu", "IOVDD decoupling, into the In1/In2 planes"))

    # The row beyond the fan-out. Tangential, so the 2520 crystal is 2.5 mm
    # deep and everything stays inside R 31.5.
    outer = Row("CPU", th, R_CPU_OUT, s0=0.0)
    outer.add(-1, "Y1", "12MHz", "Crystal:Crystal_SMD_2520-4Pin_2.5x2.0mm", 90,
              "12 MHz 2520, CL to suit C709/C710")
    outer.add(-1, "C709", "27p", FP0402C, 90, "crystal load")
    outer.add(-1, "C710", "27p", FP0402C, 90, "crystal load")
    out += outer.parts
    # The flash's two rows of 0.25 mm pads face clockwise and counter-
    # clockwise (rot 90 -- the USON's pad rows run along its long side),
    # past the fan-out's reach, where there is room for a via beside each
    # pad. Radial, one row faced the board edge and the other the fan-out,
    # and neither could take a via.
    # Turned so that the radial order of each row's pads matches the order
    # of the chip's QSPI pins along its side: SD3, SCLK, SD0 outer to inner
    # on both, and the In3 runs between them do not have to cross.
    out.append(Part("U8", "W25Q128", "Package_SON:Winbond_USON-8-1EP_3x2mm_P0.5mm_EP0.2x1.6mm",
                    29.4, 7.0, 270, "CPU", th, note="QSPI flash"))
    # ... and its 100 n and the BOOTSEL series resistor on the back directly
    # beneath its body, radial, between the two pad rows: the flash's pads
    # get a via each beside the part (tools/fanout.py, escape), and the
    # strip clockwise of it, where these two sat, is where those vias go.
    # Beneath the body nothing on the front wants a via.
    # The 100 n's 3V3 pad faces outward, where its plane tap has room; its
    # ground pad's via then sits beside the flash's inner pad row, which
    # nothing on the front reaches.
    for ref, val, fp, s, rot, note in (
            ("C711", "100n", FP0402C, 6.3, 180, "flash decoupling"),
            ("R701", "1k", FP0402R, 7.7, 0,
             "BOOTSEL series: QSPI_SS to the button on board B")):
        out.append(Part(ref, val, fp, 29.6, s, rot, "CPU", th, "B.Cu", note))
    return out

def in_frame(host, ref, val, fp, dx, dy, rot, block, note="", layer="F.Cu"):
    """A part placed in another part's footprint frame (KiCad y-down local
    coordinates), rotated with it. Verified against the chip's own pads."""
    (x0, y0), _, _, a0 = host.rect
    vx, vy = dx, -dy                       # KiCad y-down -> geometry y-up
    gx = x0 + vx * cos(a0) - vy * sin(a0)
    gy = y0 + vx * sin(a0) + vy * cos(a0)
    return Part.at_xy(ref, val, fp, gx, gy, degrees(a0) + rot, block, layer, note)

R_SIGLINK = 25.15                     # the SMD signal header is 8.6 mm deep; its
                                      # outer row's vias need 0.83 mm to the land

def signal_wedge():
    """240 deg: the signal link, the rails' bulk, the LDO, the ADC3 front end."""
    th, out = G.WEDGE_ANG[2], []
    # SMD, not through-hole, for two reasons that point the same way. A 1.27 mm
    # pitch THT header has a 1.0 mm pad on a 0.65 mm drill -- a 0.175 mm annular
    # ring, against JLCPCB's 0.254 mm for 2 oz outer copper, and you cannot fix
    # it by growing the pad because 1.16 mm pads on a 1.27 mm pitch leave
    # 0.11 mm between them. And 28 through-holes would punch 28 holes through
    # In1, In2 and In4 -- the ground plane, the VBUS plane and the second ground
    # plane -- immediately beside the CPU. The power link stays through-hole: it
    # carries 30 A and its annular ring is 0.35 mm.
    # Its pads are 2.4 x 0.74 mm on a 1.27 mm pitch, 0.53 mm apart, and
    # 19 degrees off the 45-degree grid: a 0.2 mm track leaves one only
    # along its own axis, which a 45-degree router cannot do, and the
    # outer row, facing the heatsink land, could not be left at all. So
    # every pad gets a stub and a via (tools/fanout.py, escape), and the
    # header sits 0.15 mm further in than it did, so that the outer row's
    # vias fit between its pad ends and the land at R 29.2.
    out.append(Part("J9", "SIG_LINK",
                    "Connector_PinHeader_1.27mm:PinHeader_2x14_P1.27mm_Vertical_SMD",
                    R_SIGLINK, 0, 0, "signal link", th,
                    note="20 signals + 8 interleaved grounds"))
    # Inboard of the header: the FET-temperature divider and filter, the
    # FAULT_n pull-up, and the 3V3 LDO at the clockwise end where the +3V3
    # plane on In2 reaches out to meet it.
    row = Row("signal link", th, 19.2)
    # The pull-ups are turned so their +3V3 pad is the inner one: the +3V3
    # plane is the centre disc on In2, and the tap to it is a straight stub
    # inward, which the other pad must not be in the way of.
    row.centre("R705", "10k", "Resistor_SMD:R_0402_1005Metric", 180,
               "FAULT_n pull-up: defines the pin while the overcurrent comparator "
               "is an open question")
    row.add(-1, "R803", "10k 0.1%", "Resistor_SMD:R_0603_1608Metric", 0,
            "thermistor divider, fixed leg -> ADC3")
    row.add(-1, "C802", "100n", "Capacitor_SMD:C_0603_1608Metric", 0, "ADC3 filter")
    row.add(-1, "U9", "SPX3819-3.3", "Package_TO_SOT_SMD:SOT-23-5", 90,
            "5 V -> 3V3, under the +3V3 plane's reach")
    row.add(+1, "C712", "1u", "Capacitor_SMD:C_0603_1608Metric", 0, "3V3 out")
    row.add(+1, "C713", "1u", "Capacitor_SMD:C_0603_1608Metric", 0, "5 V in")
    # RUN's pull-up and cap: RUN comes to the link from the CPU anyway.
    row.add(+1, "R704", "10k", "Resistor_SMD:R_0402_1005Metric", 180, "RUN pull-up")
    row.add(+1, "C717", "100n", "Capacitor_SMD:C_0402_1005Metric", 0, "RUN")
    out += row.parts

    # On the back, under the header: the four pins spec sec.6 calls spare,
    # brought out -- TP4 is what makes the motor-housing thermistor a build
    # option: lift R901, wire one here -- and the rails' bulk, once each,
    # under the header pins the rails arrive on.
    tp = Row("signal link", th, 22.6, layer="B.Cu", s0=1.5)
    for k, (ref, what) in enumerate(((("TP1"), "GPIO23"), (("TP2"), "GPIO24"),
                                     (("TP3"), "GPIO25"), (("TP4"), "ADC3"))):
        tp.add(+1 if k % 2 == 0 else -1, ref, what,
               "TestPoint:TestPoint_Pad_D1.5mm", 0, f"test point, {what}")
    tp.add(-1, "C603", "4u7/25V", "Capacitor_SMD:C_0603_1608Metric", 0,
           "+12 V bulk, at the link")
    tp.add(+1, "C604", "4u7", "Capacitor_SMD:C_0603_1608Metric", 0,
           "+5 V bulk, at the link")
    out += tp.parts
    return out

def encoder():
    """The centre. Nothing but this and the motor screws inside R_CENTRE."""
    return [
        Part("U10", "MT6701", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", 0.0, 0, 90,
             "encoder", 0.0, "B.Cu", "on the shaft axis, motor-facing side"),
        Part("C901", "100n", "Capacitor_SMD:C_0402_1005Metric", 4.6, 0, 90,
             "encoder", 90.0, "B.Cu", "MT6701 decoupling"),
    ]

def board_a():
    parts = []
    for i in range(3):
        parts += phase_cell(i)
    return parts + power_wedge() + cpu_wedge() + signal_wedge() + encoder()

# -------------------------------------------------------------- checking ----
def _overlap(a, b):
    """Separating-axis test between two rotated courtyards."""
    ca, cb = a.corners(), b.corners()
    for poly in (ca, cb):
        for i in range(4):
            x1, y1 = poly[i]; x2, y2 = poly[(i + 1) % 4]
            ax, ay = -(y2 - y1), (x2 - x1)
            n = hypot(ax, ay)
            if n == 0:
                continue
            ax, ay = ax / n, ay / n
            pa = [px * ax + py * ay for px, py in ca]
            pb = [px * ax + py * ay for px, py in cb]
            if min(pa) >= max(pb) or min(pb) >= max(pa):
                return False
    return True

BLOCK_AXIS = dict(zip(("power link", "CPU", "signal link"), G.WEDGE_ANG))

def check(parts):
    """Every rule the floorplan has to obey, as a list of complaints."""
    bad, by_layer = [], {}
    for p in parts:
        by_layer.setdefault(p.layer, []).append(p)

    for layer, ps in by_layer.items():
        for i in range(len(ps)):
            for j in range(i + 1, len(ps)):
                if _overlap(ps[i], ps[j]):
                    bad.append(f"{layer}: {ps[i].ref} ({ps[i].block}) overlaps "
                               f"{ps[j].ref} ({ps[j].block})")

    for p in parts:
        rr = [hypot(x, y) for x, y in p.corners()]
        if max(rr) > G.R_USABLE + 1e-9:
            bad.append(f"{p.ref}: reaches r={max(rr):.2f}, past R_USABLE {G.R_USABLE}")
        if p.block != "encoder" and min(rr) < G.ZONE_R0 - 1e-9:
            bad.append(f"{p.ref}: reaches r={min(rr):.2f}, inside the "
                       f"R{G.ZONE_R0} centre keepout")
        if p.layer == "F.Cu" and p.block.startswith("phase") and max(rr) > R_RING_ID + 1e-9:
            bad.append(f"{p.ref}: reaches r={max(rr):.2f}, under the thermal ring "
                       f"land (inner edge {R_RING_ID})")

    for p in parts:
        if p.block.startswith("phase"):
            axis, span = G.PHASE_ANG[PHASE_NAMES.index(p.block[-1])], G.PHASE_SPAN
        elif p.block in BLOCK_AXIS:
            axis, span = BLOCK_AXIS[p.block], G.WEDGE_SPAN
        else:
            continue
        for x, y in p.corners():
            da = (degrees(atan2(y, x)) - axis + 540) % 360 - 180
            if abs(da) > span / 2 + 1e-9:
                bad.append(f"{p.ref}: {da:+.1f} deg off its {p.block} axis, past "
                           f"the {span/2:.0f} deg edge")
                break

    for a, (hx, hy, hr) in zip(G.PERIM_ANG, bosses()):
        for p in parts:
            if _rect_circle(p, hx, hy, hr):
                bad.append(f"perimeter boss at {a:.0f} deg fouls {p.ref}")

    for layer, r0, r1, ang, half in sectors():
        what = "heatsink land" if layer == "F.Cu" else "phase pad"
        for p in parts:
            if p.layer == layer and _rect_sector(p, r0, r1, ang, half):
                bad.append(f"{p.ref} sits on the {what} arc at {ang:.0f} deg")

    # through-hole pads cost room on BOTH faces
    for a in parts:
        holes = a.tht()
        if not holes:
            continue
        for b in parts:
            if b is a or b.layer == a.layer:
                continue
            for hx, hy, hr in holes:
                if _rect_circle(b, hx, hy, hr + 0.2):
                    bad.append(f"{a.ref}'s through-hole pads land on {b.ref} "
                               f"({b.layer})")
                    break
            else:
                continue
            break
    for a in parts:
        for hx, hy, hr in a.tht():
            for layer, r0, r1, ang, half in sectors():
                if layer == a.layer:
                    continue
                if _in_sector(hx, hy, r0 - hr, r1 + hr, ang, half):
                    what = "heatsink land" if layer == "F.Cu" else "phase pad"
                    bad.append(f"{a.ref}'s through-hole pads land on the "
                               f"{what} arc at {ang:.0f} deg")
                    break
    return bad

def _rect_circle(p, cx, cy, r):
    """Does a circle touch a part's courtyard?"""
    (gx, gy), hw, hh, ang = p.rect
    dx, dy = cx - gx, cy - gy
    u = dx * cos(ang) + dy * sin(ang)
    v = -dx * sin(ang) + dy * cos(ang)
    du, dv = max(abs(u) - hw, 0.0), max(abs(v) - hh, 0.0)
    return hypot(du, dv) < r

def _in_sector(x, y, r0, r1, ang, half):
    rr = hypot(x, y)
    if not (r0 <= rr <= r1):
        return False
    da = (degrees(atan2(y, x)) - ang + 540) % 360 - 180
    return abs(da) <= half

def _rect_sector(p, r0, r1, ang, half):
    """Does a part's courtyard reach into an annular sector? Sampled, which is
    enough: the parts are millimetres and the sectors are centimetres."""
    pts = p.corners()
    mids = [((pts[i][0] + pts[(i+1) % 4][0]) / 2,
             (pts[i][1] + pts[(i+1) % 4][1]) / 2) for i in range(4)]
    (gx, gy), _, _, _ = p.rect
    return any(_in_sector(x, y, r0, r1, ang, half)
               for x, y in pts + mids + [(gx, gy)])

def ring_land_area():
    """Bare copper the aluminium ring clamps, mm2. Three arcs, six bolt holes."""
    arc = 3 * radians(2 * RING_HALF) / 2 * (R_RING_OD**2 - R_RING_ID**2)
    boss = G.PERIM_N * 3.14159265 * ((BOSS_PAD / 2)**2 - (G.PERIM_D / 2)**2)
    return arc + boss

def report():
    parts = board_a()
    bad = check(parts)
    n_f = sum(1 for p in parts if p.layer == "F.Cu")
    print(f"board A floorplan: {len(parts)} parts  "
          f"({n_f} front, {len(parts)-n_f} back), "
          f"{sum(1 for p in parts if p.dnp)} DNP")
    for blk in ("phase A", "phase B", "phase C", "power link", "CPU",
                "signal link", "encoder"):
        ps = [p for p in parts if p.block == blk]
        if not ps:
            continue
        rr = [hypot(x, y) for p in ps for x, y in p.corners()]
        print(f"  {blk:12s} {len(ps):3d} parts   r {min(rr):5.2f} .. {max(rr):5.2f}")
    print(f"  heatsink land  r {R_RING_ID} .. {R_RING_OD}   "
          f"{ring_land_area():.0f} mm2 of bare copper for the heatsink")
    if bad:
        print(f"\n{len(bad)} PROBLEM(S):")
        for b in bad:
            print("  !", b)
    else:
        print("\nclean: no courtyard overlaps, nothing out of bounds, "
              "nothing under the ring land.")
    return bad

if __name__ == "__main__":
    import sys
    sys.exit(1 if report() else 0)
