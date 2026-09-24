#!/usr/bin/env python3
"""geometry.py — the servodrive board geometry, in one place, and the SVGs that draw it.

Every dimension the spec quotes comes from here. Re-run to regenerate img/*.svg
after changing a number, so the drawings and the prose can never drift apart.

    python3 tools/geometry.py

Coordinates are millimetres, origin at the motor shaft axis (= the MT6701's
sensing centre). Plan views are TOP views: +x right, +y up. SVG y is flipped on
emit.
"""
from math import cos, sin, radians
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "img"

# ---------------------------------------------------------------- geometry --
DIA          = 65.0          # both boards, outer diameter
R            = DIA / 2       # 32.5
EDGE_CLEAR   = 1.0           # copper pullback from the edge
R_USABLE     = R - EDGE_CLEAR

# Motor mount — inherited verbatim from rp2350-motor-controller/hardware/encoder.
# Four M3, centred on the sensor, in two orthogonal pairs at different spacings.
MOUNT_X      = 12.5          # holes at (+-12.5, 0)
MOUNT_Y      = 9.5           # holes at (0, +-9.5)
MOUNT_D      = 3.2           # clearance for M3
MOUNT_HEAD   = 6.5           # screw-head / washer keepout diameter
R_CENTRE     = 16.0          # everything inside this radius belongs to the
                             # encoder + motor mount; no other parts

# Sections. The three phase cells are ADJACENT, and the three utility wedges
# with them, rather than the two kinds alternating. Three reasons, in order:
#
#   - the CPU stops sitting between two switching cells. In the alternating
#     layout the RP2350A had a half-bridge 25 deg away on each side; grouped,
#     it sits in the middle wedge, as far from all three as the board allows.
#   - the three motor leads leave together, so one bundle leaves the enclosure
#     through one hole instead of three.
#   - the DC bus feeds one contiguous 210 deg block from the power link at its
#     edge, rather than reaching around the board past the CPU.
#
# Cell width is unchanged at 70 deg, so nothing inside a cell had to move.
PHASE_SPAN   = 68.0          # deg. 67 is the floor: below it the DC-link row
                             # runs into the perimeter boss and the wedges lose
                             # their corners. The three cells were 60 while the
                             # small parts lived in the channel; routing showed
                             # that only GND and phase-output parts can get out
                             # of there, so five per cell came back in and the
                             # cells had to pay for them. It costs the even bolt
                             # circle -- see PERIM_ANG below.
# The three wedges get equal shares of what the phase cells leave. Splitting
# them unevenly was tried, because the power link is four parts and the CPU
# wedge twenty-six: it is worth almost nothing. The six bolts sit ON the
# section boundaries, so narrowing a wedge walks its own two bosses inward and
# its parts have to dodge them -- the power link needs 50 deg of the 156 all by
# itself, and the most the CPU can be given is 56 instead of 52.
WEDGE_SPAN   = 120.0 - PHASE_SPAN

def _wedge_start(i):
    return 3 * PHASE_SPAN + WEDGE_SPAN * i

PHASE_ANG    = [PHASE_SPAN * (i + 0.5) for i in range(3)]
WEDGE_ANG    = [_wedge_start(i) + WEDGE_SPAN / 2 for i in range(3)]

# The six M2.5 sit on the six SECTION BOUNDARIES, so every section has one at
# each edge and every phase cell's heatsink land runs the full width between
# its own two bolts. That ties the bolt circle to the section widths: 60/60 was
# the one arrangement that made it regular, and 68/52 gives that up again.
# 0, 68, 136, 204, 256, 308.
PERIM_BC     = 59.0          # bolt circle diameter
PERIM_D      = 2.7           # clearance for M2.5
PERIM_ANG    = ([PHASE_SPAN * i for i in range(3)]
                + [_wedge_start(i) for i in range(3)])
PERIM_N      = len(PERIM_ANG)
ZONE_R0      = 17.0          # no part inboard of this, on either face

# The interconnect used to be described here as two nominal rectangles. It is
# now three real footprints in tools/placement.py -- two 2x5 power headers and
# one 2x14 signal header -- so the description and the thing cannot disagree.

# Encoder
MT6701_BODY  = (6.0, 5.0)    # SO-8 lead span x body depth
MAGNET_D     = 6.0           # diametric magnet, as the MT6701 datasheet recommends.
                             # Dia 8 x 2.5 put more than the part's 100 mT maximum
                             # on the die at the short end of the air-gap
                             # tolerance (sim P5, Q10); Dia 6 keeps it inside
                             # the 20-100 mT window over the whole of it.
MAGNET_KEEP  = 12.0          # keepout on the motor-facing side

