#!/usr/bin/env python3
"""gen_boards.py — emit the servodrive KiCad projects from tools/geometry.py.

The board outlines, the motor mount pattern and the perimeter bolt circle are
not things to draw by hand twice. They come from geometry.py, which is also what
the spec quotes, so the boards and the prose cannot disagree.

    python3 tools/gen_boards.py            # create anything missing
    python3 tools/gen_boards.py --force    # overwrite, DESTROYS layout work

Refuses to overwrite an existing .kicad_pcb or .kicad_sch without --force: once
you have placed a part, this script is no longer the source of truth for that
file and re-running it would throw the work away.
"""
import argparse, json, os, re, shutil, uuid
from math import cos, sin, radians, tau
from pathlib import Path

import geometry as G
import placement as PL
import sexp
import schematic as SCH
import stitch
import fanout
import plot_layers
import export_3d

ROOT = Path(__file__).resolve().parent.parent
HW   = ROOT / "hardware"

# Deterministic UUIDs, so re-running produces byte-identical files.
NS = uuid.UUID("6f2a1c44-0000-4000-8000-000000000000")
def uid(*parts): return str(uuid.uuid5(NS, "/".join(str(p) for p in parts)))

# KiCad places the origin at the sheet's top-left; put the board centre here.
CX, CY = 148.0, 105.0
def P(x, y):
    """geometry.py coords (y up, origin at the shaft) -> KiCad page coords."""
    return (CX + x, CY - y)

# ---------------------------------------------------------------- boards ----
BOARDS = {
    "motor_board": dict(
        name="servodrive_A", title="servodrive — board A, motor board",
        layers=6, copper=["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu"],
        motor_mount=True,
        note=("MT6701 at (0,0) on the MOTOR-FACING side. Bridge, drivers, "
              "shunts, RP2350A and the DC-link ceramics on the outward side."),
    ),
    # Board S: board A and the minimum of board B on one board (2026-09-22).
    # Same stack, same mount, same cells and CPU; the two link wedges and the
    # centre carry what board B would have. Emitted only by --board s.
    "single_board": dict(
        name="servodrive_S", title="servodrive — board S, single board",
        layers=6, copper=["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu"],
        motor_mount=True, variant="s",
        note=("Board A plus the minimum of board B. 48 V max. Bus pads, TVS and cans, "
              "two LMR38010 rails, USB-C, RS-485 relay on two SH 6, expansion header."),
    ),
    "power_board": dict(
        name="servodrive_B", title="servodrive — board B, power / IO",
        layers=4, copper=["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"],
        motor_mount=False,
        note=("Mounts on the six perimeter M2.5. Bus input, ideal diode, TVS, "
              "bulk, two LMR38010 rails, FUSB302 PD, two RS-485 ports."),
    ),
}

LAYER_IDS = {"F.Cu": 0, "B.Cu": 2, "In1.Cu": 4, "In2.Cu": 6, "In3.Cu": 8, "In4.Cu": 10}
USER_LAYERS = '''		(9 "F.Adhes" user "F.Adhesive")
		(11 "B.Adhes" user "B.Adhesive")
		(13 "F.Paste" user)
		(15 "B.Paste" user)
		(5 "F.SilkS" user "F.Silkscreen")
		(7 "B.SilkS" user "B.Silkscreen")
		(1 "F.Mask" user)
		(3 "B.Mask" user)
		(17 "Dwgs.User" user "User.Drawings")
		(19 "Cmts.User" user "User.Comments")
		(21 "Eco1.User" user "User.Eco1")
		(23 "Eco2.User" user "User.Eco2")
		(25 "Edge.Cuts" user)
		(27 "Margin" user)
		(31 "F.CrtYd" user "F.Courtyard")
		(29 "B.CrtYd" user "B.Courtyard")
		(35 "F.Fab" user)
		(33 "B.Fab" user)'''

def stackup(copper):
    """2 oz outers, 1 oz inners, 1.6 mm overall — same recipe as the sister board."""
    out = ['\t\t(stackup',
           '\t\t\t(layer "F.SilkS" (type "Top Silk Screen"))',
           '\t\t\t(layer "F.Paste" (type "Top Solder Paste"))',
           '\t\t\t(layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))']
    n = len(copper)
    # Dielectrics sized so the stack really does land on 1.6 mm: copper
    # (2 x 0.07 outer + (n-2) x 0.035 inner) + masks (2 x 0.01) + prepreg
    # (2 x 0.1) + cores. The 0.1 mm outer prepreg is not a rounding choice --
    # it is what puts the In1 ground plane 0.1 mm under the commutation loop,
    # and geometry.commutation_loop() is quoted at that number.
    PREPREG, MASK = 0.10, 0.01
    cu = 2 * 0.07 + (n - 2) * 0.035
    core = (1.6 - cu - 2 * MASK - 2 * PREPREG) / (n - 3)
    d = 0
    for i, cu in enumerate(copper):
        th = 0.07 if cu in ("F.Cu", "B.Cu") else 0.035
        out.append(f'\t\t\t(layer "{cu}" (type "copper") (thickness {th}))')
        if i < n - 1:
            d += 1
            t = PREPREG if (i == 0 or i == n - 2) else core
            kind = "prepreg" if (i == 0 or i == n - 2) else "core"
            out.append(f'\t\t\t(layer "dielectric {d}" (type "{kind}") (thickness {t}) '
                       f'(material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))')
    out += ['\t\t\t(layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01))',
            '\t\t\t(layer "B.Paste" (type "Bottom Solder Paste"))',
            '\t\t\t(layer "B.SilkS" (type "Bottom Silk Screen"))',
            '\t\t\t(copper_finish "None")',
            '\t\t\t(dielectric_constraints yes)',
            '\t\t)']
    return "\n".join(out)

# ------------------------------------------------------------- variants ----
# Board A and board S come out of the same functions. Which netlist and which
# placement they read is the ACTIVE variant, set by board() and main(); board
# A is the default, so nothing that did not ask for S sees anything new.
def _s_comps():
    import schematic_s
    return schematic_s.board_s()

def _s_parts():
    import placement_s
    return placement_s.parts()

VARIANTS = {"a": dict(comps=SCH.board_a, parts=PL.board_a),
            "s": dict(comps=_s_comps, parts=_s_parts)}
_ACTIVE = "a"
_CACHE = {}

def use(variant):
    global _ACTIVE
    _ACTIVE = variant

def comps():
    """The active variant's netlist (schematic.Comp)."""
    key = ("comps", _ACTIVE)
    if key not in _CACHE:
        _CACHE[key] = VARIANTS[_ACTIVE]["comps"]()
    return _CACHE[key]

def placed():
    """The active variant's placement (placement.Part). On board S the
    netlist's value wins: the sketch's parts carry the sketch's values."""
    key = ("parts", _ACTIVE)
    if key not in _CACHE:
        ps = VARIANTS[_ACTIVE]["parts"]()
        if _ACTIVE != "a":
            val = {c.ref: c.value for c in comps()}
            for p in ps:
                p.value = val.get(p.ref, p.value)
        _CACHE[key] = ps
    return _CACHE[key]

def net_table():
    """{name: index} for the whole board. Index 0 is the no-net net.

    A board with no nets is not routable and DRC on it proves nothing: every
    pad is its own island, so clearance checks fire between pins of the same
    part and shorts between different ones go unnoticed. The netlist is in
    schematic.py; this is how it reaches the copper.
    """
    key = ("nets", _ACTIVE)
    if key not in _CACHE:
        names = sorted({n for c in comps() for n in c.nets.values() if n})
        t = {"": 0}
        t.update({n: i + 1 for i, n in enumerate(names)})
        _CACHE[key] = t
    return _CACHE[key]

def pad_nets(ref):
    """{pad number: net name} for one reference, from the netlist."""
    for c in comps():
        if c.ref == ref:
            return c.nets
    return {}

def _apply_nets(body, nets):
    """Put (net ...) on every pad the netlist has an entry for."""
    if not nets:
        return body
    table = net_table()
    for a, b in sorted(_blocks(body, "pad"), reverse=True):
        blk = body[a:b]
        num = re.match(r'\(pad\s+"([^"]*)"', blk)
        if not num:
            continue
        net = nets.get(num.group(1))
        if not net:
            continue
        m = re.search(r'\(layers[^)]*\)', blk)
        if not m:
            continue
        blk = (blk[:m.end()]
               + f'\n\t\t(net {table[net]} "{net}")'
               + blk[m.end():])
        body = body[:a] + blk + body[b:]
    return body

def embed(fp_path, lib, name, ref, x, y, ang=0.0, layer="F.Cu", value=None,
          note="", tag="", dnp=False, nets=None):
    """Put one library footprint on a board, at a position and an angle.

    Everything placed on either board goes through here -- parts, mounting
    holes, heatsink lands, phase pads. It used to be two near-identical
    functions and they drifted: the second one never learned to flip, and three
    motor-lead pads spent a build on the wrong side of the board.

    DRC compares every board footprint against its library copy, so the library
    body is what goes in, edited as little as possible: the designator, the
    value, the layer, and nothing else.
    """
    src = Path(fp_path).read_text()
    body = src[src.index("\n", src.index('(footprint "')) + 1:].rstrip()
    assert body.endswith(")"), f"{name}: unexpected footprint file shape"
    body = body[:-1].rstrip()
    for pat in (r'\t\(version \d+\)\n', r'\t\(generator "[^"]*"\)\n',
                r'\t\(generator_version "[^"]*"\)\n', r'\t\(layer "F\.Cu"\)\n'):
        body = re.sub(pat, "", body, count=1)
    body = set_property(body, "Reference", ref, "F.Fab")
    if value is not None:
        body = set_property(body, "Value", value)
    if dnp:
        # DNP is an attribute token on a board footprint, not its own
        # s-expression: "(dnp yes)" parses in a schematic and fails here.
        if re.search(r'^\t\(attr [^)]*\)', body, re.M):
            body = re.sub(r'^(\t\(attr [^)]*)\)', r'\1 dnp)', body, count=1, flags=re.M)
        else:
            body = "\t(attr dnp)\n" + body
    body = strip_silk(body)
    if layer == "B.Cu":
        body = _flip(body)
    # after the flip, not before: flipping negates any angle it finds, and the
    # angle a custom pad needs is the footprint's own, on either side.
    body = rotate_pads(body, ang)
    body = _apply_nets(body, nets)
    # Every uuid inside the library body is the library's, so two instances
    # of one footprint share them; KiCad's DRC report then looks an item up
    # by uuid and describes the wrong footprint's pad. One per instance.
    k = [0]
    def fresh(m):
        k[0] += 1
        return f'(uuid "{uid(tag, ref, "item", k[0])}")'
    body = re.sub(r'\(uuid "[0-9a-fA-F-]+"\)', fresh, body)
    body = "\n".join(("\t" + ln) if ln.strip() else ln for ln in body.split("\n"))
    px, py = P(x, y)
    fab = "B.Fab" if layer == "B.Cu" else "F.Fab"
    return (f'\t(footprint "{lib}:{name}"\n'
            f'\t\t(layer "{layer}")\n'
            f'\t\t(uuid "{uid(tag, ref, "fp")}")\n'
            f'\t\t(at {px:.4f} {py:.4f} {ang:.3f})\n'
            f'\t\t(property "servodrive_role" "{note}"\n'
            f'\t\t\t(at 0 0 0) (layer "{fab}") (hide yes) '
            f'(uuid "{uid(tag, ref, "role")}")\n'
            f'\t\t\t(effects (font (size 0.6 0.6) (thickness 0.1))))\n'
            f'{body}\n\t)')

KFP      = Path("/usr/share/kicad/footprints")
STOCK_FP = KFP / "MountingHole.pretty"
PROJ_FP  = HW / "parts/servodrive.pretty"

def mounting_hole(ref, fp_name, x, y, tag, desc, src_dir=None,
                  lib="MountingHole", rot=0.0, layer="F.Cu"):
    """A hole, a heatsink land or a phase pad: geometry with no schematic."""
    return embed((src_dir or STOCK_FP) / f"{fp_name}.kicad_mod", lib, fp_name,
                 ref, x, y, rot, layer, note=desc, tag=tag, nets=pad_nets(ref))

