"""deadcheck.py -- every pad on a net that route.py demotes must already touch
copper of its own net: a via in it, a pour of its net on its layer, or a
through-hole pad of its net under it. Anything else would be a connection the
router is not allowed to make."""
import sys
import pcbnew

DEAD = ("VBUS", "SW_A", "SW_B", "SW_C", "PHASE_A", "PHASE_B", "PHASE_C",
        "SNUB_A", "SNUB_B", "SNUB_C", "+3V3")

def check(board, dead=DEAD):
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    vias = {}
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            vias.setdefault(t.GetNetCode(), []).append(t)
    tracks = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_TRACE_T]
    zones = {}
    for z in board.Zones():
        for l in z.GetLayerSet().CuStack():
            poly = z.GetFilledPolysList(l)
            if poly.OutlineCount():
                zones.setdefault(z.GetNetCode(), []).append((l, poly))
    bad = []
    for f in board.GetFootprints():
        for p in f.Pads():
            if p.GetNetname() not in dead or p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
                continue
            code = p.GetNetCode()
            if any(p.HitTest(v.GetPosition()) for v in vias.get(code, ())):
                continue
            if any(t.GetNetCode() == code and (p.HitTest(t.GetStart()) or p.HitTest(t.GetEnd()))
                   for t in tracks):
                continue
            own = set(p.GetLayerSet().CuStack())
            if any(l in own and poly.Collide(p.GetEffectiveShape(l), 0)
                   for l, poly in zones.get(code, ())):
                continue
            if any(q.GetNetCode() == code and q is not p and q.GetAttribute() == pcbnew.PAD_ATTRIB_PTH
                   and p.HitTest(q.GetPosition()) for q in f.Pads()):
                continue
            bad.append(f"{f.GetReference()}-{p.GetNumber()} {p.GetNetname()}")
    return bad

if __name__ == "__main__":
    b = pcbnew.LoadBoard(sys.argv[1])
    bad = check(b)
    print(f"deadcheck: {len(bad)} pads on demoted nets without copper of their own net")
    for x in bad: print("  ", x)