# Stack. y = 0 is the motor rear face, +y away from the motor. Every gap here
# is DERIVED, not chosen: the standoff under board A is whatever the encoder
# chain adds up to. Change SHAFT_PROUD once the motor is known and the drawing,
# the numbers and the prose all move together.
SHAFT_PROUD  = 5.0           # shaft end above the motor rear face  [MOTOR-DEPENDENT]
MAGNET_T     = 2.5           # diametric magnet + holder cap
AIR_GAP      = 1.5           # MT6701 package face to magnet face; spec 1-3 mm
MT6701_T     = 1.75          # SO-8 body thickness, hanging below board A
PCB_T        = 1.6
GAP_AB       = 11.0          # board A parts + mated interconnect height
GAP_TOP      = 11.0          # board B connectors and bulk cans

STANDOFF_A   = SHAFT_PROUD + MAGNET_T + AIR_GAP + MT6701_T   # motor face -> board A
MOTOR_BODY   = 12.0          # drawn only, not a spec

# --------------------------------------------------- electrical design ----
# THE design point. Everything below is derived from it, so changing this one
# number moves the shunt, the copper, the interconnect and the heatsink
# together. 20 A is RMS per phase, CONTINUOUS -- the sense chain has to reach
# its PEAK, and the thermal path has to carry its loss.
BUS_V        = 60.0          # nominal bus
BUS_SILICON  = 80.0          # MOSFET and TVS class
FSW          = 20e3          # switching frequency
I_PHASE      = 20.0          # A RMS per phase, continuous  [DESIGN POINT]
I_PHASE_PK   = I_PHASE * 2**0.5                    # 28.3 A
I_PHASE_MEAN = 2 / 3.14159265 * I_PHASE_PK         # mean |i| over a cycle

# Sense chain. Full scale is set against the PEAK, not the RMS -- a chain that
# clips at 20.6 A cannot close a current loop on a 20 A RMS sinusoid, which is
# what the 1.6 mOhm shunt in draft 0.2 would have done.
# 2 x 2 mOhm since 2026-09-24: no 1.6 mOhm 2010 exists at LCSC/JLCPCB (1 and
# 2 mOhm, nothing between), so the pair is Vishay WSLP20102L000FEA (C413487).
# Full scale falls from 36.2 A to 29.0 A worst case -- 2.5% over the 28.3 A
# peak, the margin draft 0.3 turned down; taken knowingly (the other stocked
# choice, 2 x 1 mOhm, reads +-58 A at 25 mV/A).
SHUNT_N      = 2             # 2 x 2 mOhm 2010 in parallel, per sensed phase
SHUNT_EACH   = 0.002
SHUNT_R      = SHUNT_EACH / SHUNT_N                # 1.0 mOhm
INA_GAIN     = 50.0          # INA241A3 -- A3 is the 50 V/V grade, NOT A2.
                             # INA240 numbers its gains A1=20 A2=50; INA241
                             # numbers them A1=10 A2=20 A3=50. Draft 0.2 carried
                             # the INA240 mapping across and specified a 20 V/V
                             # part for a 50 V/V job.
ADC_MID      = 1.65          # half of 3V3, the zero-current output
ADC_SWING    = 0.20          # INA241 swing to the VS rail, datasheet MAX
                             # (0.07 typ). The positive direction is the limit.
I_FULL_SCALE = (ADC_MID - ADC_SWING) / (INA_GAIN * SHUNT_R)
V_PER_A      = SHUNT_R * INA_GAIN

# MOSFET: BSC030N08NS5, Infineon PG-TDSON-8 (PowerPAK SO-8), datasheet rev 2.3.
# Maxima, not typicals -- a continuous rating wants the worst part in the reel.
FET_RDSON    = 0.0030        # max at VGS = 10 V, 25 C
FET_RDSON_K  = 1.58          # x RDS(on) at Tj = 125 C, from datasheet diagram 12
FET_RTH_JC   = 0.9           # K/W max, junction to the drain pad
FET_QGD      = 19.5e-9       # max
FET_QG       = 76e-9         # max, 0..10 V
FET_CISS     = 5600e-12      # max, for the 10 -> 12 V extrapolation
FET_QRR      = 188e-9        # max body-diode reverse recovery

# Gate driver: EG2103, datasheet V1.0 2018-11-11, read 2026-09-07.
DRV_ISRC     = 0.30          # A, source -- the edge-rate limit, not the resistor
DRV_ISNK     = 0.60          # A, sink
DRV_VCC      = 12.0          # gate rail
DRV_DEADTIME = 560e-9        # internal, typ (460..660)
DRV_TON      = 780e-9        # input-to-output turn-on delay, typ
DRV_TOFF     = 220e-9        # input-to-output turn-off delay, typ
DRV_VIH      = 2.5           # min input high -- 0.8 V of margin on a 3V3 GPIO
DRV_VIL      = 1.0           # max input low
DRV_UVLO_ON  = 9.7           # VCC(on) max -- the 12 V rail must clear this
DRV_UVLO_OFF = 7.0           # VCC(off) min
DRV_VB_ON    = 9.6           # VB(on) max -- the bootstrap droop budget
DRV_RPULL    = 200e3         # internal: HIN pull-down, LIN pull-up (see below)

