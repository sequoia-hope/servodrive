#!/usr/bin/env python3
"""placement_s.py — board S, the single-board variant: a placement SKETCH.

Board A's three phase cells, its CPU wedge and the encoder, unchanged, with the
two link wedges rebuilt to carry the minimum of board B instead of the headers
to it (decided 2026-09-22):

    power wedge   230 deg  bus pads, 2 x 100 uF / 100 V polymer (C2887236),
                           SMDJ54A, the 12 V gate buck, one RS-485 port
    signal wedge  334 deg  USB-C (data + 5 V logic), the 5 V buck, 2 x SIT3088
                           full duplex, the other RS-485 port, the expansion
                           header for a PD or Ethernet board, LED, BOOTSEL

48 V operational max. Nothing here is captured or routed: it answers "does it
fit, and where", with the same courtyards and the same rules as board A --
every part below passes placement.check() or is reported as not placed.

    python3 tools/placement_s.py        # the report, and img/board_s_plan.svg

Each new part is put by a small search: the nearest legal spot to where it is
wanted, legal meaning everything placement.check() enforces. Wanting is the
design; the search only keeps it honest about the millimetres.
"""
import re
from math import cos, sin, radians, degrees, hypot, atan2, asin
from pathlib import Path

import geometry as G
import placement as PL
from placement import Part

ROOT = Path(__file__).resolve().parent.parent

PWR, SIG = "power link", "signal link"          # the block names check() knows
CPU = "CPU"                                     # board A's name for the middle wedge
AX = {PWR: G.WEDGE_ANG[0], SIG: G.WEDGE_ANG[2], CPU: G.WEDGE_ANG[1]}

# Parts of board A that this variant takes away: the three link headers, the
# three spare-GPIO test points (GPIO23-25 go to the expansion header and VBUS
# detect), and the two rail caps that become the bucks' output caps.
GONE = {"J7", "J8", "J9", "TP1", "TP2", "TP3"}

# Edge connectors reach the board edge by design; the mouth of a USB-C or a
# GH housing is meant to sit at the outline, past R_USABLE. Their pads do not.
EDGE = {"J12", "J14", "J15"}
R_EDGE = G.R + 0.05
R_RIM = G.R - 0.9            # ... and they have to reach it: a horizontal
                             # connector 2 mm in from the edge cannot be mated

# The expansion header goes in the middle of the outward face, lying between
# the four motor screw heads. Board A keeps everything inside R 17 for the
# encoder, on both faces; this bends that on the OUTWARD face for one part,
# because the two wedges have no 14 mm x 9 mm hole left in them. What it has
# to respect there is the screw heads, with room for a driver to reach them.
CENTRE = {"J13"}
HEAD_CLEAR = G.MOUNT_HEAD / 2 + 0.5          # an M3 head on the outward face, or
                                             # a standoff to the motor on the other
SCREWS = [(G.MOUNT_X, 0), (-G.MOUNT_X, 0), (0, G.MOUNT_Y), (0, -G.MOUNT_Y)]

# The motor-facing centre (decided 2026-09-22): parts under 1.5 mm tall,
# outside the magnet's keepout, clear of the four standoffs. The MT6701
# hangs 1.75 mm below the board and the magnet's face is 1.5 mm below that;
# everything here stays above the magnet and away from the sensor.
MAGNET_R = G.MAGNET_KEEP / 2
H_MAX_BACK = 1.5
# Package height, datasheet maximum for typical parts in each package. A part
# chosen for the motor-facing centre has to meet it.
HEIGHT = {
    "Resistor_SMD:R_0402_1005Metric": 0.40, "Capacitor_SMD:C_0402_1005Metric": 0.55,
    "Resistor_SMD:R_0603_1608Metric": 0.55, "Capacitor_SMD:C_0603_1608Metric": 0.90,
    "Capacitor_SMD:C_0805_2012Metric": 1.45,
    "Package_TO_SOT_SMD:SOT-23": 1.20, "Package_TO_SOT_SMD:SOT-23-5": 1.45,
    "Package_TO_SOT_SMD:SOT-23-6": 1.45, "parts_motor:DFN-8_L3.0-W3.0-P0.65-BL-EP": 1.00,
    "Diode_SMD:D_SOD-323": 1.10, "TestPoint:TestPoint_Pad_D1.0mm": 0.0,
    "Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm": 0.0,
}

# The bus lead pads: arc pads on the outward face at the power wedge's rim,
# like the phase lead pads -- 14 AWG soldered flat, no holes, so the back of
# the rim stays free for an RS-485 port. (layer, r0, r1, centre, half-span)
# The sketches as asked and before 2026-09-24; the board takes an XT30 now.
BUS_PADS = [("F.Cu", 28.7, 31.4, AX[PWR] - 5.0, 3.8, "VMOT"),
            ("F.Cu", 28.7, 31.4, AX[PWR] + 5.0, 3.8, "GND")]
BUS = "pads"                  # "pads" or "xt30": set by each sketch as it starts

def bus_pads():
    return BUS_PADS if BUS == "pads" else []

FP = dict(
    CAN="Capacitor_THT:CP_Radial_D10.0mm_P5.00mm",
    SMC="Diode_SMD:D_SMC",
    PPAD="Package_SO:TI_SO-PowerPAD-8",
    L4018="Inductor_SMD:L_Bourns-SRN4018",
    C1210="Capacitor_SMD:C_1210_3225Metric",
    C1206="Capacitor_SMD:C_1206_3216Metric",
    C0603="Capacitor_SMD:C_0603_1608Metric",
    C0402="Capacitor_SMD:C_0402_1005Metric",
    R0603="Resistor_SMD:R_0603_1608Metric",
    R0402="Resistor_SMD:R_0402_1005Metric",
    SOT23="Package_TO_SOT_SMD:SOT-23",
    SOT236="Package_TO_SOT_SMD:SOT-23-6",
    SOD123F="Diode_SMD:D_SOD-123F",
    DFN8="parts_motor:DFN-8_L3.0-W3.0-P0.65-BL-EP",
    USBC="Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12",
    GH6="Connector_JST:JST_GH_SM06B-GHS-TB_1x06-1MP_P1.25mm_Horizontal",
    EXP="Connector_PinHeader_1.27mm:PinHeader_2x10_P1.27mm_Vertical_SMD",
    LED="LED_SMD:LED_RGB_1210",
    SW="Button_Switch_SMD:SW_SPST_B3U-1000P",
    SH6="Connector_JST:JST_SH_SM06B-SRSS-TB_1x06-1MP_P1.00mm_Horizontal",
    SMB="Diode_SMD:D_SMB",
    C0805="Capacitor_SMD:C_0805_2012Metric",
    NR30="Inductor_SMD:L_Taiyo-Yuden_NR-30xx",
    SWPA4030="Inductor_SMD:L_Sunlord_SWPA4030S",
    SWPA5040="Inductor_SMD:L_Sunlord_SWPA5040S",
    JP="Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm",
    TP="TestPoint:TestPoint_Pad_D1.0mm",
    XT30="servodrive:AMASS_XT30PW-M_1x02_P2.50mm_Horizontal",     # KiCad's, courtyard redrawn
)

# The bus connector (decided 2026-09-24): the rp2350-motor-controller's XT30,
# its J9 -- AMASS XT30PW-M, horizontal, pin 1 GND and pin 2 VMOT, LCSC
# C431092 -- on the power wedge's axis on the outward face, mouth outward.
# Not at the edge: its two pegs, 11 mm apart and 3.6 mm behind the mouth,
# land on the motor-facing rim, which is the two RS-485 ports' -- and no
# other stretch of rim on the board takes a JST SH (the CPU wedge's is the
# RP2350's escape vias, through both faces; the signal wedge's is 0.1 mm
# short beside the USB-C). So it sits back from the edge until the pegs clear
# the ports (decided 2026-09-24, "horizontal set back"), and the plug's first
# few mm lie over the board: PLUG keeps that path clear on the outward face.
XT30_FACE = 13.21            # the housing's face, mm ahead of the pins (its silk)
XT30_W = 5.6                 # half the plug's width, with room: the housing is 10.1
PLUG = []                    # (layer, r0, r1, centre, half-span), set when it is placed
# ... and the VBUS spine's path on the outward face, from its VMOT pin to the
# expansion header's four VMOT pins (gen_boards.bus_spine lays the copper):
# [(x0, y0), (x1, y1), half-width], kept clear of parts once both are down
SPINE = []
SPINE_W = 1.2 + 0.45         # the spine's half-width, and room for the 60 V gap

# In front of each SIT3088 pin row the fan-out puts a via per pin, 0.43 and
# 1.14 mm beyond the pad ends (fanout.escape, ROW0/ROW_PITCH): a strip that
# later parts keep out of. Loosely placed, the bus divider sat there and left
# U15's pin 7 no escape. (Strips, not parts: they are never emitted.)
ESC_KEEP = []
PL._CY["keep:escape"] = (2.7, 1.3, 0.0, 0.0)

def _escape_strips(u):
    (ux, uy), _, hh, a = u.rect
    d = hh + 0.65
    return [Part.at_xy("ESC", "", "keep:escape", ux - sin(a) * d * k, uy + cos(a) * d * k,
                       degrees(a), "centre", u.layer) for k in (1, -1)]

def _on_spine(c, any_face=False):
    if c.layer != "F.Cu" and not any_face:
        return False
    for (x0, y0), (x1, y1), hw in SPINE:
        n = max(1, int(hypot(x1 - x0, y1 - y0) / 0.4))
        if any(_hits_circle(c, x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n, hw)
               for k in range(n + 1)):
            return True
    return False
# Placed by its body, not by the library's courtyard, a 14.3 mm rectangle
# to take the peg ears, which spends 3 mm either side of the pins at the
# back, where the TVS has to go. So: the housing and the pins, 0.25 round
# (x -7.81..2.81, y -13.6..2.0 in the footprint), and each peg's ear a
# circle on the same face (EAR_R: the ear is a 1.11 mm half-disc round it).
# gen_boards.xt30_body draws the project's copy of the footprint with exactly
# that courtyard, so KiCad's DRC checks what this checks.
PL._CY[FP["XT30"]] = (10.62, 15.6, -2.5, 5.8)
EAR_R = 1.11 + 0.35
_xcy = PL.courtyard(FP["XT30"])[3]           # the courtyard centre's offset from pin 1

# ------------------------------------------------------------ the vias ----
# Board A's own copper that this variant keeps: the fan-out's escape vias round
# the RP2350 and anything else the tools locked in the CPU wedge or the centre.
# A through via is an obstacle on BOTH faces. (x, y, keepout radius)
def _kept_vias():
    import re
    pcb = ROOT / "hardware/motor_board/servodrive_A.kicad_pcb"
    out = []
    if not pcb.exists():
        return out
    CX, CY = 148.0, 105.0                          # gen_boards.P()
    for m in re.finditer(r'\(via\b(.*?)\n\t\)', pcb.read_text(), re.S):
        b = m.group(1)
        if "(locked yes)" not in b:
            continue                               # the router's: re-routed anyway
        at = re.search(r'\(at ([-\d.]+) ([-\d.]+)\)', b)
        sz = re.search(r'\(size ([\d.]+)\)', b)
        x, y = float(at.group(1)) - CX, CY - float(at.group(2))
        r, a = hypot(x, y), degrees(atan2(y, x)) % 360
        cpu = abs((a - G.WEDGE_ANG[1] + 540) % 360 - 180) <= G.WEDGE_SPAN / 2
        links = any(abs((a - AX[b] + 540) % 360 - 180) <= G.WEDGE_SPAN / 2 for b in (PWR, SIG))
        # the CPU wedge's (its fan-out); the encoder's own; the phase cells'
        # taps into the centre disc. Not the link wedges', which are rebuilt.
        if cpu or r < 8.0 or (r < G.ZONE_R0 and not links):
            out.append((x, y, float(sz.group(1)) / 2 + 0.15))
    return out
KEPT_VIAS = _kept_vias()

# The MT6701's five stitching vias -- via-in-pad at its GND and VDD pins and
# its 100 n -- sit 2.5-4.6 mm off the axis, which is exactly where a 2x10
# header lying across the axis wants its pad rows. Taken out on a short stub
# instead, each moves ~2.5 mm, to just clear of the header's long sides:
# still inside the magnet keepout on the back, where nothing else goes.
ENC_VIAS_MOVED = [(-1.91, -4.95), (0.63, -4.95), (1.91, -4.95), (-0.48, 4.95), (0.48, 4.95)]
def encoder_vias(moved):
    kept = [v for v in KEPT_VIAS if hypot(v[0], v[1]) >= 8.0]
    if not moved:
        return kept + [v for v in KEPT_VIAS if hypot(v[0], v[1]) < 8.0]
    return kept + [(x, y, 0.38) for x, y in ENC_VIAS_MOVED]

# ------------------------------------------------------------ the rules ----
# placement.py tests rectangles, and a can's courtyard is a circle: its
# bounding square wastes 21 % of the area, at the corners where the room is
# tightest. So round parts are tested as the circles they are.
def round_r(p):
    return PL.courtyard(p.fp)[0] / 2 if p.fp == FP["CAN"] else None

def _centre(p):
    return p.rect[0]

def _sectors():
    return PL.sectors() + [s[:5] for s in bus_pads()] + PLUG

def _circle_pts(cx, cy, r, n=24):
    return [(cx + r * cos(k * 6.2831853 / n), cy + r * sin(k * 6.2831853 / n))
            for k in range(n)] + [(cx, cy)]

def _span_ok(p):
    """Inside its own wedge's angle. The cans are exempt: at R 23 a Dia 10 can
    spans 26 deg, the pair needs 1 deg more than the wedge has, and the wedge
    line there is bookkeeping -- the real neighbours are checked part by part,
    and span_over() reports how far each can crosses it."""
    axis = AX.get(p.block)
    if axis is None or round_r(p) or p.fp in PEG_FPS:
        return True             # the XT30's back end is in the centre, not the wedge
    pts = p.corners()
    for x, y in pts:
        da = (degrees(atan2(y, x)) - axis + 540) % 360 - 180
        if abs(da) > G.WEDGE_SPAN / 2 + 1e-9:
            return False
    return True

