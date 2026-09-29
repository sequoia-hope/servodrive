"""Targeted rip-up: find the cheapest way for one open net if the router's own
(unlocked) copper could be moved, rip exactly the nets on that way, make the
open net, then make the ripped nets again. Kept only if KiCad's unconnected
count drops (or, with KEEP_EQUAL set, if it stays the same: a trade, so the
net it opened can be tried in turn).

    python3 tools/targetrip.py tools BOARD NET OUT [penalty ...]

Run it on a copy with its .kicad_pro beside it (the net classes come from
there). It closed board S's last net twice: ENC_OUT with GATE_OFF on GPIO2
(2026-09-26), and EXP_GP24, HIN_A and SWCLK with six bulk cans (2026-09-29).
"""
import sys
import heapq
import math

TOOLS, PATH, NET, OUT = sys.argv[1:5]
PENALTIES = [float(p) for p in sys.argv[5:]] or [3.0, 10.0, 40.0]
sys.path.insert(0, TOOLS)
import numpy as np
import pcbnew
import finish as F

MM = 1e6


def unconnected(board):
    board.BuildConnectivity()
    return board.GetConnectivity().GetUnconnectedCount(True)


def soft_grid(board, code, w):
    """The grid with every other net's unlocked copper taken away."""
    snap = F._state(board)
    for t in list(board.GetTracks()):
        if not t.IsLocked() and t.GetNetCode() not in (0, code):
            board.Delete(t)
    g = F.Grid(board, code, width=w)
    F._rollback(board, snap)
    return g


def astar(soft, hard, starts, goals, P):
    L = F.LAYERS
    li = {l: k for k, l in enumerate(L)}
    W, H = soft.w, soft.h
    free = np.stack([soft.free[l] for l in L])
    blocked = np.stack([soft.free[l] & ~hard.free[l] for l in L])
    pen = (1.0 + P * blocked).astype(np.float32)
    via_ok = soft.via_ok
    via_pen = ~hard.via_ok
    tgt = np.ones((W, H), bool)
    for _, ix, iy in goals:
        tgt[ix, iy] = False
    from scipy import ndimage
    hf = ndimage.distance_transform_edt(tgt).astype(np.float32)
    goalset = {(li[l], ix, iy) for l, ix, iy in goals}
    g = np.full((len(L), W, H), np.inf, np.float32)
    came = np.full((len(L), W, H), -1, np.int64)
    heap = []
    for l, ix, iy in starts:
        g[li[l], ix, iy] = 0
        heapq.heappush(heap, (float(hf[ix, iy]), li[l], ix, iy))
    seen = 0
    while heap:
        f, k, ix, iy = heapq.heappop(heap)
        seen += 1
        if seen > 6_000_000:
            return None
        gc = g[k, ix, iy]
        if f > gc + hf[ix, iy] + 1e-6:
            continue
        if (k, ix, iy) in goalset:
            out, cur = [], (k, ix, iy)
            while cur is not None:
                out.append((L[cur[0]], cur[1], cur[2]))
                p = came[cur]
                cur = None if p < 0 else (int(p // (W * H)), int(p // H % W), int(p % H))
            return out[::-1]
        for dx, dy, cost in F.NEIGHBOURS:
            jx, jy = ix + dx, iy + dy
            if not (0 <= jx < W and 0 <= jy < H) or not free[k, jx, jy]:
                continue
            if dx and dy and not (free[k, ix, jy] or free[k, jx, iy]):
                continue
            ng = gc + cost * pen[k, jx, jy]
            if ng < g[k, jx, jy]:
                g[k, jx, jy] = ng
                came[k, jx, jy] = (k * W + ix) * H + iy
                heapq.heappush(heap, (float(ng + hf[jx, jy]), k, jx, jy))
        if via_ok[ix, iy]:
            for k2 in range(len(L)):
                if k2 == k or not free[k2, ix, iy]:
                    continue
                ng = gc + F.VIA_COST * (1 + (P if via_pen[ix, iy] else 0))
                if ng < g[k2, ix, iy]:
                    g[k2, ix, iy] = ng
                    came[k2, ix, iy] = (k * W + ix) * H + iy
                    heapq.heappush(heap, (float(ng + hf[ix, iy]), k2, ix, iy))
    return None


def blockers(board, soft, hard, path, code):
    """The nets whose unlocked copper sits on the path's blocked cells."""
    pts = []
    for n, (l, ix, iy) in enumerate(path):
        if not hard.free[l][ix, iy] and n % 2 == 0:
            pts.append((l, soft.point(ix, iy)))
    nets = set()
    reach = int(0.35 * MM)
    for t in board.GetTracks():
        if t.IsLocked() or t.GetNetCode() in (0, code):
            continue
        bb = t.GetBoundingBox()
        bb.Inflate(reach)
        for l, p in pts:
            if (t.Type() == pcbnew.PCB_VIA_T or t.GetLayer() == l) and bb.Contains(p):
                sh = t.GetEffectiveShape(l if t.Type() != pcbnew.PCB_VIA_T else pcbnew.F_Cu)
                if sh.Collide(p, reach):
                    nets.add(t.GetNetCode())
                    break
    return nets


board = pcbnew.LoadBoard(PATH)
code = board.FindNet(NET).GetNetCode()
was = unconnected(board)
print(f"{NET}: {len(F.pieces(board, code))} pieces; board {was} unconnected", flush=True)
best = (was, None)
tried = set()
for P in PENALTIES:
    for w in (0.15,):
        hard = F.Grid(board, code, width=w)
        soft = soft_grid(board, code, w)
        groups = F.pieces(board, code)
        a, b = F._closest(groups)
        starts = [(l, *soft.cell(p)) for it in a for p, l in F.anchors(it)
                  for l in (F.LAYERS if l is None else [l]) if l in F.LAYERS]
        goals = [(l, *soft.cell(p)) for it in b for p, l in F.anchors(it)
                 for l in (F.LAYERS if l is None else [l]) if l in F.LAYERS]
        path = astar(soft, hard, starts, goals, P)
        if path is None:
            print(f"  P={P}: no path even with the router's copper out of the way")
            continue
        hurt = blockers(board, soft, hard, path, code)
        names = sorted(board.FindNet(c).GetNetname() for c in hurt)
        key = tuple(names)
        print(f"  P={P}: path {len(path)} cells, crosses {len(hurt)} nets: {', '.join(names)}",
              flush=True)
        if key in tried:
            continue
        tried.add(key)
        snap = F._state(board)
        for t in list(board.GetTracks()):
            if not t.IsLocked() and t.GetNetCode() in hurt:
                board.Delete(t)
        ok = F.join_net(board, code)
        missed = []
        if ok:
            for c in sorted(hurt, key=lambda c: -F._span(board, c)):
                if not (F.join_net(board, c) or F.rip_and_join(board, c, False, 1)):
                    missed.append(board.FindNet(c).GetNetname())
        now = unconnected(board)
        print(f"    {NET} {'made' if ok else 'NOT made'}; not re-made: {missed or 'none'}; "
              f"{was} -> {now} unconnected", flush=True)
        # KEEP_EQUAL: a trade -- this net made, another one open -- is kept
        # when nothing better turns up, so the next net can be tried in turn
        import os
        if ok and (now < best[0] or (os.environ.get("KEEP_EQUAL") and best[1] is None
                                     and now == best[0])):
            best = (now, F._state(board))
        F._rollback(board, snap)
if best[1] is not None:
    F._rollback(board, best[1])
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(OUT, board)
    print(f"saved {OUT}: {best[0]} unconnected")
else:
    print("nothing better")
sys.stdout.flush()
import os
os._exit(0)