# ------------------------------------------------ derived footprints ----
# Three footprints this design needs that no library has. They are GENERATED,
# from the same constants as everything else, so the thermal via pitch and the
# ring land cannot drift from geometry.py's thermal model.

def _fp_head(name, descr, ref_prefix, extra_attr="smd exclude_from_pos_files"):
    return (f'(footprint "{name}"\n'
            f'\t(version 20241229)\n'
            f'\t(generator "servodrive/tools/gen_boards.py")\n'
            f'\t(layer "F.Cu")\n'
            f'\t(descr "{descr}")\n'
            f'\t(tags "servodrive thermal")\n'
            f'\t(attr {extra_attr})\n'
            f'\t(property "Reference" "{ref_prefix}**"\n'
            f'\t\t(at 0 0 0) (layer "F.Fab")\n'
            f'\t\t(effects (font (size 1 1) (thickness 0.15))))\n'
            f'\t(property "Value" "{name}"\n'
            f'\t\t(at 0 1.6 0) (layer "F.Fab")\n'
            f'\t\t(effects (font (size 0.8 0.8) (thickness 0.12))))\n')

def fet_thermal_vias(ny=None, pitch_y=None, shift=0.0, name="TDSON-8-1_ThermalVias"):
    """TDSON-8-1 with a via array in the drain pad.

    KiCad ships both the symbol for this exact MOSFET and the footprint its
    pin numbering assumes, so both come from there: pads 1-3 source, 4 gate,
    5 drain. The sister project's PowerPAK_SO-8_123 numbers them 1/2/3 =
    S/G/D, which no stock symbol matches -- a mismatch waiting to be wired
    backwards.

    The array is what gets the FET's 1.6 W into the inner copper, and the
    phase current through the board: geometry.via_thermal() puts 16 barrels
    of 0.4 mm at 7.6 K/W, against 354 K/W for the FR4 on its own. The pitch
    is set by the 0.5 mm hole-to-hole rule the project adopted so JLCPCB's
    free via-in-pad stays available (their F-48). The high side takes a
    fifth row (FET_VIA_NY_HS) -- see geometry.py for why only it can.
    """
    src = (KFP / "Package_TO_SOT_SMD.pretty" / "TDSON-8-1.kicad_mod").read_text()
    PAD_C, PAD_W, PAD_H = 1.05, 4.55, 4.41        # drain pad, from the library
    nx = G.FET_VIA_NX
    ny = G.FET_VIA_NY if ny is None else ny
    px = G.FET_VIA_PITCH
    py = G.FET_VIA_PITCH if pitch_y is None else pitch_y
    vias, k = [], 0
    for i in range(nx):
        for j in range(ny):
            dx = (i - (nx - 1) / 2) * px + shift
            dy = (j - (ny - 1) / 2) * py
            assert abs(dx) + G.VIA_PAD / 2 <= PAD_W / 2, "via array too wide"
            assert abs(dy) + G.VIA_PAD / 2 <= PAD_H / 2, "via array too tall"
            assert min(px, py) - G.VIA_DRILL >= 0.5, "inside the 0.5 mm hole-to-hole rule"
            k += 1
            vias.append(
                f'\t(pad "5" thru_hole circle\n'
                f'\t\t(at {PAD_C + dx:.4f} {dy:.4f})\n'
                f'\t\t(size {G.VIA_PAD} {G.VIA_PAD})\n'
                f'\t\t(drill {G.VIA_DRILL})\n'
                f'\t\t(layers "*.Cu")\n'
                f'\t\t(remove_unused_layers no)\n'
                f'\t\t(zone_connect 2)\n'
                f'\t)')
    out = src.replace('(footprint "TDSON-8-1"', f'(footprint "{name}"', 1)
    out = re.sub(r'\(descr "([^"]*)"',
                 lambda m: f'(descr "{m.group(1)} - plus a {k}-via thermal array in the '
                           f'drain pad, {nx} x {ny} at {px} x {py} mm '
                           f'({min(px, py) - G.VIA_DRILL:.3f} mm hole-to-hole)"',
                 out, count=1)
    i = out.rstrip().rfind(")")
    return out[:i] + "\n".join(vias) + "\n" + out[i:]

def fet_thermal_vias_hs():
    """The high side's: a fifth row of barrels at 0.905 mm, and the array
    moved 0.15 mm off the source side of the pad."""
    return fet_thermal_vias(G.FET_VIA_NY_HS, G.FET_VIA_PITCH_HS, G.FET_VIA_SHIFT_HS,
                            "TDSON-8-1_ThermalVias_HS")

def thermal_boss():
    """M2.5 perimeter hole as a plated, mask-free boss on every layer.

    Six of these are where board A's heat leaves for the aluminium ring, and
    they are also what ties the ring to GND. 4.8 mm keeps 0.6 mm to the edge.
    """
    D, PAD = G.PERIM_D, 4.8
    assert G.PERIM_BC / 2 + PAD / 2 <= G.R - 0.3, "boss pad too close to the edge"
    return (_fp_head("ThermalBoss_M2.5",
                     f"M2.5 clearance hole, plated, {PAD} mm pad on every layer with "
                     f"solder mask open both sides: the standoff/heatsink joint",
                     "H", "exclude_from_pos_files")
            + f'\t(pad "1" thru_hole circle\n'
              f'\t\t(at 0 0)\n'
              f'\t\t(size {PAD} {PAD})\n'
              f'\t\t(drill {D})\n'
              f'\t\t(layers "*.Cu" "*.Mask")\n'
              f'\t\t(remove_unused_layers no)\n'
              f'\t\t(zone_connect 2)\n'
              f'\t)\n'
              f'\t(fp_circle (center 0 0) (end {PAD/2 + 0.25:.3f} 0)\n'
              f'\t\t(stroke (width 0.05) (type solid)) (fill no) (layer "F.CrtYd"))\n'
              f'\t(fp_circle (center 0 0) (end {PAD/2 + 0.25:.3f} 0)\n'
              f'\t\t(stroke (width 0.05) (type solid)) (fill no) (layer "B.CrtYd"))\n'
              f')\n')

def sector_pad(name, r0, r1, half, descr, ref_prefix):
    """A single pad shaped as an annular sector, origin at its mid-radius.

    Both of the big pads on this board are arcs, for the same reason: a
    straight pad wide enough to be useful, placed near R 30 on a R 32.5 board,
    puts its corners off the edge. An arc follows the board instead.
    """
    rm = (r0 + r1) / 2
    pts, N = [], 24
    for k in range(N + 1):                              # outer arc
        a = radians(-half + 2 * half * k / N)
        pts.append((r1 * cos(a) - rm, r1 * sin(a)))
    for k in range(N + 1):                              # inner arc, back
        a = radians(half - 2 * half * k / N)
        pts.append((r0 * cos(a) - rm, r0 * sin(a)))
    poly = " ".join(f"(xy {x:.4f} {y:.4f})" for x, y in pts)
    area = radians(2 * half) / 2 * (r1**2 - r0**2)
    return (_fp_head(name, f"{descr} R {r0}-{r1} mm over {2*half:.0f} deg, "
                           f"{area:.0f} mm2, solder mask open. Origin at the "
                           f"mid-radius on the block axis.", ref_prefix)
            + f'\t(fp_poly (pts {poly})\n'
              f'\t\t(stroke (width 0.05) (type solid)) (fill no) (layer "F.CrtYd"))\n'
              f'\t(pad "1" smd custom\n'
              f'\t\t(at 0 0)\n'
              f'\t\t(size 1 1)\n'
              f'\t\t(layers "F.Cu" "F.Mask")\n'
              f'\t\t(zone_connect 2)\n'
              f'\t\t(options (clearance outline) (anchor circle))\n'
              f'\t\t(primitives\n'
              f'\t\t\t(gr_poly (pts {poly}) (width 0) (fill yes))\n'
              f'\t\t)\n'
              f'\t)\n'
              f')\n'), area

def thermal_land():
    """The bare copper each phase cell offers the aluminium heatsink ring.

    One arc per cell, between that cell's two perimeter bolts. With the six
    bosses this is the area geometry.thermal()'s 0.4 K/W joint assumes, and it
    is the whole reason the parts inside stop at R 29.2.
    """
    return sector_pad("ThermalLand_Phase", PL.R_RING_ID, PL.R_RING_OD,
                      PL.RING_HALF, "Bare-copper heatsink land,", "TL")[0]

def phase_pad():
    """Where a motor lead lands, on the motor-facing side."""
    return sector_pad("PhasePad_Arc", PL.PAD_R0, PL.PAD_R1, PL.PAD_HALF,
                      "Motor phase lead pad, 14 AWG,", "J")[0]

# Board S's bus input: two arc pads on the outward face at the power wedge's
# rim, a 14 AWG lead soldered flat to each (placement_s.BUS_PADS). They carry
# the whole bus current into the planes, so each takes a field of barrels --
# but not in the footprint: the RS-485 ports sit on the same stretch of rim
# on the other face, and a fixed array came through onto J14's MP pad. The
# fan-out drills them where the far face leaves room (fanout.bus_field).
BUS_R0, BUS_R1, BUS_HALF = 28.7, 31.4, 3.8

def bus_pad():
    return sector_pad("BusPad_Arc", BUS_R0, BUS_R1, BUS_HALF,
                      "Bus lead pad, 14 AWG, barrels drilled by fanout.bus_field,", "J")[0]

# ------------------------------------------------- placing footprints ----
BACK = {"F.Cu": "B.Cu", "F.Paste": "B.Paste", "F.Mask": "B.Mask",
        "F.SilkS": "B.SilkS", "F.CrtYd": "B.CrtYd", "F.Fab": "B.Fab"}

def _blocks(text, head):
    """Spans of every balanced "(head ...)" at any depth. Regex cannot do this
    on nested s-expressions, and every attempt to make it produced a file
    KiCad would not open."""
    out, i = [], 0
    while True:
        i = text.find("(" + head, i)
        if i < 0:
            return out
        nxt = text[i + 1 + len(head)]
        if nxt not in " \t\n(":
            i += 1
            continue
        d = 0
        for j in range(i, len(text)):
            if text[j] == "(":
                d += 1
            elif text[j] == ")":
                d -= 1
                if d == 0:
                    out.append((i, j + 1))
                    break
        else:
            return out
        i = out[-1][1]

def set_property(body, name, value, layer=None):
    """Rewrite a footprint property's value, and optionally its layer.

    Done by walking the s-expression rather than by regex: footprint files
    disagree about what sits between the name and the layer (an (at), maybe an
    (unlocked yes), maybe a (hide yes)), and a regex that assumed an order
    silently missed the PowerPAK and left six MOSFETs designated "AH2".
    """
    for a, b in _blocks(body, "property"):
        blk = body[a:b]
        if not blk.startswith(f'(property "{name}" '):
            continue
        i = blk.index('"', blk.index(f'"{name}"') + len(name) + 2)
        j = blk.index('"', i + 1)
        blk = blk[:i] + f'"{value}"' + blk[j + 1:]
        if layer:
            blk = re.sub(r'\(layer "[^"]*"\)', f'(layer "{layer}")', blk, count=1)
        return body[:a] + blk + body[b:]
    return body

def rotate_pads(body, ang):
    """Give every pad the footprint's absolute angle.

    KiCad does not derive a pad's orientation from its parent footprint: it
    stores an absolute angle per pad and writes one on every pad of every
    rotated footprint (checked against a KiCad-authored board -- 41 footprints
    at non-cardinal angles, every pad carrying its own angle). Leave it out and
    the pad POSITIONS rotate but the pad SHAPES do not, which on a board where
    almost nothing sits at 0 deg means every pad is drawn and checked in the
    wrong orientation. It shows up as impossible DRC violations -- a 1.27 mm
    pitch SOIC-8 reporting 0.0000 mm between adjacent pins at 90 deg and
    nothing at all at 45 deg -- and it would have been a fabricated board with
    every fine-pitch part's copper turned the wrong way.
    """
    if not ang:
        return body
    for a, b in sorted(_blocks(body, "pad"), reverse=True):
        blk = body[a:b]
        m = re.search(r'\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)', blk)
        if not m:
            continue
        rel = float(m.group(3) or 0.0)
        blk = (blk[:m.start()]
               + f'(at {m.group(1)} {m.group(2)} {(rel + ang) % 360:.3f})'
               + blk[m.end():])
        body = body[:a] + blk + body[b:]
    return body