# The sister project's DFN-8 (the SIT3088) draws its courtyard at +-1.6 mm and
# its pads out to +-1.72: two of them 0.05 mm apart passed the courtyard test
# with their pins touching. For placing, its courtyard is its pads plus 0.25.
PL._CY["parts_motor:DFN-8_L3.0-W3.0-P0.65-BL-EP"] = (3.2, 3.95, 0.0, 0.0)

# Parts with a pad on the 60 V bus beside low-voltage copper keep the Phase
# class's 0.4 mm from their neighbours' pads, which courtyards touching does
# not give: the EN divider's top resistor sat 0.36 mm from the pull-downs.
HV = {"R1003", "R801", "D1001", "C1001", "C1002"}
HV_MARGIN = 0.25

def _overlap_m(a, b, m):
    """Separating axes, with `a`'s courtyard grown by m on every side."""
    (gx, gy), hw, hh, ang = a.rect
    ca, sa = cos(ang), sin(ang)
    grown = [(gx + dx * ca - dy * sa, gy + dx * sa + dy * ca)
             for dx, dy in ((-hw - m, -hh - m), (hw + m, -hh - m), (hw + m, hh + m), (-hw - m, hh + m))]
    cb = b.corners()
    for poly in (grown, cb):
        for i in range(4):
            x1, y1 = poly[i]; x2, y2 = poly[(i + 1) % 4]
            ax, ay = -(y2 - y1), (x2 - x1)
            n = hypot(ax, ay)
            if n == 0:
                continue
            pa = [(px * ax + py * ay) / n for px, py in grown]
            pb = [(px * ax + py * ay) / n for px, py in cb]
            if min(pa) >= max(pb) or min(pb) >= max(pa):
                return False
    return True

def _touch(a, b):
    """Same-layer collision, circles as circles."""
    ra, rb = round_r(a), round_r(b)
    if ra and rb:
        (ax, ay), (bx, by) = _centre(a), _centre(b)
        return hypot(ax - bx, ay - by) < ra + rb
    if ra:
        return PL._rect_circle(b, *_centre(a), ra)
    if rb:
        return PL._rect_circle(a, *_centre(b), rb)
    if a.ref in HV:
        return _overlap_m(a, b, HV_MARGIN)
    if b.ref in HV:
        return _overlap_m(b, a, HV_MARGIN)
    return PL._overlap(a, b)

def _hits_circle(p, cx, cy, r):
    rr = round_r(p)
    if rr:
        px, py = _centre(p)
        return hypot(px - cx, py - cy) < rr + r
    return PL._rect_circle(p, cx, cy, r)

def _in_part(p, x, y):
    """Is a point inside a part's courtyard (circle or rotated rectangle)?"""
    rr = round_r(p)
    if rr:
        cx, cy = _centre(p)
        return hypot(x - cx, y - cy) <= rr
    (gx, gy), hw, hh, a = p.rect
    u = (x - gx) * cos(a) + (y - gy) * sin(a)
    v = -(x - gx) * sin(a) + (y - gy) * cos(a)
    return abs(u) <= hw and abs(v) <= hh

def _in_sector_part(p, r0, r1, ang, half):
    """Does a part touch an arc? Sampled both ways: the part's outline in the
    arc, and the arc's own points in the part -- a big part can straddle a
    narrow pad with none of its corners inside it."""
    rr = round_r(p)
    if rr:
        if any(PL._in_sector(x, y, r0, r1, ang, half)
               for x, y in _circle_pts(*_centre(p), rr)):
            return True
    elif PL._rect_sector(p, r0, r1, ang, half):
        return True
    for i in range(6):
        r = r0 + (r1 - r0) * i / 5
        for k in range(9):
            a = radians(ang - half + 2 * half * k / 8)
            if _in_part(p, r * cos(a), r * sin(a)):
                return True
    return False

def _near_sector(p, lo, hi, r0, r1, ang, half):
    """Could the part touch the arc at all? Its radial span against the arc's,
    and its bounding circle's angular reach against the arc's -- a cheap
    reject before _in_sector_part, which samples. Never says no wrongly."""
    if hi < r0 or lo > r1:
        return False
    rr = round_r(p)
    (cx, cy), hw, hh, _ = p.rect
    reach = rr if rr else hypot(hw, hh)
    d = hypot(cx, cy)
    if d <= reach:
        return True
    da = abs((degrees(atan2(cy, cx)) - ang + 540) % 360 - 180)
    return da <= half + degrees(asin(min(1.0, reach / d))) + 1.0

def _radii(p):
    rr = round_r(p)
    if rr:
        d = hypot(*_centre(p))
        return d - rr, d + rr
    rs = [hypot(x, y) for x, y in p.corners()]
    return min(rs), max(rs)

def span_over(p):
    """Degrees a round part reaches past its wedge line (0 if inside)."""
    axis, rr = AX[p.block], round_r(p)
    worst = 0.0
    for x, y in _circle_pts(*_centre(p), rr, 72):
        da = abs((degrees(atan2(y, x)) - axis + 540) % 360 - 180)
        worst = max(worst, da - G.WEDGE_SPAN / 2)
    return worst

# A through-hole lead is copper on both faces. placement.check() tests it as
# a circle of half the pad's longer side plus 0.2 mm, which is a courtyard
# rule; board S puts the cans' leads, 2 mm square pads on the 60 V bus, among
# fine-pitch parts on the far face, and the first board had C1001's + lead on
# U15's pin 7. So here a lead is its pad's bounding circle, and it keeps the
# Phase class's 0.4 mm (and a little) from the other face's courtyards.
THT_CLR = 0.45

# The XT30's two pegs locate its housing and carry no net: no 60 V to keep
# clear of, only a drill and a 0.2 mm ring. So a peg keeps its drill out of
# the far face's courtyards -- the courtyard's own margin is the room round
# the post -- and its ring PEG_CU from the far face's copper, where a lead
# keeps THT_CLR from the courtyards. It is the pegs against the two RS-485
# ports on the far face that set how far back from the edge the XT30 sits.
PEG_FPS = {FP["XT30"]}
PEG_CU = 0.25

_DRILLS = {}
def _tht_drills(fp):
    """(number, drill) of each through-hole pad, in PL.tht_pads' order."""
    if fp not in _DRILLS:
        t = PL.fp_path(fp).read_text()
        _DRILLS[fp] = [(m.group(1), float(m.group(2))) for m in re.finditer(
            r'\(pad\s+"([^"]*)"\s+(?:thru_hole|np_thru_hole)\s+\w+\s*\(at [^)]*\)\s*'
            r'\(size [^)]*\)\s*\(drill (?:oval )?([\d.]+)', t, re.S)]
        assert len(_DRILLS[fp]) == len(PL.tht_pads(fp)), fp
    return _DRILLS[fp]

def _is_peg(p, i):
    return p.fp in PEG_FPS and _tht_drills(p.fp)[i][0] == ""

def _holes(p):
    """(x, y, radius, clearance) of each through-hole pad: a lead's bounding
    circle and THT_CLR, a peg's drill and nothing."""
    out = []
    for i, ((x, y, _), (_, _, w, h)) in enumerate(zip(p.tht(), PL.tht_pads(p.fp))):
        if _is_peg(p, i):
            out.append((x, y, _tht_drills(p.fp)[i][1] / 2, 0.0))
        else:
            out.append((x, y, hypot(w, h) / 2, THT_CLR))
    return out

def _pegs(p):
    return [(x, y, r) for i, (x, y, r) in enumerate(p.tht()) if _is_peg(p, i)]

def _ears_clear(p, q):
    """Same face: does q keep out of p's peg ears?"""
    return not any(_hits_circle(q, x, y, EAR_R) for x, y, _ in _pegs(p))

def _pegs_clear(p, q):
    """Do p's pegs' rings keep PEG_CU from q's pads? (q on the other face.)"""
    import padpos
    pegs = _pegs(p)
    if not pegs:
        return True
    a = radians(q.ang)
    for num, x, y, w, h in padpos.pad_xy(q):
        for px, py, pr in pegs:
            dx, dy = px - x, py - y
            u, v = dx * cos(a) + dy * sin(a), -dx * sin(a) + dy * cos(a)
            if hypot(max(abs(u) - w / 2, 0.0), max(abs(v) - h / 2, 0.0)) < pr + PEG_CU:
                return False
    return True

def legal(c, placed):
    """Everything placement.check() enforces, for one candidate against the
    parts already down -- plus the bus pads, which check() does not know."""
    lo, hi = _radii(c)
    if hi > (R_EDGE if c.ref in EDGE else G.R_USABLE):
        return False
    if c.ref in EDGE and hi < R_RIM:
        return False
    if c.ref in CENTRE:
        if any(_hits_circle(c, sx, sy, HEAD_CLEAR) for sx, sy in SCREWS):
            return False
        if c.layer == "B.Cu" and (HEIGHT.get(c.fp, 99.0) >= H_MAX_BACK
                                  or _hits_circle(c, 0.0, 0.0, MAGNET_R)):
            return False
    elif lo < G.ZONE_R0:
        return False
    if not _span_ok(c):
        return False
    if any(_hits_circle(c, *b) for b in PL.bosses()):
        return False
    (cx, cy), hw, hh, _ = c.rect
    for layer, r0, r1, ang, half in _sectors():
        if layer == c.layer and _near_sector(c, lo, hi, r0, r1, ang, half) \
                and _in_sector_part(c, r0, r1, ang, half):
            return False
    reach0 = hypot(hw, hh)
    if any(hypot(vx - cx, vy - cy) < reach0 + vr and _hits_circle(c, vx, vy, vr)
           for vx, vy, vr in KEPT_VIAS):
        return False
    if SPINE and _on_spine(c):
        return False
    if any(q.layer == c.layer and PL._overlap(c, q) for q in ESC_KEEP):
        return False
    # ... and a transceiver's escape vias go through the board: not under
    # the VBUS spine on the other face, which keeps 0.4 mm from them
    if SPINE and c.fp == FP["DFN8"] and any(_on_spine(s, True) for s in _escape_strips(c)):
        return False
    reach = hypot(hw, hh)
    holes = _holes(c)
    for q in placed:
        (qx, qy), qw, qh, _ = q.rect
        if hypot(cx - qx, cy - qy) > reach + hypot(qw, qh) + 3.0:
            continue
        if q.layer == c.layer and (_touch(c, q) or not (_ears_clear(q, c) and _ears_clear(c, q))):
            return False
        if q.layer != c.layer:
            if any(_hits_circle(q, hx, hy, hr + cl) for hx, hy, hr, cl in holes):
                return False
            if any(_hits_circle(c, hx, hy, hr + cl) for hx, hy, hr, cl in _holes(q)):
                return False
            if not (_pegs_clear(c, q) and _pegs_clear(q, c)):
                return False
    for hx, hy, hr, _ in holes:
        for layer, r0, r1, ang, half in _sectors():
            if layer != c.layer and PL._in_sector(hx, hy, r0 - hr, r1 + hr, ang, half):
                return False
    return True

class Unplaced(Exception):
    pass

# A buck is only a buck if its input cap, bootstrap cap and inductor are
# beside it -- its hot loop is IC -> input cap -> ground, and every mm of that
# rings. So each one is placed as a MODULE: a compact cluster laid out once in
# the IC's own frame (courtyard centres, mm, y up), moved and turned as one.
# (role, footprint, dx, dy, rot)
# Laid out against the LMR38010's own pins (SNVSC73B, DDA): 1 GND, 2 EN,
# 3 VIN, 4 RT/SYNC down the left side, 5 FB, 6 PG, 7 BOOT, 8 SW up the right,
# exposed pad GND. In the IC's frame, y up, front face: pin 1 is top left at
# (-2.78, +1.905) and pin 8 top right. The sketch's first module was drawn
# before the pinout was read and had its input cap by RT and its feedback
# divider on the input side; this one puts every passive against the pin it
# serves, so each connection is a millimetre of copper:
#   cin    the 100 n, left of pins 1-3, VIN pad level with pin 3, GND pad with
#          pin 1 -- TI's own example layout (SNVSC73B fig. 9-16). EN, between
#          them, leaves there by a via; here an 0.15 mm track fits between
#          cin's pads with 0.4 mm to the VIN one, if cin sits 0.03 mm low
#   ent    (12 V only) the EN divider's top, left of cin: its VBUS pad a
#          straight run to cin's, its EN pad on the track from pin 2
#   enb    (12 V only) the divider's bottom, above it on the same track
#   rt     under pin 4, RT pad straight below it (RT/SYNC must not float)
#   fbb    under pin 5, FB pad straight below it
#   fbt    right of pin 5, its FB pad level with the pin, VOUT pad upward
#   boot   right of pins 7-8, BOOT pad level with 7, SW pad with 8
# A module on the back is this mirrored (put_module), because the IC's
# footprint is.
#
# The passives are TI's design points for 400 kHz from a 48 V bus (SNVSC73B
# table 9-1 and sec. 9.2.2), not the sketch's small ones (decided 2026-09-22,
# replaced 2026-09-23): the internal compensation is tuned for that L and
# COUT, and a 22 uF 0805 at 12 V is a few uF. Input: 100 n at the pins and at
# least 4.7 uF of ceramic rated for the bus. 12 V: 68 uH, 22 uF nominal (15
# minimum). 5 V: 33 uH, 3 x 22 uF nominal (2 minimum). Both inductors 4 x 4.
# They do not fit a rigid module: the signal wedge held the old one with
# fractions of a millimetre to spare, and a 4 x 4 inductor alone left both
# bucks nowhere. Hence the satellites and near parts below.
MOD_LMR38010 = [
    ("u",     "PPAD",   0.00,  0.00, 0),
    ("cin",   "C0805", -5.05,  0.60, 90),       # 100 n / 100 V, the hot loop
    ("ent",   "R0402", -7.00, -0.20, 90, "x"),  # 12 V only: EN runs out of pin 2
    ("enb",   "R0402", -7.00,  1.95, 90, "x"),  # between cin's pads to these two
    ("rt",    "R0402", -2.60, -3.30, 180),
    ("fbb",   "R0402",  2.60, -3.30, 0),
    ("fbt",   "R0402",  5.30, -0.80, -90),
    ("boot",  "C0402",  4.75,  1.27, 90),
]
# Satellites (put_module): each at the nearest legal spot that keeps its own
# pad within reach of the pad it joins, square to the module -- the inductor's
# SW pad by pin 8, the first output cap's + pad by the inductor's output.
# (role, footprint, anchor role, anchor pad, own pad, reach mm)
SAT_LMR38010 = {
    "12": [("l",     "SWPA4030", "u",    "8", "1", 4.5),     # SW
           ("cout",  "C1206",    "l",    "2", "1", 4.0)],    # VOUT
    "5":  [("l",     "SWPA4030", "u",    "8", "1", 4.5),
           ("cout",  "C0805",    "l",    "2", "1", 3.5)],
}
# ... and the parts that only have to be on the right copper, placed last by
# put_near(), either face, in the outward centre if the wedge is full (it is:
# decided 2026-09-23). The 4.7 uF input ceramic is on the VBUS plane through
# its via (gen_boards.in2_s gives a centre one a finger of In2), and at
# 400 kHz ten or fifteen mm of plane pair is nothing beside the 100 n at the
# pins, which takes the edges; the other output caps only add loop
# capacitance, which does not care where on the rail it is.
NEAR_LMR38010 = {
    "12": [("cout2", "C1210", "cout", "1", "1", 12.0),
           ("cbulk", "C1206", "cin",  "1", "1", 16.0)],
    "5":  [("cbulk", "C1206", "cin",  "1", "1", 16.0),
           ("cout2", "C0805", "cout", "1", "1", 12.0),
           ("cout3", "C0805", "cout", "1", "1", 12.0)],
}
# The bus divider as one piece: VBUS -> R801 -> R806 -> VBUS_SENSE, then R802
# and C801 side by side to ground, each pad beside the one it joins.
MOD_DIVIDER = [
    ("hi",  "R0603", 0.00,  0.00, 0),
    ("hi2", "R0603", 3.25,  0.00, 0),
    ("lo",  "R0603", 6.30,  0.80, 0),
    ("c",   "C0603", 6.30, -0.80, 0),
]
MOD_SOT_BUCK = [
    ("u",    "SOT236", 0.00,  0.00, 0),
    ("l",    "NR30",   4.20,  0.00, 0),
    ("cin",  "C0805",  0.00, -2.98, 0),
    ("cout", "C0805",  4.20, -3.10, 0),
    ("boot", "C0402", -0.60,  2.30, 0),
    ("fbt",  "R0402",  1.50,  2.30, 0),
    ("fbb",  "R0402",  4.20,  2.35, 0),
]

