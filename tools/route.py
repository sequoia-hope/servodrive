#!/usr/bin/env python3
"""route.py — hand board A to freerouting and bring the result back.

    python3 tools/route.py            # export, route, import, fill, DRC
    python3 tools/route.py --passes 40

The board that comes out of gen_boards.py is placed, netted and poured but has
no tracks. Most of it never needs any: GND is two solid inner planes, VBUS is a
plane, and the switch node and phase output are pours, because 20 A does not go
down a track. What is left is the signals, and those go to freerouting.

Two things have to be true of the board handed to the router, and neither is
true of the board on disk:

  - the ground POURS on F.Cu, In3 and B.Cu have to go. KiCad exports a zone as
    a Specctra `plane`, and a plane covering a whole layer leaves the router
    nowhere to put a trace. Ground still reaches every pad, through the In1 and
    In4 planes and a via -- which is what those planes are for. The pours come
    back afterwards and fill around whatever was routed.
  - the zones that remain have to be filled, or they export as empty outlines.

Everything else -- the placement, the netlist, the design rules -- is whatever
gen_boards.py wrote, so re-running that and re-running this reproduces the
board.
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stitch
import fanout
import finish
import placement as PL
import plot_layers

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "hardware/motor_board/servodrive_A.kicad_pcb"
WORK = ROOT / "hardware/motor_board/.route"
BOARD_KEY = "a"
# Board S (the single-board variant) routes the same way, in its own directory.
BOARDS = {"a": ("motor_board", "servodrive_A"), "s": ("single_board", "servodrive_S")}

def select(key):
    """Point every stage at one board: its file and its work directory."""
    global BOARD, WORK, BOARD_KEY
    d, name = BOARDS[key]
    BOARD = ROOT / "hardware" / d / f"{name}.kicad_pcb"
    WORK = ROOT / "hardware" / d / ".route"
    BOARD_KEY = key
JAR = Path("/home/sequoia/Software/magnet/route/freerouting-2.2.4.jar")

# Pours that exist to fill space, not to carry the routing. They are removed
# for the router's benefit and rebuilt from gen_boards.py afterwards.
# Zones removed before export. The three space-filling pours, because a plane
# covering a whole layer leaves the router nowhere to go; and the In2 ground
# zone, because In2 is only VBUS where the power is -- over the CPU and the two
# link wedges it is a whole free routing layer and the router needs it.
POUR_NAMES = ("ground pour F.Cu", "ground pour In3.Cu", "ground pour B.Cu",
              "ground plane In2")

def fill(board):
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())

def save(board, path):
    """SaveBoard to a temporary name, then rename over the board: pcbnew can
    segfault inside SaveBoard after a long maze pass, and on board S that left
    a 0-byte board file and the pass's work gone. Now it leaves the last good
    board where it was."""
    path = Path(path)
    tmp = path.with_name(path.stem + ".saving" + path.suffix)
    pcbnew.SaveBoard(str(tmp), board)
    os.replace(tmp, path)

def strip_pours(board):
    """Delete the space-filling ground pours; keep the planes."""
    gone = 0
    for z in list(board.Zones()):
        if z.GetZoneName() in POUR_NAMES:
            board.Remove(z)
            gone += 1
    return gone

def complete_nets(path, loose=False):
    """The nets with no unconnected items, by KiCad's own DRC, less GND
    (planes) and the ones DEAD_NETS already names. In a loose round only
    the nets whose copper is all the tools' (locked) count: a net the
    router made stays in its hands, to be moved if a neighbour needs the
    room."""
    rpt = WORK / "pre.json"
    drc(path, rpt)
    d = json.loads(rpt.read_text())
    open_nets = set()
    for it in d.get("unconnected_items", ()):
        for item in it["items"]:
            m = re.search(r"\[([^\]]*)\]", item["description"])
            if m:
                open_nets.add(m.group(1))
    board = pcbnew.LoadBoard(str(path))
    names = {n.GetNetname() for n in board.GetNetInfo().NetsByName().values() if n.GetNetCode() > 0}
    used = {p.GetNetname() for f in board.GetFootprints() for p in f.Pads() if p.GetNetCode() > 0}
    routed = {tr.GetNetname() for tr in board.GetTracks() if not tr.IsLocked()} if loose else set()
    return sorted(n for n in names & used
                  if n not in open_nets and n != "GND" and n not in DEAD_NETS
                  and n not in routed)

def export_dsn(src, dsn, loose=False, dead=()):
    board = pcbnew.LoadBoard(str(src))
    global HALOS
    HALOS = pad_halos(board)      # before the export: touching the pads
                                  # after ExportSpecctraDSN segfaults pcbnew
    n = strip_pours(board)
    fill(board)
    if not pcbnew.ExportSpecctraDSN(board, str(dsn)):
        raise RuntimeError("Specctra export failed")
    return n, protect_wiring(dsn, loose, dead)

# The pads of the nets that are taken away from the router keep their copper
# but lose their net, and with it their clearance class: freerouting keeps
# only the default 0.15 mm from a netless pad, and the 60 V pads want 0.4.
# So each of those pads gets a keepout on its own layers, its outline grown
# by the difference, written into the DSN alongside the pour keepouts.
HALO_NETS = ("VBUS", "SW_A", "SW_B", "SW_C", "PHASE_A", "PHASE_B", "PHASE_C",
             "SNUB_A", "SNUB_B", "SNUB_C",
             "SW_12V", "SW_5V")     # board S's buck switch nodes, 60 V class too
HALOS = []

def pad_halos(board, grow=0.25):
    """The pad's own outline, grown by (0.4 - the router's 0.16), as a
    polygon: a grown bounding box of a rotated SOIC pin reached the pins
    either side of it, and the gate driver's HO and LO could not leave.
    0.25 rather than 0.24: the polygon is the pad's approximated outline,
    and board S's first run put a track 0.3993 mm from a can's lead."""
    out = []
    for f in board.GetFootprints():
        for p in f.Pads():
            if p.GetNetname() not in HALO_NETS:
                continue
            # A through-hole pad is on In3 as well, and the router routes
            # there: ISENSE_A went 0.16 mm from the bus connector's VBUS pin.
            layers = ([pcbnew.F_Cu, pcbnew.In3_Cu, pcbnew.B_Cu] if p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD
                      else [l for l in p.GetLayerSet().CuStack()
                            if l in (pcbnew.F_Cu, pcbnew.B_Cu)])
            for l in layers:
                poly = pcbnew.SHAPE_POLY_SET(p.GetEffectivePolygon(l))
                poly.Inflate(int(grow * 1e6), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, int(0.01e6))
                for i in range(poly.OutlineCount()):
                    o = poly.Outline(i)
                    pts = [(o.CPoint(k).x / 1e3, o.CPoint(k).y / 1e3) for k in range(o.PointCount())]
                    out.append((board.GetLayerName(l), pts))
    return out

def protect_wiring(dsn, loose=False, dead=()):
    """Freeze the copper the tools made.

    KiCad writes everything already on the board into the DSN's `wiring`
    section as `(type route)`, which tells the router it may rip it up -- and
    it does, then spends its passes rediscovering that a GND pad wants a via
    in it. `protect` is the Specctra way of saying this one is mine.

    Every track and via the tools make is locked, and KiCad writes a locked
    item as `(type fix)`; the router's own copper from an earlier round comes
    out as `(type route)`. A strict round protects both. A loose round
    protects the tools' copper and leaves the router's in its hands, so a
    net boxed in by its neighbours' wires can have them moved: the last
    nets open are open because the router finished their neighbours first.
    The router's copper on a net the round is not routing (`dead`) is
    protected either way; it has no net to be routed on.
    """
    t = dsn.read_text()
    i = t.find("(wiring")
    if i < 0:
        return 0
    head, tail = t[:i], t[i:]
    n = tail.count("(type fix)")
    tail = tail.replace("(type fix)", "(type protect)")
    if not loose:
        n += tail.count("(type route)")
        tail = tail.replace("(type route)", "(type protect)")
    else:
        dead = set(dead) | {"GND"}
        def keep(m):
            nonlocal n
            if m.group(1).strip('"') not in dead:
                return m.group(0)
            n += 1
            return f"(net {m.group(1)})(type protect)"
        tail = re.sub(r"\(net ([^)]*)\)\(type route\)", keep, tail)
    dsn.write_text(head + tail)
    return n

# --------------------------------------------------------------- the DSN ----
# What KiCad exports is not what freerouting should be given, and the gap is
# bigger than it looks.
#
# Freerouting does not treat a Specctra `plane` as connecting anything. Given
# board A's GND -- 112 pads, two solid inner planes and a via already sitting in
# 69 of those pads -- it queued every pad as isolated, threw away the vias
# (`(type protect)` is not a thing it reads), and routed ground as 0.2 mm
# traces across the top and bottom layers. That is both wrong and most of its
# runtime: 92 of the 177 connections it could not finish were this.
#
# So ground is taken off the table. The planes go, the net goes, and In1/In4 are
# declared `power` layers, which is Specctra's way of saying "not for routing".
# The pads stay, as obstacles with no net, which is exactly what they are to a
# router that is not routing them. Everything else -- VBUS, the switch nodes,
# the phase outputs -- keeps its net and its plane, because those planes are on
# the same layer as most of their pads and freerouting does get that right.
GND_LAYERS = ("In1.Cu", "In4.Cu")
# In2 too: it is VBUS over the power half and +3V3 over the CPU wedge and the
# centre, and a track laid through either by a router that ignores
# conduction areas would be a slot cut through a plane. Every pad that needs
# one of those planes is stitched to it before the router sees the board, so
# declaring In2 `power` costs nothing and removes the only way to damage it.
PLANE_LAYERS = ("In1.Cu", "In2.Cu", "In4.Cu")

# Nets that are already made of copper before the router sees the board, and
# that it does not understand: it reads a Specctra `plane` as an obstacle and
# never as a conductor, so every pad on one of these looks isolated to it.
# On board A that is 112 ground pads, 74 VBUS, 78 switch node and 15 phase
# output -- and the six MOSFETs, whose drain pad carries a sixteen-via array
# each, were the four busiest components in the failure log by an order of
# magnitude. Left in, they are most of the router's time and none of its
# useful output.
#
# GND is deleted outright, planes and all, because In1/In4 become `power`
# layers and nothing may route there anyway. The rest keep their planes -- as
# KEEPOUTS, so the copper still blocks the router without a net to chase.
# ... and doing the same to these does NOT help, which is worth recording.
# Measured against the same board: 86 of 151 connections unrouted after one
# pass with them demoted, against 89 of 170 with them left in -- proportionally
# worse. Their planes sit on the same layers as most of their pads, which is
# the case freerouting does get right, and turning those planes into keepouts
# takes away copper that a same-net trace could otherwise cross. GND is the one
# that has to go, because its planes are on layers nothing may route on anyway.
# Off by default; pass dead=True to turn it back on.
DEAD_NETS = ("VBUS", "SW_A", "SW_B", "SW_C", "PHASE_A", "PHASE_B", "PHASE_C",
             "SNUB_A", "SNUB_B", "SNUB_C", "+3V3")
# The board is round and every part on it points at the shaft. Nothing sits
# at a multiple of 45 degrees, so a 45-degree router cannot run a track
# alongside a rotated pad row: measured, it left 116 of 170 connections and
# free angles left 93 on the same board. Free angles it is.
SNAP_ANGLE = "fortyfive_degree"
# A second round at free angles was tried for what 45 degrees could not do,
# on the theory that with everything so far protected there is little left
# to search: a free-angle pass over 33 connections had not finished in an
# hour. Every round is 45 degrees.
ROUND_SNAP = ["fortyfive_degree"]

# The maze goes after the router, not before it. Both were measured on
# this board: given the whole problem first, tools/finish.py makes 49 of
# the 61 nets and leaves 12, and freerouting then closes one of those,
# because a board full of locked copper is a worse problem than an empty
# one. Given what freerouting leaves -- six connections -- it makes four.
# Seeding it with the six by name and running it first was tried too, and
# came back with nine: it does not matter which six get the lanes out of
# the QFN's via field, only how many lanes there are.
HARD_NETS = ()
CX, CY = 148.0, 105.0        # the shaft axis, in KiCad page coordinates
EDGE = 0.35                  # outline inset, mm: min_copper_edge_clearance + a hair
# A via that leaves the router's network leaves a keepout behind, and a
# keepout has only the 0.16 mm clearance: the router put a via 0.16 from a
# 0.46 keepout and its hole 0.45 from ours. 0.58 across, the router's via
# centre is 0.7 away and the holes 0.5 apart, and a 0.25 track beside it
# is still 0.22 from the real copper.
VIA_KEEPOUT = 580            # um

def prepare_dsn(dsn, dead=DEAD_NETS):
    """Rewrite the exported DSN into the problem freerouting should solve."""
    t = dsn.read_text()
    n = {}

    # 1. In1 and In4 are solid ground. Nothing may route there.
    for lay in PLANE_LAYERS:
        t = t.replace(f"(layer {lay}\n      (type signal)",
                      f"(layer {lay}\n      (type power)", 1)
    n["power layers"] = len(PLANE_LAYERS)

    # 2. Drop the GND planes: with GND gone from the netlist a plane on that
    #    net would make every ground pad a clearance violation against it.
    out, i, k = [], 0, 0
    while True:
        j = t.find("    (plane GND ", i)
        if j < 0:
            out.append(t[i:]); break
        out.append(t[i:j])
        i = t.index("\n", t.index(")", j))          # planes are one long line
        k += 1
    t = "".join(out)
    n["planes"] = k

    # 3. Drop the net itself, and its name from the class lists.
    j = t.find("    (net GND\n")
    if j >= 0:
        t = t[:j] + t[t.index("    )\n", j) + len("    )\n"):]
        n["net"] = 1
    t = drop_from_classes(t, "GND")

    # 4. The ground vias in `wiring` refer to a net that no longer exists, so
    #    they go too -- and each leaves a keepout behind. A via sits inside its
    #    pad, which protects it on the pad's own layer, but it is a THROUGH via:
    #    without the keepout the router is free to run a trace under the pad on
    #    In3 and the via would land on top of it when the stitch is restored.
    pat = (r'^ *\(via "([^"]+)" +(-?[\d.]+) +(-?[\d.]+) '
           r'\(net GND\)\(type \w+\)\)\n')
    vias = re.findall(pat, t, re.M)
    t = re.sub(pat, "", t, flags=re.M)
    keep = []
    for name, x, y in vias:
        d = float(name.split("_")[-2].split(":")[0])          # Via[0-5]_500:200_um
        for lay in ("F.Cu", "In3.Cu", "B.Cu"):
            keep.append(f'    (keepout "" (circle {lay} {max(d, VIA_KEEPOUT):.0f} {x} {y}))')
    if keep:
        i = t.index("(via \"Via")                             # the padstack list
        i = t.rindex("\n", 0, i) + 1
        t = t[:i] + "\n".join(keep) + "\n" + t[i:]
    n["ground vias -> keepouts"] = len(vias)

    # 5. Pull the board outline in by the copper-to-edge rule. Freerouting
    #    routes right up to the boundary it is given; KiCad then measures that
    #    trace against min_copper_edge_clearance and calls it a violation. The
    #    outline is a circle about the shaft axis, so the inset is exact.
    t, k = inset_boundary(t, CX, CY, EDGE)
    n["mm off the outline"] = EDGE

    # 6. Two clearance types Specctra has and KiCad does not export, both of
    #    them standing in for a rule freerouting has no concept of: the 0.5 mm
    #    hole-to-hole floor. Copper clearance C between two 0.5/0.2 vias puts
    #    their holes 0.3 + C apart, so C = 0.3 keeps the drills legal, and the
    #    same sum against a 0.8/0.4 through-hole pad wants 0.25.
    t = t.replace("      (clearance 37.5 (type smd_smd))",
                  "      (clearance 37.5 (type smd_smd))\n"
                  "      (clearance 300 (type via_via))\n"
                  "      (clearance 250 (type via_pin))", 1)

    #    ... and in every net class's own rule too: a class with a rule of
    #    its own gets its own clearance class in freerouting, and the
    #    via_via and via_pin values in the structure rule do not reach it.
    #    Measured: a 0.5 mm via 0.679 mm from another -- 0.16 of copper,
    #    0.48 of hole to hole -- on a net in the Analog class.
    t = re.sub(r"(    \(class [^\n]*\n(?:      [^\n]*\n)*?      \(rule\n        \(width \d+\)\n        \(clearance \d+\)\n)",
               lambda m: m.group(1) + "        (clearance 300 (type via_via))\n"
                                      "        (clearance 250 (type via_pin))\n", t)
    n["class rules with via clearances"] = len(re.findall(r"\(class ", t))

    # 7. Freerouting approximates round shapes as octagons and lands a few
    #    microns inside the clearance it was given. Ten of them buys that back.
    t = re.sub(r"\(clearance (\d+)\)",
               lambda m: f"(clearance {int(m.group(1)) + 10})", t)


    # 8. Widths. The Phase and Power classes are 2.0 and 0.5 mm wide because
    #    that is what their pours and their planes are for; the few tracks
    #    the router still makes on them are stubs to a pad, not the current
    #    path, and a 2 mm stub cannot get between two pads. Every one of
    #    those classes' pads is on copper already, so 0.5 is generous.
    t = re.sub(r"\(width 2000\)", "(width 500)", t)

    # 8a. The core rail and the regulator's switch node are in the Power
    #     class, 0.5 mm wide, because +12V and +5V are. Their own runs are a
    #     few millimetres between the ring under the chip, the regulator's
    #     output pour and the caps around the chip, through a via field on
    #     0.4 mm pitch that nothing 0.5 mm wide gets through. The reference
    #     design does them at 0.2 mm. 0.25 here, as their own class.
    t = drop_from_classes(t, "+1V1")
    t = drop_from_classes(t, "VREG_LX")
    assert "  )\n  (wiring\n" in t
    t = t.replace("  )\n  (wiring\n",
                  "    (class Core +1V1 VREG_LX\n      (circuit\n"
                  "        (use_via \"Via[0-5]_500:200_um\")\n      )\n"
                  "      (rule\n        (width 250)\n        (clearance 160)\n      )\n    )\n"
                  "  )\n  (wiring\n", 1)
    n["core class"] = 1

    # 8b. The nets that are already made of copper before the router sees the
    #     board: their planes on the outer layers become keepouts -- nothing
    #     may cross a 20 A pour -- their In2 planes simply go, and their pins
    #     lose the net. Only legal because every pad on them takes a via to
    #     its own pour or plane (tools/stitch.py, tools/fanout.py); a pad that
    #     did not would be an unroutable connection, which is what the check
    #     in demote_net() is for.
    for name in (dead or ()):
        t, k = demote_net(t, name)
        n[f"{name} pins"] = k

    # 8b'. The halos round the demoted 60 V pads (see pad_halos).
    halos = ['    (keepout "" (polygon %s 0 %s))'
             % (lay, "  ".join(f"{x:.1f} {-y:.1f}" for x, y in pts))
             for lay, pts in HALOS]
    if halos:
        i = t.rindex("\n", 0, t.index('(via "Via')) + 1
        t = t[:i] + "\n".join(halos) + "\n" + t[i:]
    n["pad halos"] = len(halos)

    # 8c. The four M3 heads on the outward face: no track or via under a
    #     screw head, whatever the mask says.
    import geometry as G
    heads = []
    for hx, hy in ((G.MOUNT_X, 0), (-G.MOUNT_X, 0), (0, G.MOUNT_Y), (0, -G.MOUNT_Y)):
        heads.append(f'    (keepout "" (circle F.Cu {G.MOUNT_HEAD * 1000:.0f} '
                     f'{(CX + hx) * 1000:.1f} {-(CY - hy) * 1000:.1f}))')
    i = t.rindex("\n", 0, t.index('(via "Via')) + 1
    t = t[:i] + "\n".join(heads) + "\n" + t[i:]
    n["screw-head keepouts"] = len(heads)

    # 8c'. The FETs' gate pads, 0.85 x 0.5 mm and never on the 45-degree
    #      grid on a round board. Freerouting's 45-degree search tree wraps a
    #      tilted pad in its bounding octagon, and for the gate that octagon
    #      plus clearance runs into the source pads' halos beside it: "no
    #      accessible expansion doors", every gate, every pass. A circle is
    #      the same at every angle. The pad's inscribed circle, 0.5 mm: the
    #      copper the router no longer sees is the 0.175 mm at either end,
    #      and both ends face pads of the switch node, whose halos keep every
    #      other net 0.4 mm away regardless. The source pads share the
    #      padstack and lose the same; they are demoted and haloed anyway.
    t, k = re.subn(r'(\(padstack RoundRect\[T\]Pad_850\.000000x500\.000000_[^\n]*\n'
                   r'      \(shape )\(polygon F\.Cu 0[^)]*\)',
                   r'\1(circle F.Cu 500)', t)
    n["gate padstacks rounded"] = k

    # 8d. Freerouting joins a wire to a pin only if the wire ENDS AT THE
    #     PIN'S CENTRE, exactly (Trace.get_normal_contacts compares against
    #     DrillItem.get_center() with equals). It computes that centre itself,
    #     from the placement and the image's pin offset, rotated in doubles
    #     and rounded to its 0.1 um grid; KiCad writes wire coordinates to the
    #     whole micron. So a fan-out stub that starts on the pad's centre
    #     lands a few tenths of a micron off it, and freerouting sees a pin
    #     with nothing on it and a stub floating beside it -- 119 connections
    #     it then tried to make between a pad and its own stub and could not.
    #     Every wire end within 25 um of a pin of its net is rewritten to
    #     the centre freerouting will compute (25, not 1: for the flash the
    #     two centres differ by 9 um, and no pad is within 25 um of another
    #     pad's centre).
    t, k = snap_wire_ends(t)
    n["wire ends snapped to pin centres"] = k


    # 8e. A pin that has its stub and its via is taken off the net, and
    #     the stub becomes a keepout. Freerouting then sees the VIA as the
    #     net's end: a circle, on the grid at any angle, nothing to tee
    #     into. Left on the net, the stub is a protected trace the maze
    #     search reaches before the via and cannot insert a junction into
    #     ("the new connection could not be inserted", 791 times in one
    #     run), and a pin it cannot leave (8c'). KiCad never sees any of
    #     this: the pad, the stub and the via are all still on the board.
    t, k = strip_stubbed_pins(t)
    n["pins handed over to their vias"] = k

    # 9. Say the angle out loud. Freerouting's DSN parser reads
    #    (snap_angle ...) out of the structure scope and KiCad never writes
    #    one, so the router had been taking its own default. The brief is
    #    45 degree bends, so that is what it is told.
    i = t.index("  (placement")
    j = t.rindex("\n  )\n", 0, i) + 1          # the ) that closes (structure
    snap = os.environ.get("SERVODRIVE_SNAP", SNAP_ANGLE)
    t = t[:j] + f"    (snap_angle {snap})\n" + t[j:]
    n["snap angle"] = snap

    dsn.write_text(t)
    return n

def demote_net(t, name):
    """Take one net out of the routing problem, leaving its copper behind.

    Its plane becomes a keepout, its vias become keepouts, its pins lose their
    net. Nothing moves; the router simply stops being asked to connect what is
    already connected.
    """
    # Outer-layer pours become keepouts; a plane on In2 is deleted, because a
    # keepout there would stop every via on the board passing through it.
    # Deleted as a balanced scope: KiCad wraps a polygon over many lines,
    # and cutting the first line only left the rest of the points behind,
    # which freerouting read as the end of the board and routed nothing.
    t = drop_scopes(t, f"    (plane {name} (polygon In2.Cu ")
    t = re.sub(rf"^    \(plane {re.escape(name)} \(polygon ",
               '    (keepout "" (polygon ', t, flags=re.M)
    pat = (rf'^ *\(via "([^"]+)" +(-?[\d.]+) +(-?[\d.]+) '
           rf'\(net {re.escape(name)}\)\(type \w+\)\)\n')
    vias = re.findall(pat, t, re.M)
    t = re.sub(pat, "", t, flags=re.M)
    keep = []
    # A via on a 60 V net keeps its class's 0.4 mm, not the 0.16 the router
    # keeps from a keepout: the circle grows by the difference, as the pad
    # halos do (pad_halos). Measured as a +12V run on In3 0.33 mm from a
    # VBUS via.
    grow = 480 if name in fanout.WIDE_NETS else 0
    for pad, x, y in vias:
        d = float(pad.split("_")[-2].split(":")[0])
        for lay in ("F.Cu", "In3.Cu", "B.Cu"):
            keep.append(f'    (keepout "" (circle {lay} {max(d, VIA_KEEPOUT) + grow:.0f} {x} {y}))')
    if keep:
        i = t.rindex("\n", 0, t.index('(via "Via')) + 1
        t = t[:i] + "\n".join(keep) + "\n" + t[i:]
    j = t.find(f"    (net {name}\n")
    k = 0
    if j >= 0:
        k = len(t[j:t.index("    )\n", j)].split())
        t = t[:j] + t[t.index("    )\n", j) + len("    )\n"):]
    t = drop_from_classes(t, name)
    return t, k

def _jround(x):
    """Java's Math.round(double): floor(x + 0.5)."""
    return math.floor(x + 0.5)