def strip_silk(body):
    """Drop silkscreen graphics from a placed footprint.

    At 0.3 mm between courtyards every outline lands on a neighbour's pad;
    KiCad reported 46 silk_over_copper on the first pass. The part outlines
    live on the fabrication layer, which is where the assembly drawing reads
    them from anyway.
    """
    cuts = []
    for head in ("fp_line", "fp_arc", "fp_circle", "fp_poly", "fp_rect", "fp_text"):
        for a, b in _blocks(body, head):
            if '"F.SilkS"' in body[a:b] or '"B.SilkS"' in body[a:b]:
                cuts.append((a, b))
    for a, b in sorted(cuts, reverse=True):
        body = body[:a] + body[b:]
    return re.sub(r"\n\s*\n+", "\n", body)

def mirror_text(body):
    """Back-layer text has to be mirrored, or it reads backwards through the
    board -- KiCad's nonmirrored_text_on_back_layer, 102 of them."""
    for a, b in sorted(_blocks(body, "effects"), reverse=True):
        blk = body[a:b]
        if "justify" in blk:
            blk = re.sub(r"\(justify ([^)]*)\)",
                         lambda m: f"(justify {m.group(1)} mirror)"
                         if "mirror" not in m.group(1) else m.group(0), blk, count=1)
        else:
            blk = blk[:blk.rfind(")")] + " (justify mirror))"
        body = body[:a] + blk + body[b:]
    return body

def _flip(body):
    """Mirror a footprint body to the back, the way KiCad stores it.

    KiCad keeps every board item in top-view coordinates, so a back-side
    footprint is the library original with every y negated, every angle
    negated, and F.* swapped for B.*. Verified against a real board rather
    than assumed - see the sister project's back-side SOT-23-5.
    """
    def neg_at(m):
        parts = m.group(1).split()
        parts[1] = f"{-float(parts[1]):g}"
        if len(parts) > 2:
            parts[2] = f"{-float(parts[2]):g}"
        return "(at " + " ".join(parts) + ")"
    body = re.sub(r'\(at ([^()]*)\)', neg_at, body)
    for key in ("start", "end", "mid", "center", "xy"):
        body = re.sub(rf'\({key} ([-\d.eE+]+) ([-\d.eE+]+)\)',
                      lambda m, k=key: f"({k} {m.group(1)} {-float(m.group(2)):g})", body)
    body = re.sub(r'"(F)\.(Cu|Paste|Mask|SilkS|CrtYd|Fab)"',
                  lambda m: f'"B.{m.group(2)}"', body)
    return mirror_text(body)

def place(part, tag):
    """Embed one library footprint at a placement.py position."""
    lib, name = part.fp.split(":", 1)
    return embed(PL.fp_path(part.fp), lib, name, part.ref, part.x, part.y,
                 part.ang, part.layer, value=part.value,
                 note=(part.note or part.block).replace('"', "'"),
                 tag=tag, dnp=part.dnp, nets=pad_nets(part.ref))

def gr_circle(cx, cy, r, layer, width, tag, fill="no"):
    px, py = P(cx, cy)
    return (f'\t(gr_circle (center {px:.4f} {py:.4f}) (end {px + r:.4f} {py:.4f})\n'
            f'\t\t(stroke (width {width}) (type solid)) (fill {fill}) (layer "{layer}") '
            f'(uuid "{uid(tag)}"))')

def gr_arc(a0, a1, r, layer, width, tag):
    pts = [P(r * cos(radians(a)), r * sin(radians(a))) for a in (a0, (a0 + a1) / 2, a1)]
    (sx, sy), (mx, my), (ex, ey) = pts
    return (f'\t(gr_arc (start {sx:.4f} {sy:.4f}) (mid {mx:.4f} {my:.4f}) (end {ex:.4f} {ey:.4f})\n'
            f'\t\t(stroke (width {width}) (type dash)) (layer "{layer}") (uuid "{uid(tag)}"))')

def gr_line(x1, y1, x2, y2, layer, width, tag, style="solid"):
    (a, b), (c, d) = P(x1, y1), P(x2, y2)
    return (f'\t(gr_line (start {a:.4f} {b:.4f}) (end {c:.4f} {d:.4f})\n'
            f'\t\t(stroke (width {width}) (type {style})) (layer "{layer}") (uuid "{uid(tag)}"))')

def gr_rect_at(cx, cy, w, h, ang, layer, width, tag):
    """Rectangle centred at (cx,cy), rotated ang degrees, drawn as 4 lines."""
    a = radians(ang)
    corners = []
    for dx, dy in ((-w/2, -h/2), (w/2, -h/2), (w/2, h/2), (-w/2, h/2)):
        corners.append((cx + dx*cos(a) - dy*sin(a), cy + dx*sin(a) + dy*cos(a)))
    out = []
    for i in range(4):
        x1, y1 = corners[i]; x2, y2 = corners[(i+1) % 4]
        out.append(gr_line(x1, y1, x2, y2, layer, width, f"{tag}{i}", "dash"))
    return "\n".join(out)

def gr_text(txt, x, y, layer, size, tag, just=None):
    """`justify center` is not valid KiCad — centred is the default, so omit it."""
    px, py = P(x, y)
    j = f' (justify {just})' if just else ''
    return (f'\t(gr_text "{txt}" (at {px:.4f} {py:.4f} 0) (layer "{layer}") (uuid "{uid(tag)}")\n'
            f'\t\t(effects (font (size {size} {size}) (thickness {size/7:.3f})){j}))')

# ----------------------------------------------------------------- zones ----
# Most of a six-layer board's "routing" is not routing. In1 and In4 are solid
# ground, In2 is the VBUS plane, and between them they carry every amp on the
# board; what the autorouter is left with is the signals. In1 in particular is
# not a convenience -- it is the 0.1 mm return plane under the commutation
# loop, and geometry.commutation_loop()'s 0.39 nH is quoted against it staying
# solid.

def disc(r, n=96):
    """The board outline as a polygon, for a zone to fill to."""
    pts = []
    for i in range(n):
        x, y = G.polar(i * 360.0 / n, r)
        px, py = P(x, y)
        pts.append(f"(xy {px:.4f} {py:.4f})")
    return " ".join(pts)

def sector_poly(a0, a1, r0, r1, n=48):
    pts = []
    for i in range(n + 1):
        x, y = G.polar(a0 + (a1 - a0) * i / n, r1)
        px, py = P(x, y); pts.append(f"(xy {px:.4f} {py:.4f})")
    for i in range(n + 1):
        x, y = G.polar(a1 + (a0 - a1) * i / n, r0)
        px, py = P(x, y); pts.append(f"(xy {px:.4f} {py:.4f})")
    return " ".join(pts)

def rs_poly(axis, corners, n=24):
    """A polygon given as (r, s) corners in one cell's frame -- radius and
    mm of arc from the cell axis -- with every constant-radius edge drawn as
    an arc. For the pours whose outline is not a simple band."""
    from math import degrees
    pts = []
    k = len(corners)
    for i in range(k):
        (ra, sa), (rb, sb) = corners[i], corners[(i + 1) % k]
        m = n if abs(ra - rb) < 1e-9 and abs(sb - sa) > 0.5 else 1
        for j in range(m):
            r = ra + (rb - ra) * j / m
            s = sa + (sb - sa) * j / m
            x, y = G.polar(axis + degrees(s / r), r); px, py = P(x, y)
            pts.append(f"(xy {px:.4f} {py:.4f})")
    return " ".join(pts)

def notched_band(r0, r1, s0, s1, notch_in=(), notch_out=(), tabs=()):
    """(r, s) corners of a band with rectangular notches cut into its inner
    edge (raising r0 over a span of s) and its outer edge (lowering r1), and
    tabs standing out of its outer edge. A tab is its own list of (r, s)
    corners in decreasing s, the first and last of them on r1."""
    pts = [(r0, s0)]
    for a, b, r in sorted(notch_in):
        pts += [(r0, a), (r, a), (r, b), (r0, b)]
    pts += [(r0, s1), (r1, s1)]
    outer = [(b, [(r1, b), (r, b), (r, a), (r1, a)]) for a, b, r in notch_out]
    outer += [(tab[0][1], list(tab)) for tab in tabs]
    for _, seq in sorted(outer, key=lambda e: -e[0]):
        pts += seq
    pts += [(r1, s0)]
    return pts

def cell_rs(axis, x, y):
    """Board coordinates -> (r, s) in the cell whose axis is `axis` deg."""
    from math import hypot, atan2, degrees
    r = hypot(x, y)
    return r, radians((degrees(atan2(y, x)) - axis + 540) % 360 - 180) * r

def via_tab(axis, fet, r1, margin_r=0.2):
    """The switch-node pour's reach over a low-side FET's drain vias on B.Cu.

    Only the innermost row of that array used to reach the B.Cu pour, which
    stopped at r1 = R 25.2: the other barrels sat in the clearance between it
    and the phase-output pour, joined to F.Cu and to nothing below. Sim P3
    (Q7) had four barrels carrying the whole phase current, the busiest
    7.3 A peak. This is the array's own rectangle, in the FET's frame, from
    where its sides cross r1 out to its outer row: tangentially it stops at
    the centres of the end columns, so the fill reaches every pad and no
    further, and radially the fill is held off the lead pad by clearance.
    Returned as tab corners for notched_band()."""
    from math import cos, sin
    nx, ny = G.FET_VIA_NX, G.FET_VIA_NY          # the low side's array
    PAD_C = 1.05
    hx = (nx - 1) / 2 * G.FET_VIA_PITCH
    hy = (ny - 1) / 2 * G.FET_VIA_PITCH + margin_r
    a = radians(fet.ang)
    def board(lx, ly):                       # footprint frame, KiCad y-down
        vx, vy = lx, -ly
        return (fet.x + vx * cos(a) - vy * sin(a), fet.y + vx * sin(a) + vy * cos(a))
    corners = [cell_rs(axis, *board(PAD_C + dx, dy))
               for dx, dy in ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy))]
    inner = sorted(corners)[:2]              # the two nearest the shaft
    outer = sorted(corners)[2:]
    def cross(p, q):                         # where p -> q crosses r1, as (r1, s)
        (ra, sa), (rb, sb) = p, q
        f = (r1 - ra) / (rb - ra)
        return (r1, sa + (sb - sa) * f)
    # pair each inner corner with the outer corner on the same side (nearest s)
    sides = []
    for pi in inner:
        po = min(outer, key=lambda q: abs(q[1] - pi[1]))
        sides.append((cross(pi, po), po))
    sides.sort(key=lambda e: -e[0][1])       # decreasing s
    (b_hi, o_hi), (b_lo, o_lo) = sides
    assert inner[0][0] < r1 and inner[1][0] < r1, "the via array has to reach into the band"
    return [b_hi, o_hi, o_lo, b_lo]

def arc_band(axis, r0, r1, s0, s1, n=32):
    """A sector in one cell's frame: radii, and tangential offsets in mm.

    Taking the edges in millimetres rather than degrees is deliberate: what a
    power pour has to stop short of is pads, and a pad is a fixed number of
    millimetres from the cell axis whatever the radius.
    """
    from math import degrees
    pts = []
    for i in range(n + 1):
        s = s0 + (s1 - s0) * i / n
        x, y = G.polar(axis + degrees(s / r1), r1); px, py = P(x, y)
        pts.append(f"(xy {px:.4f} {py:.4f})")
    for i in range(n + 1):
        s = s1 + (s0 - s1) * i / n
        x, y = G.polar(axis + degrees(s / r0), r0); px, py = P(x, y)
        pts.append(f"(xy {px:.4f} {py:.4f})")
    return " ".join(pts)