# Where the relay's four transceivers go on the motor-facing centre, each
# toward its own port: port IN (J14) takes the rim slot at 241 deg, OUT (J15)
# the one at 219. Tuned against the room there is -- the M3 standoffs at
# (-12.5, 0) and (0, -9.5), C1001's leads, the magnet keepout, R 17.
RELAY_AT = {"U14": (-5.5, -12.5), "U15": (-10.0, -8.5), "U17": (-12.0, -6.0), "U18": (-7.5, -4.5)}
# ... and with the XT30 (bus="xt30"), whose pins land in that quadrant: two
# identical pairs, the receiver first along the row
RELAY_PAIRS = [("U18", "U17"), ("U14", "U15")]
RELAY_PAIRS_AT = [(8.0, 8.5), (-6.5, -6.0)]

class Sketch:
    """The board as it fills up: board A's kept parts, then each new one."""

    def __init__(self):
        self.parts = [p for p in PL.board_a()
                      if p.block not in (PWR, SIG) or p.ref not in GONE]
        # the link wedges' own small parts are re-placed below, not kept
        self.parts = [p for p in self.parts if p.block not in (PWR, SIG)]
        self.new, self.missed = [], []

    def put(self, ref, value, fp, block, r, s, rot=0, layer="F.Cu", note="",
            rots=None, step=0.25, dnp=False, reach=16.0):
        """The nearest legal spot to the one wanted, within `reach` of it --
        the whole wedge by default; a few mm for parts that only work beside
        something else."""
        axis = AX[block]
        wx, wy = PL.polar_xy(axis + degrees(s / r), r)
        cands = []
        n = int(reach / step)
        for i in range(-n, n + 1):
            for j in range(-n, n + 1):
                if hypot(i, j) * step > reach:
                    continue
                x, y = wx + i * step, wy + j * step
                rr = hypot(x, y)
                if not (G.ZONE_R0 <= rr <= G.R):
                    continue
                da = (degrees(atan2(y, x)) - axis + 540) % 360 - 180
                if abs(da) > G.WEDGE_SPAN / 2:
                    continue
                cands.append((hypot(i, j) * step, rr, radians(da) * rr))
        cands.sort()
        for _, rr, ss in cands:
            for rt in (rots or (rot,)):
                c = Part(ref, value, fp, rr, ss, rt, block, axis, layer, note, dnp)
                if legal(c, self.parts):
                    self.parts.append(c); self.new.append(c)
                    return c
        self.missed.append((ref, value, fp, block, layer, note))
        return None

    def put_module(self, module, parts, block, layer, r, s, step=0.4, snap=45, flex=0.0,
                   sats=()):
        """Place a whole module: `parts` maps each role to (ref, value, note).
        Nearest legal base to (r, s) in the wedge, any of eight turns.

        The turns are absolute multiples of `snap` degrees, not turns from
        the radial: a module is a knot of short connections, and a router in
        45-degree mode cannot leave a fine-pitch pad that is off its grid
        (route.py). On the back the module is mirrored about its x axis,
        because the footprints are -- unmirrored, the input cap lands by RT.

        `flex`: each part after the first may sit up to that far from its
        spot in the module, nearest first, turned as drawn so its pads still
        face the pins they serve. Rigid, the 12 V module with its EN divider
        fitted nowhere on the board; a millimetre of give is what a person
        laying it out would take.

        `sats`: parts placed after the rigid ones, each wherever it fits with
        its pad near the pad it joins (_satellite) -- (role, footprint key,
        anchor role, anchor pad, own pad, reach). A base whose satellites do
        not all fit is no base."""
        offs = [(0.0, 0.0)]
        if flex:
            k = int(flex / 0.2)
            offs = sorted({(i * 0.2, j * 0.2) for i in range(-k, k + 1) for j in range(-k, k + 1)
                           if hypot(i, j) * 0.2 <= flex + 1e-9}, key=lambda o: hypot(*o))
        axis = AX[block]
        wx, wy = PL.polar_xy(axis + degrees(s / r), r)
        cands = []
        n = int(16.0 / step)
        for i in range(-n, n + 1):
            for j in range(-n, n + 1):
                x, y = wx + i * step, wy + j * step
                rr = hypot(x, y)
                da = (degrees(atan2(y, x)) - axis + 540) % 360 - 180
                if G.ZONE_R0 <= rr <= G.R and abs(da) <= G.WEDGE_SPAN / 2:
                    cands.append((hypot(i, j) * step, x, y))
        cands.sort()
        flip = -1 if layer.startswith("B.") else 1
        for _, x0, y0 in cands:
            radial = degrees(atan2(y0, x0))
            base = round(radial / snap) * snap if snap else radial
            for turn in range(0, 360, 45):
                a = base + turn
                ca, sa = cos(radians(a)), sin(radians(a))
                built, ok = [], True
                for role, fpk, dx, dy, rot, *along in module:
                    if role not in parts:
                        continue
                    ref, value, note = parts[role]
                    # On the back the module's own frame is mirrored about its
                    # x axis, so a turn of +a is a turn of -a relative to the
                    # footprint's (mirrored) frame; Part.at_xy takes the angle
                    # as seen from the front, which is a + flip * rot.
                    give = offs if built else [(0.0, 0.0)]
                    if along:
                        # a part that ends a channel slides along it, not across
                        give = [o for o in give if abs(o[1]) < 0.21]
                    for ox, oy in give:
                        mx, my = dx + ox, dy + oy
                        c = Part.at_xy(ref, value, FP[fpk], x0 + mx * ca - flip * my * sa,
                                       y0 + mx * sa + flip * my * ca, a + flip * rot, block, layer, note)
                        if legal(c, self.parts + built):
                            break
                    else:
                        ok = False
                        break
                    built.append(c)
                for srole, fpk, arole, apad, opad, sreach in (sats if ok else ()):
                    if srole not in parts:
                        continue
                    host = next((c for c in built if c.ref == parts[arole][0]), None)
                    c = host and self._satellite(parts[srole], FP[fpk], block, layer,
                                                 host, apad, opad, sreach, a, built)
                    if c is None:
                        ok = False
                        break
                    built.append(c)
                if ok:
                    self.parts += built; self.new += built
                    return built
        return None

    def _satellite(self, part, fp, block, layer, host, apad, opad, reach, a, built, step=0.25,
                   layers=None, turn=90):
        """One of a module's satellites: the nearest legal spot, square to the
        module, that puts its pad `opad` within `reach` of `host`'s pad `apad`
        -- on `layer`, or on the first of `layers` that has one."""
        import padpos
        ref, value, note = part
        hx, hy = next((x, y) for n, x, y, w, h in padpos.pad_xy(host) if n == apad)
        k = int(reach / step)
        grid = [(hypot(i, j) * step, i * step, j * step) for i in range(-k, k + 1)
                for j in range(-k, k + 1) if hypot(i, j) * step <= reach]
        placed = self.parts + built
        for lay in (layers or (layer,)):
            turns = []
            for t in range(0, 360, turn):
                probe = Part.at_xy(ref, value, fp, 0.0, 0.0, a + t, block, lay, note)
                px, py = next((x, y) for n, x, y, w, h in padpos.pad_xy(probe) if n == opad)
                turns.append((a + t, px, py))
            for d, ox, oy, ang, px, py in sorted((d, ox, oy, ang, px, py) for d, ox, oy in grid
                                                 for ang, px, py in turns):
                c = Part.at_xy(ref, value, fp, hx + ox - px, hy + oy - py, ang, block, lay, note)
                if legal(c, placed):
                    return c
        return None

    def put_near(self, part, fp, host, apad, opad, reach, layers, a=0.0, centre=False):
        """A part that only has to be on the same copper as `host`'s pad,
        not beside it: the nearest legal spot within `reach`, square to the
        board's 45-degree grid, on the first of `layers` that has room --
        and with `centre`, failing that, the outward centre's nearest."""
        c = self._satellite(part, fp, host.block, layers[0], host, apad, opad, reach,
                            round(a / 45.0) * 45.0, [], layers=layers, turn=45)
        if c is None and centre:
            c = self._near_centre(part, fp, host, apad, opad)
        if c is None:
            self.missed.append((part[0], part[1], fp, host.block, layers[0], part[2]))
            return None
        self.parts.append(c); self.new.append(c)
        return c

    def _near_centre(self, part, fp, host, apad, opad, step=0.25):
        """The spot inside R 17 on the outward face whose pad `opad` is
        nearest `host`'s pad `apad`, under the centre's own rules."""
        import padpos
        ref, value, note = part
        hx, hy = next((x, y) for n, x, y, w, h in padpos.pad_xy(host) if n == apad)
        CENTRE.add(ref)
        cands = []
        k = int(G.ZONE_R0 / step)
        for ang in (0, 45, 90, 135, 180, 225, 270, 315):
            probe = Part.at_xy(ref, value, fp, 0.0, 0.0, ang, "centre", "F.Cu", note)
            px, py = next((x, y) for n, x, y, w, h in padpos.pad_xy(probe) if n == opad)
            for i in range(-k, k + 1):
                for j in range(-k, k + 1):
                    x, y = i * step, j * step
                    if hypot(x, y) <= G.ZONE_R0:
                        cands.append((hypot(x + px - hx, y + py - hy), x, y, ang))
        cands.sort()
        # ... but not in front of the CPU: between the M3 head at (0, -9.5)
        # and C1002 is the one way on the outward face from the RP2350 to
        # the header, encoder and LED, and a cap there left five of them
        # unrouted
        cpu = G.WEDGE_ANG[1]
        for d, x, y, ang in cands:
            if abs((degrees(atan2(y, x)) - cpu + 540) % 360 - 180) < G.WEDGE_SPAN / 2 + 8:
                continue
            c = Part.at_xy(ref, value, fp, x, y, ang, "centre", "F.Cu",
                           note + f"; in the outward centre, {d:.0f} mm from {host.ref}")
            if max(_radii(c)) <= G.ZONE_R0 and legal(c, self.parts):
                return c
        CENTRE.discard(ref)
        return None

    def put_rigid(self, build, x, y, gs, reach=4.0, step=0.25, r_max=None):
        """A rigid group in the centre: build(x0, y0, g) -> [Part], turned by
        the first of `gs` that fits anywhere within `reach`, at the nearest
        legal (x0, y0) to (x, y) for that turn. All or nothing."""
        n = int(reach / step)
        cands = sorted((hypot(i, j) * step, x + i * step, y + j * step)
                       for i in range(-n, n + 1) for j in range(-n, n + 1)
                       if hypot(i, j) * step <= reach)
        for g in gs:
            for _, x0, y0 in cands:
                built = build(x0, y0, g)
                placed = self.parts[:]
                for c in built:
                    CENTRE.add(c.ref)
                    if max(_radii(c)) > (r_max or G.ZONE_R0) or not legal(c, placed):
                        break
                    placed.append(c)
                else:
                    self.parts += built; self.new += built
                    return built
        return None

    def pair(self, chips, pitch=5.0, layer="B.Cu"):
        """build() for two SIT3088 side by side and turned alike, each with its
        100 n past pin 8, the end of its bus-side row, and a receiver its
        120 R beyond its A and B pins: the same group twice, so the four
        transceivers read as two copies of one thing. chips: [(ref, note,
        cap, term, tnote)], in order along the row."""
        import padpos
        def build(x0, y0, g):
            out = []
            ca, sa = cos(radians(g)), sin(radians(g))
            for k, (ref, note, cap, term, tnote) in enumerate(chips):
                d = (k - (len(chips) - 1) / 2) * pitch
                u = Part.at_xy(ref, "SIT3088", FP["DFN8"], x0 + d * ca, y0 + d * sa, g, "centre",
                               layer, note)
                out.append(u)
                pads = {n: (px, py) for n, px, py, w, h in padpos.pad_xy(u)}
                ax, ay = pads["8"][0] - pads["7"][0], pads["8"][1] - pads["7"][1]
                n = hypot(ax, ay)
                out.append(Part.at_xy(cap, "100n", FP["C0402"], pads["8"][0] + ax / n * 1.3,
                                      pads["8"][1] + ay / n * 1.3, degrees(atan2(ay, ax)) + 90,
                                      "centre", layer, f"{ref} decoupling, at VCC"))
                if term:
                    (ux, uy) = u.rect[0]
                    mx, my = (pads["6"][0] + pads["7"][0]) / 2, (pads["6"][1] + pads["7"][1]) / 2
                    vx, vy = mx - ux, my - uy
                    m = hypot(vx, vy)
                    out.append(Part.at_xy(term, "120R", FP["R0603"], mx + vx / m * 2.6,
                                          my + vy / m * 2.6,
                                          degrees(atan2(pads["7"][1] - pads["6"][1],
                                                        pads["7"][0] - pads["6"][0])),
                                          "centre", layer, tnote))
            return out
        return build

    def put_first(self, ref, value, fp, tries, rot=0, note="", rots=None, reach=16.0):
        """put() at the first of several (block, layer, r, s) that works."""
        for blk, layer, r, s in tries:
            if self.put(ref, value, fp, blk, r, s, rot, layer, note, rots=rots,
                        reach=reach) is not None:
                return self.parts[-1]
            self.missed.pop()
        self.missed.append((ref, value, fp, tries[0][0], tries[0][1], note))
        return None

    def put_xy(self, ref, value, fp, x, y, ang=0, note="", angs=None, step=0.25,
               reach=9.0, layer="F.Cu", r_max=None):
        """The centre's version of put(): board coordinates, either face."""
        cands = []
        n = int(reach / step)
        for i in range(-n, n + 1):
            for j in range(-n, n + 1):
                d = hypot(i, j) * step
                if d <= reach:
                    cands.append((d, x + i * step, y + j * step))
        cands.sort()
        CENTRE.add(ref)
        for _, cx, cy in cands:
            for a in (angs or (ang,)):
                c = Part.at_xy(ref, value, fp, cx, cy, a, "centre", layer, note)
                if max(_radii(c)) <= (r_max or G.ZONE_R0) and legal(c, self.parts):
                    self.parts.append(c); self.new.append(c)
                    return c
        CENTRE.discard(ref)
        self.missed.append((ref, value, fp, "centre", layer, note))
        return None