# The pull resistors the datasheet forced. EG2103's LIN is active-low with an
# internal 200 k pull-up; RP2350's GPIOs come out of reset as inputs with a
# ~60 k pull-down, and erratum E9 can latch a Bank 0 input at ~2.1-2.2 V. The
# divider of those two lands LIN near 1.2 V -- between VIL 1.0 and VIH 2.5,
# which is the one place a gate driver input must never sit. External pulls
# make the reset state a property of the board instead of two errata.
#
# Both pulls go to GND, and that is a decision, not a default: HIN = LIN = 0
# is the truth table's low-side-on state, so while the CPU is in reset every
# low side conducts and the motor is braked through its own windings. It was
# a coast (LIN up) until 2026-09-22. 4.7 k against the 200 k internal pull-up
# holds LIN at 0.11 V, and <= 9 k is still the E9 workaround. The brake lasts
# only as long as the 12 V gate rail does, so board B's GATE_EN has to default
# ON through a CPU reset for it to be a brake rather than a brief one.
R_LIN_PULLDN = 4700.0        # LIN -> GND    (brake at reset)
R_HIN_PULLDN = 4700.0        # HIN -> GND    (<= 9 k also clears E9)

# Board copper and vias
CU_K         = 385.0         # W/m.K
FR4_K        = 0.35          # W/m.K, through-plane
VIA_DRILL    = 0.40          # thermal via, mm
VIA_PLATE    = 0.025         # barrel plating, mm
VIA_PAD      = 0.78          # 0.19 mm annular ring; 0.78 rather than 0.80 so
                             # the high side's five rows fit the drain pad
# The drain pad's via array, 4 along the pad's 4.55 mm side (tangential on this
# board) and 4 or 5 along its 4.41 mm side (radial). The two FETs of a cell
# no longer take the same number, because they are not limited by the same
# thing (sim P3, Q7):
#   - the LOW side's barrels were islands: only the inner row reached the
#     switch-node pour on B.Cu. That is fixed by the pour (gen_boards.via_tab),
#     not by more barrels -- and there is no room for more: a fifth row puts
#     its outer pads within 0.15 mm of the motor-lead pad on B.Cu.
#   - the HIGH side's were all connected and still crowded: 2.4 A rms in the
#     busiest barrel against a 2 A criterion. It gets a fifth row. On B.Cu
#     beneath it there is only the phase-output pour, which clears around it.
# Neither can take a fifth COLUMN: it lands 0.2 mm inside the shunts'
# courtyards on the back.
FET_VIA_NX    = 4             # along the drain pad's 4.55 mm side
FET_VIA_NY    = 4             # along its 4.41 mm side: the low side
FET_VIA_NY_HS = 5             # ... and the high side
FET_VIA_PITCH = 0.95          # hole-to-hole 0.55 mm, clears the 0.5 rule (F-48)
FET_VIA_PITCH_HS = 0.905      # the high side's radial pitch, 0.505 hole to hole
FET_VIA_SHIFT_HS = 0.15       # and its array moved that far along the pad, away
                              # from the source pads: the drain pad has 0.46 mm
                              # to spare that way, and the fifth row's end
                              # barrels otherwise come within 0.15 mm of the
                              # shunt's courtyard on the back
FET_VIA_N     = FET_VIA_NX * FET_VIA_NY         # 16, the low side: the worse of
                                                # the two, and via_thermal()'s figure
FET_VIA_N_HS  = FET_VIA_NX * FET_VIA_NY_HS      # 20
PCB_LAYERS_A = 6

# Thermal environment
T_AMBIENT    = 25.0
TJ_MAX       = 125.0         # design limit; the part is rated 150
H_STILL_AIR  = 15.0          # W/m2.K, natural convection + radiation, anodised
R_STACK_BARE = 15.0          # K/W, the bare stacked pair -- spec draft 0.2

def polar(ang_deg, r):
    a = radians(ang_deg)
    return (r * cos(a), r * sin(a))

# ------------------------------------------------------------------ model --
# Everything below is arithmetic on the constants above. It is here rather than
# in the prose so that the spec cannot quote a number the design no longer has.