# A zone's own clearance overrides its netclass, so it has to be told the same
# thing twice: 0.4 mm around the 60 V copper, 0.15 elsewhere -- the value the
# netclass would have applied if the zone were not carrying its own.
ZONE_CLEAR = {"VBUS": 0.4, "SW_A": 0.4, "SW_B": 0.4, "SW_C": 0.4,
              "PHASE_A": 0.4, "PHASE_B": 0.4, "PHASE_C": 0.4,
              "SNUB_A": 0.4, "SNUB_B": 0.4, "SNUB_C": 0.4}

def zone(net, name, layers, poly, tag, priority=0, solid=False,
         keep_islands=True, clearance=None, min_thickness=0.2, min_island=None):
    table = net_table()
    cl = ZONE_CLEAR.get(net, 0.2) if clearance is None else clearance
    connect = (f"(connect_pads yes (clearance {cl}))" if solid
               else f"(connect_pads (clearance {cl}))")
    lay = " ".join(f'"{l}"' for l in layers)
    # 0 is ALWAYS remove, 1 is NEVER, 2 is by area. Emitting 1 for the pours
    # was backwards and left fifteen isolated fills on the routed board.
    if min_island:
        island = f'\t\t\t(island_removal_mode 2) (island_area_min {min_island}))\n'
    else:
        island = f'\t\t\t(island_removal_mode {1 if keep_islands else 0}))\n'
    return (f'\t(zone\n'
            f'\t\t(net {table[net]})\n'
            f'\t\t(net_name "{net}")\n'
            f'\t\t(layers {lay})\n'
            f'\t\t(uuid "{uid(tag)}")\n'
            f'\t\t(name "{name}")\n'
            f'\t\t(hatch edge 0.5)\n'
            f'\t\t(priority {priority})\n'
            f'\t\t{connect}\n'
            f'\t\t(min_thickness {min_thickness})\n'
            f'\t\t(filled_areas_thickness no)\n'
            f'\t\t(fill yes (thermal_gap 0.3) (thermal_bridge_width 0.5)\n'
            f'{island}'
            f'\t\t(polygon (pts {poly}))\n'
            f'\t)')

def keyhole(a0, a1, r_in, r_out, n=96):
    """A disc of r_in with a sector a0..a1 (deg, CCW) reaching out to r_out:
    one simple polygon, so a net can have the centre AND a wedge on one layer
    without two zones overlapping."""
    pts = []
    span = (a1 - a0) % 360
    rest = 360 - span
    m = max(8, int(n * rest / 360))
    for i in range(m + 1):                       # the disc, the long way round
        x, y = G.polar(a1 + rest * i / m, r_in)
        px, py = P(x, y); pts.append(f"(xy {px:.4f} {py:.4f})")
    k = max(8, int(n * span / 360))
    for i in range(k + 1):                       # out along a0, round to a1
        x, y = G.polar(a0 + span * i / k, r_out)
        px, py = P(x, y); pts.append(f"(xy {px:.4f} {py:.4f})")
    return " ".join(pts)

# The +3V3 plane on In2: the CPU wedge and a little of the signal wedge, where
# the LDO sits, plus the whole centre disc. The disc is what feeds the three
# phase cells -- each needs 3V3 for its sense amplifier, and cell A for its thermistor
# -- and the encoder: a via anywhere inside R 17 lands on it. It costs the
# VBUS plane its innermost 2.5 mm, which it can spare at 47 A/mm2.
R_3V3_DISC = 17.0
R_VBUS_IN  = 17.5
A_3V3      = (G.WEDGE_ANG[1] - G.WEDGE_SPAN / 2 - 4, G.WEDGE_ANG[1] + G.WEDGE_SPAN / 2 + 12)

def _pad_xy(part, side):
    """Centre of a two-terminal part's pad in board coordinates: side +1 is
    the pad at the footprint's +x (pin 2 of a chip resistor or capacitor),
    -1 the one at -x (pin 1)."""
    from math import cos, sin
    # the chip footprints are symmetric: the pads sit at +-x about the origin
    pads = {"Resistor_SMD:R_0603_1608Metric": 0.825, "Capacitor_SMD:C_0603_1608Metric": 0.775}
    d = side * pads[part.fp]
    a = radians(part.ang)
    return part.x + d * cos(a), part.y + d * sin(a)

def zones(tag):
    """The plane stack. VBUS gets In2 where the power is, ground gets the rest."""
    out, edge = [], G.R_USABLE
    for lay in ("In1.Cu", "In4.Cu"):
        out.append(zone("GND", f"ground plane {lay}", [lay], disc(edge),
                        f"{tag}-z-{lay}", priority=0, solid=True))
    # In2: VBUS over the phase block and the power link that feeds it, ground
    # over the rest. The CPU does not want a 60 V plane under it.
    # One sector, not two: the phase block and the power link that feeds it are
    # contiguous, and two overlapping zones of one net on one layer is a DRC
    # error rather than a redundancy.
    if _ACTIVE == "s":
        out += in2_s(tag, edge)
    else:
        lo = G.PHASE_ANG[0] - G.PHASE_SPAN / 2 - 4
        hi = G.WEDGE_ANG[0] + G.WEDGE_SPAN / 2 - 2      # the CPU's back column starts at 258
        out.append(zone("VBUS", "VBUS plane In2", ["In2.Cu"],
                        sector_poly(lo, hi, R_VBUS_IN, edge), f"{tag}-z-vbus",
                        priority=2, solid=True))
        # ... and +3V3 over the CPU wedge, for the same reason in miniature. 26 of
        # the 40 3V3 pads are in this one wedge, and as tracks they were a fifth of
        # everything left for the router. A plane costs nothing here: In2 under the
        # CPU was going to be ground, In1 already is, and In1/In2 as a 3V3-to-GND
        # pair is a better decoupling capacitor than the pour it replaces.
        # Priority 1 keeps it under VBUS, which owns the 4 deg of overlap at 240.
        out.append(zone("+3V3", "3V3 plane In2", ["In2.Cu"],
                        keyhole(A_3V3[0], A_3V3[1], R_3V3_DISC, edge),
                        f"{tag}-z-3v3", priority=1, solid=True))
    out.append(zone("GND", "ground plane In2", ["In2.Cu"], disc(edge),
                    f"{tag}-z-in2gnd", priority=0, solid=True, min_island=2.0))
    # The switch node and the phase output are 20 A RMS. IPC-2152 wants about
    # 6 mm of 2 oz copper for that, so they are pours, not tracks -- an
    # autorouter handed SW_A would give it a trace, and the trace would be
    # wrong by a factor of three. Only the signals are left for the router.
    parts = {p.ref: p for p in placed()}
    for i, X in enumerate("ABC"):
        ax = G.PHASE_ANG[i]
        n = i + 1
        # F.Cu: the island between the two FETs. Its edges are set by the pads
        # either side of it -- the low-side source at s = -7.1 and the
        # high-side drain from s = +3.0 -- not by a round number.
        # The island is bounded by the pads: from the high-side source row at
        # s = -1.3 (clockwise of the centre line now that the FETs are turned)
        # to the low-side source row at +7.1, less its clearance; and from
        # 1.0 mm above the gate pads, which sit at the INNER end of the rows
        # and are reached from below, out to the heatsink land.
        out.append(zone(f"SW_{X}", f"switch node {X}, F.Cu", ["F.Cu"],
                        arc_band(ax, PL.R_FET - 0.9, PL.R_RING_ID, PL.S_SW_F0, PL.S_SW_F1),
                        f"{tag}-z-sw-f-{X}", priority=5, solid=True,
                        keep_islands=False))
        # B.Cu: the low-side drain's via cluster out to both shunt inner pads,
        # passing inboard of the high-side cluster, which is VBUS. Its inner
        # edge reaches down to R 20 so the driver's VS pin, the bootstrap
        # cap, the bleed and the sense tap all sit over it and take a via.
        # And a tab out over the whole of the low-side via array, so every
        # barrel carries phase current rather than the innermost row alone.
        out.append(zone(f"SW_{X}", f"switch node {X}, B.Cu", ["B.Cu"],
                        rs_poly(ax, notched_band(PL.R_SW_B0, 25.2, -PL.S_SW_B, PL.S_SW_B,
                                                 PL.SW_B_NOTCH_IN, PL.SW_B_NOTCH_OUT,
                                                 [via_tab(ax, parts[f"Q{2*n}"], 25.2)])),
                        f"{tag}-z-sw-b-{X}", priority=5, solid=True,
                        keep_islands=False))
        # B.Cu: the snubber's middle node, joining the facing pads of its
        # capacitor and resistor. A pour rather than a track, like every other
        # net here that swings with the switch node: it is complete before
        # the router sees it, and it gets the 60 V clearance.
        c_out = cell_rs(ax, *_pad_xy(parts[f"C{n}09"], +1))
        r_out = cell_rs(ax, *_pad_xy(parts[f"R{n}13"], -1))
        rm = (c_out[0] + r_out[0]) / 2
        out.append(zone(f"SNUB_{X}", f"snubber {X}, B.Cu", ["B.Cu"],
                        rs_poly(ax, [(rm - 0.3, c_out[1]), (rm - 0.3, r_out[1]),
                                     (rm + 0.3, r_out[1]), (rm + 0.3, c_out[1])]),
                        f"{tag}-z-snub-{X}", priority=6, solid=True,
                        keep_islands=False))
        # B.Cu: the lead pad's band, and its reach out into the channel on
        # either side of the lead pad, where the clamp sits.
        S0, S1 = PL.S_PH_NOTCH, PL.S_SW_B + 3.0
        R0, R1, R2 = PL.R_PH0, PL.R_PH1, PL.R_PH2
        out.append(zone(f"PHASE_{X}", f"phase {X} output, B.Cu", ["B.Cu"],
                        rs_poly(ax, [(R0, -S1), (R0, S1), (R2, S1), (R2, S0),
                                     (R1, S0), (R1, -S0), (R2, -S0), (R2, -S1)]),
                        f"{tag}-z-ph-{X}", priority=4, solid=True,
                        keep_islands=False))

    out += cpu_copper(tag)
    if _ACTIVE == "s":
        out += bus_spine(tag)

    # In3 is the signal layer; F.Cu and B.Cu carry the parts. All three take a
    # ground pour at the lowest priority, which stitches the planes together and
    # gives every via somewhere to land.
    #
    # Solid connection, not thermal relief. Every ground pad's real path to the
    # planes is the via in it, not these pours, so a relief here buys nothing
    # for soldering a reflowed board and costs the two-spoke rule: a routed
    # trace beside a 0402 leaves its ground pad one spoke, and DRC calls that a
    # starved thermal.
    for lay in ("F.Cu", "In3.Cu", "B.Cu"):
        out.append(zone("GND", f"ground pour {lay}", [lay], disc(edge),
                        f"{tag}-z-pour-{lay}", priority=0, solid=True,
                        # Routing carves these into islands. One that has no
                        # via is floating copper -- a ratsnest between the two
                        # pours, and an antenna -- so stitch.tie_islands()
                        # puts a via in every one it can, and whatever is
                        # still isolated after that goes, whatever its size:
                        # an area floor kept the ones above it, and five of
                        # those came back from every routing run as isolated
                        # copper.
                        keep_islands=False))
    return out

# ---------------------------------------------------------- board S zones ----
def _pts(geom):
    """A shapely polygon's exterior as a zone's (xy ...) list, page coords."""
    out = []
    for x, y in list(geom.exterior.coords)[:-1]:
        px, py = P(x, y)
        out.append(f"(xy {px:.4f} {py:.4f})")
    return " ".join(out)

def _pad(ref, num):
    """(x, y) of one pad of a placed part, geometry coordinates."""
    import padpos
    p = next(q for q in placed() if q.ref == ref)
    return next((x, y) for n, x, y, w, h in padpos.pad_xy(p) if n == num)

