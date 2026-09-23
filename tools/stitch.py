#!/usr/bin/env python3
"""stitch.py — drop a via in every pad that sits over its own plane.

Board A carries its power on copper, not on tracks: GND is two solid inner
planes, VBUS is a sector of a third, and each switch node and phase output is a
pour on F.Cu and B.Cu. What that leaves is 200-odd pads whose whole "route" is
one hole straight down to the plane already underneath them.

Handing that to an autorouter is a waste of both our time and its. It has to
discover, for each of 112 GND pads, that the answer is a via at the pad centre,
and it does it badly: the first headless run left 117 connections unrouted, most
of them exactly this. Worse, the connection it does find is often a stub of
trace to a via a millimetre away, which is a longer return path than the via in
the pad, for no reason.

So this runs first. For every SMD pad whose net has a filled zone on some other
layer directly beneath it, put a via in the pad -- via-in-pad, which JLCPCB
fills and plates over for free on six layers (F-48), and which is why the pad
centre is allowed to be a hole at all. Afterwards the DSN handed to the router
has GND, VBUS, SW_x and PHASE_x already connected, and freerouting is left with
the signals, which is the part it is good at.

Rules obeyed, all of them the project's existing ones:

  - the via's pad fits inside the SMD pad it sits in;
  - hole-to-hole >= 0.5 mm to every other hole on the board, POFV's floor;
  - the drill clears foreign copper by min_hole_clearance, and the via's PAD
    clears it by the wider of the two nets' netclass clearances.

That last one is the whole trick, and the first version of this got it wrong:
a via is a THROUGH via, so its annulus exists on all six layers, not just the
one the pad is on. Fitting inside an F.Cu pad says nothing about what the same
copper does on B.Cu. Checking only the drill put 33 clearance violations on the
board -- a 0.5 mm via pad reaches 0.25 mm out, further than a 0.2 mm drill.

A pad that fails any of it is left alone and reported, and the router picks it
up as an ordinary connection. On board A that puts 93 vias in and leaves 77
pads out: 37 are not over a plane of their own net, 24 have something on the
far face in the way, 13 are QFN pins too small to hold a hole, and 3 are inside
the hole-to-hole rule.
"""
import argparse
import json
import math
from collections import Counter
from pathlib import Path

import pcbnew

MM = 1e6
H2H = 0.52         # hole edge to hole edge, mm. The rule is 0.5 (JLCPCB
                   # POFV floor); the extra 20 um is because the pad
                   # positions are rounded to the micron on the way
                   # through Specctra and a via placed at exactly 0.5
                   # comes back at 0.4963.
HOLE_CU = 0.25     # drill to foreign copper, mm
FIT = 0.0          # extra margin the via pad must keep inside the SMD pad, mm

def mm(v):  return int(round(v * MM))
def zone_index(board):
    """{layer: [(netcode, filled polygons), ...]} for every filled zone."""
    idx = {}
    for z in board.Zones():
        if z.GetIsRuleArea():
            continue
        for l in z.GetLayerSet().CuStack():
            if not board.GetEnabledLayers().Contains(l):
                continue
            poly = z.GetFilledPolysList(l)
            if poly.OutlineCount():
                idx.setdefault(l, []).append((z.GetNetCode(), poly, z.GetZoneName()))
    return idx

def already(board):
    """Via positions per net, so a second run is a no-op.

    This is run again after the router comes back, to put back anything the
    Specctra round trip dropped; without this it would instead find a second
    legal spot in every pad big enough for two.
    """
    out = {}
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            out.setdefault(t.GetNetCode(), []).append(t.GetPosition())
    return out