def losses(i_rms=None, fsw=None):
    """Board A dissipation, W, term by term. Worst-case parts, hot silicon."""
    i = I_PHASE if i_rms is None else i_rms
    f = FSW if fsw is None else fsw
    i_pk, i_mean = i * 2**0.5, 2 / 3.14159265 * i * 2**0.5

    cond = 3 * i**2 * FET_RDSON * FET_RDSON_K

    # The edge is set by the driver, not by the gate resistor: EG2103 saturates
    # at 0.3 A, and reaching that from 12 V into a 3.2 V plateau would need
    # Rg ~ 28 ohm, so anything smaller buys nothing.
    t_on  = FET_QGD / DRV_ISRC
    t_off = FET_QGD / DRV_ISNK
    e_on  = 0.5 * BUS_V * i_mean * t_on
    e_off = 0.5 * BUS_V * i_mean * t_off
    e_rr  = FET_QRR * BUS_V              # complementary body diode, per turn-on
    sw    = 3 * (e_on + e_off + e_rr) * f

    shunt = 2 * i**2 * SHUNT_R           # two sensed phases; C is reconstructed

    q_gate = FET_QG + FET_CISS * (DRV_VCC - 10.0)
    gate   = 6 * q_gate * DRV_VCC * f + 3 * 300e-6 * DRV_VCC

    logic = 0.26                         # RP2350A + LDO drop + MT6701 + 2x INA241

    return {"conduction": cond, "switching": sw, "shunts": shunt,
            "gate": gate, "logic": logic,
            "total": cond + sw + shunt + gate + logic,
            "t_on": t_on, "t_off": t_off}

def via_thermal(n=None, drill=None):
    """K/W through one via array: n plated barrels plus the FR4 they sit in."""
    n = FET_VIA_N if n is None else n
    d = VIA_DRILL if drill is None else drill
    r_i = d / 2
    a_cu = 3.14159265 * ((r_i + VIA_PLATE)**2 - r_i**2) * 1e-6     # m2
    r_one = (PCB_T * 1e-3) / (CU_K * a_cu)
    a_pad = (3.81 * 3.91 - n * 3.14159265 * r_i**2) * 1e-6         # FET drain pad
    r_fr4 = (PCB_T * 1e-3) / (FR4_K * a_pad)
    r_cu = r_one / n
    return r_cu * r_fr4 / (r_cu + r_fr4), r_one

def thermal(r_sink, i_rms=None):
    """Junction temperature for a given heatsink-to-ambient resistance."""
    L = losses(i_rms)
    p_fet = (L["conduction"] + L["switching"]) / 6
    r_via, _ = via_thermal()
    R_SPREAD = 1.0        # board copper, phase cells -> the six perimeter bosses
    R_JOINT  = 0.4        # boss lands -> ring, through a 0.25 mm gap pad
    t_board = T_AMBIENT + L["total"] * (r_sink + R_SPREAD + R_JOINT)
    return {"P": L["total"], "P_fet": p_fet, "r_via": r_via,
            "t_board": t_board,
            "t_case": t_board + p_fet * r_via,
            "t_j": t_board + p_fet * (r_via + FET_RTH_JC)}

def sink_needed(i_rms=None):
    """The heatsink-to-ambient resistance, and the area, that TJ_MAX demands."""
    L = losses(i_rms)
    p_fet = (L["conduction"] + L["switching"]) / 6
    r_via, _ = via_thermal()
    local = p_fet * (r_via + FET_RTH_JC)
    r_tot = (TJ_MAX - T_AMBIENT - local) / L["total"]
    r_sink = r_tot - 1.0 - 0.4
    return r_sink, 1.0 / (r_sink * H_STILL_AIR) * 1e4   # K/W, cm2

def bootstrap(c_boot=1e-6):
    """Droop on the bootstrap cap over one full-duty period, against VB(on)."""
    q_gate = FET_QG + FET_CISS * (DRV_VCC - 10.0)
    q_leak = 300e-6 * (1 / FSW)          # driver high-side quiescent, worst case
    v_start = DRV_VCC - 0.8              # less the bootstrap diode drop
    droop = (q_gate + q_leak) / c_boot
    return {"v_start": v_start, "droop": droop, "v_end": v_start - droop,
            "margin": v_start - droop - DRV_VB_ON}

def commutation_loop(length_mm=15.4, width_mm=5.0, h_mm=0.10):
    """Loop inductance and the overshoot it puts on an 80 V FET.

    h is F.Cu to the In1 ground plane -- the whole argument for a 6-layer board
    with a 0.1 mm prepreg is that this number is small.
    """
    MU0 = 4 * 3.14159265e-7
    l_pcb = MU0 * (h_mm * 1e-3) * (length_mm * 1e-3) / (width_mm * 1e-3)
    l_pkg = 2 * 0.5e-9                   # two PowerPAK SO-8 in the loop
    l_esl = 0.6e-9                       # 1210 ceramic, paralleled
    l_tot = l_pcb + l_pkg + l_esl
    di_dt = I_PHASE_PK / (FET_QGD / DRV_ISNK)
    return {"l_pcb": l_pcb, "l_total": l_tot, "di_dt": di_dt,
            "overshoot": l_tot * di_dt, "v_peak": BUS_V + l_tot * di_dt}