# Board S's In2. The bus now has to reach three places board A's never did:
# the two bulk cans in the centre and the two bucks in the signal wedge, whose
# In2 was ground on board A because the signal link lived there. So VBUS is
# every sector but the CPU wedge's -- 306 deg round through 0 to 254 --
# and +3V3 is the CPU wedge and the centre disc, as before, less the reach
# into the signal wedge that fed board A's LDO (on board S the LDO is in the
# centre, on the disc itself). The cans' + leads are at R 17 and R 17.8, on
# the disc's rim: the VBUS sector reaches in over each of them.
S_VBUS = (G.WEDGE_ANG[2] - G.WEDGE_SPAN / 2 - 2 - 360, G.WEDGE_ANG[0] + G.WEDGE_SPAN / 2 - 2)
S_3V3 = (G.WEDGE_ANG[1] - G.WEDGE_SPAN / 2 - 4, G.WEDGE_ANG[1] + G.WEDGE_SPAN / 2 + 2)
CAN_TAB = (1.6, 6.0)          # mm inward of the lead, deg either side of it
PAD_TAB_W = 2.6               # mm across a finger to a centre cap's VBUS pad

def in2_s(tag, edge):
    from shapely.geometry import Polygon, Point
    from math import hypot, atan2, degrees
    a0, a1 = S_VBUS
    n = 160
    outer = [G.polar(a0 + (a1 - a0) * i / n, edge) for i in range(n + 1)]
    inner = [G.polar(a1 + (a0 - a1) * i / n, R_VBUS_IN) for i in range(n + 1)]
    vbus = Polygon(outer + inner)
    for ref in ("C1001", "C1002"):
        x, y = _pad(ref, "1")
        r, th = hypot(x, y), degrees(atan2(y, x))
        dr, da = CAN_TAB
        tab = [G.polar(th - da + 2 * da * i / 12, r - dr) for i in range(13)]
        tab += [G.polar(th + da - 2 * da * i / 12, R_VBUS_IN + 0.5) for i in range(13)]
        vbus = vbus.union(Polygon(tab))
    # ... and a finger in under the VBUS pad of each buck input cap that the
    # link wedges had no room for (placement_s puts them in the outward
    # centre): VBUS is plane-only, never routed, so this is its way in.
    # Capacitors only -- the header's VMOT pins have the spine on F.Cu.
    for p in placed():
        if p.block != "centre" or not p.ref.startswith("C") or p.ref in ("C1001", "C1002"):
            continue
        for num, net in pad_nets(p.ref).items():
            if net != "VBUS":
                continue
            x, y = _pad(p.ref, num)
            r, th = hypot(x, y), degrees(atan2(y, x))
            da = degrees(PAD_TAB_W / 2 / r)
            tab = [G.polar(th - da + 2 * da * i / 12, r - 1.0) for i in range(13)]
            tab += [G.polar(th + da - 2 * da * i / 12, R_VBUS_IN + 0.5) for i in range(13)]
            vbus = vbus.union(Polygon(tab))
    # Islands go: the escape vias along the sector's inner edge cut slivers
    # off it that touch no VBUS pad or via, and DRC calls them isolated.
    out = [zone("VBUS", "VBUS plane In2", ["In2.Cu"], _pts(vbus), f"{tag}-z-vbus",
                priority=2, solid=True, keep_islands=False)]
    # its islands too: the cans' tabs and the vias round them can cut off a
    # sliver of the disc with nothing on it (one did, by C1002, 2026-09-23)
    out.append(zone("+3V3", "3V3 plane In2", ["In2.Cu"],
                    keyhole(S_3V3[0], S_3V3[1], R_3V3_DISC, edge),
                    f"{tag}-z-3v3", priority=1, solid=True, keep_islands=False))
    return out

def bus_spine(tag):
    """VBUS on the outward face, from the VMOT pad through the TVS's cathode
    and C1001's + lead to the expansion header's four VMOT pins.

    The planes join all of these already; this is the short way between
    them. The TVS clamps a bus that arrives on this pad, so the pad and the
    cathode should be one piece of copper, not two vias and a plane apart. And
    the header's VMOT pins are on the axis, inside In2's +3V3 disc, where a PD
    board can put 5 A through them: this is their only way to the bus. It
    passes C1001's - lead by 1.6 mm and stops short of the M3 head at (0,
    -9.5); the fill keeps the Phase class's 0.4 mm from everything else."""
    from shapely.geometry import LineString, Polygon
    from shapely.ops import unary_union
    cp = _pad("C1001", "1")
    k = _pad("D1001", "1")
    vm = G.polar(placement_bus_axis() - 5.0, BUS_R0 + 0.8)      # into the VMOT pad
    pins = [_pad("J13", n) for n in ("1", "3", "5", "7")]
    xs = [x for x, _ in pins]
    ys = [y for _, y in pins]
    row = Polygon([(min(xs) - 0.37, max(ys) + 1.2), (max(xs) + 0.37, max(ys) + 1.2),
                   (max(xs) + 0.37, min(ys) - 1.45), (min(xs) - 0.37, min(ys) - 1.45)])
    down = LineString([(min(xs) + 1.0, min(ys) - 1.0), (-5.1, -11.0), cp]).buffer(1.2, join_style=2)
    out_leg = LineString([cp, k, vm]).buffer(1.5, join_style=2)
    spine = unary_union([row, down, out_leg])
    return [zone("VBUS", "VBUS spine F.Cu", ["F.Cu"], _pts(spine), f"{tag}-z-spine",
                 priority=3, solid=True, keep_islands=False)]

def placement_bus_axis():
    import placement_s
    return placement_s.AX[placement_s.PWR]

# Raspberry Pi's RP2350A minimal design (RP-006440) puts five small pours
# around the chip's regulator pins and a ring of 1V1 under the body joining
# the four DVDD pins. These are those polygons, in the chip's frame, to the
# 0.05 mm. The ring is widened from 2.2 to 2.3 so a via can land in it.
CPU_ZONES = [
    ("+1V1", "1V1 ring under U7", 3,
     [(2.3, 2.3), (-2.3, 2.3), (-2.3, -2.3), (2.3, -2.3)]),
    ("GND", "VREG_PGND, U7 pin 47", 4,
     [(2.3, -3.15), (2.3, -4.85), (2.45, -4.85), (2.45, -5.3), (2.3, -5.3),
      (2.3, -5.8), (3.1, -5.8), (3.4, -5.5), (3.4, -4.75), (2.5, -3.85),
      (2.5, -3.15)]),
    ("VREG_LX", "VREG_LX, U7 pin 48 to L701", 5,
     [(1.9, -3.15), (1.9, -3.95), (1.85, -4.0), (1.85, -6.15), (2.35, -6.65),
      (2.35, -8.05), (3.05, -8.05), (3.05, -6.35), (2.75, -6.05), (2.3, -6.05),
      (2.15, -5.9), (2.15, -4.0), (2.1, -3.95), (2.1, -3.15)]),
    ("+1V1", "DVDD, L701 to C715", 6,
     [(1.65, -5.3), (1.65, -6.67), (1.65, -8.05), (0.95, -8.05), (0.95, -6.2),
      (1.25, -5.9), (1.25, -5.75), (1.05, -5.75), (0.7, -6.1), (0.5, -6.1),
      (0.2, -5.8), (0.2, -5.2), (0.5, -4.9), (1.0, -4.9), (1.0, -5.1),
      (1.55, -5.1), (1.55, -5.3)]),
    ("+3V3", "VREG_VIN, U7 pin 49 to C714", 7,
     [(1.5, -2.7), (1.5, -4.05), (1.25, -4.3), (1.25, -4.85), (1.7, -4.85),
      (1.7, -2.7)]),
]

def cpu_copper(tag):
    """The reference design's pours around the RP2350, rotated into place."""
    from math import cos, sin, radians
    u7 = next(p for p in placed() if p.ref == "U7")
    (x0, y0), _, _, a0 = u7.rect
    out = []
    for net, name, prio, pts in CPU_ZONES:
        poly = []
        for dx, dy in pts:
            vx, vy = dx, -dy
            gx = x0 + vx * cos(a0) - vy * sin(a0)
            gy = y0 + vx * sin(a0) + vy * cos(a0)
            px, py = P(gx, gy)
            poly.append(f"(xy {px:.4f} {py:.4f})")
        out.append(zone(net, name, ["F.Cu"], " ".join(poly),
                        f"{tag}-z-cpu-{name}", priority=prio, solid=True,
                        clearance=0.15, min_thickness=0.15, keep_islands=False))
    return out

def board(key, cfg):
    use(cfg.get("variant", "a"))
    tag = cfg["name"]
    o = []
    o.append('(kicad_pcb')
    o.append('\t(version 20241229)')
    o.append('\t(generator "pcbnew")')
    o.append('\t(generator_version "9.0")')
    o.append('\t(general (thickness 1.6) (legacy_teardrops no))')
    o.append('\t(paper "A4")')
    o.append(f'\t(title_block (title "{cfg["title"]}") (date "2026-09-07") (rev "A")\n'
             f'\t\t(company "Sequoia Hope Alexander")\n'
             f'\t\t(comment 1 "Generated by tools/gen_boards.py from tools/geometry.py")\n'
             f'\t\t(comment 2 "CERN-OHL-P"))')
    o.append('\t(layers')
    for cu in cfg["copper"]:
        o.append(f'\t\t({LAYER_IDS[cu]} "{cu}" signal)')
    o.append(USER_LAYERS)
    o.append('\t)')
    o.append('\t(setup')
    o.append(stackup(cfg["copper"]))
    o.append('\t\t(pad_to_mask_clearance 0)')
    o.append('\t\t(allow_soldermask_bridges_in_footprints no)')
    o.append('\t\t(tenting front back)')
    o.append(f'\t\t(aux_axis_origin {CX} {CY})')
    o.append(f'\t\t(grid_origin {CX} {CY})')
    o.append('\t)')
    if cfg["motor_mount"]:
        for name, idx in sorted(net_table().items(), key=lambda kv: kv[1]):
            o.append(f'\t(net {idx} "{name}")')
    else:
        o.append('\t(net 0 "")')

    # ---- board outline
    o.append(gr_circle(0, 0, G.R, "Edge.Cuts", 0.1, f"{tag}-edge"))

    # ---- mounting holes
    if cfg["motor_mount"]:
        for i, (x, y) in enumerate([(G.MOUNT_X, 0), (-G.MOUNT_X, 0),
                                    (0, G.MOUNT_Y), (0, -G.MOUNT_Y)]):
            o.append(mounting_hole(f"H{i+1}", "MountingHole_3.2mm_M3", x, y, tag,
                                   "M3 motor mount, from the encoder pattern"))
            o.append(gr_circle(x, y, G.MOUNT_HEAD/2, "Cmts.User", 0.08,
                               f"{tag}-headkeep{i}"))
    for i, a in enumerate(G.PERIM_ANG):
        x, y = G.polar(a, G.PERIM_BC / 2)
        o.append(mounting_hole(f"H{10+i}", "ThermalBoss_M2.5", x, y, tag,
                               "M2.5 standoff, heatsink joint and ground bond",
                               src_dir=PROJ_FP, lib="servodrive"))

    # ---- documentation: zones, guides, dimensions (User.Drawings)
    o.append(gr_circle(0, 0, G.R_CENTRE, "Dwgs.User", 0.12, f"{tag}-centrekeep"))
    o.append(gr_circle(0, 0, G.R_USABLE, "Dwgs.User", 0.08, f"{tag}-usable"))
    if cfg["motor_mount"]:
        for i, a in enumerate(G.PHASE_ANG):
            for edge in (a - G.PHASE_SPAN/2, a + G.PHASE_SPAN/2):
                x0, y0 = G.polar(edge, G.ZONE_R0); x1, y1 = G.polar(edge, G.R_USABLE)
                o.append(gr_line(x0, y0, x1, y1, "Dwgs.User", 0.08, f"{tag}-pc{i}{edge}", "dash"))
            o.append(gr_arc(a - G.PHASE_SPAN/2, a + G.PHASE_SPAN/2, G.ZONE_R0,
                            "Dwgs.User", 0.08, f"{tag}-pca{i}"))
            tx, ty = G.polar(a, 15.0)
            o.append(gr_text(f"phase {'ABC'[i]}", tx, ty, "Dwgs.User", 1.6, f"{tag}-pct{i}"))
        labels = (("power link", "RP2350A", "signal link") if _ACTIVE == "a"
                  else ("power", "RP2350A", "signal"))
        for a, label in zip(G.WEDGE_ANG, labels):
            tx, ty = G.polar(a, 15.0)
            o.append(gr_text(label, tx, ty, "Dwgs.User", 1.4, f"{tag}-wt{a}"))
        # the line every part on this board has to stay inside, and why
        o.append(gr_circle(0, 0, PL.R_RING_ID, "Dwgs.User", 0.12, f"{tag}-ringid"))
        o.append(gr_text(f"parts stop at R{PL.R_RING_ID} - beyond is the heatsink ring land",
                         0, -14.0, "Dwgs.User", 0.9, f"{tag}-ringnote"))

        # --- the placement itself
        for part in placed():
            o.append(place(part, tag))
        for i, ang in enumerate(G.PHASE_ANG):
            for ref, fp, r0, r1, layer, why in (
                    (f"TL{i+1}", "ThermalLand_Phase", PL.R_RING_ID, PL.R_RING_OD,
                     "F.Cu", "bare copper for the aluminium heatsink ring"),
                    (f"J{i+1}", "PhasePad_Arc", PL.PAD_R0, PL.PAD_R1,
                     "B.Cu", f"phase {'ABC'[i]} motor lead, 14 AWG")):
                lx, ly = G.polar(ang, (r0 + r1) / 2)
                o.append(mounting_hole(ref, fp, lx, ly, tag, why,
                                       src_dir=PROJ_FP,
                                       lib="servodrive", rot=ang, layer=layer))
        if _ACTIVE == "s":
            # the bus's lead pads, VMOT counter-clockwise of GND: the order
            # placement_s.BUS_PADS has them in, which its checks placed by
            import placement_s as PS
            for ref, (_, r0, r1, ang, half, net) in zip(("J4", "J5"), PS.BUS_PADS):
                assert (r0, r1, half) == (BUS_R0, BUS_R1, BUS_HALF), "bus pad drifted"
                assert pad_nets(ref) == {"1": "VBUS" if net == "VMOT" else net}, ref
                lx, ly = G.polar(ang, (r0 + r1) / 2)
                o.append(mounting_hole(ref, "BusPad_Arc", lx, ly, tag,
                                       f"bus lead, {net}, 14 AWG soldered flat",
                                       src_dir=PROJ_FP, lib="servodrive", rot=ang))
    o.append(gr_text(cfg["title"], 0, G.R + 3.0, "Cmts.User", 1.6, f"{tag}-ttl"))
    if cfg["motor_mount"]:
        o += zones(tag)
    o.append(gr_text(cfg["note"], 0, G.R + 5.2, "Cmts.User", 1.0, f"{tag}-note"))
    o.append('\t(embedded_fonts no)')
    o.append(')')
    return "\n".join(o) + "\n"