def holes(board):
    """Every existing hole: (x, y, drill radius) in nm."""
    out = []
    for f in board.GetFootprints():
        for p in f.Pads():
            if p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
                d = max(p.GetDrillSize().x, p.GetDrillSize().y)
                out.append((p.GetPosition().x, p.GetPosition().y, d // 2))
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            out.append((t.GetPosition().x, t.GetPosition().y, t.GetDrill() // 2))
    return out

def foreign_shapes(board, netcode, clear):
    """Everything not on `netcode`, paired with the clearance it demands.

    Tracks and vias count, not just pads: this runs a second time after the
    router comes back, to put back the vias the Specctra round trip drops, and
    by then there is copper on the board that was not there the first time.
    """
    def cl(item):
        net = item.GetNet()
        return clear.get(net.GetNetClassName() if net else "Default", mm(0.15))
    def cl_pad(p):
        # ... or more, if the footprint or the pad asks for it: TI's PowerPAD
        # footprint carries (clearance 0.2), and DRC holds a via to it
        own = max(p.GetLocalClearance() or 0, p.GetParentFootprint().GetLocalClearance() or 0)
        return max(cl(p), own + (mm(0.001) if own else 0))
    out = [(p, cl_pad(p)) for f in board.GetFootprints() for p in f.Pads()
           if p.GetNetCode() != netcode]
    out += [(t, cl(t)) for t in board.GetTracks() if t.GetNetCode() != netcode]
    return out

def fits_in_pad(pad, cx, cy, r, n=16):
    """Is a circle of radius r at (cx, cy) inside the pad?"""
    if not pad.HitTest(pcbnew.VECTOR2I(cx, cy)):
        return False
    for i in range(n):
        a = 2 * math.pi * i / n
        p = pcbnew.VECTOR2I(int(cx + r * math.cos(a)), int(cy + r * math.sin(a)))
        if not pad.HitTest(p):
            return False
    return True

def candidates(pad, r, step=0.1):
    """Every spot in the pad that will hold a circle of radius r, pad centre
    first and then outwards, so a via only moves as far as it has to."""
    pos, bb = pad.GetPosition(), pad.GetBoundingBox()
    out, s = [], mm(step)
    nx = max(0, (bb.GetWidth() // 2 - r) // s)
    ny = max(0, (bb.GetHeight() // 2 - r) // s)
    for i in range(-nx, nx + 1):
        for j in range(-ny, ny + 1):
            x, y = pos.x + i * s, pos.y + j * s
            out.append((i * i + j * j, x, y))
    out.sort()
    return [(x, y) for _, x, y in out if fits_in_pad(pad, x, y, r)]

def clear_of(pads, cx, cy, r_cu, mine, r_hole):
    """Is a via legal here?

    Two rules, and the second is the one that is easy to forget: the drill has
    to keep HOLE_CU from foreign copper, and the via's own PAD has to keep the
    netclass clearance from it. Checking only the hole is how the first run of
    this put 33 clearance violations on the board -- a 0.5 mm via pad reaches
    0.25 mm from the centre, which is further than a 0.2 mm drill does.

    Clearance is the wider of the two nets' classes, which is KiCad's rule.
    """
    v = pcbnew.VECTOR2I(cx, cy)
    for p, theirs in pads:
        need = max(r_cu + max(mine, theirs) + mm(0.005), r_hole + mm(HOLE_CU))
        bb = p.GetBoundingBox()
        bb.Inflate(need)
        if not bb.Contains(v):
            continue
        for l in p.GetLayerSet().CuStack():
            if p.GetEffectiveShape(l).Collide(v, need):
                return False
            break
    return True


def rules(board):
    """(via sizes, clearances, floor) per netclass, in nm, from the .kicad_pro.

    pcbnew's SWIG wrapper hands back NETCLASS as an opaque pointer, so these
    come from the project file instead -- the same file KiCad itself reads
    them from. The floor is the smallest via the board's own rules allow:
    a hole at the minimum, opened out until the annular ring is legal.
    """
    pro = Path(board.GetFileName()).with_suffix(".kicad_pro")
    j = json.loads(pro.read_text())
    via, clear = {}, {}
    for c in j["net_settings"]["classes"]:
        via[c["name"]] = (mm(c.get("via_diameter", 0.5)), mm(c.get("via_drill", 0.2)))
        clear[c["name"]] = mm(c.get("clearance", 0.15))
    r = j["board"]["design_settings"]["rules"]
    hole = mm(r["min_through_hole_diameter"])
    floor = (max(mm(r["min_via_diameter"]), hole + 2 * mm(r["min_via_annular_width"])),
             hole)
    return via, clear, floor

def netclass_via(sizes, net):
    return sizes.get(net.GetNetClassName(), (mm(0.5), mm(0.25)))

def stitch(board, verbose=True):
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    zi = zone_index(board)
    hs = holes(board)
    sizes, clear, floor = rules(board)
    have = already(board)
    made, skipped = Counter(), Counter()
    detail = []
    foreign = {}
    n_via = 0

    for f in board.GetFootprints():
        for pad in f.Pads():
            if pad.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
                continue
            net, code = pad.GetNet(), pad.GetNetCode()
            if not net or not net.GetNetname():
                continue
            # PAD::GetLayer() answers F.Cu for a pad on a flipped footprint;
            # only the layer set knows. Believing GetLayer() put 15 vias
            # between a B.Cu pad and a B.Cu pour, which DRC reports as
            # "connected on only one layer" and is exactly what it is.
            if any(pad.HitTest(v) for v in have.get(code, ())):
                continue                      # this pad is already stitched
            own = set(pad.GetLayerSet().CuStack())
            targets = [l for l, zs in zi.items()
                       if l not in own and any(nc == code for nc, _, _ in zs)]
            if not targets:
                continue

            if code not in foreign:
                foreign[code] = foreign_shapes(board, code, clear)
            mine = clear.get(net.GetNetClassName(), mm(0.15))
            why, spot = None, None
            # Netclass size first, then the board minimum; the pad centre
            # first, then outwards. A 1.15 x 2.7 mm DC-link pad has plenty of
            # room to move the hole clear of whatever sits on the other face,
            # and every one of these is a via that would otherwise be a
            # routed connection.
            for d, k in (netclass_via(sizes, net), floor):
                for cx, cy in candidates(pad, d // 2 + mm(FIT)):
                    v = pcbnew.VECTOR2I(cx, cy)
                    if not any(nc == code and poly.Contains(v)
                               for l in targets for nc, poly, _ in zi[l]):
                        why = "no plane"; continue
                    if any(math.hypot(hx - cx, hy - cy) < hr + k // 2 + mm(H2H)
                           for hx, hy, hr in hs):
                        why = "h2h"; continue
                    if not clear_of(foreign[code], cx, cy, d // 2, mine, k // 2):
                        why = "foreign"; continue
                    spot, dia, drill = (cx, cy), d, k
                    break
                if spot:
                    break
            if not spot:
                why = why or "small"
                skipped[{"no plane": "pad is not over its own plane",
                         "h2h": "hole-to-hole under 0.5 mm",
                         "foreign": "drill too near foreign copper",
                         "small": "pad too small for a via"}[why]] += 1
                detail.append((f.GetReference(), pad.GetNumber(),
                               net.GetNetname(), why))
                continue

            via = pcbnew.PCB_VIA(board)
            via.SetPosition(pcbnew.VECTOR2I(*spot))
            via.SetWidth(dia)
            via.SetDrill(drill)
            via.SetViaType(pcbnew.VIATYPE_THROUGH)
            via.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
            via.SetNetCode(code)
            via.SetLocked(True)         # tool-made: route.py protects it
            board.Add(via)
            hs.append((spot[0], spot[1], drill // 2))
            made[net.GetNetname()] += 1
            n_via += 1

    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    if verbose:
        print(f"stitch: {n_via} vias in pads")
        for n, c in made.most_common():
            print(f"    {n:12s} {c}")
        if skipped:
            print("  left to the router:")
            for n, c in skipped.most_common():
                print(f"    {n:30s} {c}")
    return n_via, detail

def tie_islands(board, verbose=True):
    """Put a via in every pour island that has no hole of its own.

    Routing cuts the F.Cu and B.Cu ground pours into islands. An island that
    touches a ground pad is connected to the net as far as the filler is
    concerned, but not to the planes, and KiCad reports it as a ratsnest
    between the two pours. Setting the zones to drop islands does not help --
    these are not orphans, they just have no via.
    """
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    sizes, clear, floor = rules(board)
    hs = holes(board)
    zi = zone_index(board)
    made = 0
    for z in board.Zones():
        if not z.GetZoneName().startswith("ground pour"):
            continue
        code = z.GetNetCode()
        net = z.GetNet()
        mine = clear.get(net.GetNetClassName(), mm(0.15))
        far = foreign_shapes(board, code, clear)
        own = set(z.GetLayerSet().CuStack())
        # Only a hole ON THIS NET ties an island to the planes. A via of some
        # other net passing through it is just a void.
        ours = [(v.GetPosition().x, v.GetPosition().y) for v in board.GetTracks()
                if v.Type() == pcbnew.PCB_VIA_T and v.GetNetCode() == code]
        ours += [(pad.GetPosition().x, pad.GetPosition().y)
                 for f in board.GetFootprints() for pad in f.Pads()
                 if pad.GetNetCode() == code
                 and pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH]
        for l in own:
            poly = z.GetFilledPolysList(l)
            for i in range(poly.OutlineCount()):
                one = pcbnew.SHAPE_POLY_SET()
                one.AddOutline(poly.Outline(i))
                if any(one.Contains(pcbnew.VECTOR2I(x, y)) for x, y in ours):
                    continue                      # already tied to the planes
                d, k = netclass_via(sizes, net)
                spot = _island_spot(one, board, zi, code, own, hs, far, mine, d, k)
                if spot is None:
                    d, k = floor
                    spot = _island_spot(one, board, zi, code, own, hs, far,
                                        mine, d, k)
                if spot is None:
                    continue
                via = pcbnew.PCB_VIA(board)
                via.SetPosition(pcbnew.VECTOR2I(*spot))
                via.SetWidth(d)
                via.SetDrill(k)
                via.SetViaType(pcbnew.VIATYPE_THROUGH)
                via.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
                via.SetNetCode(code)
                via.SetLocked(True)
                board.Add(via)
                hs.append((spot[0], spot[1], k // 2))
                made += 1
    if made:
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    if verbose:
        print(f"  {made} vias tying pour islands to the planes")
    return made

def _island_spot(one, board, zi, code, own, hs, far, mine, d, k, step=0.25):
    """A legal via position inside one filled island, or None."""
    bb = one.BBox()
    r = d // 2
    s = mm(step)
    x0, y0 = bb.GetX(), bb.GetY()
    nx = max(1, bb.GetWidth() // s)
    ny = max(1, bb.GetHeight() // s)
    if nx * ny > 40000:
        return None
    for i in range(1, int(nx)):
        for j in range(1, int(ny)):
            x, y = x0 + i * s, y0 + j * s
            v = pcbnew.VECTOR2I(x, y)
            if not one.Contains(v):
                continue
            # the whole annulus has to be inside the island
            if not all(one.Contains(pcbnew.VECTOR2I(
                    int(x + r * math.cos(a * math.pi / 4)),
                    int(y + r * math.sin(a * math.pi / 4)))) for a in range(8)):
                continue
            if not any(nc == code and poly.Contains(v)
                       for l, zs in zi.items() if l not in own
                       for nc, poly, _ in zs):
                continue
            if any(math.hypot(hx - x, hy - y) < hr + k // 2 + mm(H2H)
                   for hx, hy, hr in hs):
                continue
            if not clear_of(far, x, y, r, mine, k // 2):
                continue
            return (x, y)
    return None

def run(path):
    """Stitch a board file in place. Quiet about boards that have no zones."""
    board = pcbnew.LoadBoard(str(path))
    if not board.Zones():
        return 0
    n, _ = stitch(board, verbose=False)
    pcbnew.SaveBoard(str(path), board)
    board.BuildConnectivity()
    print(f"  stitch {path.name}: {n} vias in pads, "
          f"{board.GetConnectivity().GetUnconnectedCount(True)} connections left "
          f"for the router")
    return n

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("board")
    ap.add_argument("--detail", action="store_true")
    a = ap.parse_args()
    b = pcbnew.LoadBoard(a.board)
    n, detail = stitch(b)
    if a.detail:
        for d in sorted(detail): print('   ', ' '.join(d))
    pcbnew.SaveBoard(a.board, b)
    b.BuildConnectivity()
    print("unconnected:", b.GetConnectivity().GetUnconnectedCount(True))

if __name__ == "__main__":
    main()