# ------------------------------------------------------------- floorplan ----
OPTIONS = dict(tvs="SMC", usb=True, port_b="GH6", can_rot=90)

def board_s(**opt):
    """opt: tvs "SMC" (SMDJ54A, 3 kW) or "SMB" (SMBJ54A, 600 W); usb False
    moves USB to the expansion board; port_b "GH6" or "SH6"; can_rot 0 puts
    a can's pins radial, 90 tangential."""
    o = dict(OPTIONS, **opt)
    global KEPT_VIAS, BUS
    KEPT_VIAS = encoder_vias(False)              # board A's vias, as they are
    BUS = "pads"
    S = Sketch()
    S.options = o
    F, B = "F.Cu", "B.Cu"

    # ---- power wedge, 230 deg --------------------------------------------
    # The two cans first: they are the biggest thing on the board and there
    # are exactly two places they go. Either side of the axis, as far in as
    # the centre keepout allows, so the rim between them is free for the
    # bus pads -- the bus enters straight into the bulk.
    S.put("C1001", "100u/100V polymer", FP["CAN"], PWR, 22.4, -5.6, o["can_rot"], F,
          "bulk, C2887236, Dia 10 x 12")
    S.put("C1002", "100u/100V polymer", FP["CAN"], PWR, 22.4, 5.6, o["can_rot"], F,
          "bulk, C2887236, Dia 10 x 12")
    # Back, largest first: one RS-485 port at the rim, facing out (rot 270
    # on the back puts a horizontal housing's mouth radially outward), and
    # the TVS radial under the VMOT pad, where the bus arrives.
    S.put("J14", "RS485 port A", FP["GH6"], PWR, 28.8, 12.0, 270, B,
          "full-duplex RS-485, JST-GH 6, motor-facing side, mouth outward")
    tvs = ("SMDJ54A", FP["SMC"], "3 kW") if o["tvs"] == "SMC" else ("SMBJ54A", "Diode_SMD:D_SMB", "600 W")
    S.put("D1001", tvs[0], tvs[1], PWR, 25.5, -11.0, 0, B,
          f"bus TVS, {tvs[2]}, 54 V standoff, 60-66 V breakdown, under the VMOT pad",
          rots=(0, 90))
    # The 12 V gate buck, inboard of them.
    S.put("U11", "LMR38010 12V", FP["PPAD"], PWR, 20.0, -1.0, 90, B,
          "12 V gate rail, 4.2-80 V in", rots=(90, 0))
    S.put("L1001", "33u", FP["L4018"], PWR, 20.0, 5.0, 0, B, "12 V buck inductor",
          rots=(0, 90))
    S.put("C1003", "2u2/100V", FP["C1210"], PWR, 23.5, -1.0, 90, B, "12 V buck input",
          rots=(90, 0))
    S.put("C603", "22u/25V", FP["C1206"], PWR, 23.5, 3.0, 90, B, "+12 V out",
          rots=(90, 0))
    S.put("Q1001", "2N7002", FP["SOT23"], PWR, 18.8, -8.0, 0, B,
          "GATE_EN high pulls EN low: kills the gate rail", rots=(0, 90))
    for ref, val, r, s, note in (
            ("C1005", "100n", 22.5, -4.0, "12 V buck bootstrap"),
            ("R1001", "100k", 18.0, 6.5, "12 V feedback, top"),
            ("R1002", "11k", 18.0, 7.7, "12 V feedback, bottom"),
            ("R1003", "470k", 18.0, -10.0, "EN from VMOT: on unless pulled down"),
            ("R1004", "22k", 18.0, -11.2, "EN divider, bottom"),
            ("R1005", "10k", 18.0, 9.0, "GATE_EN pull-down: rail ON through reset (E9 < 9 k)")):
        S.put(ref, val, FP["R0402"] if ref[0] == "R" else FP["C0402"], PWR, r, s, 90, B, note,
              rots=(90, 0))
    # The bus divider, which lived here beside J8: VBUS is the In2 sector.
    for ref, val, r, s, note in (
            ("R801", "56k 0.1%", 25.5, 11.0, "bus divider, high leg"),
            ("R806", "56k 0.1%", 23.5, 11.0, "bus divider, high leg, second half"),
            ("R802", "4k7 0.1%", 21.5, 11.0, "bus divider, low leg"),
            ("C801", "100n", 19.5, 11.0, "ADC2 filter")):
        S.put(ref, val, FP["R0603"] if ref[0] == "R" else FP["C0603"], PWR, r, s, 0, B, note,
              rots=(0, 90))

    # ---- the centre, outward face -----------------------------------------
    # The expansion header, long axis along x, between the four screw heads:
    # the middle of the stack, 2 mm clear of every head. GPIO19-24 come from
    # the CPU 21 mm away; VMOT and GND pins are paired so a PD board's current
    # passes the encoder as a dipole, not a loop.
    j13 = Part.at_xy("J13", "EXPANSION", FP["EXP"], 0.0, 0.0, 90.0, "centre", "F.Cu",
                     "2x10 1.27 SMD: 4 VMOT, 5 GND, +5V, +3V3, RUN, GPIO19-24, 2 spare")
    if legal(j13, S.parts):
        S.parts.append(j13); S.new.append(j13)
    else:
        S.missed.append(("J13", "EXPANSION", FP["EXP"], "centre", "F.Cu", ""))

    # ---- signal wedge, 334 deg ------------------------------------------
    # Outward face: USB-C at the rim, mouth out; the 5 V buck where J9 was;
    # the 3V3 LDO row kept on its radius.
    if o["usb"]:
        S.put("J12", "USB-C", FP["USBC"], SIG, 27.6, -10.0, 90, F,
              "USB 2.0 data + 5 V logic, no PD; HRO TYPE-C-31-M-12")
    S.put("U12", "LMR38010 5V", FP["PPAD"], SIG, 22.0, 5.0, 90, F, "5 V logic rail",
          rots=(90, 0))
    S.put("L1002", "33u", FP["L4018"], SIG, 26.5, 5.5, 0, F, "5 V buck inductor",
          rots=(0, 90))
    S.put("C1006", "2u2/100V", FP["C1210"], SIG, 22.0, 10.0, 90, F, "5 V buck input",
          rots=(90, 0))
    S.put("C604", "22u/10V", FP["C1206"], SIG, 26.0, 10.0, 90, F, "+5V out",
          rots=(90, 0))
    lrow = [("U9", "SPX3819-3.3", "Package_TO_SOT_SMD:SOT-23-5", 90, "5 V -> 3V3"),
            ("C712", "1u", FP["C0603"], 0, "3V3 out"),
            ("C713", "1u", FP["C0603"], 0, "5 V in"),
            ("R705", "10k", FP["R0402"], 0, "FAULT_n pull-up"),
            ("R803", "10k 0.1%", FP["R0603"], 0, "thermistor divider -> ADC3"),
            ("C802", "100n", FP["C0603"], 0, "ADC3 filter"),
            ("R704", "10k", FP["R0402"], 0, "RUN pull-up"),
            ("C717", "100n", FP["C0402"], 0, "RUN")]
    for k, (ref, val, fp, rot, note) in enumerate(lrow):
        S.put(ref, val, fp, SIG, 18.4, -9.5 + 1.6 * k, rot, F, note, rots=(rot, rot + 90))
    for ref, val, r, s, note in (
            ("C1008", "100n", 19.0, 8.0, "5 V buck bootstrap"),
            ("R1006", "100k", 19.0, 9.2, "5 V feedback, top"),
            ("R1007", "27k", 19.0, 10.4, "5 V feedback, bottom")):
        S.put(ref, val, FP["R0402"] if ref[0] == "R" else FP["C0402"], SIG, r, s, 90, F,
              note, rots=(90, 0))
    # The status LED and BOOTSEL: into the centre's corners beside J13, which
    # is the one place on the outward face with room left. Milliamps, and no
    # iron: nothing the encoder under the far face can see.
    S.put_xy("D1103", "RGB", FP["LED"], 8.5, -7.5, 0, "status LED, three GPIO",
             angs=(0, 90, 45, -45))
    for k, ref in enumerate(("R1107", "R1108", "R1109")):
        S.put_xy(ref, "1k", FP["R0402"], 9.0 + 1.2 * k, -4.8, 90, "LED series",
                 angs=(90, 0))
    S.put_xy("SW1", "BOOTSEL", FP["SW"], -8.5, -7.5, 0, "BOOTSEL to GND, via R701",
             angs=(0, 90, 45, -45))

    # Back: the other RS-485 port at the rim, the two transceivers and their
    # termination, the USB front end.
    pb = FP["GH6"] if o["port_b"] == "GH6" else FP["SH6"]
    S.put("J15", "RS485 port B", pb, SIG, 29.0, 0.0 if not o["usb"] else 8.0, 270, B,
          f"full-duplex RS-485, JST-{o['port_b'][:2]} 6, wired pin-for-pin with J14")
    S.put("U14", "SIT3088", FP["DFN8"], SIG, 23.5, 3.0, 0, B, "RS-485 receiver, DOWN pair")
    S.put("U15", "SIT3088", FP["DFN8"], SIG, 23.5, 8.0, 0, B, "RS-485 driver, UP pair")
    if o["usb"]:
        S.put("U13", "USBLC6-2SC6", FP["SOT236"], SIG, 25.0, -6.0, 0, B,
              "USB ESD; pin 5 on VBUS is fine without PD", rots=(0, 90))
    for ref, val, fp, r, s, note in (
            ("D1101", "SM712", FP["SOT23"], 26.5, 1.0, "RS-485 ESD, DOWN pair"),
            ("D1102", "SM712", FP["SOT23"], 26.5, 11.0, "RS-485 ESD, UP pair"),
            ("C1102", "100n", FP["C0402"], 21.3, 3.0, "U14 decoupling"),
            ("C1103", "100n", FP["C0402"], 21.3, 8.0, "U15 decoupling"),
            ("R1105", "120R", FP["R0603"], 20.0, 11.0, "DOWN termination, behind JP1"),
            ("R1106", "120R", FP["R0603"], 20.0, 13.0, "UP termination, behind JP2"),
            ("JP1", "TERM", FP["JP"], 23.0, 12.5, "open by default (F-19)"),
            ("JP2", "TERM", FP["JP"], 18.5, 8.0, "open by default")):
        S.put(ref, val, fp, SIG, r, s, 0, B, note, rots=(0, 90))
    usb_parts = [
        ("R1101", "5k1", FP["R0402"], 22.5, -4.0, "CC1 pull-down: a sink, 5 V only"),
        ("R1102", "5k1", FP["R0402"], 22.5, -5.2, "CC2 pull-down"),
        ("R1103", "10k", FP["R0402"], 22.5, -8.0, "VBUS_DET divider, top"),
        ("R1104", "15k", FP["R0402"], 22.5, -9.2, "VBUS_DET divider, bottom -> GPIO25"),
        ("D1002", "SS14", FP["SOD123F"], 19.5, -4.5, "USB VBUS -> +5V, OR'd with the buck"),
        ("D1003", "SS14", FP["SOD123F"], 19.5, -9.0, "5 V buck -> +5V")]
    for ref, val, fp, r, s, note in usb_parts:
        if o["usb"] or ref == "D1003":
            S.put(ref, val, fp, SIG, r, s, 90, B, note, rots=(90, 0))
    for ref, val, r, s in (("TP4", "ADC3", 27.5, -11.0), ("TP5", "SWCLK", 29.5, -9.0),
                           ("TP6", "SWDIO", 29.5, -11.0), ("TP7", "RUN", 27.5, -13.0)):
        S.put(ref, val, FP["TP"], SIG, r, s, 0, B, f"test point, {val}")
    return S