# ------------------------------------------------------------- .kicad_pro ---
# Design rules chosen for JLCPCB's free tier with room to spare — area is not
# the constraint here (spec §2), so there is nothing to buy by running tight.
# Hole-to-hole is set at 0.5 mm deliberately: JLCPCB fills and copper-caps
# via-in-pad for free on 6-layer boards, but POFV needs >=0.45 hole-to-hole.
# The sister project's F-48 is the cost of deciding this late — 253 via pairs
# already too close to adopt it. Deciding it on an empty board costs nothing.
def project(cfg):
    # The key names here are not cosmetic. KiCad 9 wants microvia_diameter /
    # microvia_drill and a priority on every class; with uvia_* and no
    # priority it silently discards the whole list and falls back to its
    # factory 0.2 mm netclass -- which is how a board can report an 0.2 mm
    # clearance rule that appears nowhere in this file. Checked by setting
    # Default to 0.5 and watching DRC quote it back.
    nets = [
        # 0.5/0.20 and not 0.5/0.25: the annular ring is (dia - drill) / 2,
        # so 0.25 leaves 0.125 mm against JLCPCB's 0.13 mm minimum -- the
        # project shipped a default via its own rule outlawed, and nothing
        # noticed until there were vias on the board to check.
        dict(name="Default", clearance=0.15, track_width=0.2, via_diameter=0.5,
             via_drill=0.2, microvia_diameter=0.3, microvia_drill=0.1,
             diff_pair_width=0.2, diff_pair_gap=0.25, diff_pair_via_gap=0.25,
             wire_width=6, bus_width=12, line_style=0, priority=2147483647,
             schematic_color="rgba(0, 0, 0, 0.000)", pcb_color="rgba(0, 0, 0, 0.000)"),
        # 0.15 is JLCPCB's floor for 2 oz outer copper and it is also the
        # widest clearance a 0.65 mm-pitch VSSOP-8 can hold: the INA241's
        # adjacent pins sit 0.15 mm apart, so a class at 0.20 makes every
        # sense amp a DRC violation that no layout can fix. Only Phase is
        # opened up, because that is the 60 V net.
        dict(name="Power", clearance=0.15, track_width=0.5, via_diameter=0.6, via_drill=0.3),
        dict(name="Phase", clearance=0.4, track_width=2.0, via_diameter=0.8, via_drill=0.4),
        # 0.25, not 0.35: the gate resistors are 0402s now, and 0.35 could
        # not leave a 0.5 mm pad between two rotated neighbours. The gate
        # current is 0.3 A pulses; the width was never for the current.
        dict(name="Gate", clearance=0.15, track_width=0.25, via_diameter=0.5, via_drill=0.2),
        dict(name="Analog", clearance=0.15, track_width=0.25, via_diameter=0.5, via_drill=0.2),
    ]
    base = nets[0]
    for i, n in enumerate(nets[1:]):
        for k, v in base.items():
            n.setdefault(k, v)
        n["priority"] = i
    return {
        "board": {
            "3dviewports": [], "design_settings": {
                "defaults": {"board_outline_line_width": 0.1, "copper_line_width": 0.2,
                             "copper_text_size_h": 1.0, "copper_text_size_v": 1.0,
                             "copper_text_thickness": 0.15, "other_line_width": 0.15,
                             "silk_line_width": 0.12, "silk_text_size_h": 0.8,
                             "silk_text_size_v": 0.8, "silk_text_thickness": 0.12},
                "diff_pair_dimensions": [], "drc_exclusions": [],
                # Every footprint here is embedded verbatim from its library by
                # gen_boards.py, so it cannot actually differ. But KiCad's
                # comparison re-derives geometry through the placement angle,
                # and on a round board almost nothing sits at a multiple of
                # 90 deg: a part rotated 37.5 deg reports a mismatch against an
                # identical library copy. Verified in isolation before muting.
                "rule_severities": {"lib_footprint_mismatch": "ignore"},
                "rules": {
                    "allow_blind_buried_vias": False,   # JLCPCB does not offer them (F-48)
                    "allow_microvias": False,
                    "max_error": 0.005,
                    "min_clearance": 0.15,
                    "min_copper_edge_clearance": 0.3,
                    "min_hole_clearance": 0.25,
                    "min_hole_to_hole": 0.5,           # POFV-compatible, see note above
                    "min_microvia_diameter": 0.2, "min_microvia_drill": 0.1,
                    "min_resolved_spokes": 2, "min_silk_clearance": 0.0,
                    "min_text_height": 0.8, "min_text_thickness": 0.08,
                    # JLCPCB drills 0.15 and up; 0.2 is the smallest hole
                    # this board uses, in the stitching vias, and 0.25 would
                    # outlaw the annular ring those need.
                    "min_through_hole_diameter": 0.2,
                    "min_track_width": 0.15,
                    "min_via_annular_width": 0.13, "min_via_diameter": 0.45,
                    "solder_mask_to_copper_clearance": 0.005,
                },
                "track_widths": [0.0, 0.15, 0.2, 0.25, 0.35, 0.5, 1.0, 2.0],
                "via_dimensions": [{"diameter": 0.0, "drill": 0.0},
                                   {"diameter": 0.5, "drill": 0.2},
                                   {"diameter": 0.6, "drill": 0.3},
                                   {"diameter": 0.8, "drill": 0.4}],
            },
            "layer_presets": [], "viewports": [],
        },
        "boards": [], "cvpcb": {"equivalence_files": []},
        "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
        "meta": {"filename": cfg["name"] + ".kicad_pro", "version": 3},
        "net_settings": {"classes": nets, "meta": {"version": 4}, "net_colors": None,
                         "netclass_assignments": None,
                         # Without patterns every net is Default, which is how
                         # a 0.5 A rail ends up the width of a test point.
                         "netclass_patterns":
                             [{"netclass": "Power", "pattern": q} for q in
                              ("+12V", "+5V", "+3V3", "+3V3A", "+1V1", "VREG_LX")
                              + (("+5V_BUCK", "USB_VBUS") if cfg.get("variant") == "s" else ())]
                             + [{"netclass": "Gate", "pattern": q} for q in
                                ("GH_*", "GL_*", "HO_*", "LO_*", "VB_*")]
                             + [{"netclass": "Analog", "pattern": q} for q in
                                ("ISENSE_*", "SNSP_*", "SNSN_*", "VBUS_SENSE",
                                 "FET_TEMP")]
                             # The 60 V nets. GND is deliberately not here: it
                             # is every second pad on the board and a 0.4 mm
                             # clearance on it would close the control side.
                             + [{"netclass": "Phase", "pattern": q} for q in
                                ("PHASE_*", "SW_*", "SNUB_*", "VBUS")]},
        "pcbnew": {"last_paths": {}, "page_layout_descr_file": ""},
        "schematic": {"annotate_start_num": 0, "legacy_lib_dir": "", "legacy_lib_list": [],
                      "meta": {"version": 1},
                      "page_layout_descr_file": "", "plot_directory": "",
                      "spice_current_sheet_as_root": False, "spice_external_command": "",
                      "spice_model_current_sheet_as_root": True, "spice_save_all_currents": False,
                      "spice_save_all_dissipations": False, "spice_save_all_voltages": False,
                      "subpart_first_id": 65, "subpart_id_separator": 0},
        "sheets": [[uid(cfg["name"], "rootsheet"), "Root"]],
        "text_variables": {},
    }

# ------------------------------------------------------------- .kicad_sch ---
SHEETS_A = [
    ("01_power_stage",  "Three half-bridges: 6x BSC030N08NS5, 3x EG2103, bootstrap,\\n"
                        "gate networks, 2x inline shunt + INA241A2, DC-link ceramics"),
    ("02_control",      "RP2350A QFN-60, crystal, QSPI flash, 3V3 LDO, ADC front ends"),
    ("03_encoder",      "MT6701 on the motor-facing side, SSI to SPI0"),
    ("04_interconnect", "2x8 power header, 2x14 signal header to board B"),
]
SHEETS_B = [
    ("01_input",        "XT30, SMDJ64A, bulk, LTC4359 + E100N4P0HL1 ideal diode"),
    ("02_rails",        "2x LMR38010: 12 V Vgate, 5 V Vlogic"),
    ("03_usb_pd",       "USB-C, FUSB302BMPX, USBLC6-2P6 (pin 5 to +3V3, F-39)"),
    ("04_rs485",        "2x SIT3088ETK full duplex, two chained ports, jumpered 120R"),
    ("05_interconnect", "Mating headers, status LED, debug, BOOTSEL"),
]