def interconnect(pins_per_rail, a_per_pin=3.0):
    """What the board A <-> board B power link actually carries.

    Bus current plus the share of the DC-link ripple that does NOT go into the
    ceramics on board A. This is the check spec sec.1 asked for and is the
    reason the 2x8 header is not enough at 20 A.
    """
    # 3-phase VSI DC-link ripple, m = 0.8, unity power factor
    m, cosphi = 0.8, 1.0
    i_ripple = I_PHASE * (2 * m * (3**0.5 / (4 * 3.14159265)
               + cosphi**2 * (3**0.5 / 3.14159265 - 9 * m / 16)))**0.5
    v_ph = 0.612 * BUS_V / 3**0.5
    i_bus = 3 * v_ph * I_PHASE / BUS_V
    # split at 20 kHz between 13 uF of ceramic on A and 300 uF of bulk on B
    z_cer = 1 / (2 * 3.14159265 * FSW * 13e-6)
    z_bulk = 0.2                          # ESR-dominated
    share = z_cer / (z_cer + z_bulk)      # fraction taken by the bulk on board B
    i_link = (i_bus**2 + (i_ripple * share)**2)**0.5
    return {"i_bus": i_bus, "i_ripple": i_ripple, "share": share,
            "i_link": i_link, "capacity": pins_per_rail * a_per_pin,
            "ok": i_link <= pins_per_rail * a_per_pin}

# ------------------------------------------------------------------- style --
STYLE = """
  .board  { fill: var(--panel); stroke: var(--ink); stroke-width: .35; }
  .zone   { fill: var(--zone); stroke: var(--line); stroke-width: .18;
            stroke-dasharray: 1.2 .9; }
  .keep   { fill: none; stroke: var(--warn); stroke-width: .16;
            stroke-dasharray: .8 .7; }
  .hole   { fill: var(--bg); stroke: var(--ink); stroke-width: .28; }
  .part   { fill: var(--partfill); stroke: var(--accent); stroke-width: .28; }
  .pad    { fill: var(--copper); stroke: none; }
  .lead   { stroke: var(--muted); stroke-width: .16; fill: none; }
  .dim    { stroke: var(--muted); stroke-width: .14; fill: none; }
  text    { font-family: ui-sans-serif, system-ui, sans-serif;
            fill: var(--ink); }
  .lbl    { font-size: 1.7px; }
  .sub    { font-size: 1.35px; fill: var(--muted); }
  .tiny   { font-size: 1.30px; fill: var(--muted); }
  .ctr    { text-anchor: middle; }
  .rt     { text-anchor: end; }
"""

VARS_LIGHT = """
  :root { --bg:#ffffff; --panel:#f4f2ec; --ink:#1e2126; --muted:#6b7280;
          --line:#c9c4b8; --accent:#6b4fa0; --warn:#a05a00;
          --zone:#ffffff00; --partfill:#eae4f5; --copper:#c08a3e; }
"""
VARS_DARK = """
  @media (prefers-color-scheme: dark) {
    :root { --bg:#1e2128; --panel:#262a33; --ink:#e8e6e1; --muted:#9aa3af;
            --line:#3d434f; --accent:#a98fd8; --warn:#e0a44c;
            --zone:#00000000; --partfill:#332c47; --copper:#b8823c; }
  }
"""

def svg_open(vb, w, h):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" '
            f'width="{w}" height="{h}" role="img">\n<style>'
            + VARS_LIGHT + VARS_DARK + STYLE + '</style>\n')

# ------------------------------------------------------------- plan drawing --
def sector(a0, a1, r0, r1):
    """Annular sector path, math angles, emitted in flipped-y screen space."""
    (x0i, y0i), (x0o, y0o) = polar(a0, r0), polar(a0, r1)
    (x1i, y1i), (x1o, y1o) = polar(a1, r0), polar(a1, r1)
    large = 1 if abs(a1 - a0) > 180 else 0
    return (f"M {x0i:.2f} {-y0i:.2f} L {x0o:.2f} {-y0o:.2f} "
            f"A {r1} {r1} 0 {large} 0 {x1o:.2f} {-y1o:.2f} "
            f"L {x1i:.2f} {-y1i:.2f} "
            f"A {r0} {r0} 0 {large} 1 {x0i:.2f} {-y0i:.2f} Z")