def _pin_centres(t):
    """Every pin's centre as freerouting will compute it, in DSN units, and
    every pin's net: {(ref, pin): (x, y)}, {(ref, pin): net}, unit."""
    res = re.search(r"\(resolution um (\d+)\)", t)
    unit = int(res.group(1)) if res else 10
    # images: pin offsets
    pins = {}
    for m in re.finditer(r'^    \(image (\S+)\n(.*?)^    \)', t, re.M | re.S):
        name = m.group(1).strip('"')
        pins[name] = [(pm.group(1), float(pm.group(2)), float(pm.group(3)))
                      for pm in re.finditer(r'^\s+\(pin \S+ (?:\(rotate [^)]*\) )?(\S+) (-?[\d.]+) (-?[\d.]+)\)', m.group(2), re.M)]
    # placement: component -> (image, x, y, side, rotation)
    centres = {}                                    # (ref, pin) -> (x, y) units
    for m in re.finditer(r'^    \(component (\S+)\n((?:      \(place .*\n)+)', t, re.M):
        image = m.group(1).strip('"')
        for pm in re.finditer(r'      \(place (\S+) (-?[\d.]+) (-?[\d.]+) (front|back) (-?[\d.]+)', m.group(2)):
            ref, px, py, side, rot = pm.group(1), float(pm.group(2)), float(pm.group(3)), pm.group(4), float(pm.group(5))
            loc = (_jround(px * unit), _jround(py * unit))
            for pname, ox, oy in pins.get(image, ()):
                vx, vy = _jround(ox * unit), _jround(oy * unit)
                if side == "back":
                    vx = -vx                        # KiCad writes (place_control (flip_style rotate_first)): mirror first
                if rot % 90 == 0:
                    for _ in range(int(rot // 90) % 4):
                        vx, vy = -vy, vx
                else:
                    a = math.radians(rot)
                    fx, fy = vx * math.cos(a) - vy * math.sin(a), vx * math.sin(a) + vy * math.cos(a)
                    vx, vy = _jround(fx), _jround(fy)
                centres[(ref, pname)] = (loc[0] + vx, loc[1] + vy)
    # nets: pin -> net
    netof = {}
    for m in re.finditer(r'^    \(net (\S+)\n      \(pins ([^)]*)\)', t, re.M):
        for pin in m.group(2).split():
            ref, _, pname = pin.rpartition("-")
            netof[(ref, pname)] = m.group(1)
    return centres, netof, unit

def snap_wire_ends(t, tol_um=25.0):
    """Move every wire endpoint that lies within `tol_um` of a pin of the same
    net onto the pin centre as freerouting computes it (see prepare_dsn 8d).
    Coordinates are written in um with one decimal: the DSN resolution is
    0.1 um, so that is exact."""
    centres, netof, unit = _pin_centres(t)
    by_net = {}
    for key, c in centres.items():
        by_net.setdefault(netof.get(key), []).append(c)
    # ... and every via of the net. A protected via the router routed to
    # comes back from the session up to 25 um from where it was and goes
    # back there (import_ses); the router's wire still ends where the
    # session put the via, and a wire end that is not the via's centre is
    # not on the via.
    for name, x, y, net in re.findall(r'^ *\(via "([^"]+)" +(-?[\d.]+) +(-?[\d.]+) \(net ([^)]*)\)\(type \w+\)\)', t, re.M):
        by_net.setdefault(net, []).append((float(x) * unit, float(y) * unit))
    k = 0
    def fix(m):
        nonlocal k
        head, coords, net = m.group(1), m.group(2), m.group(3)
        v = coords.split()
        pts = [[float(v[i]), float(v[i + 1])] for i in range(0, len(v) - 1, 2)]
        for idx in (0, len(pts) - 1):
            x, y = pts[idx]
            # the nearest pin or via of the net within reach: a via sitting
            # in a pad is not the pad's centre, and the wire ends on one
            near = [(max(abs(cx / unit - x), abs(cy / unit - y)), cx, cy)
                    for cx, cy in by_net.get(net, ())]
            near = [c for c in near if c[0] <= tol_um + 5]
            if near:
                _, cx, cy = min(near)
                if (cx / unit, cy / unit) != (x, y):
                    pts[idx] = [cx / unit, cy / unit]
                    k += 1
        return head + "  ".join(f"{x:.1f} {y:.1f}" for x, y in pts) + ")(net " + net + ")"
    t = re.sub(r'(\(wire \(path \S+ \d+ +)([-\d. ]+?)\)\(net ([^)]*)\)', fix, t)
    return t, k

def _stroke(a, b, w):
    """A rectangle around segment a-b of width w, with square caps of w/2."""
    (x0, y0), (x1, y1) = a, b
    dx, dy = x1 - x0, y1 - y0
    L = math.hypot(dx, dy) or 1.0
    ux, uy = dx / L, dy / L
    nx, ny, h = -uy, ux, w / 2
    pts = [(x0 - ux * h + nx * h, y0 - uy * h + ny * h), (x1 + ux * h + nx * h, y1 + uy * h + ny * h),
           (x1 + ux * h - nx * h, y1 + uy * h - ny * h), (x0 - ux * h - nx * h, y0 - uy * h - ny * h)]
    return " ".join(f"{x:.1f} {y:.1f}" for x, y in pts)

def strip_stubbed_pins(t, tol_um=25.0):
    """For every chain of protected wires on a net the router still routes
    that runs from a pin to a via: the pin leaves the net's pin list, and
    the chain becomes keepouts on its layers. The net keeps its protected
    vias. A chain between vias, or between pins, is an earlier round's
    route on a net still open; it stays a wire, so the router sees what is
    already joined. Returns the text and the number of pins handed over."""
    centres, netof, unit = _pin_centres(t)
    live = set(re.findall(r"^    \(net (\S+)\n      \(pins", t, re.M))
    pat = re.compile(r'    \(wire \(path (\S+) (\d+) +((?:-?[\d.]+ -?[\d.]+ *)+)\)'
                     r'\(net ([^)]*)\)\(type protect\)\)\n')
    vias = {}
    for name, x, y, net in re.findall(r'^ *\(via "([^"]+)" +(-?[\d.]+) +(-?[\d.]+) \(net ([^)]*)\)\(type protect\)\)', t, re.M):
        vias.setdefault(net, []).append((float(x), float(y)))
    pins = {}
    for key, (cx, cy) in centres.items():
        pins.setdefault(netof.get(key), []).append((key, cx / unit, cy / unit))
    wires = [(m.start(), m.end(), lay, w, net,
              [(float(v[k]), float(v[k + 1])) for v in [c.split()] for k in range(0, len(v) - 1, 2)])
             for m in pat.finditer(t) for lay, w, c, net in [m.groups()]]
    # chains: wires of one net joined end to end (union-find on endpoints)
    def near(a, b):
        return abs(a[0] - b[0]) <= tol_um and abs(a[1] - b[1]) <= tol_um
    parent = list(range(len(wires)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    by_net = {}
    for i, w in enumerate(wires):
        by_net.setdefault(w[4], []).append(i)
    for net, idx in by_net.items():
        for a in range(len(idx)):
            for b in range(a + 1, len(idx)):
                wa, wb = wires[idx[a]], wires[idx[b]]
                if any(near(p, q) for p in (wa[5][0], wa[5][-1]) for q in (wb[5][0], wb[5][-1])):
                    parent[find(idx[a])] = find(idx[b])
    chains = {}
    for i in range(len(wires)):
        chains.setdefault(find(i), []).append(i)
    keepouts, handed, drop = [], set(), set()
    for root, idx in chains.items():
        net = wires[idx[0]][4]
        if net not in live:
            continue
        ends = [e for i in idx for e in (wires[i][5][0], wires[i][5][-1])]
        chain_pins = [key for key, x, y in pins.get(net, ()) if any(near((x, y), e) for e in ends)]
        # a via to 30 um: the router's own wire ends where the session put
        # the via, and the via went back up to 25 um from there (import_ses)
        has_via = any(abs(v[0] - e[0]) <= 30 and abs(v[1] - e[1]) <= 30
                      for v in vias.get(net, ()) for e in ends)
        if not chain_pins:
            continue                        # via to via, or a loose end: stays a wire
        if has_via:
            handed.update(chain_pins)
        for i in idx:
            lay, w, pts = wires[i][2], wires[i][3], wires[i][5]
            for a, b in zip(pts, pts[1:]):
                keepouts.append(f'    (keepout "" (polygon {lay} 0 {_stroke(a, b, float(w))}))')
            drop.add(i)
    # remove the converted wires (from the end, so offsets stay valid)
    for i in sorted(drop, reverse=True):
        s, e = wires[i][0], wires[i][1]
        t = t[:s] + t[e:]
    def strip(m):
        net, plist = m.group(1), m.group(2).split()
        keep = [p for p in plist if tuple(p.rsplit("-", 1)) not in handed]
        return f"    (net {net}\n      (pins {' '.join(keep)})"
    t = re.sub(r"^    \(net (\S+)\n      \(pins ([^)]*)\)", strip, t, flags=re.M)
    if keepouts:
        i = t.rindex("\n", 0, t.index('(via "Via')) + 1
        t = t[:i] + "\n".join(keepouts) + "\n" + t[i:]
    return t, len(handed)

def drop_scopes(t, prefix):
    """Remove every balanced s-expression that starts with `prefix`."""
    while True:
        i = t.find(prefix)
        if i < 0:
            return t
        d, j = 0, i
        while j < len(t):
            if t[j] == "(":
                d += 1
            elif t[j] == ")":
                d -= 1
                if d == 0:
                    break
            j += 1
        j += 1
        if j < len(t) and t[j] == "\n":
            j += 1
        t = t[:i] + t[j:]

def drop_from_classes(t, name):
    """Take a net's name out of every (class ...) list, as a whole token.

    Done by tokens rather than by a \\b regex: "+3V3" has no word boundary
    in front of it, so the regex left it in the Power class after the net
    itself was gone, and freerouting read a class naming a net that did not
    exist, gave up on the network quietly and routed nothing.
    """
    out, i = [], 0
    while True:
        j = t.find("    (class ", i)
        if j < 0:
            out.append(t[i:]); break
        k = t.find("\n      (", j)              # the first sub-scope of the class
        head = t[j:k]
        toks = head.split()
        toks = [x for x in toks if x != name]
        # re-wrap: "(class NAME n1 n2 ..." with the original line breaks lost;
        # freerouting does not care about the wrapping
        out.append(t[i:j] + "    " + " ".join(toks))
        i = k
    return "".join(out)

def inset_boundary(t, cx, cy, by):
    """Shrink the outline polygon radially about (cx, cy) by `by` mm."""
    i = t.index("(path pcb 0", t.index("    (boundary"))
    j = t.index(")", i)
    head, body = t[i:i + len("(path pcb 0")], t[i + len("(path pcb 0"):j]
    v = body.split()
    out = []
    for a, b in zip(v[0::2], v[1::2]):
        x, y = float(a) / 1000.0, -float(b) / 1000.0
        dx, dy = x - cx, y - cy
        r = (dx * dx + dy * dy) ** 0.5
        s = (r - by) / r if r else 1.0
        out.append(f"{(cx + dx * s) * 1000:.1f} {-(cy + dy * s) * 1000:.1f}")
    return t[:i] + head + "  " + "  ".join(out) + t[j:], len(out)

FR_CONFIG = """{
  "profile": { "id": "00000000-0000-4000-8000-000000000000", "email": "",
               "allow_telemetry": false, "allow_contact": false },
  "gui": { "enabled": false, "input_directory": "",
           "dialog_confirmation_timeout": 0 },
  "usage_and_diagnostic_data": { "disable_analytics": true },
  "feature_flags": { "multi_threading": true }
}
"""

def run_freerouting(dsn, ses, passes, timeout):
    """Headless. Given only -de/-do it still opens a window, and on a machine
    with no display that is a stack trace and no session file, so the GUI is
    turned off twice: once in the JVM and once in freerouting's own config.
    The config lives in java.io.tmpdir, which is why that is redirected into
    the work directory rather than left at /tmp."""
    if not JAR.exists():
        raise FileNotFoundError(f"no freerouting jar at {JAR}")
    cfg = WORK / "fr"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "freerouting.json").write_text(FR_CONFIG)
    cmd = ["java", "-Djava.awt.headless=true", f"-Djava.io.tmpdir={cfg}",
           "-Xmx8g", "-jar", str(JAR),
           "-de", str(dsn), "-do", str(ses), "-mp", str(passes)]
    print("  " + " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    tail = (r.stdout + r.stderr).strip().splitlines()
    for line in tail[-12:]:
        print("   |", line[:150])
    if not ses.exists():
        raise RuntimeError("freerouting produced no session file")
    return r.returncode

def import_ses(src, ses, out):
    """Merge the routed session onto the ORIGINAL board, pours and all.

    KiCad's SES import replaces the board's tracks with the session's, and the
    session does not carry the stitching vias -- freerouting keeps no record of
    wiring it was not asked to route. So the stitch runs again afterwards. It
    is idempotent, it now counts the fresh tracks as obstacles, and the spots
    it wants were keepouts while the router worked, so it puts the same 93 vias
    back where they were.
    """
    board = pcbnew.LoadBoard(str(src))
    # KiCad's SES import throws away every track and via on the board and
    # keeps only what the session carries, and freerouting writes back
    # nothing it was told to protect. The stitching vias, the fan-out and
    # the plane taps were all legal before the router ran and the router
    # routed FROM them, so they go back exactly as they were -- not by
    # searching again, which the router's own tracks would now defeat.
    keep = [(t.Duplicate(), t.GetNetname()) for t in board.GetTracks()]
    if os.environ.get("SERVODRIVE_LOOSE"):
        # The router had its own earlier copper to rip up this round, and
        # what it ripped up and did not put back is not to be put back
        # either: only the tools' copper (locked) and the copper on nets the
        # round did not route come back regardless.
        dead = set(json.loads((WORK / "dead.json").read_text())) | {"GND"}
        keep = [(t, name) for t, name in keep if t.IsLocked() or name in dead]
    if Path(ses).stat().st_size == 0:
        # freerouting found nothing to route and wrote nothing: the board
        # stands as it is
        return board, 0, 0, 0, 0
    # The session carries placement as well as wiring, and freerouting writes
    # every rotation back as a whole degree: the DSN says Q1 is at -68.403,
    # the session says 292, and KiCad's import turns the part to match. On a
    # round board that is most parts, by up to half a degree -- measured on
    # 2026-09-22 as 93 of board A's 140 footprints off the generator's angles,
    # and on board S as a USB-C pad pushed 0.024 mm onto the fan-out's stub
    # beside it. The router routed against the exact angles (the DSN's), so
    # the exact angles are what go back.
    place = {f.GetReference(): (f.GetPosition(), f.GetOrientation()) for f in board.GetFootprints()}
    if not pcbnew.ImportSpecctraSES(board, str(ses)):
        raise RuntimeError("Specctra session import failed")
    for f in board.GetFootprints():
        pos, ori = place.get(f.GetReference(), (None, None))
        if pos is None:
            continue
        if f.GetOrientation().AsDegrees() != ori.AsDegrees():
            f.SetOrientation(ori)
        if f.GetPosition() != pos:
            f.SetPosition(pos)
    # The import renumbers the nets. A duplicate carries the old code, and
    # put back as it was it lands on whatever net has that code now -- the
    # gate of one FET reported unconnected to a track on another phase's
    # gate net. So every restored item is re-bound to its net by name.
    for t, name in keep:
        t.SetNet(board.FindNet(name))
    # The session import does not refill, and the pours are as they were
    # before the router: whole. Refilled, the islands the routing cut are
    # what the ground taps and the island ties below look at.
    fill(board)
    # ... except what the session DID bring back. Freerouting does write
    # back some of the protected vias, moved by up to 25 um -- measured as
    # fifteen hole-to-hole violations between a via and itself -- so the
    # match is by net and position within 60 um, and the session's copy
    # wins, because the router's tracks end on it.
    near = {}
    for t in board.GetTracks():
        for p in (t.GetStart(), t.GetEnd()):
            near.setdefault((t.Type(), t.GetNetCode(), p.x // 60000, p.y // 60000), []).append((p, t))
    def close(p, q):
        return abs(q.x - p.x) <= 60000 and abs(q.y - p.y) <= 60000
    def present(t, but=None):
        """The board's own copy of this item, or None: a via of the net
        within 60 um, or a track of the net on the layer with both ends
        within 60 um. (One end is not enough: the round's new wire may start
        where an old one ended, and the old one still has to come back.)"""
        p = t.GetStart()
        cx, cy = p.x // 60000, p.y // 60000
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for q, item in near.get((t.Type(), t.GetNetCode(), cx + dx, cy + dy), ()):
                    if item is but or not close(q, p):
                        continue
                    if t.Type() == pcbnew.PCB_VIA_T:
                        return item
                    if item.GetLayer() != t.GetLayer():
                        continue
                    other = item.GetEnd() if q == item.GetStart() else item.GetStart()
                    if close(other, t.GetEnd()):
                        return item
        return None
    # KiCad's session import keeps a LOCKED item -- and the session carries
    # its own copy of some of the protected vias, up to 25 um from where
    # they were, which is the difference between the fan-out's 0.1505 mm
    # clearance and a DRC violation. The original stays, the copy goes; the
    # router's tracks end inside the original's pad regardless.
    moved = 0
    for t in list(board.GetTracks()):
        if not t.IsLocked():
            continue
        twin = present(t, but=t)
        while twin is not None and not twin.IsLocked():
            board.Delete(twin)
            moved += 1
            near = {}
            for u in board.GetTracks():
                for p in (u.GetStart(), u.GetEnd()):
                    near.setdefault((u.Type(), u.GetNetCode(), p.x // 60000, p.y // 60000), []).append((p, u))
            twin = present(t, but=t)
    restored = 0
    for t, _ in keep:
        if present(t) is not None:
            continue
        board.Add(t)
        restored += 1
    dangling = drop_dangling(board)
    bad = normalise_vias(board, Path(src).with_suffix('.kicad_pro'))
    slivers = drop_slivers(board)
    n = stitch.stitch(board, verbose=False)[0] + restored
    # ... and anything the round trip still lost is searched for again.
    obs = fanout.Obstacles(board)
    plane_nets = {z.GetNetCode() for z in board.Zones()
                  if pcbnew.In2_Cu in z.GetLayerSet().CuStack() and z.GetNetCode() > 0}
    obs.plane_layer = lambda code: pcbnew.In2_Cu if code in plane_nets else None
    fanout.qfn(board, obs, verbose=False)
    fanout.gates(board, obs, verbose=False)
    fanout.sense(board, obs, verbose=False)
    for ref in fanout.escape_refs(board):
        fanout.escape(board, obs, ref, verbose=False)
    fanout.links(board, obs, verbose=False)
    fanout.taps(board, obs, verbose=False)
    n += stitch.tie_islands(board, verbose=False)
    moved += dedupe(board)
    c = chamfer(board)
    fill(board)
    save(board, out)
    return board, n, c, bad, slivers + dangling, moved

def dedupe(board):
    """Two items of one geometry on one net: the second goes (the locked
    one stays, if either is). A post-route fan-out pass that did not see
    an escape the session brought back makes it again; measured as six
    doubled segments on one board."""
    seen, gone = {}, 0
    for t in list(board.GetTracks()):
        if t.Type() == pcbnew.PCB_VIA_T:
            key = ("via", t.GetNetCode(), t.GetPosition().x, t.GetPosition().y)
        else:
            a, b = (t.GetStart().x, t.GetStart().y), (t.GetEnd().x, t.GetEnd().y)
            key = ("seg", t.GetLayer(), t.GetNetCode(), t.GetWidth()) + tuple(sorted((a, b)))
        if key not in seen:
            seen[key] = t
            continue
        if t.IsLocked() and not seen[key].IsLocked():
            board.Delete(seen[key]); seen[key] = t
        else:
            board.Delete(t)
        gone += 1
    # ... and two vias of one net a few microns apart, which is the same via
    # to DRC ("drilled holes co-located") and not to an exact match: a
    # fan-out escape and the router's via at the end of its own wire, on
    # board S. The locked one stays; the tracks that ended on the other are
    # moved onto it.
    vias = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_VIA_T]
    dead = set()
    for i, a in enumerate(vias):
        if id(a) in dead:
            continue
        for b in vias[i + 1:]:
            if id(b) in dead or b.GetNetCode() != a.GetNetCode():
                continue
            pa, pb = a.GetPosition(), b.GetPosition()
            if abs(pa.x - pb.x) > 50000 or abs(pa.y - pb.y) > 50000:
                continue
            keep, drop = (b, a) if (b.IsLocked() and not a.IsLocked()) else (a, b)
            to, fr = keep.GetPosition(), drop.GetPosition()
            for tr in board.GetTracks():
                if tr.Type() != pcbnew.PCB_TRACE_T or tr.GetNetCode() != keep.GetNetCode():
                    continue
                if tr.GetStart() == fr:
                    tr.SetStart(to)
                if tr.GetEnd() == fr:
                    tr.SetEnd(to)
            board.Delete(drop)
            dead.add(id(drop))
            gone += 1
            if drop is a:
                break
    return gone

def drop_dangling_vias(board, report):
    """Delete the vias KiCad's DRC reports as dangling (copper of their net
    on one layer only)."""
    d = json.loads(Path(report).read_text())
    # ... except on a net that is still open: its escape vias are what the
    # next round routes to.
    open_nets = set()
    for it in d.get("unconnected_items", ()):
        for i in it["items"]:
            m = re.search(r"\[([^\]]*)\]", i["description"])
            if m:
                open_nets.add(m.group(1))
    spots = set()
    for v in d["violations"]:
        if v["type"] != "via_dangling":
            continue
        for i in v["items"]:
            m = re.search(r"\[([^\]]*)\]", i["description"])
            if m and m.group(1) in open_nets:
                continue
            spots.add((int(round(i["pos"]["x"] * 1e6)), int(round(i["pos"]["y"] * 1e6))))
    doomed = [v for v in board.GetTracks() if v.Type() == pcbnew.PCB_VIA_T
              and any(abs(v.GetPosition().x - x) < 1000 and abs(v.GetPosition().y - y) < 1000
                      for x, y in spots)]
    for v in doomed:
        board.Delete(v)
    return len(doomed)

def drop_dangling(board, rounds=6):
    """Remove track segments with a free end.

    A route the router gave up on can leave a stub behind -- 0.4 mm of TP23
    on B.Cu ending in nothing, which DRC calls a dangling track. A segment
    end is attached if a pad, a via, a zone fill or another segment of its
    net is on it; anything else goes, and again, since removing one segment
    can free the end of the next."""
    removed = 0
    zones = [(z.GetNetCode(), l, z.GetFilledPolysList(l))
             for z in board.Zones() if not z.GetIsRuleArea()
             for l in z.GetLayerSet().CuStack()]
    pads = [(p.GetNetCode(), p) for f in board.GetFootprints() for p in f.Pads()]
    for _ in range(rounds):
        tracks = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_TRACE_T]
        vias = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_VIA_T]
        by_net = {}
        for t in tracks:
            by_net.setdefault(t.GetNetCode(), []).append(t)
        vias_by_net = {}
        for v in vias:
            vias_by_net.setdefault(v.GetNetCode(), []).append(v)
        def attached(t, pt):
            code, layer = t.GetNetCode(), t.GetLayer()
            for o in by_net.get(code, ()):
                if o is not t and o.GetLayer() == layer and \
                        o.GetEffectiveShape(layer).Collide(pt, 0):
                    return True
            for v in vias_by_net.get(code, ()):
                if v.GetEffectiveShape(layer).Collide(pt, 0):
                    return True
            for c, p in pads:
                if c == code and layer in p.GetLayerSet().CuStack() and p.HitTest(pt):
                    return True
            for c, l, poly in zones:
                if c == code and l == layer and poly.Contains(pt):
                    return True
            return False
        doomed = [t for t in tracks
                  if not attached(t, t.GetStart()) or not attached(t, t.GetEnd())]
        if not doomed:
            break
        for t in doomed:
            board.Delete(t)           # Remove() orphans it and corrupts the list
        removed += len(doomed)
    return removed

def spread_holes(board, report, pro, margin=0.01):
    """Fix drills that ended up inside the hole-to-hole rule.

    Freerouting has no such rule -- Specctra cannot express one, and the
    via_via clearance in the DSN only approximates it -- so a handful of its
    vias land 30-40 um too close.

    The first fix tried here was to move a via apart, and it was the wrong one:
    the tracks that end on it have to move too, which takes them off 45 degrees
    and pushed two of them inside their clearance. Shrinking a drill changes no
    geometry at all. A 0.6/0.30 via beside a 0.5/0.20 one is 0.46 mm hole to
    hole; take the first down to 0.5/0.20 and it is 0.51, with the annular ring
    going the right way. Only if that is not enough does anything move.
    """
    import json
    import math
    d = json.loads(Path(report).read_text())
    small_d, small_k = _floor_via(pro)
    vias = {(v.GetPosition().x, v.GetPosition().y): v
            for v in board.GetTracks() if v.Type() == pcbnew.PCB_VIA_T}
    holes = dict(vias)
    for f in board.GetFootprints():
        for pad in f.Pads():
            if pad.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
                holes[(pad.GetPosition().x, pad.GetPosition().y)] = pad
    shrunk = moved = 0
    for v in d["violations"]:
        if v["type"] != "hole_to_hole":
            continue
        want = float(re.search(r"min ([\d.]+) mm", v["description"]).group(1))
        pts = [(int(round(i["pos"]["x"] * 1e6)), int(round(i["pos"]["y"] * 1e6)))
               for i in v["items"]]
        # The report gives positions to the micron and a router's via can sit
        # between two (146.6926 read back as 146.693): the nearest hole, then.
        def nearest(p):
            q = min(holes, key=lambda h: math.hypot(h[0] - p[0], h[1] - p[1]))
            return q if math.hypot(q[0] - p[0], q[1] - p[1]) <= 2000 else p
        pts = [nearest(p) for p in pts]
        pair = [holes.get(p) for p in pts]
        if None in pair:
            continue
        centres = math.hypot(pts[0][0] - pts[1][0], pts[0][1] - pts[1][1])

        def drill(o):
            return (o.GetDrill() if o.Type() == pcbnew.PCB_VIA_T
                    else max(o.GetDrillSize().x, o.GetDrillSize().y))

        # 1. shrink whichever via has the bigger drill, if that is enough
        order = sorted((o for o in pair if o.Type() == pcbnew.PCB_VIA_T),
                       key=drill, reverse=True)
        for via in order:
            other = pair[0] if pair[1] is via else pair[1]
            if via.GetDrill() <= small_k:
                continue
            if centres - small_k / 2 - drill(other) / 2 >= want * 1e6 + margin * 1e6:
                via.SetDrill(small_k)
                # ... and take a diameter that goes with that drill in the
                # board's own via table, or normalise_vias() will put the old
                # drill straight back on the next pass.
                via.SetWidth(min(via.GetWidth(pcbnew.F_Cu), small_d))
                shrunk += 1
                break
        else:
            # 2. otherwise move a via, and its tracks with it -- either of the
            # two, straight apart or a little off it: on board S a router via
            # boxed in by two tracks could not move, and the escape via beside
            # it could
            got = float(re.search(r"actual ([\d.]+) mm", v["description"]).group(1))
            step = int((want - got + margin) * 1e6)
            obs = fanout.Obstacles(board)
            found = None
            for pick in [p for p in pts if p in vias]:
                other = pts[1] if pick == pts[0] else pts[0]
                dx, dy = pick[0] - other[0], pick[1] - other[1]
                n = math.hypot(dx, dy)
                if not n:
                    continue
                via = vias[pick]
                for off in (0, 15, -15, 30, -30):
                    a = math.atan2(dy, dx) + math.radians(off)
                    s = step / max(math.cos(math.radians(off)), 0.5)
                    to = (pick[0] + int(math.cos(a) * s), pick[1] + int(math.sin(a) * s))
                    # ... only where the via's copper, moved, still clears
                    # everything else: a nudge of 25 um put one 4 um inside a
                    # track's clearance on In3.
                    if obs.clear(fanout.circle(to, via.GetWidth(pcbnew.F_Cu) / 1e6),
                                 via.GetNetCode(), None):
                        found = (pick, to, via)
                        break
                if found:
                    break
            if found is None:
                continue
            pick, to, via = found
            vias.pop(pick)
            via.SetPosition(pcbnew.VECTOR2I(*to))
            for tr in board.GetTracks():
                if tr.Type() != pcbnew.PCB_TRACE_T:
                    continue
                if (tr.GetStart().x, tr.GetStart().y) == pick:
                    tr.SetStart(pcbnew.VECTOR2I(*to))
                if (tr.GetEnd().x, tr.GetEnd().y) == pick:
                    tr.SetEnd(pcbnew.VECTOR2I(*to))
            vias[to] = via
            holes[to] = via
            moved += 1
    return shrunk, moved

def _floor_via(pro):
    """The smallest drill the board allows, and the widest pad that goes with
    it in the board's own via table."""
    import json
    ds = json.loads(Path(pro).read_text())["board"]["design_settings"]
    r = ds["rules"]
    hole = stitch.mm(r["min_through_hole_diameter"])
    dia = max([stitch.mm(v["diameter"]) for v in ds["via_dimensions"]
               if v["diameter"] and stitch.mm(v["drill"]) == hole]
              + [max(stitch.mm(r["min_via_diameter"]),
                     hole + 2 * stitch.mm(r["min_via_annular_width"]))])
    return dia, hole

def drop_slivers(board, shortest=0.005):
    """Delete degenerate track segments.

    The Specctra round trip leaves sub-micron stubs -- a corner point emitted
    twice, half a micron apart. They are invisible, they are not at any angle
    in particular, and they turn a clean 45 degree bend into a right angle that
    chamfer() then refuses to cut because there is nothing to cut. Seven of the
    nine right angles left on the first routed board were these.
    """
    import math
    n = 0
    for tr in list(board.GetTracks()):
        if tr.Type() != pcbnew.PCB_TRACE_T:
            continue
        d = math.hypot(tr.GetEnd().x - tr.GetStart().x,
                       tr.GetEnd().y - tr.GetStart().y) / 1e6
        if d < shortest:
            board.Delete(tr)          # Remove() orphans it and corrupts the list
            n += 1
    return n

def chamfer(board, cut=0.4):
    """Turn every right-angle corner into a pair of 45 degree bends.

    The brief was no stair-stepped right angles. Freerouting leaves a couple of
    dozen of them -- its pull-tight pass does not reach corners that are
    already short. Cutting one is safe by construction: both new endpoints lie
    on the old track and the diagonal between them lies inside the corner it
    replaces, so a chamfer only ever removes copper and can never create a
    clearance violation.

    A corner is only cut where exactly two segments meet, nothing else is at
    that point, and there is room to take `cut` off both.
    """
    from collections import defaultdict
    ends = defaultdict(list)
    for tr in board.GetTracks():
        if tr.Type() != pcbnew.PCB_TRACE_T:
            continue
        for p in (tr.GetStart(), tr.GetEnd()):
            ends[(tr.GetLayer(), tr.GetNetCode(), p.x, p.y)].append(tr)
    holes = {(v.GetPosition().x, v.GetPosition().y)
             for v in board.GetTracks() if v.Type() == pcbnew.PCB_VIA_T}
    for f in board.GetFootprints():
        for pad in f.Pads():
            holes.add((pad.GetPosition().x, pad.GetPosition().y))

    made = 0
    for (layer, net, x, y), pair in ends.items():
        if len(pair) != 2 or pair[0] is pair[1]:
            continue
        a, b = pair
        if a.GetWidth() != b.GetWidth() or (x, y) in holes:
            continue
        # An earlier cut may already have moved one of these ends: a track
        # with a right angle at both ends is chamfered twice, and the second
        # visit still holds the first visit's coordinates.
        if not (_touches(a, x, y) and _touches(b, x, y)):
            continue
        va, la = _away(a, x, y)
        vb, lb = _away(b, x, y)
        if va is None or vb is None:
            continue
        if abs(va[0] * vb[0] + va[1] * vb[1]) > 1e-9:      # not a right angle
            continue
        d = min(int(cut * 1e6), la // 2, lb // 2)
        if d < int(0.05 * 1e6):
            continue
        pa = (x + int(va[0] * d), y + int(va[1] * d))
        pb = (x + int(vb[0] * d), y + int(vb[1] * d))
        _move(a, x, y, pa)
        _move(b, x, y, pb)
        seg = pcbnew.PCB_TRACK(board)
        seg.SetStart(pcbnew.VECTOR2I(*pa))
        seg.SetEnd(pcbnew.VECTOR2I(*pb))
        seg.SetWidth(a.GetWidth())
        seg.SetLayer(layer)
        seg.SetNetCode(net)
        board.Add(seg)
        made += 1
    return made

def _touches(tr, x, y):
    return (tr.GetStart().x, tr.GetStart().y) == (x, y) or \
           (tr.GetEnd().x, tr.GetEnd().y) == (x, y)

def _away(tr, x, y):
    """Unit vector from the corner along the track, and the track's length."""
    s, e = tr.GetStart(), tr.GetEnd()
    far = e if (s.x, s.y) == (x, y) else s
    dx, dy = far.x - x, far.y - y
    n = (dx * dx + dy * dy) ** 0.5
    return ((dx / n, dy / n), int(n)) if n else (None, 0)

def _move(tr, x, y, to):
    if (tr.GetStart().x, tr.GetStart().y) == (x, y):
        tr.SetStart(pcbnew.VECTOR2I(*to))
    else:
        tr.SetEnd(pcbnew.VECTOR2I(*to))

def angles(board):
    """Every track segment's direction, bucketed.

    The brief was 45 degree bends and no stair-stepped right angles, and the
    only way to know is to measure what came back. Arcs are counted separately;
    they are not stair steps either way.
    """
    from collections import Counter
    c = Counter()
    for t in board.GetTracks():
        if t.Type() != pcbnew.PCB_TRACE_T:
            continue
        dx = t.GetEnd().x - t.GetStart().x
        dy = t.GetEnd().y - t.GetStart().y
        if dx == 0 and dy == 0:
            c["zero length"] += 1
        elif dx == 0 or dy == 0:
            c["orthogonal"] += 1
        elif abs(abs(dx) - abs(dy)) <= 2:      # 2 nm of rounding
            c["45 degree"] += 1
        else:
            c["other"] += 1
    return c

def stair_steps(board):
    """Right angles between two tracks, split into corners and tees.

    A corner -- exactly two segments, one horizontal, one vertical -- is the
    shape the brief rules out, and chamfer() removes every one it can reach.
    A tee is three segments: a run going straight through and a branch leaving
    it at 90 degrees. That is a junction, not a stair step, and cutting it
    would move the branch off the run. So is a right angle AT A DRILL -- one
    track arriving at a via and another leaving it -- which is why chamfer()
    will not cut those either: there is no cusp of copper to cut, only a
    round pad with two tracks on it, and taking 0.4 mm off both would leave
    the via holding nothing.
    """
    from collections import defaultdict
    ends = defaultdict(list)
    for t in board.GetTracks():
        if t.Type() != pcbnew.PCB_TRACE_T:
            continue
        for p in (t.GetStart(), t.GetEnd()):
            ends[(t.GetLayer(), p.x, p.y)].append(t)
    holes = {(v.GetPosition().x, v.GetPosition().y)
             for v in board.GetTracks() if v.Type() == pcbnew.PCB_VIA_T}
    for f in board.GetFootprints():
        for pad in f.Pads():
            holes.add((pad.GetPosition().x, pad.GetPosition().y))
    corners = tees = 0
    for (lay, x, y), ts in ends.items():
        ort = [t for t in ts
               if (t.GetEnd().x - t.GetStart().x) == 0
               or (t.GetEnd().y - t.GetStart().y) == 0]
        h = [bool(t.GetEnd().x - t.GetStart().x) for t in ort]
        if not (True in h and False in h):
            continue
        if len(ts) == 2 and (x, y) not in holes:
            corners += 1
        else:
            tees += 1
    return corners, tees

def under_the_ring(board):
    """Foreign copper on F.Cu where the heatsink ring clamps.

    The ring lands on bare copper at R 29.2 .. 31.5 -- that contact is the
    20 A rating -- and it is aluminium. Anything but ground under it is a
    short waiting for the screws to be tightened. The floorplan keeps parts
    out; nothing kept the router out, so this looks.
    """
    import math
    import geometry as G
    c = board.GetBoardEdgesBoundingBox().GetCenter()

    def in_land(pt):
        x, y = (pt.x - c.x) / 1e6, (pt.y - c.y) / 1e6
        r = math.hypot(x, y)
        if not PL.R_RING_ID <= r <= PL.R_RING_OD:
            return False
        # ... and inside one of the three sectors. Radius alone is not the
        # land: the same annulus over a utility wedge is ordinary copper, and
        # checking only the radius reports eight violations that are not.
        a = math.degrees(math.atan2(-y, x)) % 360
        return any(abs((a - th + 180) % 360 - 180) <= PL.RING_HALF
                   for th in G.PHASE_ANG)

    bad = []
    for tr in board.GetTracks():
        if tr.GetNetname() == "GND":
            continue
        if tr.Type() == pcbnew.PCB_VIA_T:
            if in_land(tr.GetPosition()):
                bad.append((tr.GetNetname(), "via"))
        elif tr.IsOnLayer(pcbnew.F_Cu):
            if any(in_land(p) for p in (tr.GetStart(), tr.GetEnd())):
                bad.append((tr.GetNetname(), "track"))
    return bad

def normalise_vias(board, pro):
    """Give every imported via the drill its pad size was drawn for.

    KiCad's SES import takes a via's diameter from the Specctra padstack and
    its drill from the NETCLASS, which are not the same thing: the stitching
    vias on the 60 V nets are 0.46/0.20 where the pad is too small for the
    0.8/0.40 the Phase class asks for, and they came back 0.46/0.40 -- a
    0.03 mm annular ring, fifteen of them. The diameter is the honest half of
    the pair, so the drill is rebuilt from it.
    """
    import json
    j = json.loads(Path(pro).read_text())
    ds = j["board"]["design_settings"]
    table = {stitch.mm(v["diameter"]): stitch.mm(v["drill"])
             for v in ds["via_dimensions"] if v["diameter"]}
    r = ds["rules"]
    hole = stitch.mm(r["min_through_hole_diameter"])
    table[max(stitch.mm(r["min_via_diameter"]),
              hole + 2 * stitch.mm(r["min_via_annular_width"]))] = hole
    n = 0
    for v in board.GetTracks():
        if v.Type() != pcbnew.PCB_VIA_T:
            continue
        want = table.get(v.GetWidth(pcbnew.F_Cu))
        if want is not None and v.GetDrill() != want:
            v.SetDrill(want)
            n += 1
    return n

def stats(board):
    board.BuildConnectivity()
    tracks = [t for t in board.GetTracks()]
    seg = sum(1 for t in tracks if t.Type() == pcbnew.PCB_TRACE_T)
    arcs = sum(1 for t in tracks if t.Type() == pcbnew.PCB_ARC_T)
    vias = sum(1 for t in tracks if t.Type() == pcbnew.PCB_VIA_T)
    return dict(segments=seg, arcs=arcs, vias=vias,
                unconnected=board.GetConnectivity().GetUnconnectedCount(True))

def drc(path, report):
    r = subprocess.run(["kicad-cli", "pcb", "drc", "--format", "json",
                        "-o", str(report), str(path)],
                       capture_output=True, text=True, cwd=str(path.parent))
    return r.stdout.strip()

def summarise(report):
    """What DRC found, by kind, and what is still unconnected, by net."""
    import json
    from collections import Counter
    d = json.loads(Path(report).read_text())
    kinds = Counter(v["type"] for v in d["violations"])
    nets = Counter()
    for it in d["unconnected_items"]:
        m = re.search(r"\[([^\]]*)\]", it["items"][0]["description"])
        nets[m.group(1) if m else "?"] += 1
    return kinds, nets, d["violations"]

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--passes", type=int, default=20,
                    help="freerouting optimiser passes")
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--skip-route", action="store_true",
                    help="reuse the existing .ses")
    ap.add_argument("--polish", action="store_true",
                    help="no routing: just clean up and re-check the board as it "
                         "stands -- slivers, via drills, stitching, chamfers, fill")
    ap.add_argument("--export-only", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--rounds", type=int, default=1,
                    help="export/route/import cycles; each starts from the last "
                         "result, which is how the tail of the board gets done")
    ap.add_argument("--no-maze", action="store_true",
                    help="do not run tools/finish.py before and after the router")
    ap.add_argument("--board", choices=sorted(BOARDS), default="a",
                    help="a: board A (motor_board); s: board S (single_board)")
    ap.add_argument("--loose-first", action="store_true",
                    help="the first round loose too: for a board that is already routed "
                         "and has a few connections left")
    ap.add_argument("--first", default="",
                    help="comma-separated nets for the maze to make before the router "
                         "(HARD_NETS for this run)")
    ap.add_argument("--pcb", help="route this copy of the board instead (its project "
                                  "file beside it, work in .route/ beside it; no plots)")
    ap.add_argument("--loose", action="store_true",
                    help="rounds after the first leave the router's own earlier "
                         "wires in its hands (only the tools' copper is protected), "
                         "so the last nets can have their neighbours moved")
    args = ap.parse_args()
    select(args.board)
    if args.pcb:
        global BOARD, WORK
        BOARD = Path(args.pcb).resolve()
        WORK = BOARD.parent / ".route"

    WORK.mkdir(exist_ok=True)
    if args.export_only:
        dsn = WORK / f"{BOARD.stem}.dsn"
        # Every net that is already copper end to end goes the way of the
        # 60 V nets: the gate nets (an arc and a via each, fanout.gates),
        # SNSP (an arc, fanout.sense), SNSN where its In3 links fit. A net
        # the router has nothing to do on is one it cannot rip up, tee into
        # or, as it did with a protected polyline joined twice, normalise
        # for ever.
        loose = bool(os.environ.get("SERVODRIVE_LOOSE"))
        done = complete_nets(BOARD, loose)
        dead = DEAD_NETS + tuple(done)
        (WORK / "dead.json").write_text(json.dumps(dead))
        n, prot = export_dsn(BOARD, dsn, loose, dead)
        fix = prepare_dsn(dsn, dead=dead)
        fix["nets complete before routing"] = len(done)
        fix["round"] = "loose" if loose else "strict"
        print(f"{os.path.relpath(dsn, ROOT)}  ({dsn.stat().st_size//1024} kB, "
              f"{n} space-filling pours removed, {prot} items protected, "
              + ", ".join(f"{v} {k}" for k, v in fix.items()) + " dropped)")
        sys.stdout.flush()
        os._exit(0)
    if args.polish:
        polish(BOARD)
        sys.stdout.flush()
        os._exit(0)
    # The maze goes first. tools/finish.py routes what it can on a board
    # that is still only the fan-out's copper, which is when there is room:
    # it makes about four fifths of the nets, deterministically, at 45
    # degrees, every segment checked against the same clearance test the
    # fan-out uses. Freerouting then has a smaller problem, and one where
    # everything already made is locked and out of its way.
    first = tuple(n for n in args.first.split(",") if n) or HARD_NETS
    if not args.no_maze and first:
        print("maze:", flush=True)
        made, left = maze(BOARD, first)
        print(f"  {len(made)} of the {len(first)} nets made before the router"
              + ("" if not left else f", {len(left)} not: " + ", ".join(left)), flush=True)

    best, keep = None, WORK / "best.kicad_pcb"
    # SERVODRIVE_SNAP set from outside wins every round; otherwise the
    # rounds take ROUND_SNAP in turn (the last entry repeats).
    snap_override = os.environ.get("SERVODRIVE_SNAP")
    for r in range(args.rounds):
        if args.rounds > 1:
            print(f"=== round {r + 1} of {args.rounds} ===", flush=True)
        os.environ["SERVODRIVE_SNAP"] = snap_override or ROUND_SNAP[min(r, len(ROUND_SNAP) - 1)]
        if (args.loose and r > 0) or args.loose_first:
            os.environ["SERVODRIVE_LOOSE"] = "1"
            print("  (loose: the router's earlier wires are its to move)", flush=True)
        left = one_round(args)
        args.skip_route = False
        # A round can come back worse: freerouting rips up a net it cannot
        # finish, and what it finds the second time is not always better than
        # what it had. Keep the best board rather than the last one.
        if best is None or left < best:
            best = left
            shutil.copy(BOARD, keep)
        elif left > best:
            print(f"  worse than round's best ({left} vs {best}) -- keeping the best")
            shutil.copy(keep, BOARD)
        if left == 0:
            print("  nothing left to route")
            break
    if not args.no_maze:
        # ... and last: what the router leaves is boxed in by the router's
        # own wires, and the maze may take them out of the way and put them
        # back (finish.rip_and_join).
        if keep.exists():
            shutil.copy(keep, BOARD)
        print("maze:", flush=True)
        made, left = maze(BOARD)
        print(f"  {len(made)} nets joined after the router, {len(left)} left"
              + ("" if not left else ": " + ", ".join(left)), flush=True)
        if made:
            settle(BOARD)
    # The page's copper viewer stacks one plot per layer; re-make them here so
    # what index.html shows is the board that was just routed.
    if not args.pcb:
        print("plots:", flush=True)
        plot_layers.run(BOARD_KEY)
    sys.stdout.flush()
    # pcbnew's SWIG teardown segfaults on a board this size after the work is
    # done and saved, which turns a good run into a non-zero exit. Leave now.
    os._exit(0)

def maze(path, only=None):
    """tools/finish.py on whatever KiCad's DRC says is still in pieces --
    or, before the router runs, on the nets in `only` that are."""
    board = pcbnew.LoadBoard(str(path))
    rpt = WORK / "pre.json"
    drc(path, rpt)
    d = json.loads(rpt.read_text())
    nets = []
    for it in d.get("unconnected_items", ()):
        m = re.search(r"\[([^\]]*)\]", it["items"][0]["description"])
        if m and m.group(1) not in nets and (only is None or m.group(1) in only):
            nets.append(m.group(1))
    if not nets:
        return [], []
    made, left = finish.finish(board, nets, verbose=True)
    fill(board)
    save(board, path)
    return made, left

def settle(path):
    """The repairs that follow any change to the copper: unused vias and the
    stubs that led to them, drills too close, the pours filled again."""
    rpt = WORK / "drc.json"
    drc(path, rpt)
    kinds, nets, viol = summarise(rpt)
    if kinds.get("via_dangling") or kinds.get("track_dangling"):
        board = pcbnew.LoadBoard(str(path))
        gone = drop_dangling_vias(board, rpt)
        gone_t = drop_dangling(board)
        fill(board)
        save(board, path)
        print(f"  dangling: {gone} unused vias and {gone_t} track ends removed")
        drc(path, rpt)
        kinds, nets, viol = summarise(rpt)
    if kinds.get("hole_to_hole"):
        board = pcbnew.LoadBoard(str(path))
        shrunk, moved = spread_holes(board, rpt, Path(path).with_suffix(".kicad_pro"))
        fill(board)
        save(board, path)
        print(f"  hole-to-hole: {shrunk} drills narrowed, {moved} vias nudged")
        drc(path, rpt)
        kinds, nets, viol = summarise(rpt)
    board = pcbnew.LoadBoard(str(path))
    # the maze router lays 45s, but a corner where its route meets one of
    # the router's can still be square
    c = chamfer(board)
    if c:
        fill(board)
        save(board, path)
        print(f"  {c} right angles chamfered")
    s = stats(board)
    print(f"  {s['segments']} track segments, {s['vias']} vias, {s['unconnected']} unconnected")
    corners, tees = stair_steps(board)
    a = angles(board)
    print("  angles: " + ", ".join(f"{k} {v}" for k, v in a.most_common())
          + f"; right-angle corners {corners}, tees {tees}")
    ring = under_the_ring(board)
    print(f"  heatsink land: {'clear' if not ring else str(len(ring)) + ' items of foreign copper'}")
    if kinds:
        print("  violations: " + ", ".join(f"{k} {v}" for k, v in kinds.most_common()))
    if nets:
        print("  still unconnected, by net: "
              + ", ".join(f"{k} {v}" for k, v in nets.most_common(18)))
    return sum(nets.values())

def polish(path):
    """Everything the import does except the import, then fix what DRC finds."""
    board = pcbnew.LoadBoard(str(path))
    pro = Path(path).with_suffix(".kicad_pro")
    print("polish:")
    rpt = WORK / "drc.json"
    shrunk = moved = 0
    if rpt.exists():
        shrunk, moved = spread_holes(board, rpt, pro)
    print(f"  {normalise_vias(board, pro)} via drills corrected, "
          f"{drop_slivers(board)} degenerate segments dropped, "
          f"{shrunk} drills narrowed, {moved} vias nudged")
    print(f"  {stitch.stitch(board, verbose=False)[0]} stitching vias added, "
          f"{chamfer(board)} right angles chamfered")
    stitch.tie_islands(board)
    fill(board)
    save(board, path)
    s = stats(board)
    print(f"  {s['segments']} track segments, {s['vias']} vias, "
          f"{s['unconnected']} unconnected")
    ring = under_the_ring(board)
    print(f"  heatsink land: {'clear' if not ring else ring}")
    a = angles(board)
    corners, tees = stair_steps(board)
    print("  angles: " + ", ".join(f"{k} {v}" for k, v in a.most_common())
          + f"; right-angle corners {corners}, tees {tees}")
    rpt = WORK / "drc.json"
    print("drc:")
    for line in drc(path, rpt).splitlines():
        print("  " + line)
    kinds, nets, viol = summarise(rpt)
    if kinds:
        print("  violations: " + ", ".join(f"{k} {v}" for k, v in kinds.most_common()))
        seen = set()
        for v in viol:
            if v["type"] in seen:
                continue
            seen.add(v["type"])
            print("    e.g. " + v["description"][:100])
    if nets:
        print("  still unconnected, by net: "
              + ", ".join(f"{k} {v}" for k, v in nets.most_common(20)))
    return sum(nets.values())

def one_round(args):
    dsn, ses = WORK / f"{BOARD.stem}.dsn", WORK / f"{BOARD.stem}.ses"

    if not args.skip_route:
        print("export:")
        # In a child process: pcbnew is not itself again after
        # ExportSpecctraDSN -- touching a pad afterwards segfaults, and so,
        # later in the same process, did the import. The child exports,
        # prepares, prints its summary and exits.
        r = subprocess.run([sys.executable, __file__, "--export-only", "--board", BOARD_KEY]
                           + (["--pcb", str(BOARD)] if args.pcb else []),
                           capture_output=True, text=True)
        for line in r.stdout.splitlines():
            print("  " + line)
        if r.returncode != 0 or not dsn.exists():
            print(r.stderr[-2000:])
            raise SystemExit("export failed")
        print("route:")
        run_freerouting(dsn, ses, args.passes, args.timeout)
    if not ses.exists():
        raise SystemExit("no session file to import")

    print("import:")
    board, restored, cut, bad, slivers, copies = import_ses(BOARD, ses, BOARD)
    print(f"  {restored} stitching vias restored, {copies} session copies of them dropped, "
          f"{cut} right angles chamfered, {bad} via drills corrected, "
          f"{slivers} degenerate segments dropped")
    s = stats(board)
    print(f"  {s['segments']} track segments, {s['arcs']} arcs, {s['vias']} vias, "
          f"{s['unconnected']} unconnected")
    ring = under_the_ring(board)
    print(f"  heatsink land: {'clear' if not ring else str(len(ring)) + ' items of foreign copper ' + str(sorted(set(ring))[:6])}")
    a = angles(board)
    corners, tees = stair_steps(board)
    print("  angles: " + ", ".join(f"{k} {v}" for k, v in a.most_common())
          + f"; right-angle corners {corners}, tees {tees}")
    print("drc:")
    rpt = WORK / "drc.json"
    for line in drc(BOARD, rpt).splitlines():
        print("  " + line)
    kinds, nets, viol = summarise(rpt)
    if kinds.get("via_dangling") or kinds.get("track_dangling"):
        # A via the router did not use -- a pad's escape when the router
        # found its own way to the pad -- is copper on one layer, and KiCad
        # is the judge of that. It goes, then the stub that led to it.
        board = pcbnew.LoadBoard(str(BOARD))
        gone = drop_dangling_vias(board, rpt)
        gone_t = drop_dangling(board)
        fill(board)
        save(board, BOARD)
        print(f"  dangling: {gone} unused vias and {gone_t} track ends removed")
        for line in drc(BOARD, rpt).splitlines():
            print("  " + line)
        kinds, nets, viol = summarise(rpt)
    if kinds.get("hole_to_hole"):
        # Specctra cannot say hole-to-hole and the router lands a few drills
        # 20-40 um too close; narrow a drill or nudge a via, then look again.
        board = pcbnew.LoadBoard(str(BOARD))
        shrunk, moved = spread_holes(board, rpt, Path(BOARD).with_suffix(".kicad_pro"))
        fill(board)
        save(board, BOARD)
        print(f"  hole-to-hole: {shrunk} drills narrowed, {moved} vias nudged")
        for line in drc(BOARD, rpt).splitlines():
            print("  " + line)
        kinds, nets, viol = summarise(rpt)
    if kinds:
        print("  violations: " + ", ".join(f"{k} {v}" for k, v in kinds.most_common()))
        seen = set()
        for v in viol:
            if v["type"] in seen:
                continue
            seen.add(v["type"])
            print("    e.g. " + v["description"][:100])
            for i in v["items"]:
                print("         " + i["description"][:90])
    if nets:
        print("  still unconnected, by net: "
              + ", ".join(f"{k} {v}" for k, v in nets.most_common(18)))
    return sum(nets.values())

if __name__ == "__main__":
    main()