# Parts allowed into the centre, inside R 17 (decided 2026-09-22): low current,
# nothing switching, nothing ferrous. The outward face takes what wants to be
# reached; the motor-facing face takes parts under 1.5 mm, outside the magnet
# keepout. The bulk cans were moved in too, at the user's call, as far from
# the shaft axis as the screw heads allow.
CENTRE_OK = {"J13", "D1103", "R1107", "R1108", "R1109", "SW1",
             "TP4", "TP5", "TP6", "TP7",
             "U14", "U15", "U17", "U18", "C1102", "C1103", "C1104", "C1105",
             "R1105", "R1106", "JP1", "JP2",
             "R1103", "R1104", "U16", "R1005", "C1001", "C1002",
             # the bucks' overflow, 2026-09-23: 4.7 uF inputs, the 12 V's second 22 uF
             "C1004", "C1007", "C1009", "C1010", "C1011"}

def _pad_of(S, ref, num):
    import padpos
    p = next(q for q in S.parts if q.ref == ref)
    return next((x, y) for n, x, y, w, h in padpos.pad_xy(p) if n == num)

def spine_path(S):
    """The VBUS spine's path: from below the header's VMOT row (pins 1, 3, 5,
    7) to the XT30's VMOT pin."""
    if not all(any(p.ref == r for p in S.parts) for r in ("J4", "J13")):
        return
    import padpos
    j13 = next(p for p in S.parts if p.ref == "J13")
    row = [(x, y) for n, x, y, w, h in padpos.pad_xy(j13) if n in ("1", "3", "5", "7")]
    x0 = min(x for x, _ in row) + 1.0
    y0 = min(y for _, y in row) - 1.0
    SPINE[:] = [((x0, y0), _pad_of(S, "J4", "2"), SPINE_W)]

def place_xt30(S):
    """The XT30 on the power wedge's axis, outward face, mouth out, as far
    out as the ports on the far face let its pegs come: its face in 0.05 mm
    steps from the edge. Then the plug's path to the edge is kept clear."""
    CENTRE.add("J4")                        # its back end is inside R 17
    for k in range(int(8.0 / 0.05)):
        face = G.R - 0.05 * k
        c = Part("J4", "XT30", FP["XT30"], face - XT30_FACE + _xcy, 0.0, 270, PWR, AX[PWR],
                 "F.Cu", f"bus input, AMASS XT30PW-M as the rp2350-motor-controller's J9: "
                 f"pin 1 GND, pin 2 VMOT; mouth outward, {G.R - face:.1f} mm in from the edge")
        if legal(c, S.parts):
            S.parts.append(c); S.new.append(c)
            PLUG[:] = [("F.Cu", face, G.R + 1.0, AX[PWR], degrees(asin(XT30_W / face)))]
            return c
    CENTRE.discard("J4")
    S.missed.append(("J4", "XT30", FP["XT30"], PWR, "F.Cu", "bus input"))
    return None