def callout(a, lines, r_in=R, r_out=R + 2.4, r_text=R + 3.2):
    """Leader line out of the board edge, with left/right-anchored label."""
    xi, yi = polar(a, r_in)
    xo, yo = polar(a, r_out)
    x, y = polar(a, r_text)
    if x > 1.0:
        anchor, dx = "start", 0.8
    elif x < -1.0:
        anchor, dx = "end", -0.8
    else:
        anchor, dx = "middle", 0.0
    out = [f'<path class="dim" d="M {xi:.2f} {-yi:.2f} L {xo:.2f} {-yo:.2f}"/>']
    y0 = -y + 0.6 - 1.5 * (len(lines) - 1) / 2
    for i, (txt, cls) in enumerate(lines):
        out.append(f'<text class="{cls}" text-anchor="{anchor}" '
                   f'x="{x + dx:.2f}" y="{y0 + 1.75 * i:.2f}">{txt}</text>')
    return "\n".join(out)

def board_a_plan():
    """Board A, outward face, drawn from the actual placement.

    Not a sketch of the intent -- the same coordinates gen_boards.py puts in
    the KiCad file, so the drawing cannot describe a board that is not there.
    Front-side courtyards are solid, back-side ones dashed.
    """
    import placement as PL
    s = [svg_open("-54 -44 108 88", 940, 766)]
    a = s.append
    a('<title>Board A, motor board, outward face, in mm. '
      'Solid: outward side. Dashed: motor-facing side.</title>')
    a(f'<circle class="board" cx="0" cy="0" r="{R}"/>')

    # the heatsink land, and the line every part stops at
    for ang in PHASE_ANG:
        a(f'<path class="pad" d="{sector(ang - PL.RING_HALF, ang + PL.RING_HALF, PL.R_RING_ID, PL.R_RING_OD)}" opacity=".85"/>')
        a(f'<path class="lead" d="{sector(ang - PL.PAD_HALF, ang + PL.PAD_HALF, PL.PAD_R0, PL.PAD_R1)}" '
          f'fill="none" stroke-dasharray="1 .8"/>')
    a(f'<circle class="keep" cx="0" cy="0" r="{PL.R_RING_ID}"/>')
    a(f'<circle class="keep" cx="0" cy="0" r="{R_CENTRE}"/>')

    # every placed part, at its real courtyard
    for part in PL.board_a():
        pts = " ".join(f"{x:.2f},{-y:.2f}" for x, y in part.corners())
        back = part.layer.startswith("B.")
        extra = ' stroke-dasharray=".6 .5" opacity=".55"' if back else ''
        a(f'<polygon class="part" points="{pts}"{extra}/>')

    # board-B mounting / heatsink bosses
    for ang in PERIM_ANG:
        x, y = polar(ang, PERIM_BC / 2)
        a(f'<circle class="hole" cx="{x:.2f}" cy="{-y:.2f}" r="{PERIM_D/2}"/>')
    for (x, y) in [(MOUNT_X, 0), (-MOUNT_X, 0), (0, MOUNT_Y), (0, -MOUNT_Y)]:
        a(f'<circle class="keep" cx="{x}" cy="{-y}" r="{MOUNT_HEAD/2}"/>')
        a(f'<circle class="hole" cx="{x}" cy="{-y}" r="{MOUNT_D/2}"/>')

    a('<text class="tiny ctr" x="0" y="0.5">MT6701</text>')
    a('<text class="tiny ctr" x="0" y="3.2">far face</text>')

    for i, ang in enumerate(PHASE_ANG):
        a(callout(ang - 20, [(f'phase {"ABC"[i]}', 'lbl'),
                             ('2 FET + driver + DC link', 'tiny'),
                             ('shunt and lead pad on the far face', 'tiny')]))
    a(callout(PHASE_ANG[0] + 20, [('heatsink land', 'lbl'),
                                  ('bare copper, R 29.2-31.5,', 'tiny'),
                                  ('one per cell', 'tiny')]))
    a(callout(0.0,   [('power link', 'lbl'), ('2 x 2x5, 10 VBUS + 10 GND', 'tiny')]))
    a(callout(120.0, [('RP2350A', 'lbl'), ('QFN-60 + crystal + flash + LDO', 'tiny')]))
    a(callout(240.0, [('signal link', 'lbl'), ('2x14 @ 1.27, 18 signals', 'tiny')]))

    a(f'<path class="dim" d="M {-R} 40.5 H {R} M {-R} 39.5 v 2 M {R} 39.5 v 2"/>')
    a(f'<text class="tiny ctr" x="0" y="40.0">&#216;{DIA:.0f} board, '
      f'{len(PL.board_a())} parts</text>')
    a(f'<text class="tiny ctr" x="0" y="43.4">dashed courtyards are on the '
      f'motor-facing side; nothing but the encoder inside &#216;{R_CENTRE*2:.0f}</text>')
    a(f'<text class="tiny ctr" x="0" y="-38.5">&#216;{PERIM_BC:.0f} bolt circle, '
      f'{PERIM_N} &#215; M2.5 &#8212; standoff, heatsink joint and ground bond</text>')
    a('</svg>')
    return "\n".join(s)