def sch_notes(cfg, sheets):
    """Root sheet: title block, hierarchical sheet boxes, and the design notes
    that a person opening this file cold would otherwise have to go and find."""
    tag = cfg["name"]
    o = ['(kicad_sch', '\t(version 20250114)', '\t(generator "eeschema")',
         '\t(generator_version "9.0")', f'\t(uuid "{uid(tag, "sch")}")', '\t(paper "A4")',
         f'\t(title_block (title "{cfg["title"]}") (date "2026-09-07") (rev "A")\n'
         f'\t\t(company "Sequoia Hope Alexander")\n'
         f'\t\t(comment 1 "Sheet skeleton generated by tools/gen_boards.py")\n'
         f'\t\t(comment 2 "Capture is manual from here. See ../../spec.html")\n'
         f'\t\t(comment 3 "CERN-OHL-P"))',
         '\t(lib_symbols)']
    x, y = 25.0, 35.0
    for i, (nm, desc) in enumerate(sheets):
        su = uid(tag, "sheet", nm)
        o.append(f'''	(sheet (at {x} {y}) (size 60 18)
		(fields_autoplaced yes)
		(stroke (width 0.1524) (type solid))
		(fill (color 0 0 0 0.0000))
		(uuid "{su}")
		(property "Sheetname" "{nm}" (at {x} {y - 0.79} 0)
			(effects (font (size 1.27 1.27)) (justify left bottom)))
		(property "Sheetfile" "{nm}.kicad_sch" (at {x} {y + 18.6} 0)
			(effects (font (size 1.27 1.27)) (justify left top)))
		(instances (project "{tag}" (path "/" (page "{i+2}"))))
	)''')
        o.append(f'\t(text "{desc}" (exclude_from_sim no) (at {x + 1.5} {y + 5} 0)\n'
                 f'\t\t(effects (font (size 1.0 1.0)) (justify left top)) '
                 f'(uuid "{uid(tag, "sheetdesc", nm)}"))')
        y += 24.0
        if y > 160:
            y = 35.0; x += 70.0
    o.append(f'\t(text "{cfg["note"]}" (exclude_from_sim no) (at 25 25 0)\n'
             f'\t\t(effects (font (size 1.27 1.27) (bold yes)) (justify left top)) '
             f'(uuid "{uid(tag, "note")}"))')
    o.append(f'''	(sheet_instances
		(path "/" (page "1"))
	)''')
    o.append('\t(embedded_fonts no)')
    o.append(')')
    return "\n".join(o) + "\n"

def sub_sheet(cfg, nm, desc):
    tag = cfg["name"]
    return f'''(kicad_sch
	(version 20250114)
	(generator "eeschema")
	(generator_version "9.0")
	(uuid "{uid(tag, "subsch", nm)}")
	(paper "A4")
	(title_block (title "{cfg['title']} — {nm}") (date "2026-09-07") (rev "A")
		(company "Sequoia Hope Alexander"))
	(lib_symbols)
	(text "{desc}" (exclude_from_sim no) (at 25 30 0)
		(effects (font (size 1.27 1.27)) (justify left top)) (uuid "{uid(tag, "subtxt", nm)}"))
	(embedded_fonts no)
)
'''

# ------------------------------------------------------------ lib tables ----
SISTER = Path("/home/sequoia/pcb/rp2350-motor-controller")
BIKE   = Path("/home/sequoia/pcb/rp2350-bike-light")
# Raspberry Pi's RP2350A minimal design, RP-006440: the source of the CPU's
# regulator cluster, its pours and the 2016 inductor footprint.
RPI_REF = Path("/home/sequoia/pcb/RP-006440-DD-2-RP2350A Minimal Board Kicad archive")

SYM_TABLE = '''(sym_lib_table
  (version 7)
  (lib (name "servodrive")(type "KiCad")(uri "${KIPRJMOD}/../parts/servodrive.kicad_sym")(options "")(descr "Project symbols: RP2350A, MT6701"))
  (lib (name "encoder_parts")(type "KiCad")(uri "%s/hardware/encoder/encoder_parts.kicad_sym")(options "")(descr "Sister project: MT6701 and friends"))
  (lib (name "integrated")(type "KiCad")(uri "%s/hardware/integrated.kicad_sym")(options "")(descr "Sister project: XL7005A etc"))
)
''' % (SISTER, SISTER)

FP_TABLE = '''(fp_lib_table
  (version 7)
  (lib (name "servodrive")(type "KiCad")(uri "${KIPRJMOD}/../parts/servodrive.pretty")(options "")(descr "Project footprints"))
  (lib (name "parts_motor")(type "KiCad")(uri "%s/parts")(options "")(descr "Sister project footprints"))
  (lib (name "RP2350_60QFN")(type "KiCad")(uri "%s/RP2350_60QFN_minimal.pretty")(options "")(descr "RP2350A QFN-60"))
)
''' % (SISTER / "hardware", BIKE)

KSYM = Path("/usr/share/kicad/symbols")

def derive_symbol(src_lib, src_name, new_name, footprint, value=None, descr=None):
    """Copy a stock KiCad symbol under a new name, with our footprint on it.

    Three parts here have no symbol of their own and a perfectly good stand-in:
    EG2103 is pin-for-pin IR2103 (same family, same active-low LIN), INA241
    shares INA240's 8-pin arrangement, and the W25Q16JV has the W25Q128JV's
    SPI pinout whatever the package. Copying beats drawing: the geometry is
    someone else's problem and the pin numbering is already right.

    Any `extends` is flattened, because a schematic carries its own copy of
    every symbol and a derived one whose base is absent will not load.
    """
    root = sexp.parse((KSYM / f"{src_lib}.kicad_sym").read_text())
    src = next(s for s in sexp.findall(root, "symbol")
               if sexp.unq(s[1]) == src_name)
    ext = sexp.find(src, "extends")
    if ext:
        base = next(s for s in sexp.findall(root, "symbol")
                    if sexp.unq(s[1]) == sexp.unq(ext[1]))
        body = [c for c in base[2:]
                if not (isinstance(c, list) and c[0] in ("property", "extends"))]
        props = {sexp.unq(c[1]): c for c in sexp.findall(base, "property")}
        props.update({sexp.unq(c[1]): c for c in sexp.findall(src, "property")})
        old_name = sexp.unq(base[1])
    else:
        body = [c for c in src[2:] if not (isinstance(c, list) and c[0] == "property")]
        props = {sexp.unq(c[1]): c for c in sexp.findall(src, "property")}
        old_name = src_name

    def retarget(node):
        if isinstance(node, list) and node and node[0] == "symbol":
            nm = sexp.unq(node[1])
            if nm.startswith(old_name + "_"):
                return ["symbol", sexp.q(new_name + nm[len(old_name):])] + node[2:]
        return node

    def setprop(name, val):
        blk = props.get(name)
        if blk is None:
            props[name] = ["property", sexp.q(name), sexp.q(val),
                           ["at", "0", "0", "0"],
                           ["effects", ["font", ["size", "1.27", "1.27"]],
                            ["hide", "yes"]]]
        else:
            props[name] = blk[:2] + [sexp.q(val)] + blk[3:]

    setprop("Footprint", footprint)
    if value:
        setprop("Value", value)
    if descr:
        setprop("Description", descr)
    out = ["symbol", sexp.q(new_name)]
    for c in body:
        out.append(retarget(c))
    for name in ("Reference", "Value", "Footprint", "Datasheet", "Description"):
        if name in props:
            out.append(props.pop(name))
    out.extend(props.values())
    return out

DERIVED_SYMBOLS = [
    ("Driver_FET", "IR2103", "EG2103", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
     "EG2103",
     "EG Micro EG2103 600 V half-bridge gate driver. Pin-for-pin IR2103: "
     "HIN active high, LIN active LOW, interlock, 560 ns internal dead time, "
     "VCC 10-20 V, 0.3/0.6 A."),
    ("Amplifier_Current", "INA240A1D", "INA241A3", "Package_TO_SOT_SMD:TSOT-23-8",
     "INA241A3",
     "TI INA241A3, 50 V/V current-sense amplifier, -5 to +110 V common mode, "
     "enhanced PWM rejection. A3 is the 50 V/V grade -- INA241 numbers its "
     "gains differently from INA240."),
    ("Memory_Flash", "W25Q128JVS", "W25Q16JV",
     "Package_SON:Winbond_USON-8-1EP_3x2mm_P0.5mm_EP0.2x1.6mm",
     "W25Q16JVUXIQ", "Winbond 16 Mbit QSPI flash, USON-8 2x3 mm -- the RP2350 "
     "reference design's part; no 128 Mbit part comes in this package."),
]

def lmr38010_symbol():
    """TI LMR38010, drawn from its datasheet's pin table (SNVSC73B, DDA):
    1 GND, 2 EN, 3 VIN, 4 RT/SYNC, 5 FB, 6 PG, 7 BOOT, 8 SW, exposed pad
    GND as pin 9. No stock symbol has this pinout -- the LMR336x0 and
    LMR36510 in KiCad's library share the package and not the pins -- so
    this is drawn rather than derived. Board S's two rails."""
    def pin(kind, x, y, rot, name, num):
        return ["pin", kind, "line", ["at", f"{x}", f"{y}", f"{rot}"], ["length", "2.54"],
                ["name", sexp.q(name), ["effects", ["font", ["size", "1.27", "1.27"]]]],
                ["number", sexp.q(num), ["effects", ["font", ["size", "1.27", "1.27"]]]]]
    def prop(name, val, x, y, hide=False):
        eff = ["effects", ["font", ["size", "1.27", "1.27"]]]
        if hide:
            eff.append(["hide", "yes"])
        return ["property", sexp.q(name), sexp.q(val), ["at", f"{x}", f"{y}", "0"], eff]
    return ["symbol", sexp.q("LMR38010"),
            ["pin_names", ["offset", "1.016"]],
            ["exclude_from_sim", "no"], ["in_bom", "yes"], ["on_board", "yes"],
            prop("Reference", "U", -7.62, 10.16),
            prop("Value", "LMR38010", 2.54, 10.16),
            prop("Footprint", "Package_SO:TI_SO-PowerPAD-8", 0, -15.24, True),
            prop("Datasheet", "https://www.ti.com/lit/ds/symlink/lmr38010.pdf", 0, -17.78, True),
            prop("Description", "4.2-80 V, 1 A synchronous buck, HSOIC-8 PowerPAD. "
                 "VREF 1.0 V; RT/SYNC must not float; EN max VIN + 0.3 V.", 0, -20.32, True),
            ["symbol", sexp.q("LMR38010_0_1"),
             ["rectangle", ["start", "-7.62", "7.62"], ["end", "7.62", "-7.62"],
              ["stroke", ["width", "0.254"], ["type", "default"]], ["fill", ["type", "background"]]]],
            ["symbol", sexp.q("LMR38010_1_1"),
             pin("power_in", -10.16, 5.08, 0, "VIN", "3"),
             pin("input", -10.16, 2.54, 0, "EN", "2"),
             pin("input", -10.16, -2.54, 0, "RT/SYNC", "4"),
             pin("passive", 10.16, 5.08, 180, "BOOT", "7"),
             pin("power_out", 10.16, 2.54, 180, "SW", "8"),
             pin("input", 10.16, -2.54, 180, "FB", "5"),
             pin("open_collector", 10.16, -5.08, 180, "PG", "6"),
             pin("power_in", -1.27, -10.16, 90, "GND", "1"),
             pin("passive", 1.27, -10.16, 90, "EP", "9")],
            ["embedded_fonts", "no"]]

# Symbols this project draws itself, kept in the library whatever else is in
# it: board A's regeneration rewrites the file only under --force.
DRAWN_SYMBOLS = {
    "LMR38010": lmr38010_symbol,
    # The SIT3088ETK is pin for pin a MAX3485, as the sister project draws
    # it; derived, so the library holds the same flattened symbol the sheet
    # does and ERC has nothing to compare against a stock `extends`.
    "SIT3088": lambda: derive_symbol(
        "Interface_UART", "MAX3485", "SIT3088",
        "parts_motor:DFN-8_L3.0-W3.0-P0.65-BL-EP", "SIT3088ETK",
        "SIT3088ETK 3.3 V RS-485 transceiver, DFN-8 3x3, MAX3485 pinout: "
        "1 RO, 2 RE#, 3 DE, 4 DI, 5 GND, 6 A, 7 B, 8 VCC."),
}

def ensure_symbols():
    """Append any drawn symbol the project library lacks. Idempotent."""
    sym_dst = HW / "parts" / "servodrive.kicad_sym"
    if not sym_dst.exists():
        return
    t = sym_dst.read_text()
    add = [sexp.dump(gen(), indent=1) for name, gen in DRAWN_SYMBOLS.items()
           if f'(symbol "{name}"' not in t]
    if not add:
        return
    i = t.rstrip().rfind(")")
    t = t[:i] + "".join("\t" + b.replace("\n", "\n\t") + "\n" for b in add) + t[i:]
    sym_dst.write_text(t)
    print(f"  added {len(add)} drawn symbol(s) to {sym_dst.relative_to(ROOT)}")