def board_s_open(power="two", port="SH6", can_rot=90, back_centre=True, tvs="SMC",
                 tvs_first=False, tvs_centre=False, ports=2, cans="wedge", relay=False,
                 exp="2x10", exp_first=False, buck5="sig", enc_vias="kept", mod_snap=45,
                 mod_flex=0.0, in_ccw=True, bus="pads"):
    """Board S with the centre open.

      power    "two": the spec's two LMR38010; "one": one LMR38010, a load
               switch on GATE_EN and a SOT-23-6 buck for 5 V
      port     "SH6" or "GH6" for the RS-485 connectors; ports 1 or 2
      relay    the CPU relays between the two ports (point-to-point links)
               instead of wiring them pin for pin
      cans     "wedge": both in the power wedge; "centre": in the middle
      tvs      "SMC" (SMDJ54A, 3 kW) or "SMB" (SMBJ54A, 600 W)
      bus      "pads": two arc pads for 14 AWG leads; "xt30": the XT30 on the
               power wedge's rim (with relay and cans="centre" only)
    """
    global KEPT_VIAS, BUS
    KEPT_VIAS = encoder_vias(enc_vias == "moved")
    BUS = bus
    PLUG[:] = []
    SPINE[:] = []
    ESC_KEEP[:] = []
    xt30 = bus == "xt30"
    assert not xt30 or (relay and ports == 2 and cans == "centre"), "xt30: relay, cans centre"
    S = Sketch()
    S.options = dict(power=power, port=port, can_rot=can_rot, back_centre=back_centre,
                     tvs=tvs, tvs_first=tvs_first, ports=ports, cans=cans, relay=relay,
                     exp=exp, enc_vias=enc_vias, bus=bus)
    F, B = "F.Cu", "B.Cu"
    BK = B if back_centre else F                 # where the centre's low parts go
    pf = FP["SH6"] if port == "SH6" else FP["GH6"]

    # ---- the bulk -----------------------------------------------------------
    if cans == "centre":
        # One either side of the CPU's axis, as far from the shaft axis as the
        # four screw heads let them be: their ripple current is the one thing
        # here the encoder could see. They reach past R 17 into the link
        # wedges' empty fronts. VBUS has to reach them on In2. With the XT30
        # the power wedge's quadrant is its back end, so C1001 goes to the
        # other side of the M3 head at 180 deg, in front of phases B and C.
        for ref, ang in (("C1001", 135.0 if xt30 else 230.0), ("C1002", 320.0)):
            x, y = PL.polar_xy(ang, 14.5)
            # + lead outward (the footprint's pad 1 is its origin, pad 2 is
            # 5 mm along +x): it lands on In2's VBUS at R 17, the short way
            # to the bridges, and the - lead inboard on the ground planes.
            S.put_xy(ref, "100u/100V polymer", FP["CAN"], x, y, ang + 180,
                     "bulk, C2887236, Dia 10 x 12, in the centre, + lead outward",
                     angs=(ang + 180, ang + 270, ang + 225, ang + 135), reach=4.0, r_max=21.0)
    else:
        S.put("C1001", "100u/100V polymer", FP["CAN"], PWR, 22.4, -5.6, can_rot, F,
              "bulk, C2887236, Dia 10 x 12")
        S.put("C1002", "100u/100V polymer", FP["CAN"], PWR, 22.4, 5.6, -can_rot, F,
              "bulk, C2887236, Dia 10 x 12")

    # ---- every edge connector before anything that could take its rim --------
    if ports == 2:
        # With the relay, port IN takes the counter-clockwise slot, toward the
        # CPU wedge, where its two transceivers are (U14, U15 at 223-246
        # deg), and OUT the clockwise one by U17 and U18 (190-207). The other
        # way round the two ports' pairs crossed each other through the
        # CPU's In3 traffic on the way in, and the last net on board S's
        # first routes was always one of them.
        s_in = 6.5 if (relay and in_ccw) else -6.5
        S.put("J14", "RS485 IN (upstream)" if relay else "RS485 IN", pf, PWR, 29.0, s_in,
              270, B, f"full-duplex RS-485, JST-{port[:2]} 6, motor-facing rim, mouth outward")
        S.put("J15", "RS485 OUT (downstream)" if relay else "RS485 OUT", pf, PWR, 29.0, -s_in,
              270, B, "a link of its own, relayed by the CPU" if relay else
              "the same six nets, pin for pin: the chain's way on")
    else:
        S.put("J14", "RS485", pf, PWR, 29.0, 12.0, 270, B,
              f"full-duplex RS-485, JST-{port[:2]} 6, motor-facing rim; chained in the harness")
    S.put("J12", "USB-C", FP["USBC"], SIG, 27.6, -10.0, 90, F,
          "USB 2.0 data + 5 V logic, no PD; HRO TYPE-C-31-M-12")
    tv = ("SMDJ54A", FP["SMC"], "3 kW") if tvs == "SMC" else ("SMBJ54A", FP["SMB"], "600 W")
    tvs_note = f"bus TVS, {tv[2]}, 54 V standoff, 60-66 V breakdown"
    if xt30:
        place_xt30(S)
        # The TVS under it on the motor-facing face, between its pins and its
        # pegs, cathode toward the VMOT pin: the one place left on the board
        # for an SMC, and the right one -- before the ESD parts and the bucks,
        # which otherwise take it.
        S.put("D1001", tv[0], tv[1], PWR, 21.7, 0.0, 270, B,
              tvs_note + "; under the XT30, cathode toward its VMOT pin", reach=1.5, step=0.05)
    # ESD at the ports, one SM712 per pair: a relay has four pairs, not two.
    if xt30:
        # one a side outside the XT30's pegs on the motor-facing face (the
        # TVS fills the middle), and its twin on the outward face over it:
        # the DOWN pair's on the port's face, the UP pair's through a via
        esd = []
        for ref, s, face, what in (("D1101", 8.0, B, "IN, DOWN pair"), ("D1104", -8.0, B, "OUT, DOWN pair"),
                                   ("D1102", 8.0, F, "IN, UP pair"), ("D1105", -8.0, F, "OUT, UP pair")):
            S.put(ref, "SM712", FP["SOT23"], PWR, 23.5, s, 0, face,
                  f"RS-485 ESD, {what}, at the port", rots=(0, 90), reach=3.0, step=0.05)
    elif ports != 2:
        esd = [("D1101", 6.0, "DOWN pair"), ("D1102", 9.0, "UP pair")]
    elif relay:
        k = 1 if in_ccw else -1
        esd = [("D1101", 8.0 * k, "IN, DOWN pair"), ("D1102", 4.0 * k, "IN, UP pair"),
               ("D1104", -4.0 * k, "OUT, DOWN pair"), ("D1105", -8.0 * k, "OUT, UP pair")]
    else:
        esd = [("D1101", -3.0, "DOWN pair"), ("D1102", 3.0, "UP pair")]
    for ref, s, what in esd:
        S.put(ref, "SM712", FP["SOT23"], PWR, 24.5, s, 0, B,
              f"RS-485 ESD, {what}, at the port", rots=(0, 90))

    # The expansion header, near the middle of the stack. Not ON the axis: the
    # MT6701's own vias come through there, 2.5-4.6 mm out, and a 1.27 mm
    # header's pad rows cannot straddle them. If the centre is full, the power
    # wedge's front, which the cans may have left empty.
    exp_fp = FP["EXP"] if exp == "2x10" else "Connector_PinHeader_1.27mm:PinHeader_2x08_P1.27mm_Vertical_SMD"
    exp_note = ("2x10 1.27 SMD: 4 VMOT, 5 GND, +5V, +3V3, RUN, GPIO19-24, SWCLK, SWDIO"
                if exp == "2x10" else
                "2x8 1.27 SMD: 3 VMOT, 3 GND, +5V, RUN, GPIO19-24, SWCLK, SWDIO -- "
                "no +3V3: the expansion board makes its own from +5V")
    def place_exp():
        if enc_vias == "moved":
            j = Part.at_xy("J13", "EXPANSION", exp_fp, 0.0, 0.0, 90.0, "centre", F,
                           exp_note + "; across the axis, the MT6701's vias moved clear")
            CENTRE.add("J13")
            if legal(j, S.parts):
                S.parts.append(j); S.new.append(j)
                return
        if S.put_xy("J13", "EXPANSION", exp_fp, 0.0, 1.0, 90.0, exp_note,
                    angs=(90, 0), reach=9.0) is None:
            S.missed.pop()
            # the power wedge's front, at its 256 deg end, leaving the 204 deg
            # end behind the VMOT pad for the TVS
            S.put_first("J13", "EXPANSION", exp_fp,
                        [(PWR, F, 24.0, 6.5), (SIG, F, 22.0, 6.0), (PWR, F, 24.3, 0.0)],
                        0, exp_note, rots=(0, 90))
    if exp_first:
        place_exp()
    if xt30 and exp_first:
        spine_path(S)

    # ---- the TVS ----------------------------------------------------------
    def place_tvs():
        # It clamps regen, which is milliseconds, so anywhere on the VBUS
        # plane will do -- behind the VMOT pad by preference; with the XT30,
        # on the motor-facing face under it, by its VMOT pin.
        if any(p.ref == "D1001" for p in S.parts):
            return                              # the XT30 put it under itself
        got = S.put_first("D1001", tv[0], tv[1],
                          [(PWR, B, 21.5, -4.0), (PWR, F, 23.0, -7.5), (PWR, F, 22.0, 0.0),
                           (SIG, B, 24.0, 8.0), (SIG, B, 21.0, -2.0), (SIG, F, 21.0, 8.0)],
                          270, tvs_note, rots=(270, 0, 240, 300, 210, 330))
        if got is None and tvs_centre:
            S.missed.pop()
            S.put_xy("D1001", tv[0], tv[1], -8.0, -9.0, 45,
                     tvs_note + "; in the outward centre, toward the bus pads",
                     angs=(45, 0, 90, 135), reach=9.0)
    if tvs_first:
        place_tvs()

    # ---- the rails, each buck a compact module ------------------------------
    def lmr(u, cin, cbulk, boot, l, couts, fbt, fbb, label, vin_note, tries, rt=None):
        # VREF is 1.000 V (SNVSC73B 7.5), so the bottom leg is 100k / (Vout - 1):
        # 9.09k for 12 V and 24.9k for 5 V, the datasheet's own Table 9-1
        # values. The sketch had 11k and 27k, which is 10.1 V and 4.7 V.
        twelve = label == "+12 V"
        parts = {"u": (u, "LMR38010SDDAR", f"{label} rail, {vin_note}"),
                 "cin": (cin, "100n/100V", f"{label} buck input, HF, at VIN/GND"),
                 "cbulk": (cbulk, "4u7/100V", f"{label} buck input, 4.7 uF ceramic"),
                 "boot": (boot, "100n", f"{label} bootstrap, BOOT to SW"),
                 "l": (l, "68u" if twelve else "33u",
                       f"{label} buck inductor, SWPA4030S" + ("680MT" if twelve else "330MT")),
                 "fbt": (fbt, "100k", f"{label} feedback, top"),
                 "fbb": (fbb, "9k09" if twelve else "24k9", f"{label} feedback, bottom")}
        for role, ref in zip(("cout", "cout2", "cout3"), couts):
            parts[role] = (ref, "22u/25V", f"{label} out")
        if rt:
            # RT/SYNC must not float or be grounded: 64.9k is 400 kHz, the
            # datasheet's design point for 33 uH at 5 V from 48 V.
            parts["rt"] = (rt, "64k9", f"{label} RT: 400 kHz")
        if twelve:
            # EN rises at 1.25 V typ, 1.4 V max, and falls at 1.10 V: 470k over
            # 68k starts the rail at 9.9 V typ (11.1 V worst case) and drops it
            # at 8.7 V, inside the 12 V floor. The sketch's 22k never started
            # below 26 V. EN may not exceed VIN + 0.3 V; a divider cannot, and
            # it holds the 2N7002's drain to 11 V at the TVS's 87 V clamp.
            parts["ent"] = ("R1003", "470k", "EN from VMOT: UVLO, on unless pulled down")
            parts["enb"] = ("R1004", "68k", "EN divider, bottom: 9.9 V start")

        rail = "12" if twelve else "5"
        for blk, face, r, s in tries:
            got = S.put_module(MOD_LMR38010, parts, blk, face, r, s, snap=mod_snap,
                               flex=mod_flex, sats=SAT_LMR38010[rail])
            if got:
                # the module's near parts wait until both modules are down:
                # put first, the 12 V one's took the only room the 5 V had
                other = "B.Cu" if face == "F.Cu" else "F.Cu"
                for role, fpk, arole, apad, opad, reach in NEAR_LMR38010[rail]:
                    if role in parts:
                        host = next(c for c in got if c.ref == parts[arole][0])
                        pending.append((role != "cbulk", parts[role], FP[fpk], host, apad,
                                        opad, reach, (face, other), got[0].ang))
                return got[0]
        for role in parts:
            S.missed.append((parts[role][0], parts[role][1], "", tries[0][0], tries[0][1],
                             f"{label} buck: no room for it as a compact module"))
        return None
    pending = []

    def near(host, ref, val, fp, rot, note):
        return S.put_first(ref, val, fp, [(host.block, host.layer, host.r, host.s)],
                           rot, note, rots=(rot, (rot + 90) % 180), reach=8.0)

    if power == "two":
        u11 = lmr("U11", "C1003", "C1004", "C1005", "L1001", ("C603", "C1009"), "R1001",
                  "R1002", "+12 V", "EN on a VMOT divider, GATE_OFF pulls it down",
                  [(SIG, B, 21.0, 3.0), (PWR, B, 20.0, 4.0), (SIG, F, 22.0, 5.0),
                   (PWR, F, 22.0, 0.0)], rt="R1008")
        if u11:
            # The 2N7002 beside the EN divider, its drain turned to it, and its
            # pull-down at its gate: EN is then all short links the fan-out
            # makes (fanout.near_links). Placed as "the nearest spot to the
            # IC" it went 6 mm off into the USB corner and EN_12V was the last
            # net the router could not make.
            import padpos
            pads = {(p.ref, n): (x, y) for p in S.parts if p.ref in ("R1003", "R1004")
                    for n, x, y, w, h in padpos.pad_xy(p)}
            got = None
            if ("R1003", "2") in pads and ("R1004", "1") in pads:
                ex = (pads[("R1003", "2")][0] + pads[("R1004", "1")][0]) / 2
                ey = (pads[("R1003", "2")][1] + pads[("R1004", "1")][1]) / 2
                (ux, uy) = u11.rect[0]
                dx, dy = ex - ux, ey - uy
                n = hypot(dx, dy) or 1.0
                tx, ty = ex + dx / n * 2.4, ey + dy / n * 2.4
                axis = AX[u11.block]
                tr = hypot(tx, ty)
                ts = radians((degrees(atan2(ty, tx)) - axis + 540) % 360 - 180) * tr
                def drain_cost(rot):
                    c = Part(("Q1001"), "", FP["SOT23"], tr, ts, rot, u11.block, axis, u11.layer)
                    d = next((x, y) for nn, x, y, w, h in padpos.pad_xy(c) if nn == "3")
                    return hypot(d[0] - ex, d[1] - ey)
                rots = sorted(range(0, 360, 45), key=drain_cost)
                got = S.put("Q1001", "2N7002", FP["SOT23"], u11.block, tr, ts, rots[0], u11.layer,
                            "GATE_OFF high pulls EN low: kills the gate rail", rots=rots, reach=3.0)
                if got is None:
                    S.missed.pop()
            if got is None:
                # no room beyond the divider (the module can put it at the
                # wedge's edge): then anywhere on the module's face with its
                # drain by the divider's EN pad -- not "near the IC", which
                # once put it 15 mm off by the USB ESD part, and not the other
                # face, where it takes the 5 V module's room
                enb = next((p for p in S.parts if p.ref == "R1004"), None)
                if enb is not None:
                    got = S.put_near(("Q1001", "2N7002", "GATE_OFF high pulls EN low: kills the "
                                      "gate rail"), FP["SOT23"], enb, "1", "3", 6.0,
                                     (u11.layer,))
                    if got is None:
                        S.missed.pop()
            if got is None:
                got = near(u11, "Q1001", "2N7002", FP["SOT23"], 0,
                           "GATE_OFF high pulls EN low: kills the gate rail")
            if got is not None:
                g = next((x, y) for nn, x, y, w, h in padpos.pad_xy(got) if nn == "1")
                (qx, qy) = got.rect[0]
                dx, dy = g[0] - qx, g[1] - qy
                n = hypot(dx, dy) or 1.0
                tx, ty = g[0] + dx / n * 1.2, g[1] + dy / n * 1.2
                axis = AX[got.block]
                tr = hypot(tx, ty)
                ts = radians((degrees(atan2(ty, tx)) - axis + 540) % 360 - 180) * tr
                if S.put("R1005", "4k7", FP["R0402"], got.block, tr, ts, 90, got.layer,
                         "GATE_OFF pull-down: the rail is ON through reset, so a reset brakes; "
                         "4k7 clears E9", rots=(90, 0, 45, 135), reach=3.0) is None:
                    S.missed.pop()
                    note = ("GATE_OFF pull-down: the rail is ON through reset, so a reset brakes; "
                            "4k7 clears E9")
                    if S.put_near(("R1005", "4k7", note), FP["R0402"], got, "1", "1", 4.0,
                                  (got.layer,)) is None:
                        S.missed.pop()
                        near(u11, "R1005", "4k7", FP["R0402"], 90, note)
        # The 5 V module on the power wedge's front, beside the bus pads its
        # input comes from: over the 12 V one, on the other face of the signal
        # wedge, neither module could drop a via.
        tries5 = [(PWR, F, 23.5, 7.0), (SIG, F, 22.0, 5.0), (SIG, B, 21.0, 3.0), (PWR, B, 20.0, 4.0)]
        if buck5 == "pwr":
            # The power wedge's back, inboard of the ports, when the cans have
            # taken their pins out of it -- leaving the signal wedge's front.
            tries5 = [(PWR, B, 20.0, 0.0)] + tries5
        lmr("U12", "C1006", "C1007", "C1008", "L1002", ("C604", "C1010", "C1011"), "R1006",
            "R1007", "+5 V", "logic; EN tied to VIN", tries5, rt="R1009")
    else:
        u11 = lmr("U11", "C1003", "C1004", "C1005", "L1001", ("C603", "C1009"), "R1001",
                  "R1002", "+12 V", "the one 80 V buck",
                  [(SIG, F, 22.0, 5.0), (SIG, B, 21.0, 3.0), (PWR, B, 20.0, 4.0),
                   (PWR, F, 22.0, 0.0)])
        if u11:
            for ref, val, note in (("R1003", "470k", "EN from VMOT: UVLO"),
                                   ("R1004", "22k", "EN divider, bottom")):
                near(u11, ref, val, FP["R0402"], 90, note)
        S.put_xy("U16", "TPS22810", FP["SOT236"], 7.0, 10.0, 0,
                 "gate-rail load switch, enable = GATE_EN pulled up: a reset brakes. "
                 "No current limit.", angs=(0, 90, 45, -45), layer=BK)
        S.put_xy("R1005", "10k", FP["R0402"], 9.5, 7.0, 90, "GATE_EN pull-up", angs=(90, 0),
                 layer=BK)
        small = {"u": ("U12", "TPS560430", "12 V -> 5 V, 0.6 A, SOT-23-6"),
                 "l": ("L1002", "10u", "5 V buck inductor, 3 x 3"),
                 "cin": ("C1006", "4u7/25V", "5 V buck input"),
                 "cout": ("C604", "22u/10V", "+5V out"),
                 "boot": ("C1008", "100n", "5 V buck bootstrap"),
                 "fbt": ("R1006", "100k", "5 V feedback, top"),
                 "fbb": ("R1007", "22k", "5 V feedback, bottom")}
        for blk, face, r, s in ((PWR, B, 20.0, 5.0), (SIG, B, 21.0, 8.0), (SIG, F, 21.0, 8.0),
                                (PWR, F, 22.0, 0.0)):
            if S.put_module(MOD_SOT_BUCK, small, blk, face, r, s):
                break
        else:
            for role in small:
                S.missed.append((small[role][0], small[role][1], "", SIG, B,
                                 "5 V buck: no room for it as a compact module"))
    if not tvs_first:
        place_tvs()

    # ---- the centre -----------------------------------------------------------
    if not exp_first:
        place_exp()
    if relay:
        # Each port is a point-to-point full-duplex link: one receiver and one
        # driver per port, every driver alone on its pair so its enable is
        # tied on, and a 120 R across every receiver, always -- no jumpers.
        # Port IN on UART0 (GPIO0/1), port OUT on a PIO UART on GPIO2/3,
        # which were RS485_DE and RS485_TERM_EN and are not needed now.
        rs = None
        relay_chips = [
            ("U14", "port IN: receiver, DOWN pair -> UART0 RX", "C1102", "R1105",
             "port IN DOWN pair termination, always on"),
            ("U15", "port IN: driver, UP pair <- UART0 TX, DE on", "C1103", None, None),
            ("U17", "port OUT: driver, DOWN pair <- PIO TX, DE on", "C1104", None, None),
            ("U18", "port OUT: receiver, UP pair -> PIO RX", "C1105", "R1106",
             "port OUT UP pair termination, always on")]
    else:
        rs = [("U14", "SIT3088", FP["DFN8"], -5.5, -12.5, "RS-485 receiver, DOWN pair", BK),
              ("U15", "SIT3088", FP["DFN8"], -9.5, -10.0, "RS-485 driver, UP pair", BK),
              ("C1102", "100n", FP["C0402"], -5.5, -15.0, "U14 decoupling", BK),
              ("C1103", "100n", FP["C0402"], -11.5, -11.5, "U15 decoupling", BK),
              ("R1105", "120R", FP["R0603"], -8.0, -13.5, "DOWN termination, behind JP1", BK),
              ("R1106", "120R", FP["R0603"], -12.0, -8.0, "UP termination, behind JP2", BK),
              ("JP1", "TERM", FP["JP"], -9.0, -6.0, "open by default (F-19); reachable here", F),
              ("JP2", "TERM", FP["JP"], -13.0, -5.0, "open by default", F)]
    # Each transceiver turned so its bus pins (6 A, 7 B) face its own port's
    # pads -- the pairs arrive at the side they connect to, not round the far
    # side of the part -- then its 100 n at its VCC pin and, for a receiver,
    # its 120 R across its A and B pins, on the side toward the port.
    port_of = {"U14": "J14", "U15": "J14", "U17": "J15", "U18": "J15"}
    import padpos
    def port_centre(ref):
        jp = next((p for p in S.parts if p.ref == port_of.get(ref)), None)
        if jp is None:
            return None
        pads = [(px, py) for n, px, py, w, h in padpos.pad_xy(jp) if n in ("2", "3", "4", "5")]
        return sum(p[0] for p in pads) / len(pads), sum(p[1] for p in pads) / len(pads)
    def facing(ref, fp, x, y, face):
        angs = [0, 90, 45, -45, 135, -135, 180, -90]
        tgt = port_centre(ref)
        if tgt is None:
            return angs[:6]
        def cost(a):
            c = Part.at_xy(ref, "", fp, x, y, a, "centre", face, "")
            ab = [(px, py) for n, px, py, w, h in padpos.pad_xy(c) if n in ("6", "7")]
            mx, my = sum(p[0] for p in ab) / 2, sum(p[1] for p in ab) / 2
            return hypot(mx - tgt[0], my - tgt[1])
        return sorted(angs, key=cost)
    if relay and xt30:
        # Two identical pairs, turned alike and square to the board (asked
        # 2026-09-24: "make the SIT placement cosmetically cleaner"; the
        # pairs had each chip turned to face its port, at 0, 45 and 315
        # deg). The XT30's pins have taken the motor-facing quadrant they
        # were in, so: IN beside the CPU, OUT in the quadrant opposite.
        chip = {c[0]: c for c in relay_chips}
        # turned so the bus pins face the ports: shorter pairs, and their
        # escape vias go that way, not under whatever is on the other face
        ports_at = [PL.polar_xy(AX[PWR], 27.0)]
        def facing_ports(x, y, gs):
            def cost(g):
                u = Part.at_xy("UX", "", FP["DFN8"], x, y, g, "centre", BK)
                ab = [(px, py) for n, px, py, w, h in padpos.pad_xy(u) if n in ("6", "7")]
                mx, my = sum(q[0] for q in ab) / 2, sum(q[1] for q in ab) / 2
                return hypot(mx - ports_at[0][0], my - ports_at[0][1])
            return sorted(gs, key=cost)
        g0 = None
        for (a, b), (x, y) in zip(RELAY_PAIRS, RELAY_PAIRS_AT):
            got = S.put_rigid(S.pair([chip[a], chip[b]]), x, y,
                              (g0,) if g0 is not None else facing_ports(x, y, (0, 90, 180, 270)),
                              reach=5.0)
            if got is not None:
                g0 = got[0].ang
                ESC_KEEP.extend(s for u in got if u.fp == FP["DFN8"] for s in _escape_strips(u))
                continue
            # No room for the pair (the XT30's pins have its quadrant): each
            # chip alone with its own parts, turned as the other pair if it
            # can be, square to the board if not, at 45 deg if it must --
            # "place the rest loosely" (decided 2026-09-24).
            g1 = g0 if g0 is not None else 90
            for c in (chip[a], chip[b]):
                for gs in ((g1,), facing_ports(x, y, (g1 + 90, g1 + 180, g1 + 270)),
                           facing_ports(x, y, (45, 135, 225, 315))):
                    got = S.put_rigid(S.pair([c]), x, y, gs, reach=17.0)
                    if got is not None:
                        ESC_KEEP.extend(_escape_strips(got[0]))
                        break
                else:
                    # the chip, then its parts wherever they fit near it
                    ref, note, cap, term, tnote = c
                    u = S.put_xy(ref, "SIT3088", FP["DFN8"], x, y, g1, note,
                                 angs=(g1, g1 + 90, g1 + 180, g1 + 270, 45, 135, 225, 315),
                                 layer=BK, reach=17.0)
                    if u is None:
                        continue
                    (ux, uy) = u.rect[0]
                    S.put_xy(cap, "100n", FP["C0402"], ux, uy, 0, f"{ref} decoupling, at VCC",
                             angs=(0, 90, 45, 135), layer=BK, reach=5.0)
                    if term:
                        S.put_xy(term, "120R", FP["R0603"], ux, uy, 0, tnote,
                                 angs=(0, 90, 45, 135), layer=BK, reach=6.0)
    elif rs is not None:
        for ref, val, fp, x, y, note, face in rs:
            angs = facing(ref, fp, x, y, face) if ref in port_of else (0, 90, 45, -45, 135, -135)
            S.put_xy(ref, val, fp, x, y, angs[0], note, angs=angs, layer=face)
    else:
        # the other way round, each port's pair of chips takes the other's spots
        swap = {"U14": "U17", "U15": "U18", "U17": "U14", "U18": "U15"}
        for ref, note, cap, term, tnote in relay_chips:
            x, y = RELAY_AT[ref if in_ccw else swap[ref]]
            angs = facing(ref, FP["DFN8"], x, y, BK)
            u = S.put_xy(ref, "SIT3088", FP["DFN8"], x, y, angs[0], note, angs=angs, layer=BK)
            if u is None:
                continue
            pads = {n: (px, py) for n, px, py, w, h in padpos.pad_xy(u)}
            (ux, uy) = u.rect[0]
            def beyond(p, d):
                dx, dy = p[0] - ux, p[1] - uy
                n = hypot(dx, dy) or 1.0
                return p[0] + dx / n * d, p[1] + dy / n * d
            # The 100 n past the END of the pin row, beyond pin 8, not in front
            # of it: in front of pins 7 and 8 is where their escape vias go
            # (fanout.escape), and a cap there left pin 7 no way out.
            ax, ay = pads["8"][0] - pads["7"][0], pads["8"][1] - pads["7"][1]
            n = hypot(ax, ay) or 1.0
            cx, cy = pads["8"][0] + ax / n * 1.3, pads["8"][1] + ay / n * 1.3
            S.put_xy(cap, "100n", FP["C0402"], cx, cy, degrees(atan2(ay, ax)) + 90,
                     f"{ref} decoupling, at VCC",
                     angs=(degrees(atan2(ay, ax)) + 90, 0, 90, 45, -45), layer=BK, reach=4.0)
            if term:
                # ... and the 120 R beyond the escape rows, not on them
                mid = ((pads["6"][0] + pads["7"][0]) / 2, (pads["6"][1] + pads["7"][1]) / 2)
                a67 = degrees(atan2(pads["7"][1] - pads["6"][1], pads["7"][0] - pads["6"][0]))
                for dist, reach in ((2.6, 1.2), (3.0, 1.2), (2.2, 1.2), (3.6, 1.2), (2.6, 3.0),
                                    (0.0, 4.0)):
                    tx, ty = beyond(mid, dist)
                    got = S.put_xy(term, "120R", FP["R0603"], tx, ty, a67, tnote,
                                   angs=(a67, a67 + 180, a67 + 45, a67 - 45, a67 + 90),
                                   layer=BK, reach=reach)
                    if got is not None:
                        break
                    S.missed.pop()
                else:
                    S.missed.append((term, "120R", FP["R0603"], "centre", BK, tnote))
    S.put_xy("D1103", "RGB", FP["LED"], 9.0, 9.0, 45, "status LED, three GPIO",
             angs=(45, 0, 90, -45))
    for k, ref in enumerate(("R1107", "R1108", "R1109")):
        S.put_xy(ref, "1k", FP["R0402"], 6.5 + 1.2 * k, 11.5, 90, "LED series",
                 angs=(90, 0, 45))
    # BOOTSEL: with the XT30, the one spot left on the outward face, at the
    # top of the centre -- its old one is C1001's now
    sx, sy = (0.0, 15.0) if xt30 else (-9.0, 9.0)
    S.put_xy("SW1", "BOOTSEL", FP["SW"], sx, sy, -45, "BOOTSEL to GND, via R701",
             angs=(-45, 0, 90, 45))
    for k, (ref, val) in enumerate((("TP4", "ADC3"), ("TP5", "SWCLK"),
                                    ("TP6", "SWDIO"), ("TP7", "RUN"))):
        S.put_xy(ref, val, FP["TP"], -3.0 + 1.8 * k, 7.0, 0,
                 f"test point, {val}, outward face")
    for ref, val, x, y, note in (
            ("R1103", "10k", 9.0, -9.5, "VBUS_DET divider, top"),
            ("R1104", "15k", 10.2, -9.5, "VBUS_DET divider, bottom -> GPIO25")):
        S.put_xy(ref, val, FP["R0402"], x, y, 90, note, angs=(90, 0, 45), layer=BK)

    # ---- the 3V3 LDO and its row ----------------------------------------------
    lrow = [("U9", "SPX3819-3.3", "Package_TO_SOT_SMD:SOT-23-5", 90, "5 V -> 3V3"),
            ("C712", "1u", FP["C0603"], 0, "3V3 out"),
            ("C713", "1u", FP["C0603"], 0, "5 V in"),
            ("R705", "10k", FP["R0402"], 0, "FAULT_n pull-up"),
            ("R803", "10k 0.1%", FP["R0603"], 0, "thermistor divider -> ADC3"),
            ("C802", "100n", FP["C0603"], 0, "ADC3 filter"),
            ("R704", "10k", FP["R0402"], 0, "RUN pull-up"),
            ("C717", "100n", FP["C0402"], 0, "RUN")]
    if back_centre:
        # Into the motor-facing centre, toward the signal wedge: the LDO feeds
        # the +3V3 disc on In2 directly above it, and the rest are the CPU's.
        for k, (ref, val, fp, rot, note) in enumerate(lrow):
            x, y, reach = 9.0 + 1.3 * (k % 4), -6.5 - 2.0 * (k // 4), 9.0
            if xt30:
                # the relay's second pair has the LDO's corner: the LDO goes
                # wherever the centre has room, and its two caps go with it
                u9 = next((q for q in S.parts if q.ref == "U9"), None)
                if ref in ("C712", "C713") and u9 is not None:
                    (x, y), reach = u9.rect[0], 6.0
                else:
                    # the LDO from the upper right, leaving the strip by the
                    # XT30's VMOT pin to the bus divider
                    (x, y) = (5.0, 13.0) if ref == "U9" else (x, y)
                    reach = 26.0
            S.put_xy(ref, val, fp, x, y, rot, note, angs=(rot, rot + 90, 45, 135), layer=B,
                     reach=reach)
    else:
        for k, (ref, val, fp, rot, note) in enumerate(lrow):
            S.put(ref, val, fp, SIG, 18.4, -9.5 + 1.6 * k, rot, F, note, rots=(rot, rot + 90))

    # ---- signal wedge's back: USB front end, bus divider ----------------------
    S.put_first("U13", "USBLC6-2SC6", FP["SOT236"],
                [(SIG, B, 25.0, -6.0), (SIG, F, 21.0, -6.0)], 0,
                "USB ESD, as close to the connector as the wedge allows", rots=(0, 90))
    for ref, val, fp, r, s, note in (
            ("R1101", "5k1", FP["R0402"], 22.5, -4.0, "CC1 pull-down: a sink, 5 V only"),
            ("R1102", "5k1", FP["R0402"], 22.5, -5.2, "CC2 pull-down"),
            ("D1002", "B5819WS", "Diode_SMD:D_SOD-323", 19.5, -4.5, "USB VBUS -> +5V"),
            ("D1003", "B5819WS", "Diode_SMD:D_SOD-323", 19.5, -9.0,
             "5 V buck -> +5V, so USB cannot back-feed the rails")):
        S.put_first(ref, val, fp, [(SIG, B, r, s), (SIG, F, r, s)], 90, note, rots=(90, 0))
    div = {"hi": ("R801", "56k 0.1%", "bus divider, high leg"),
           "hi2": ("R806", "56k 0.1%", "bus divider, high leg, second half"),
           "lo": ("R802", "4k7 0.1%", "bus divider, low leg"),
           "c": ("C801", "100n", "ADC2 filter")}
    if xt30:
        # one piece, not four: placed part by part it fell in three places
        for blk, face, r, s in ((PWR, B, 19.5, 9.0), (PWR, B, 19.5, -9.0), (SIG, B, 21.0, -8.0),
                                (SIG, F, 20.0, 8.0), (PWR, F, 20.0, 8.0)):
            if S.put_module(MOD_DIVIDER, div, blk, face, r, s, step=0.25, snap=15):
                break
        else:
            # nowhere in the wedges: as one piece in the motor-facing centre,
            # by the XT30's VMOT pin; failing that, part by part wherever
            # there is a pocket (decided 2026-09-24: "place the rest loosely")
            vx, vy = _pad_of(S, "J4", "2")
            def build(x0, y0, g):
                ca, sa = cos(radians(g)), sin(radians(g))
                return [Part.at_xy(div[role][0], div[role][1], FP[fpk], x0 + dx * ca - dy * sa,
                                   y0 + dx * sa + dy * ca, g + rot, "centre", B, div[role][2])
                        for role, fpk, dx, dy, rot in MOD_DIVIDER]
            if S.put_rigid(build, vx, vy, range(0, 360, 45), reach=12.0) is None:
                for role, fpk, *_ in MOD_DIVIDER:
                    ref, val, note = div[role]
                    if S.put_xy(ref, val, FP[fpk], vx, vy, 0, note, angs=(0, 90, 45, 135),
                                layer=B, reach=17.0) is None:
                        S.missed.pop()
                        S.put_first(ref, val, FP[fpk], [(PWR, B, 19.5, 9.0), (PWR, F, 20.0, 8.0),
                                                         (SIG, B, 21.0, -8.0), (SIG, F, 20.0, 8.0)],
                                    0, note, rots=(0, 90))
    else:
        for (ref, val, note), r, s in zip(div.values(), (19.0,) * 4, (8.0, 10.0, 12.0, 14.0)):
            fp = FP["R0603"] if ref[0] == "R" else FP["C0603"]
            S.put_first(ref, val, fp, [(PWR, B, r, s), (SIG, B, 25.0, s - 10.0), (PWR, F, r, s)],
                        0, note, rots=(0, 90))

    # ---- last, the bucks' near parts: whatever room the rest leave ------------
    # The link wedges hold both modules and not much else. What does not fit
    # goes to the outward centre (decided 2026-09-23): ceramics, nothing
    # switching or ferrous, the 4.7 uF input caps first to the spots nearest
    # their bucks. The In1/In2 plane pair under them makes ten or fifteen mm
    # nothing at 400 kHz; the 100 n at each IC's pins takes the edges.
    for _, part, fp, host, apad, opad, reach, layers, a in sorted(pending, key=lambda q: q[0]):
        if S.put_near(part, fp, host, apad, opad, reach, layers, a=a, centre=True) is None and xt30:
            # With the XT30 the outward centre has no room left outside the
            # CPU's channel: the power wedge's front, either side of the
            # XT30 (phase C's gate driver is beside the one at 210 deg), or
            # the CPU wedge's back. An output cap only adds capacitance to
            # its rail, wherever it is.
            S.missed.pop()
            S.put_first(part[0], part[1], fp, [(PWR, F, 24.5, -9.0), (PWR, F, 24.5, 9.0),
                                               (CPU, B, 22.0, -3.0)], 0,
                        part[2] + "; beside the XT30", rots=(0, 45, 90, 135), reach=6.0)
    return S

# The arrangement board S is built from (decided 2026-09-22): the centre open
# on both faces, the cans in it, two compact LMR38010 modules, the 3 kW TVS at
# the bus pads, the CPU-relay RS-485 on two SH 6, the 2x10 header across the
# axis with the MT6701's vias moved clear. The TVS goes in before the bucks so
# it gets the VMOT pad's corner of the power wedge; a buck module turns in 15
# degree steps, since at 45 the 5 V one has nowhere to go -- its short links
# are laid as copper by the fan-out (fanout.modules), not left to a router
# that wants the 45-degree grid.
LAYOUT = dict(power="two", tvs="SMC", ports=2, port="SH6", cans="centre", relay=True,
              exp="2x10", exp_first=True, enc_vias="moved", tvs_first=True, mod_snap=15,
              mod_flex=1.2, bus="xt30")

def layout():
    """Board S as it is built: the sketch's search, frozen by LAYOUT."""
    return board_s_open(**LAYOUT)

def parts():
    """Every part gen_boards.py places on board S, as placement.Part."""
    return list(layout().parts)

# ---------------------------------------------------------------- report ----
def check_s(parts):
    """placement.check(), with the round parts tested as circles, the one rule
    edge connectors break on purpose excepted, and the bus pads added."""
    rnd = {p.ref for p in parts if round_r(p)}
    geo = (" overlaps ", ": reaches r=", " deg off its ", " fouls ", " sits on the ")
    bad = []
    for b in PL.check(parts):
        if any(k in b for k in geo) and any(
                b.startswith(f"{r}:") or b.startswith(f"{r} ") or f" {r} (" in b
                or b.endswith(f" {r}") for r in rnd):
            continue                        # redone below, as circles
        if any(b.startswith(f"{e}: reaches r=") and "past R_USABLE" in b for e in EDGE):
            continue
        if any(b.startswith(f"{c}: reaches r=") and "centre keepout" in b for c in CENTRE):
            continue
        peg = {p.ref for p in parts if p.fp in PEG_FPS}
        if any(b.startswith(f"{r}'s through-hole pads land on") or
               (b.startswith(f"{r}: ") and " deg off its " in b) for r in peg):
            continue                        # redone below with the pegs' own rule
        bad.append(b)
    new = {PWR, SIG, "centre"} | {p.block for p in parts if p.ref in EDGE}
                                          # board A's own pairs are DRC-proven
    for a in parts:
        for b in parts:
            if a.block not in new and b.block not in new:
                continue
            if b.layer != a.layer and any(_hits_circle(b, hx, hy, hr + cl)
                                          for hx, hy, hr, cl in _holes(a)):
                bad.append(f"{a.ref}'s through-hole leads come within {THT_CLR} mm of {b.ref}")
            if b.layer != a.layer and not _pegs_clear(a, b):
                bad.append(f"{a.ref}'s pegs come within {PEG_CU} mm of {b.ref}'s copper")
            if a is not b and b.layer == a.layer and not _ears_clear(a, b):
                bad.append(f"{b.ref} is on {a.ref}'s peg ears")
    for p in parts:
        lo, hi = _radii(p)
        lim = R_EDGE if p.ref in EDGE else G.R_USABLE
        if hi > lim + 1e-9:
            bad.append(f"{p.ref}: reaches r={hi:.2f}, past {lim}")
        for layer, r0, r1, ang, half, net in bus_pads():
            if p.layer == layer and _in_sector_part(p, r0, r1, ang, half):
                bad.append(f"{p.ref} sits on the {net} bus pad")
        if p.ref in EDGE and hi < R_RIM:
            bad.append(f"{p.ref}: an edge connector {G.R - hi:.2f} mm short of the edge")
        if p.ref in CENTRE and any(_hits_circle(p, sx, sy, HEAD_CLEAR) for sx, sy in SCREWS):
            bad.append(f"{p.ref}: in the way of a motor screw head or standoff")
        if p.ref in CENTRE and p.layer == "B.Cu":
            if HEIGHT.get(p.fp, 99.0) >= H_MAX_BACK:
                bad.append(f"{p.ref}: too tall for the motor-facing centre")
            if _hits_circle(p, 0.0, 0.0, MAGNET_R):
                bad.append(f"{p.ref}: inside the magnet keepout")
        if p.ref not in rnd:
            continue
        if (lo < G.ZONE_R0 and p.ref not in CENTRE) or not _span_ok(p) \
                or any(_hits_circle(p, *b) for b in PL.bosses()):
            bad.append(f"{p.ref}: out of its wedge, or on a boss")
        for q in parts:
            if q is not p and q.layer == p.layer and _touch(p, q) and (q.ref not in rnd or q.ref > p.ref):
                bad.append(f"{p.layer}: {p.ref} overlaps {q.ref}")
        for layer, r0, r1, ang, half in PL.sectors():
            if layer == p.layer and _in_sector_part(p, r0, r1, ang, half):
                bad.append(f"{p.ref} sits on an arc at {ang:.0f} deg")
    return bad

def report(S=None):
    S = S or board_s()
    bad = check_s(S.parts)
    for blk in (PWR, SIG):
        for layer in ("F.Cu", "B.Cu"):
            ps = [p for p in S.new if p.block == blk and p.layer == layer]
            area = sum(PL.courtyard(p.fp)[0] * PL.courtyard(p.fp)[1] for p in ps)
            face = "outward" if layer == "F.Cu" else "motor side"
            print(f"  {blk:12s} {face:10s} {len(ps):3d} parts  {area:6.1f} mm2 of courtyard")
    print(f"board S sketch: {len(S.parts)} parts, {len(S.new)} of them new or re-placed")
    if S.missed:
        print(f"\n{len(S.missed)} NOT PLACED:")
        for m in S.missed:
            print("  -", m[0], m[1], "|", m[3], "|", m[4])
    if bad:
        print(f"\n{len(bad)} PROBLEM(S):")
        for b in bad:
            print("  !", b)
    else:
        print("\ncheck: clean (placement.check() rules, edge connectors excepted)")
    return S, bad

# ---------------------------------------------------------------- drawing ---
def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _sector_path(a0, a1, r0, r1, n=24):
    pts = [PL.polar_xy(a0 + (a1 - a0) * k / n, r1) for k in range(n + 1)]
    pts += [PL.polar_xy(a1 - (a1 - a0) * k / n, r0) for k in range(n + 1)]
    return "M " + " L ".join(f"{x:.2f} {-y:.2f}" for x, y in pts) + " Z"

def _panel(S, layer, ox, title, sub):
    """One face, plan view from the outward side (the motor-facing face is
    seen THROUGH the board, so both panels register)."""
    o = [f'<g transform="translate({ox} 0)">',
         f'<text class="h" x="0" y="-38.2" text-anchor="middle">{title}</text>',
         f'<text class="sub" x="0" y="-35.9" text-anchor="middle">{sub}</text>',
         f'<circle class="board" cx="0" cy="0" r="{G.R}"/>']
    for blk in (PWR, SIG):
        a = AX[blk]
        o.append(f'<path class="wedge" d="{_sector_path(a - G.WEDGE_SPAN/2, a + G.WEDGE_SPAN/2, G.ZONE_R0, G.R)}"/>')
    for ly, r0, r1, ang, half in PL.sectors():
        if ly == layer:
            o.append(f'<path class="land" d="{_sector_path(ang - half, ang + half, r0, r1)}"/>')
    for ly, r0, r1, ang, half, net in bus_pads():
        if ly == layer:
            o.append(f'<path class="buspad" d="{_sector_path(ang - half, ang + half, r0, r1)}"/>')
            tx, ty = PL.polar_xy(ang, (r0 + r1) / 2)
            o.append(f'<text class="pl" x="{tx:.2f}" y="{-ty + 0.4:.2f}" text-anchor="middle">{net}</text>')
    o.append(f'<circle class="keep" cx="0" cy="0" r="{G.ZONE_R0}"/>')
    for x, y, r in PL.bosses():
        o.append(f'<circle class="boss" cx="{x:.2f}" cy="{-y:.2f}" r="{r:.2f}"/>')
    for x, y in SCREWS:
        o.append(f'<circle class="keep" cx="{x}" cy="{-y}" r="{HEAD_CLEAR}"/>')
        o.append(f'<circle class="boss" cx="{x}" cy="{-y}" r="{G.MOUNT_D/2}"/>')
    if layer == "B.Cu":
        o.append(f'<circle class="magnet" cx="0" cy="0" r="{MAGNET_R}"/>')
        o.append(f'<text class="pl2" x="0" y="{-MAGNET_R - 0.5:.2f}" text-anchor="middle">'
                 f'magnet keepout</text>')
    new = {id(p) for p in S.new}
    labels = []
    for p in S.parts:
        on = p.layer == layer
        holes = p.tht()
        if not on and not holes:
            continue
        cls = ("new" if id(p) in new else "old") if on else "ghost"
        rr = round_r(p)
        if on:
            tip = _esc(f"{p.ref}  {p.value}" + (f" -- {p.note}" if p.note else ""))
            if rr:
                (cx, cy) = _centre(p)
                o.append(f'<circle class="{cls}" cx="{cx:.2f}" cy="{-cy:.2f}" r="{rr:.2f}">'
                         f'<title>{tip}</title></circle>')
            else:
                pts = " ".join(f"{x:.2f},{-y:.2f}" for x, y in p.corners())
                o.append(f'<polygon class="{cls}" points="{pts}"><title>{tip}</title></polygon>')
        for hx, hy, hr in holes:
            o.append(f'<circle class="hole" cx="{hx:.2f}" cy="{-hy:.2f}" r="{hr:.2f}"/>')
        if on and id(p) in new:
            (cx, cy) = _centre(p)
            labels.append((cx, cy, p))
    for cx, cy, p in labels:
        big = max(PL.courtyard(p.fp)[:2]) > 5.5
        cls = "lb" if big else "ls"
        o.append(f'<text class="{cls}" x="{cx:.2f}" y="{-cy + (0.55 if big else 0.3):.2f}" '
                 f'text-anchor="middle">{p.ref}</text>')
    o.append('</g>')
    return "\n".join(o)

SVG_STYLE = """
  :root { --bg:#ffffff; --panel:#f4f2ec; --ink:#1e2126; --muted:#6b7280; --line:#c9c4b8;
          --accent:#6b4fa0; --newfill:#e3d9f5; --warn:#a05a00; --copper:#c08a3e; --bus:#b3372f; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#1e2128; --panel:#262a33; --ink:#e8e6e1; --muted:#9aa3af; --line:#3d434f;
            --accent:#b89ce8; --newfill:#3a3152; --warn:#e0a44c; --copper:#b8823c; --bus:#e06a5f; } }
  .board { fill: var(--panel); stroke: var(--ink); stroke-width: .3; }
  .wedge { fill: var(--accent); fill-opacity: .06; stroke: var(--accent); stroke-width: .12;
           stroke-dasharray: .8 .6; }
  .land  { fill: var(--copper); fill-opacity: .55; stroke: none; }
  .buspad{ fill: var(--bus); fill-opacity: .7; stroke: none; }
  .keep  { fill: none; stroke: var(--warn); stroke-width: .12; stroke-dasharray: .6 .5; }
  .magnet{ fill: var(--warn); fill-opacity: .08; stroke: var(--warn); stroke-width: .15; }
  .pl2   { font-size: 0.9px; fill: var(--warn); }
  .boss  { fill: var(--bg); stroke: var(--ink); stroke-width: .18; }
  .old   { fill: var(--muted); fill-opacity: .22; stroke: var(--muted); stroke-width: .12; }
  .new   { fill: var(--newfill); stroke: var(--accent); stroke-width: .18; }
  .ghost { fill: none; stroke: none; }
  .hole  { fill: var(--bg); stroke: var(--ink); stroke-width: .1; }
  text   { font-family: ui-sans-serif, system-ui, sans-serif; fill: var(--ink); }
  .h     { font-size: 2.1px; font-weight: 600; }
  .sub   { font-size: 1.35px; fill: var(--muted); }
  .lb    { font-size: 1.25px; font-weight: 600; }
  .ls    { font-size: 0.62px; }
  .pl    { font-size: 1.0px; font-weight: 600; fill: #fff; }
"""

def render(S, path):
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-37 -41 150 79" '
         f'width="1500" height="790" role="img">',
         f'<title>Board S placement sketch: outward face and motor-facing face</title>',
         f'<style>{SVG_STYLE}</style>',
         _panel(S, "F.Cu", 0, "outward face",
                "the face away from the motor; "
                + ("XT30" if getattr(S, "options", {}).get("bus") == "xt30" else "bus pads")
                + ", cans, USB-C, expansion header"),
         _panel(S, "B.Cu", 76, "motor-facing face, seen through the board",
                "same orientation as the left; RS-485 ports face out at the rim"),
         '</svg>']
    Path(path).write_text("\n".join(s) + "\n")