# ------------------------------------------------------------ stack drawing --
def ladder(items, x_text, min_gap=3.4):
    """Spread labels vertically so they cannot collide, then elbow a leader
    from each feature to its label. items: (y_feature, x_feature, [(txt,cls)])."""
    items = sorted(items, key=lambda it: it[0])
    ys, last = [], -1e9
    for (yf, _, lines) in items:
        y = max(yf, last + min_gap) if last > -1e9 else yf
        need = min_gap * (len(lines) - 1) * .55
        y = max(y, last + min_gap + need) if last > -1e9 else y
        ys.append(y); last = y + min_gap * (len(lines) - 1) * .55
    out = []
    for (yf, xf, lines), y in zip(items, ys):
        out.append(f'<path class="dim" d="M {xf:.2f} {yf:.2f} L {x_text-2.2:.2f} {yf:.2f} '
                   f'L {x_text-1.0:.2f} {y:.2f}"/>')
        y0 = y + 0.5 - 1.6 * (len(lines) - 1) / 2
        for i, (txt, cls) in enumerate(lines):
            out.append(f'<text class="{cls}" x="{x_text:.2f}" y="{y0 + 1.8*i:.2f}">{txt}</text>')
    return "\n".join(out)

def stack_side():
    """Side elevation. y_up is the physical axis; SVG y is -y_up."""
    def rect(cls, x, w, y_up, h, extra=""):
        return (f'<rect class="{cls}" x="{x:.2f}" y="{-(y_up+h):.2f}" '
                f'width="{w:.2f}" height="{h:.2f}" {extra}/>')

    y = 0.0
    s = [svg_open("-52 -44 138 62", 980, 440)]
    a = s.append
    a('<title>servodrive stack elevation, in mm</title>')

    # motor
    a(rect("board", -35, 70, -MOTOR_BODY, MOTOR_BODY))
    a(f'<text class="sub ctr" x="0" y="{MOTOR_BODY/2+0.6:.2f}">motor</text>')
    a(f'<path class="dim" d="M -35 0 H 35"/>')

    # encoder chain
    a(rect("pad", -3, 6, 0, SHAFT_PROUD))
    y = SHAFT_PROUD
    a(rect("part", -4, 8, y, MAGNET_T))
    y += MAGNET_T + AIR_GAP
    a(rect("part", -3.2, 6.4, y, MT6701_T, 'opacity=".7"'))
    y += MT6701_T
    # the air gap, hatched by a pair of thin guides
    a(f'<path class="keep" d="M -9 {-(SHAFT_PROUD+MAGNET_T):.2f} H 9 '
      f'M -9 {-(SHAFT_PROUD+MAGNET_T+AIR_GAP):.2f} H 9"/>')

    # standoffs under board A, at the motor mount pattern
    for sx in (-MOUNT_X, MOUNT_X):
        a(rect("board", sx-1.6, 3.2, 0, STANDOFF_A))
    a(rect("part", -R, DIA, STANDOFF_A, PCB_T))
    yA = STANDOFF_A + PCB_T

    # standoffs to board B, at the perimeter bolt circle
    for sx in (-PERIM_BC/2, PERIM_BC/2):
        a(rect("board", sx-1.6, 3.2, yA, GAP_AB))
    a(rect("part", -R, DIA, yA + GAP_AB, PCB_T))
    yB = yA + GAP_AB + PCB_T

    # board B's connectors, sketched
    a(rect("zone", -R, DIA, yB, GAP_TOP))
    a(rect("part", -26, 9, yB, 9.0, 'opacity=".6"'))
    a(rect("part", 4, 11, yB, 6.5, 'opacity=".6"'))
    a(rect("part", 19, 8, yB, 10.0, 'opacity=".6"'))
    top = yB + GAP_TOP

    # vertical dimension for the derived standoff, on the left
    xd = -38.5
    a(f'<path class="dim" d="M {xd} 0 V {-STANDOFF_A:.2f} '
      f'M {xd-1} 0 h 2 M {xd-1} {-STANDOFF_A:.2f} h 2"/>')
    a(f'<text class="tiny" text-anchor="end" x="{xd-1.8:.2f}" '
      f'y="{-STANDOFF_A/2-0.4:.2f}">{STANDOFF_A:.2f} mm</text>')
    a(f'<text class="tiny" text-anchor="end" x="{xd-1.8:.2f}" '
      f'y="{-STANDOFF_A/2+1.4:.2f}">M3 standoff</text>')

    # labels
    a(ladder([
        (-(SHAFT_PROUD - 2.5), 3.0,
         [('shaft, proud of the rear face', 'lbl'),
          (f'{SHAFT_PROUD:.1f} mm &#8212; set by the motor, not by us', 'tiny')]),
        (-(SHAFT_PROUD + MAGNET_T/2), 4.0,
         [(f'&#216;{MAGNET_D:.0f} diametric magnet', 'lbl'),
          (f'{MAGNET_T:.1f} mm with its holder cap', 'tiny')]),
        (-(SHAFT_PROUD + MAGNET_T + AIR_GAP/2), 9.0,
         [(f'air gap {AIR_GAP:.1f} mm', 'lbl'),
          ('MT6701 wants 1&#8211;3 mm; this is the', 'tiny'),
          ('dimension the whole stack is built around', 'tiny')]),
        (-(STANDOFF_A + PCB_T/2), R,
         [('board A &#8212; motor board', 'lbl'),
          (f'6 layer, 2 oz outer, {PCB_T} mm; MT6701 on the underside', 'tiny')]),
        (-(yA + GAP_AB/2), PERIM_BC/2 + 1.6,
         [(f'{GAP_AB:.0f} mm on M2.5 perimeter standoffs', 'lbl'),
          ('board A parts + mated interconnect height', 'tiny')]),
        (-(yA + GAP_AB + PCB_T/2), R,
         [('board B &#8212; power / IO', 'lbl'),
          (f'4 layer, 2 oz outer, {PCB_T} mm', 'tiny')]),
        (-(yB + GAP_TOP/2), 27,
         [('XT30, two RS-485 ports, USB-C,', 'lbl'),
          ('100 &#181;F/100 V cans', 'tiny')]),
    ], x_text=40.0))

    a(f'<text class="tiny ctr" x="0" y="{-top-3.0:.2f}">'
      f'{top:.1f} mm above the motor rear face</text>')
    a('</svg>')
    return "\n".join(s)