def seed_parts(force=False):
    """Copy the two symbols and the one footprint this design cannot start
    without, so the project opens standalone even if a sibling repo moves."""
    pdir = HW / "parts"; (pdir / "servodrive.pretty").mkdir(parents=True, exist_ok=True)
    for src in (BIKE / "RP2350_60QFN_minimal.pretty" /
                "RP2350-QFN-60-1EP_7x7_P0.4mm_EP3.4x3.4mm_ThermalVias.kicad_mod",
                RPI_REF / "RP2350_60QFN_minimal.pretty" / "L_pol_2016.kicad_mod",
                RPI_REF / "RP2350_60QFN_minimal.pretty" / "C_0402_1005Metric_small_pads.kicad_mod"):
        dst = pdir / "servodrive.pretty" / src.name
        if src.exists() and (force or not dst.exists()):
            shutil.copy2(src, dst); print(f"  copied {dst.relative_to(ROOT)}")
        elif not src.exists() and not dst.exists():
            print(f"  ! missing {src}")
    # Anything copied in may be an older format that still uses
    # (fp_text reference ...), which nothing downstream can re-designate.
    if shutil.which("kicad-cli"):
        import subprocess
        subprocess.run(["kicad-cli", "fp", "upgrade", str(pdir / "servodrive.pretty")],
                       capture_output=True, text=True)

    for name, gen in (("TDSON-8-1_ThermalVias", fet_thermal_vias),
                      ("TDSON-8-1_ThermalVias_HS", fet_thermal_vias_hs),
                      ("ThermalBoss_M2.5", thermal_boss),
                      ("ThermalLand_Phase", thermal_land),
                      ("PhasePad_Arc", phase_pad),
                      ("BusPad_Arc", bus_pad)):
        dst = pdir / "servodrive.pretty" / f"{name}.kicad_mod"
        dst.write_text(gen())
        print(f"  wrote  {dst.relative_to(ROOT)}  (generated)")

    sym_dst = pdir / "servodrive.kicad_sym"
    if force or not sym_dst.exists():
        blocks = []
        for src, want in ((BIKE / "RP2350_bike_light.kicad_sym", "RP2350_60QFN"),
                          (SISTER / "hardware/encoder/encoder_parts.kicad_sym", "MT6701")):
            if not src.exists():
                print(f"  ! missing {src}"); continue
            txt = src.read_text()
            i = txt.find(f'(symbol "{want}"')
            if i < 0:
                print(f"  ! {want} not in {src.name}"); continue
            depth = 0
            for n in range(i, len(txt)):
                if txt[n] == '(': depth += 1
                elif txt[n] == ')':
                    depth -= 1
                    if depth == 0:
                        blocks.append(txt[i:n+1]); break
        for args in DERIVED_SYMBOLS:
            blocks.append(sexp.dump(derive_symbol(*args), indent=1))
        sym_dst.write_text('(kicad_symbol_lib\n\t(version 20241209)\n\t(generator "gen_boards.py")\n'
                           + "\n".join("\t" + b.replace("\n", "\n\t") for b in blocks)
                           + "\n)\n")
        print(f"  wrote {sym_dst.relative_to(ROOT)}  ({len(blocks)} symbols)")
    ensure_symbols()

# --------------------------------------------------------------- renders ----
# kicad-cli exports everything in flat #000000, which is invisible on a dark
# page. Rewrite to currentColor and give the standalone SVG its own theme
# block, so the same file reads correctly in both.
SVG_THEME = '''<style>
  :root { color: #1e2126; }
  @media (prefers-color-scheme: dark) { :root { color: #d7dbe2; } }
</style>
'''

def render_svgs(which="ab"):
    """Export each board's outline + placement guides for the project page."""
    if not shutil.which("kicad-cli"):
        print("  ! kicad-cli not found, skipping renders"); return
    import subprocess
    # The inner layers are where most of the routing ended up -- In3 is the
    # only signal layer that crosses the whole board -- so they get drawn too.
    views = [("motor_board", "board_a_front.svg", "F.Cu,Edge.Cuts", False),
             ("motor_board", "board_a_back.svg",  "B.Cu,Edge.Cuts", True),
             ("motor_board", "board_a_in2.svg",   "In2.Cu,Edge.Cuts", False),
             ("motor_board", "board_a_in3.svg",   "In3.Cu,Edge.Cuts", False),
             ("power_board", "board_b_kicad.svg", "Edge.Cuts,Dwgs.User", False)]
    if which == "s":
        views = [("single_board", "board_s_front.svg", "F.Cu,Edge.Cuts", False),
                 ("single_board", "board_s_back.svg", "B.Cu,Edge.Cuts", True)]
    for key, out, layers, mirror in views:
        cfg = BOARDS[key]
        src = HW / key / f"{cfg['name']}.kicad_pcb"
        dst = ROOT / "img" / out
        cmd = ["kicad-cli", "pcb", "export", "svg", "--layers", layers,
               "--page-size-mode", "2", "--exclude-drawing-sheet", "--black-and-white",
               "-o", str(dst), str(src)]
        if mirror:
            cmd.insert(cmd.index("-o"), "--mirror")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not dst.exists():
            print(f"  ! export failed for {out}: {r.stderr.strip()[:120]}"); continue
        t = dst.read_text().replace("#000000", "currentColor")
        i = t.index(">", t.index("<svg")) + 1
        dst.write_text(t[:i] + "\n" + SVG_THEME + t[i:])
        print(f"  wrote  {dst.relative_to(ROOT)}")
    # ... and the per-layer plots the copper viewer on index.html stacks,
    # and the GLB its 3D viewer draws.
    plot_layers.run("s" if which == "s" else "a")
    export_3d.run("s" if which == "s" else "a")

# ------------------------------------------------------------------ main ----
def write(path, text, force, protect=False):
    rel = path.relative_to(ROOT)
    if path.exists() and protect and not force:
        print(f"  keep   {rel}  (exists; --force to overwrite)"); return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(f"  wrote  {rel}")
    return True

SHEETS_S = [
    ("01_power_stage",  "Three half-bridges: 6x BSC030N08NS5, 3x EG2103, bootstrap,\\n"
                        "gate networks, 2x inline shunt + INA241A3, DC-link ceramics"),
    ("02_control",      "RP2350A QFN-60, crystal, QSPI flash, 3V3 LDO, ADC supply"),
    ("03_encoder",      "MT6701 on the motor-facing side, SSI to SPI0"),
    ("04_power",        "Bus pads, SMDJ54A, 2x 100 uF polymer, bus divider,\\n"
                        "2x LMR38010: 12 V gate rail (GATE_OFF kills it), 5 V logic"),
    ("05_io",           "USB-C (data + 5 V), RS-485 relay on two SH 6, expansion header,\\n"
                        "RGB LED, BOOTSEL, test pads"),
]

def emit_s(cfg, force, pcb_too=True):
    """Board S: checked against itself, written to hardware/single_board/."""
    import placement_s as PS
    import schematic_s as SCHS
    use("s")
    bad = PS.check_s(placed())
    if bad:
        print("board S floorplan is not clean; refusing to emit it:")
        for b in bad:
            print("  !", b)
        raise SystemExit(1)
    bad = SCHS.check()
    if bad:
        print("board S netlist and placement disagree; refusing to emit it:")
        for b in bad:
            print("  !", b)
        raise SystemExit(1)
    d = HW / "single_board"
    print("single_board:")
    write(d / "sym-lib-table", SYM_TABLE, force)
    write(d / "fp-lib-table", FP_TABLE, force)
    write(d / f"{cfg['name']}.kicad_pro", json.dumps(project(cfg), indent=2) + "\n", force)
    write(d / f"{cfg['name']}.kicad_sch", sch_notes(cfg, SHEETS_S), force, protect=True)
    glob = SCHS.global_nets()
    for nm, desc in SHEETS_S:
        cs = [c for c in comps() if c.sheet == nm]
        text = SCH.sheet_text(nm, cs, cfg["name"], uid(cfg["name"], "subsch", nm),
                              uid(cfg["name"], "sheet", nm), f"{cfg['title']} — {nm}",
                              flags=SCHS.SHEET_FLAGS.get(nm, ()), glob=glob)
        write(d / f"{nm}.kicad_sch", text, force, protect=True)
    pcb = d / f"{cfg['name']}.kicad_pcb"
    if pcb_too and write(pcb, board("single_board", cfg), force, protect=True):
        stitch.run(pcb)
        if not os.environ.get("SERVODRIVE_NO_FANOUT"):
            fanout.run(pcb)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true",
                    help="overwrite existing boards and schematics — destroys layout work")
    ap.add_argument("--board", choices=("ab", "s"), default="ab",
                    help="ab: boards A and B, as always; s: board S alone")
    ap.add_argument("--no-pcb", action="store_true",
                    help="with --board s: the project and sheets, not the board")
    args = ap.parse_args()

    # Parts first: the floorplan measures generated footprints, so they have
    # to exist before it can be checked.
    print("parts:")
    seed_parts(args.force)
    if args.board == "s":
        emit_s(BOARDS["single_board"], args.force, pcb_too=not args.no_pcb)
        if not args.no_pcb:
            print("renders:")
            render_svgs("s")
        return

    bad = PL.check(PL.board_a())
    if bad:
        print("floorplan is not clean; refusing to emit a board:")
        for b in bad:
            print("  !", b)
        raise SystemExit(1)
    bad = SCH.check()
    if bad:
        print("netlist and placement disagree; refusing to emit anything:")
        for b in bad:
            print("  !", b)
        raise SystemExit(1)
    for key, cfg in BOARDS.items():
        if cfg.get("variant", "a") != "a":
            continue
        d = HW / key
        print(f"{key}:")
        write(d / "sym-lib-table", SYM_TABLE, args.force)
        write(d / "fp-lib-table", FP_TABLE, args.force)
        write(d / f"{cfg['name']}.kicad_pro",
              json.dumps(project(cfg), indent=2) + "\n", args.force)
        sheets = SHEETS_A if cfg["motor_mount"] else SHEETS_B
        write(d / f"{cfg['name']}.kicad_sch", sch_notes(cfg, sheets), args.force, protect=True)
        for nm, desc in sheets:
            if cfg["motor_mount"]:
                # Board A's sheets come from the netlist in schematic.py, which
                # is checked against the placement before either is written.
                comps = [c for c in SCH.board_a() if c.sheet == nm]
                text = SCH.sheet_text(
                    nm, comps, cfg["name"],
                    uid(cfg["name"], "subsch", nm),      # this file's own uuid
                    uid(cfg["name"], "sheet", nm),       # its (sheet) in the root
                    f"{cfg['title']} — {nm}",
                    flags=SCH.SHEET_FLAGS.get(nm, ()))
            else:
                text = sub_sheet(cfg, nm, desc)
            write(d / f"{nm}.kicad_sch", text, args.force, protect=True)
        if write(d / f"{cfg['name']}.kicad_pcb", board(key, cfg), args.force,
                 protect=True):
            # The power nets on this board are copper, not tracks, so most
            # pads' whole connection is one hole down to the plane already
            # under them. Those vias are part of the design, not of the
            # routing, and they go in here so that regenerating the board
            # reproduces them. See tools/stitch.py.
            stitch.run(d / f"{cfg['name']}.kicad_pcb")
            # ... and the RP2350's escape vias, its DVDD ring stubs, and a
            # tap for every +3V3 pad that is not over the plane. Same
            # reasoning: these are the design, not the routing.
            if cfg["motor_mount"] and not os.environ.get("SERVODRIVE_NO_FANOUT"):
                fanout.run(d / f"{cfg['name']}.kicad_pcb")
    print("renders:")
    render_svgs()
    print(f"\nboard Ø{G.DIA:.0f} mm, origin at the shaft axis, "
          f"page ({CX}, {CY}). Re-run after editing tools/geometry.py.")

if __name__ == "__main__":
    main()