def parts_table(S):
    """The new and re-placed parts, as rows for single.html."""
    face = {"F.Cu": "outward", "B.Cu": "motor side"}
    where = {PWR: "power wedge, 230&deg;", SIG: "signal wedge, 334&deg;", "centre": "centre"}
    rows = []
    order = {"centre": 1, PWR: 0, SIG: 2}
    for p in sorted(S.new, key=lambda p: (order.get(p.block, 3), p.layer, p.ref)):
        (cx, cy) = _centre(p)
        rows.append(f"<tr><td>{p.ref}</td><td>{_esc(p.value)}</td>"
                    f"<td>{where.get(p.block, p.block)}, {face[p.layer]}</td>"
                    f"<td>R {hypot(cx, cy):.1f}</td><td>{_esc(p.note)}</td></tr>")
    return "\n".join(rows)

def write_page_table(S, missed_asked):
    page = ROOT / "single.html"
    if not page.exists():
        return
    t = page.read_text()
    for key, body in (("parts", parts_table(S)),
                      ("missed", ", ".join(f"<code>{m[0]}</code> ({_esc(m[1])})"
                                           for m in missed_asked) or "nothing")):
        a, b = f"<!-- {key}:begin -->", f"<!-- {key}:end -->"
        if a in t and b in t:
            pad = "\n" if key == "parts" else ""     # inline text takes no line breaks
            t = t[:t.index(a) + len(a)] + pad + body + pad + t[t.index(b):]
    page.write_text(t)
    print("  wrote single.html tables")

if __name__ == "__main__":
    for name, build, out in (("as asked", board_s, "board_s_asked.svg"),
                             ("the arrangement that fits: cans in the centre, two "
                              "compact bucks, 3 kW TVS, CPU-relay RS-485, 2x10 header on "
                              "the axis, the bus on an XT30",
                              layout,
                              "board_s_plan.svg")):
        print(f"== {name}")
        S, _ = report(build())
        for p in S.new:
            if round_r(p) and p.block in AX:
                print(f"  {p.ref} crosses its wedge line by {span_over(p):.1f} deg")
        render(S, ROOT / "img" / out)
        print("  wrote img/" + out)
        if build is board_s:
            missed_asked = S.missed
    write_page_table(S, missed_asked)