if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    (OUT / "board_a_plan.svg").write_text(board_a_plan())
    (OUT / "stack.svg").write_text(stack_side())
    print("wrote", OUT / "board_a_plan.svg")
    print("wrote", OUT / "stack.svg")
    print(f"usable annulus R{R_CENTRE}-{R_USABLE} = "
          f"{3.14159*(R_USABLE**2 - R_CENTRE**2):.0f} mm2 per side")
    print(f"standoff under board A = {STANDOFF_A:.2f} mm  (derived)")
    print(f"total above motor rear face = "
          f"{STANDOFF_A + 2*PCB_T + GAP_AB + GAP_TOP:.1f} mm")

    # Print the model too, so re-running this file is also how you check that
    # the numbers the spec quotes are still the numbers the design has.
    L = losses()
    print(f"\ndissipation at {I_PHASE:.0f} A RMS/phase, {FSW/1e3:.0f} kHz, {BUS_V:.0f} V:")
    for k in ("conduction", "switching", "shunts", "gate", "logic", "total"):
        print(f"  {k:12s} {L[k]:5.2f} W")
    print(f"  edges {L['t_on']*1e9:.0f} ns on / {L['t_off']*1e9:.0f} ns off, driver-limited")
    print(f"  at 10 A RMS: {losses(10.0)['total']:.2f} W")

    r_via, r_one = via_thermal()
    r_sink, area = sink_needed()
    print(f"\nthermal: via array {r_via:.2f} K/W ({FET_VIA_N} x {VIA_DRILL} mm)")
    print(f"  heatsink must be <= {r_sink:.2f} K/W  =>  >= {area:.0f} cm2 of external surface")
    for label, r in (("bare stack", R_STACK_BARE), ("finned ring", 4.8)):
        th = thermal(r)
        print(f"  {label:12s} ({r:4.1f} K/W): board {th['t_board']:5.1f} C, "
              f"Tj {th['t_j']:5.1f} C  {'OK' if th['t_j'] <= TJ_MAX else 'TOO HOT'}")

    b = bootstrap()
    print(f"\nbootstrap 1 uF: {b['droop']*1e3:.0f} mV droop, "
          f"{b['margin']:.2f} V over VB(on)")
    c = commutation_loop()
    print(f"commutation loop: {c['l_total']*1e9:.2f} nH -> {c['overshoot']:.1f} V overshoot, "
          f"{c['v_peak']:.1f} V peak on {BUS_SILICON:.0f} V silicon")
    for n in (8, 10):
        i = interconnect(n)
        print(f"power link {n}+{n} pins: {i['i_link']:.1f} A RMS vs {i['capacity']:.0f} A  "
              f"{'OK' if i['ok'] else 'OVER'}")
    print(f"sense: {SHUNT_R*1e3:.1f} mOhm x{INA_GAIN:.0f} = {V_PER_A*1e3:.0f} mV/A, "
          f"+-{I_FULL_SCALE:.1f} A full scale vs {I_PHASE_PK:.1f} A peak")
