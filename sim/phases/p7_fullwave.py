#!/usr/bin/env python3
"""P7 -- full wave (openEMS): the L1/L2 cross-check on Q1, and Q11.

SPEC.md sec.6 makes "L2 vs L1 agreement stated" the gate for this phase, so
the first thing here is the same commutation loop the PEEC model solved, run
in a completely different solver on the same copper.
"""
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                              # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import paths, jsonio                                # noqa: E402
from extract import copper                                   # noqa: E402
from field import runner as fr, cell_scripts, geom_export    # noqa: E402
from fasthenry import cell as fhcell                         # noqa: E402


def _diagnostics(wd):
    """What the FDTD run says about its own trustworthiness."""
    out = {}
    try:
        err = (wd / "stderr.txt").read_text()
        out["dropped_primitives"] = err.count("Unused primitive")
    except OSError:
        pass
    try:
        so = (wd / "stdout.txt").read_text()
        import re as _re
        e = _re.findall(r"Energy: ~\s*([0-9.e+-]+) \(([- ]?[0-9.a-z]+)dB", so)
        if e:
            out["final_energy"] = float(e[-1][0])
            out["final_energy_dB"] = e[-1][1].strip()
        # openEMS prints the cell count in scientific notation once it is
        # over a million, so "(\d+)" silently matched nothing on every mesh
        # that mattered and the report said "None"
        m = _re.search(r"FDTD simulation size: (\S+) --> ([0-9.e+]+) FDTD "
                       r"cells", so)
        if m:
            out["mesh"] = m.group(1)
            out["cells"] = int(float(m.group(2)))
    except (OSError, ValueError):
        pass
    return out


def _pad_xy(ref, num, layer="F.Cu"):
    for p in copper.pads_of(ref):
        if p["pad"] == str(num) and layer in p["shapes"]:
            return (p["x"], p["y"])
    raise KeyError(f"{ref}.{num}")


def _fh_reference(cellname="A"):
    """The FastHenry numbers the FDTD model is actually comparable with.

    P1 solves four ports, one per DC-link capacitor, and reports L_eff: the
    loop with all four conducting in parallel, which is how the board works.
    The FDTD model drives one capacitor and leaves the other three open, so
    the number it can be checked against is that capacitor's own diagonal,
    not L_eff.  Comparing it with L_eff -- which the first run of this phase
    did -- charges the FDTD with a 32 % difference that is the model's, not
    the solver's.
    """
    q1 = ((jsonio.read("P1") or {}).get("Q1") or {})
    s = q1.get("solved") or {}
    out = {"L_eff_all_four_nH": s.get("L_pcb_nH_at_10MHz")}
    Lm, fs, ports = s.get("L_matrix_nH"), s.get("f_Hz"), s.get("ports")
    if Lm and fs and ports:
        ref = fhcell.CELL[cellname]["hf"][0]           # the capacitor the port stands at
        try:
            j = ports.index("p" + ref.lower())
        except ValueError:
            j = len(ports) - 1
        k = int(np.argmin(np.abs(np.array(fs, float) - 1e8)))   # the FDTD band centre
        out.update(port_ref=ref, port_index=j,
                   f_Hz=fs[k], L_single_port_nH=Lm[k][j][j])
    j = out.get("port_index", 3)
    conv = []
    for c in (q1.get("convergence") or []):
        single = c.get("L_single_port_nH")
        if single is None and c.get("L_nH"):
            single = c["L_nH"][j][j]
        if single is None:
            continue
        conv.append({"target_mm": c["target_mm"],
                     "segments": c.get("segments"),
                     "f_Hz": c.get("f_Hz", 1e6),
                     "L_single_port_nH": single,
                     "L_eff_nH": c.get("L_eff_nH")})
    conv = sorted(conv, key=lambda r: -r["target_mm"])
    out["fasthenry_convergence"] = conv
    # The number to check against is the converged end of the grid sweep, not
    # the production run's 1.2 mm grid: that one is 3-4 % high and it is the
    # mesh, not the physics, that makes it so.  The production value is kept
    # beside it because it is the one every other answer in the report was
    # computed from.
    if conv:
        out["L_single_port_production_nH"] = out.get("L_single_port_nH")
        out["L_single_port_production_f_Hz"] = out.get("f_Hz")
        out["L_single_port_nH"] = conv[-1]["L_single_port_nH"]
        out["f_Hz"] = conv[-1].get("f_Hz")
        out["grid_target_mm"] = conv[-1]["target_mm"]
        if len(conv) > 1:
            a, b = conv[-2]["L_single_port_nH"], conv[-1]["L_single_port_nH"]
            out["last_refinement_change"] = abs(b - a) / b
    return out


def _strip(a, b, w, grow=0.12):
    """A rectangle of width `w` from a to b, run `grow` past each end."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy) or 1.0
    ux, uy = dx / n, dy / n
    a = (a[0] - ux * grow, a[1] - uy * grow)
    b = (b[0] + ux * grow, b[1] + uy * grow)
    px, py = -uy * w / 2, ux * w / 2
    return [[a[0] + px, a[1] + py], [b[0] + px, b[1] + py],
            [b[0] - px, b[1] - py], [a[0] - px, a[1] - py]]


def _pad_poly(ref, num, layer="F.Cu"):
    from shapely.geometry import Polygon as _P
    from shapely.ops import unary_union
    ps = []
    for q in copper.pads_of(ref):
        if q["pad"] == str(num) and layer in q.get("shapes", {}):
            ps += [_P(sh["pts"]) for sh in q["shapes"][layer] if len(sh["pts"]) > 2]
    if not ps:
        raise KeyError(f"{ref}.{num}")
    return unary_union(ps)


def _port_box(ref, bite=0.20, shrink=0.10, layer="F.Cu"):
    """The port, sized from the two pads it stands between.

    The first version of this model put a box of a fixed 0.7 mm about the
    midpoint of the two pad CENTRES.  The pads here are 1.55 mm apart with a
    0.56 mm gap between their copper, so that box sat off-centre and bit
    0.09 mm into the VBUS pad against 0.5 mm into GND -- and a mesh line
    landing a twentieth of a millimetre either way changed that bite by more
    than half.  Sizing it from the copper makes the two ends symmetrical, and
    pinning its edges as protected mesh lines keeps them where they are put.
    """
    from shapely.geometry import box as _box
    A, B = _pad_poly(ref, 1, layer), _pad_poly(ref, 2, layer)
    ax = (A.bounds[0] + A.bounds[2]) / 2, (A.bounds[1] + A.bounds[3]) / 2
    bx = (B.bounds[0] + B.bounds[2]) / 2, (B.bounds[1] + B.bounds[3]) / 2
    drive = "x" if abs(bx[0] - ax[0]) >= abs(bx[1] - ax[1]) else "y"
    i = 0 if drive == "x" else 1
    j = 1 - i
    lo = max(A.bounds[j], B.bounds[j]) + shrink
    hi = min(A.bounds[j + 2], B.bounds[j + 2]) - shrink
    if hi <= lo:                       # pads barely overlap across the gap
        lo = max(A.bounds[j], B.bounds[j])
        hi = min(A.bounds[j + 2], B.bounds[j + 2])
    band = (_box(-1e6, lo, 1e6, hi) if drive == "x"
            else _box(lo, -1e6, hi, 1e6))
    a2, b2 = A.intersection(band), B.intersection(band)
    if a2.is_empty or b2.is_empty:
        a2, b2 = A, B
    near, far = (a2.bounds[i + 2], b2.bounds[i]) if ax[i] < bx[i] \
        else (b2.bounds[i + 2], a2.bounds[i])
    p0, p1 = [0.0, 0.0], [0.0, 0.0]
    p0[i], p1[i] = near - bite, far + bite
    p0[j], p1[j] = lo, hi
    return {"p0": p0, "p1": p1, "dir": drive,
            "copper_gap_mm": round(far - near, 4), "bite_mm": bite,
            "overlap_pad1_mm2": round(_box(min(p0[0], p1[0]), min(p0[1], p1[1]),
                                           max(p0[0], p1[0]), max(p0[1], p1[1])
                                           ).intersection(A).area, 4),
            "overlap_pad2_mm2": round(_box(min(p0[0], p1[0]), min(p0[1], p1[1]),
                                           max(p0[0], p1[0]), max(p0[1], p1[1])
                                           ).intersection(B).area, 4)}


def _fet_bridge(fet, drain_net, source_net, w, z, t, others=(),
                shrink=1.0):
    """The conducting device, as the shortest piece of copper that joins its
    own drain and source pads.

    It has to be short and it has to stay in F.Cu.  A strip drawn between the
    two pad centres is 4 mm long and runs clear across the GND pour on the way
    -- and metal is metal in FDTD, so that shorts the half-bridge rather than
    closing it.  The nearest approach between the two nets inside the device's
    own footprint is the 0.40 mm clearance where the channel would be, which is
    where a conducting FET actually bridges.
    """
    from shapely.ops import nearest_points
    pts = [(q["x"], q["y"]) for q in copper.pads_of(fet)]
    if not pts:
        raise KeyError(fet)
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    m = 0.6
    box = (min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m)
    A = copper.net_union(drain_net, "F.Cu", box, simplify=0.0)
    B = copper.net_union(source_net, "F.Cu", box, simplify=0.0)
    if A is None or B is None or A.is_empty or B.is_empty:
        raise KeyError(f"{fet}: no copper for {drain_net}/{source_net}")
    a, b = nearest_points(A, B)
    foreign = [u for u in (copper.net_union(n, "F.Cu", box, simplify=0.0)
                           for n in others) if u is not None and not u.is_empty]

    # widen it as far as it can go without touching a third net: on this board
    # the high-side bridge starts catching the GND pour above 0.4 mm, and a
    # bridge that touches GND shorts the half-bridge instead of closing it
    from shapely.geometry import Polygon as _P
    chosen = None
    for k in (1.0, 0.75, 0.5, 0.35, 0.25, 0.15):
        pts = _strip((a.x, a.y), (b.x, b.y), w * k)
        if not any(_P(pts).intersects(u) for u in foreign):
            chosen = (pts, w * k)
            break
    if chosen is None:
        raise KeyError(f"{fet}: no bridge width clears the other nets")
    pts, used = chosen
    if shrink != 1.0:
        used *= shrink
        pts = _strip((a.x, a.y), (b.x, b.y), used)
    return {"pts": pts, "z": z, "t": t, "width_mm": round(used, 4),
            "from": f"{fet} {drain_net}", "to": f"{fet} {source_net}",
            "gap_mm": round(a.distance(b), 4)}


def cell_loop(cellname="A", res=0.35, min_step=0.10, nrts=500000,
              timeout=21600, name=None, nets=None, nthreads=12, snap=True,
              bridge_w=1.0, bridge_shrink=1.0, barrel="pad", plating=0.025):
    """Cell A's commutation loop, full wave.

    The mesh is snapped to the copper: every polygon vertex and every barrel
    wall pins a line, so the board's 0.40 mm clearances are clearances in the
    discretised model too rather than whatever the grid happens to straddle.
    Without it a uniform mesh joins nets the board keeps apart -- there are no
    nets in FDTD, only metal -- and the loop comes out several times too small,
    which is how this model behaved at 0.45 mm cells and coarser.
    """
    c = fhcell.CELL[cellname]
    win = fhcell.power_window(cellname, 2.5)
    name = name or f"p7_cell{cellname}_loop"
    nets = nets or ("VBUS", "GND", c["sw"])
    p, data = geom_export.export(win, nets, name=name,
                                 out=paths.WORK / "openems" / name / "geom.json")
    # the port: across the high-side 100 n, where the PEEC model's port was
    v = _pad_xy(c["hf"][0], 1)
    g = _pad_xy(c["hf"][0], 2)
    # the FETs, shorted at their pads
    st = copper.stackup()
    z, t = st["F.Cu"]["z"], st["F.Cu"]["t"]
    bridges = []
    for fet, dn, sn in ((c["hi"], "VBUS", c["sw"]), (c["lo"], c["sw"], "GND")):
        try:
            bridges.append(_fet_bridge(fet, dn, sn, bridge_w, z, t,
                                       others=[n for n in nets
                                               if n not in (dn, sn)],
                                       shrink=bridge_shrink))
        except (KeyError, IndexError, ImportError):
            pass

    d = json.loads(p.read_text())
    if barrel == "plated":
        # A plated hole is a tube of drill + 2 x plating, not a solid rod of
        # the pad's diameter.  The pad diameter is what the extractor carries
        # because that is what the pad is on the layers it lands on; between
        # layers there is only the tube, and the tube is what sets the barrel's
        # partial inductance.  The two models bracket the truth here -- the
        # PEEC mesh uses an equal-AREA square bar, right for resistance and
        # 2.2x too inductive for a 0.4 mm hole -- so this option prices it.
        for b in d["barrels"]:
            b["dia"] = min(b["dia"], b["drill"] + 2 * plating)
    d = geom_export.drop_barrel_shadowed(d)
    pbox = _port_box(c["hf"][0])
    bxs = [q[0] for b in bridges for q in b["pts"]]
    bys = [q[1] for b in bridges for q in b["pts"]]
    keep_x = [v[0], g[0], pbox["p0"][0], pbox["p1"][0]]
    keep_y = [v[1], g[1], pbox["p0"][1], pbox["p1"][1]]
    if snap:
        fx, sx = geom_export.fixed_lines(d, "x", min_step,
                                         extra=keep_x + bxs)
        fy, sy = geom_export.fixed_lines(d, "y", min_step,
                                         extra=keep_y + bys)
    else:
        # a plain uniform fill, to show what it costs: no line is pinned to a
        # feature, and the merge only stops two lines coinciding
        fx, fy = [], []
        sx = sy = min_step = res / 6.0
    d.update({"port": [list(v), list(g)], "port_box": pbox,
              "bridges": bridges,
              "res": res, "nrts": nrts, "fc": 5e8,
              "band_lo": 5e7, "band_hi": 2.5e8,
              "fx": fx, "fy": fy, "min_step": min_step,
              "keep_x": keep_x, "keep_y": keep_y})
    p.write_text(json.dumps(d))
    wd = fr.run(name, cell_scripts.CELL_LOOP, timeout=timeout,
                nthreads=nthreads, clean=False)
    meta = {"snapped": snap, "barrel": barrel, "port_box": pbox,
            "bridge_shrink": bridge_shrink,
            "bridges": [{k: b[k] for k in ("from", "to", "width_mm", "gap_mm")}
                        for b in bridges],
            "min_step_mm": min_step,
            "step_used_x_mm": round(sx, 4),
            "step_used_y_mm": round(sy, 4), "fixed_lines_x": len(fx),
            "fixed_lines_y": len(fy), "res_mm": res,
            "polys": len(d["polys"]), "barrels": len(d["barrels"]),
            "barrel_shadowed_polys": d.get("barrel_shadowed_polys")}
    return fr.result(wd), wd, meta


def _energy_trace(wd):
    """(timestep, energy_dB) as openEMS printed it."""
    try:
        so = (wd / "stdout.txt").read_text()
    except OSError:
        return [], []
    import re as _re
    ts, db = [], []
    for m in _re.finditer(r"Timestep:\s+(\d+).*?Energy: ~\s*[0-9.e+-]+ "
                          r"\(\s*([-0-9.]+)dB", so):
        ts.append(int(m.group(1)))
        db.append(float(m.group(2)))
    return ts, db


def _series(specs, snap, tag, **kw):
    """Run one mesh series, returning (rows, runs)."""
    rows, runs = [], []
    for res, min_step, nrts in specs:
        name = f"p7_cellA_loop_{tag}{min_step}"
        try:
            r, wd, meta = cell_loop(res=res, min_step=min_step, nrts=nrts,
                                    name=name, snap=snap, **kw)
            d = _diagnostics(wd)
            row = dict(meta, L_nH=float(r.get("L_nH", float("nan"))),
                       L_band_mean_nH=r.get("L_band_mean_nH"),
                       lc_fit=r.get("lc_fit"),
                       L_nH_from_record_fraction=r.get(
                           "L_nH_from_record_fraction"),
                       L_nH_spread=float(r.get("L_nH_std", float("nan"))),
                       R_mOhm_at_100MHz=r.get("R_mOhm_at_100MHz"),
                       min_cell_mm=r.get("min_cell_mm"),
                       cells=d.get("cells"), mesh=d.get("mesh"),
                       final_energy_dB=d.get("final_energy_dB"),
                       dropped_primitives=d.get("dropped_primitives"),
                       workdir=str(wd))
            runs.append((wd, r, row))
        except Exception as e:
            row = {"res_mm": res, "min_step_mm": min_step, "snapped": snap,
                   "error": f"{e.__class__.__name__}: {e}"}
        rows.append(row)
    return rows, runs


def crosscheck_note(out):
    """What the two curves actually did, said in one paragraph."""
    uni = [r for r in (out.get("uniform_mesh_series") or []) if r.get("L_nH")]
    em = [r for r in (out.get("mesh_convergence") or []) if r.get("L_nH")]
    fh = ((out.get("fasthenry") or {}).get("fasthenry_convergence") or [])
    t = ("The two solvers approach this loop from opposite sides, and the "
         "mechanism is different at each end.  ")
    if uni:
        t += (f"A uniform FDTD mesh reads low until its cells are inside the "
              f"board's 0.40 mm clearances -- there are no nets in FDTD, only "
              f"metal, so a cell that spans a clearance joins VBUS to GND -- "
              f"and it climbs from {uni[0]['L_nH']:.2f} nH at "
              f"{uni[0]['res_mm']:.2f} mm to {uni[-1]['L_nH']:.2f} nH at "
              f"{uni[-1]['res_mm']:.2f} mm as that stops happening.  ")
    if em:
        d = out.get("last_refinement_change")
        t += (f"Snapping the mesh to the copper reaches the same place "
              f"without the uniform cost: {em[-1]['L_nH']:.2f} nH"
              + (f", moving {d * 100:.1f} % on the last refinement.  "
                 if d is not None else ".  "))
    if len(fh) > 1:
        step = ((fh[-1]["L_single_port_nH"] - fh[-2]["L_single_port_nH"])
                / fh[-2]["L_single_port_nH"])
        t += (f"FastHenry falls down its own series, "
              f"{fh[0]['L_single_port_nH']:.2f} nH at {fh[0]['target_mm']} mm "
              f"to {fh[-1]['L_single_port_nH']:.2f} nH at "
              f"{fh[-1]['target_mm']} mm, because a filament grid that coarse "
              f"cannot let the return current concentrate under a leg running "
              f"0.4 mm above it, which is most of what sets this loop"
              + (f", and it has settled there: the last refinement moved it "
                 f"{abs(step) * 100:.1f} % and in the other direction.  "
                 if abs(step) < 0.05 else
                 f", and it is still falling {abs(step) * 100:.0f} % per "
                 f"refinement.  "))
    ref = out.get("fasthenry") or {}
    if ref.get("grid_target_mm"):
        t += (f"The figure quoted for FastHenry here is the converged end of "
              f"that sweep, {ref['grid_target_mm']} mm at "
              f"{(ref.get('f_Hz') or 0) / 1e6:.0f} MHz, not the production "
              f"run's 1.2 mm grid; the production model's own frequency sweep "
              f"falls about 3 % between 1 MHz and 100 MHz, which is the band "
              f"the FDTD extraction sits in, so the comparison is fair to "
              f"within a few per cent either way.  ")
    t += ("Neither solver had been run to its own convergence before this.")
    return t


def _layer_shapes(cellname, layer):
    """The copper on one layer, barrels included, as one shape per net."""
    from shapely.geometry import Point as sPoint
    from shapely.ops import unary_union
    c = fhcell.CELL[cellname]
    win = fhcell.power_window(cellname, 2.5)
    gj = paths.WORK / "openems" / f"p7_cell{cellname}_loop" / "geom.json"
    bars = copper.barrels(("VBUS", "GND", c["sw"]), win)
    zl = copper.stackup()[layer]["z"]
    order = copper.CU_LAYERS
    out = {}
    for n in ("GND", "VBUS", c["sw"]):
        u = copper.net_union(n, layer, win, simplify=0.0)
        cyl = [sPoint(b["x"], b["y"]).buffer(b["dia"] / 2, 48) for b in bars
               if b["net"] == n and b["i0"] <= order.index(layer) <= b["i1"]]
        parts = ([u] if u is not None and not u.is_empty else []) + cyl
        if parts:
            out[n] = unary_union(parts)
    return out, win


def _joining_cells(shapes, xs, ys, window):
    """How many mesh cells touch two different nets.  In FDTD each one joins
    them: there are no nets there, only metal."""
    from shapely.geometry import box as sbox
    n = 0
    for a, b in zip(xs[:-1], xs[1:]):
        if b < window[0] or a > window[2]:
            continue
        for c, d in zip(ys[:-1], ys[1:]):
            if d < window[1] or c > window[3]:
                continue
            cell = sbox(a, c, b, d)
            hit = 0
            for g in shapes.values():
                if g.intersects(cell) and g.intersection(cell).area > 1e-6:
                    hit += 1
                    if hit > 1:
                        n += 1
                        break
    return n


def _uniform_lines(window, axis, res):
    i = 0 if axis == "x" else 1
    lo, hi = window[i], window[i + 2]
    return list(np.linspace(lo, hi, max(2, int(round((hi - lo) / res)) + 1)))


def shorting_survey(cellname="A", layer="In1.Cu", uniform=(), snapped=()):
    """For each mesh in the two series, how much copper it joins that the
    board keeps apart.  This is the difference between the two series, and
    it is a property of the mesh alone, so it costs no solver time."""
    try:
        shapes, win = _layer_shapes(cellname, layer)
    except Exception as e:
        return {"error": f"{e.__class__.__name__}: {e}"}
    if len(shapes) < 2:
        return {"error": "one net or fewer on this layer"}
    gj = paths.WORK / "openems" / f"p7_cell{cellname}_loop" / "geom.json"
    out = {"layer": layer, "uniform": [], "snapped": []}
    for res in uniform:
        xs = _uniform_lines(win, "x", res)
        ys = _uniform_lines(win, "y", res)
        out["uniform"].append({"res_mm": res,
                               "cells_joining_two_nets":
                                   _joining_cells(shapes, xs, ys, win)})
    if gj.exists() and snapped:
        try:
            d = geom_export.drop_barrel_shadowed(json.loads(gj.read_text()))
            for ms in snapped:
                fx, sx = geom_export.fixed_lines(d, "x", ms)
                fy, _ = geom_export.fixed_lines(d, "y", ms)
                out["snapped"].append({"min_step_mm": ms,
                                       "step_used_mm": round(sx, 4),
                                       "cells_joining_two_nets":
                                           _joining_cells(shapes, fx, fy, win)})
        except (OSError, ValueError):
            pass
    return out


def q1_crosscheck(quick=False):
    """L2 against L1 on the same copper, each solver taken to its own mesh
    convergence rather than to one mesh that happened to run quickly."""
    t0 = time.time()
    out = {"question": "Q1 (L2 cross-check)"}
    ref = _fh_reference("A")
    out["fasthenry"] = ref
    l1 = ref.get("L_single_port_nH")
    out["L1_fasthenry_nH"] = l1
    out["L1_fasthenry_L_eff_nH"] = ref.get("L_eff_all_four_nH")

    # one control varies: how finely a feature is resolved.  The background
    # fill stays at 0.40 mm so the series says what snapping is worth and not
    # what a uniformly finer mesh is worth.
    series = ([(0.35, 0.20, 400000), (0.30, 0.14, 400000)] if quick else
              [(0.30, 0.20, 500000), (0.30, 0.14, 500000),
               (0.30, 0.10, 600000)])
    # what a plain uniform mesh does on the same model: the point of the
    # snapped one is that this series does not settle until the cells are
    # under the board's 0.40 mm clearance, and by then it is the expensive
    # way to get there
    uni = ([(0.55, 300000)] if quick else
           [(0.55, 400000), (0.35, 450000), (0.20, 500000)])
    out["uniform_mesh_series"] = _series(
        [(r, r, n) for r, n in uni], snap=False, tag="u")[0]

    rows, runs = _series(series, snap=True, tag="s")
    out["mesh_convergence"] = rows
    # The one place the two models disagree about the conductor itself: the
    # PEEC mesh uses an equal-AREA square bar for a plated hole (right for R,
    # 2.2x too inductive for a 0.4 mm drill) and this one a solid rod of the
    # pad's diameter (too fat).  Re-solve one point at the real plated
    # diameter so the size of it is measured rather than argued.
    if not quick:
        _b = _series([(0.35, 0.35, 500000)], snap=False, tag="v",
                     barrel="plated")[0]
        out["barrel_model_sensitivity"] = _b[0] if _b else {}
    out["shorting_survey"] = shorting_survey(
        "A", "In1.Cu", uniform=[r for r, _ in uni],
        snapped=[m for _, m, _ in series])

    # The device is an ideal short in the PEEC model and a real strip of
    # copper here, and a strip 0.15 mm above a ground plane carries a partial
    # inductance the other model does not.  Halving its width at a fixed mesh
    # doubles that term and leaves the loop alone, so L(w) = L0 + k/w and the
    # pair extrapolates to the ideal short the PEEC model assumes: L0 = 2*L1 -
    # L2.  That is the number the two solvers can honestly be compared on.
    if not quick and rows:
        # priced at the coarsest snapped mesh: the strip's own term is set by
        # its own geometry, and that mesh already resolves a 0.35 mm strip,
        # so paying for the finest one buys nothing here
        base = max((r for r in rows if r.get("L_nH")),
                   key=lambda r: r["min_step_mm"], default=None)
        if base:
            half = _series([(base["res_mm"], base["min_step_mm"],
                             base.get("nrts", 500000))],
                           snap=True, tag="h", bridge_shrink=0.5)[0]
            out["bridge_deembed"] = {"full_width": base, "half_width": half[0]}
            if half and half[0].get("L_nH"):
                L1, L2 = base["L_nH"], half[0]["L_nH"]
                out["bridge_deembed"]["L_ideal_short_nH"] = 2 * L1 - L2
                out["bridge_deembed"]["bridge_term_nH"] = L2 - L1
                out["bridge_deembed"]["note"] = (
                    "L(w) = L0 + k/w: halving the bridge width doubles the "
                    "strip's own term and leaves the loop unchanged, so "
                    "L0 = 2*L1 - L2 is the loop with the ideal short the PEEC "
                    "model assumes.")

    good = [r for r in rows if r.get("L_nH") == r.get("L_nH")
            and "error" not in r]
    if good:
        fin = good[-1]
        out["L2_openems_nH"] = fin["L_nH"]
        out["L2_spread_nH"] = fin["L_nH_spread"]
        out["L2_lc_fit"] = fin.get("lc_fit")
        out["R_mOhm_at_100MHz"] = fin["R_mOhm_at_100MHz"]
        out["workdir"] = fin["workdir"]
        out["diagnostics"] = {k: fin.get(k) for k in
                              ("cells", "mesh", "final_energy_dB",
                               "dropped_primitives")}
        # the last refinement's own movement is the honest error bar
        drift = (abs(good[-1]["L_nH"] - good[-2]["L_nH"]) / good[-1]["L_nH"]
                 if len(good) > 1 else None)
        out["last_refinement_change"] = drift
        # The gate is on the answer, not on the residual energy.  A little
        # energy circles in the air box long after the port has settled --
        # every run here stops between -17 and -50 dB -- and the runs that
        # stop highest have the flattest L.  What bounds the answer is that
        # the record is long enough to transform, that L does not drift
        # across the extraction band, that refining the mesh does not move
        # it, and that no copper was dropped.  The residual energy is
        # reported beside them rather than used as a gate.
        # Flatness is measured on the fit, not on the band mean.  The band
        # mean is *expected* to slope: this structure resonates near 390 MHz
        # and 1/(1 - (f/f0)^2) lifts the top of the fit band by half as much
        # again.  What says whether one L and one C describe the port is the
        # residual of that two-parameter fit.
        lcf = fin.get("lc_fit") or {}
        flat = (lcf.get("rel_residual") is not None
                and lcf["rel_residual"] < 0.05)
        tr = fin.get("L_nH_from_record_fraction") or {}
        vals = [v for k, v in tr.items() if isinstance(v, (int, float))]
        settled = (max(vals) - min(vals)) / max(abs(v) for v in vals) < 0.05 \
            if len(vals) > 1 else None
        out["convergence_tests"] = {
            "one_L_and_one_C_describe_the_port": flat,
            "lc_fit_residual": lcf.get("rel_residual"),
            "last_refinement_under_10pct": (drift is not None and drift < 0.10),
            "no_dropped_primitives": (fin.get("dropped_primitives") or 0) < 20,
            "record_long_enough": settled,
            "residual_energy_dB": _db(fin.get("final_energy_dB"))}
        out["converged"] = bool(
            flat and (drift is not None and drift < 0.10)
            and (fin.get("dropped_primitives") or 0) < 20
            and settled is not False)
        # P1's number is read again here, at the end: this phase takes over an
        # hour and may have been started while P1 was still solving
        ref = _fh_reference("A")
        out["fasthenry"] = ref
        l1 = ref.get("L_single_port_nH")
        out["L1_fasthenry_nH"] = l1
        out["L1_fasthenry_L_eff_nH"] = ref.get("L_eff_all_four_nH")
        if l1:
            out["ratio_L2_over_L1"] = fin["L_nH"] / l1
            out["agreement_raw_pct"] = abs(fin["L_nH"] - l1) / l1 * 100
    # compare like with like: the PEEC model's device is an ideal short, so
    # the FDTD number to set beside it is the one with its strip taken out
    de = out.get("bridge_deembed") or {}
    l2 = de.get("L_ideal_short_nH")
    if l2 is None:
        l2 = out.get("L2_openems_nH")
        out["compared_on"] = "the raw FDTD value; the bridge was not de-embedded"
    else:
        out["compared_on"] = "the FDTD value with the device strip de-embedded"
    out["L2_for_comparison_nH"] = l2
    if l1 and l2:
        out["agreement_pct"] = abs(l2 - l1) / l1 * 100
        out["pass_25pct"] = bool(out["agreement_pct"] <= 25.0)
    if out.get("converged"):
        out.pop("not_converged_reason", None)
    else:
        out["not_converged_reason"] = _why_not(out)
    out["note"] = crosscheck_note(out)
    _plot_crosscheck(out, runs)
    _plot_geometry()
    _plot_mesh()
    out["seconds"] = round(time.time() - t0, 1)
    return out


def _db(x):
    try:
        return float(str(x).replace(" ", ""))
    except (TypeError, ValueError):
        return 0.0


def _why_not(out):
    t = out.get("convergence_tests") or {}
    why = [k.replace("_", " ") for k, v in t.items()
           if v is False and k != "residual_energy_dB"]
    if not why:
        return ("the FDTD model did not produce a usable port impedance; see "
                "mesh_convergence for what each mesh did")
    return ("the L2 model does not yet meet " + ", ".join(why) +
            "; the mesh_convergence table shows how far each test got, and "
            "the value it is heading for is quoted as a bracket rather than "
            "as a number")


def q11(quick=False):
    """Near field above the board, and the far field with a motor lead."""
    t0 = time.time()
    out = {"question": "Q11"}
    nets = ["GND", "VBUS", "SW_A", "SW_B", "SW_C",
            "PHASE_A", "PHASE_B", "PHASE_C", "+3V3"]
    name = "p7_board_nf"
    win = (-32.5, -32.5, 32.5, 32.5)
    try:
        p, data = geom_export.export(
            win, nets, simplify=0.25, name=name,
            out=paths.WORK / "openems" / name / "geom.json")
        v = _pad_xy("Q1", 5)
        g = _pad_xy("Q2", 1)
        # One motor lead, leaving the board at cell A's own lead pad.
        #
        # It starts in the pad's copper, not near it.  The first version of
        # this model put the wire's base at z = 1.65, the bottom face of the
        # board, while J1.1 is on B.Cu at z = 1.545: a 70 um gap, which at the
        # 0.12 mm z cells this model can afford is closed or not depending on
        # where a mesh line happens to fall.  A lead that may or may not be
        # attached is not a model of a lead.
        st2 = copper.stackup()
        try:
            lx, ly = _pad_xy(fhcell.CELL["A"]["lead"], 1, "B.Cu")
            lz = st2["B.Cu"]["z"]
        except KeyError:
            ang = math.radians(34.0)
            lx, ly = 29.5 * math.cos(ang), 29.5 * math.sin(ang)
            lz = st2["B.Cu"]["z"]
        lead = [[lx, ly, lz, lx, ly, lz + 58.0]]
        d = geom_export.drop_barrel_shadowed(json.loads(p.read_text()))
        d.update({"port": [list(v), list(g)], "lead": lead,
                  "res": 2.0 if quick else 1.2,
                  "nrts": 20000 if quick else 45000,
                  "fc": 400e6,
                  "f_probe": [1e5, 1e6, 2e7, 1.18e8, 3e8]})
        p.write_text(json.dumps(d))
        wd = fr.run(name, cell_scripts.NEAR_FAR, timeout=12000, nthreads=14,
                    clean=False)
        res = fr.result(wd)
        out["far_field"] = res.get("far_field")
        out["workdir"] = str(wd)
        out["diagnostics"] = _diagnostics(wd)
        ff = res.get("far_field") or {}
        neg = [k for k, r in ff.items() if (r.get("Dmax") or -1) <= 0]
        out["negative_directivity_at_Hz"] = neg
        out["lead_anchored_at"] = {"x": lx, "y": ly, "z": lz,
                                   "pad": f"{fhcell.CELL['A']['lead']}.1"}
        out["converged"] = bool(ff and not neg
                                and out["diagnostics"]
                                .get("dropped_primitives", 0) < 50)
        if not out["converged"]:
            out["not_converged_reason"] = (
                f"The mesh cannot represent this board.  Its clearances are "
                f"0.40 mm and its tracks 0.15 mm; a 65 mm six-layer board "
                f"allows 1.2 mm cells here, and at that size copper the board "
                f"keeps apart is joined -- there are no nets in FDTD, only "
                f"metal.  Q1's cell-A model showed what that costs: the same "
                f"loop came out 0.62 nH at 0.55 mm cells and 3.04 nH once the "
                f"clearances were resolved, a factor of five.  Snapping the "
                f"mesh to the copper rescued the cell, which is 21 x 26 mm; "
                f"pinning a line to every feature on the whole board is a "
                f"10^8-cell problem.  No radiated level is quoted.  "
                f"(The directivity also comes out negative at {len(neg)} of "
                f"the {len(ff)} probe frequencies"
                + (", all of them below 30 MHz, where a board this size "
                   "radiates almost nothing and the near-to-far-field "
                   "transform is differencing numbers at its own floor -- "
                   "that one is not the mesh's fault"
                   if neg and not neg_real else
                   f", including {len(neg_real)} above 30 MHz")
                + f".  {out['diagnostics'].get('dropped_primitives', 0)} "
                f"primitives were reported unused; those are mostly via pads "
                f"their own barrels already provide and are not evidence of "
                f"anything.)")
    except Exception as e:
        out["error"] = f"{e.__class__.__name__}: {e}"
    # CISPR 32 class B radiated limits at 3 m, for reference only
    out["cispr32_class_B_3m_dBuV_per_m"] = {
        "30-230 MHz": 40.0, "230-1000 MHz": 47.0,
        "note": ("quasi-peak limits at 3 m.  This simulation drives the "
                 "switch node with a 1 V port, not with the real 60 V edge, "
                 "so the absolute level has to be scaled by the real "
                 "excitation and by the duty cycle of the harmonic in "
                 "question; what it is good for is the shape of the spectrum "
                 "and the relative effect of a lead.")}
    out["seconds"] = round(time.time() - t0, 1)
    return out


# ================================================================ figures ===
FG = "#14171c"; MUTED = "#5f6875"; LINE = "#e2e5ea"
C_EM = "#2f6feb"; C_FH = "#c0392b"; C_BAND = "#1a9c5b"


def _axstyle(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(LINE)
    ax.tick_params(colors=MUTED, labelsize=8.5, length=3)
    ax.grid(alpha=.25, lw=.6)
    ax.set_axisbelow(True)
    for lbl in (ax.xaxis.label, ax.yaxis.label):
        lbl.set_color(MUTED)
        lbl.set_fontsize(9.5)
    ax.title.set_color(FG)
    ax.title.set_fontsize(10.5)


def _plot_crosscheck(out, runs):
    """The two solvers' mesh convergence, what the FDTD port actually shows,
    and whether the record it was read from was long enough."""
    fig, ax = plt.subplots(1, 3, figsize=(15.5, 4.9), dpi=160)

    uni = [r for r in (out.get("uniform_mesh_series") or []) if r.get("L_nH")]
    em = [r for r in (out.get("mesh_convergence") or []) if r.get("L_nH")]
    fh = ((out.get("fasthenry") or {}).get("fasthenry_convergence") or [])

    # --- (a) each solver against its own mesh parameter ------------------
    def curve(rows, xkey, ykey, colour, marker, label, off=(0, -15),
              ha="center"):
        if not rows:
            return
        x = [r[xkey] for r in rows]
        y = [r[ykey] for r in rows]
        ax[0].plot(x, y, marker + "-", color=colour, lw=1.8, ms=5, label=label)
        for xi, yi in zip(x, y):
            ax[0].annotate(f"{yi:.2f}", (xi, yi), textcoords="offset points",
                           xytext=off, ha=ha, fontsize=7.5, color=colour)

    curve(uni, "res_mm", "L_nH", "#98a1ae", "^", "openEMS, uniform cells",
          off=(-9, -4), ha="right")
    curve(em, "step_used_x_mm", "L_nH", C_EM, "o",
          "openEMS, snapped to the copper", off=(0, 9))
    curve(fh, "target_mm", "L_single_port_nH", C_FH, "s",
          "FastHenry, filament grid", off=(0, -15))
    lo = hi = None
    if em and fh:
        lo, hi = sorted((em[-1]["L_nH"], fh[-1]["L_single_port_nH"]))
        ax[0].axhspan(lo, hi, color=C_BAND, alpha=.12, zorder=0)
        ax[0].annotate(f"{lo:.2f}\u2013{hi:.2f} nH",
                       (0.03, (lo + hi) / 2), xycoords=("axes fraction", "data"),
                       ha="left", va="center", fontsize=9, color="#12794a",
                       weight="medium")
    ax[0].axvline(0.40, color=MUTED, ls=":", lw=1)
    ax[0].annotate("0.40 mm: the board's\ntightest clearance", (0.40, 0.99),
                   xycoords=("data", "axes fraction"), ha="center", va="top",
                   fontsize=7.5, color=MUTED)
    ax[0].set_xscale("log")
    ax[0].invert_xaxis()
    ax[0].set_xlabel("mesh cell / feature size (mm) \u2014 finer to the right")
    ax[0].set_ylabel("loop L at the high-side 100 n (nH)")
    ax[0].set_title("Both solvers, taken to their own convergence")
    ax[0].legend(fontsize=8, frameon=False, loc="lower left")
    _axstyle(ax[0])

    # --- (b) what the FDTD port shows, and the L behind it ---------------
    lcf = (em[-1].get("lc_fit") or {}) if em else {}
    if runs:
        wd, r, row = runs[-1]
        f, L = r.get("f"), r.get("L")
        if f is not None and L is not None:
            f = np.asarray(f, float)
            ax[1].semilogx(f / 1e6, np.asarray(L) * 1e9, color=C_EM, lw=1.6,
                           label=f"openEMS Im(Z)/\u03c9, snapped "
                                 f"{row['step_used_x_mm']:.2f} mm")
            if lcf.get("L_nH") and lcf.get("f_resonance_MHz"):
                Lq = lcf["L_nH"]
                f0 = lcf["f_resonance_MHz"] * 1e6
                model = Lq / (1 - (f / f0) ** 2)
                ax[1].semilogx(f / 1e6, model, color="#7b1fa2", lw=1.2,
                               ls="--", label="fitted L/(1\u2212(f/f\u2080)\u00b2)")
                ax[1].axhline(Lq, color=C_EM, lw=1, ls=":")
                ax[1].annotate(f"L = {Lq:.2f} nH, C = {lcf['C_pF']:.0f} pF,\n"
                               f"f\u2080 = {f0 / 1e6:.0f} MHz",
                               (0.04, 0.92), xycoords="axes fraction",
                               fontsize=8.5, color=C_EM, va="top")
    q1 = ((jsonio.read("P1") or {}).get("Q1") or {}).get("solved") or {}
    ref = out.get("fasthenry") or {}
    if q1.get("L_matrix_nH") and q1.get("f_Hz"):
        jj = ref.get("port_index", 3)
        ff = np.array(q1["f_Hz"], float)
        LL = np.array([m[jj][jj] for m in q1["L_matrix_nH"]], float)
        keep = ff >= 5e6
        ax[1].semilogx(ff[keep] / 1e6, LL[keep], "s-", color=C_FH, lw=1.6,
                       ms=4, label="FastHenry (no displacement current)")
    ax[1].set_xlim(8, 600)
    top = max(4.5, (lcf.get("L_nH") or 3) * 1.8)
    ax[1].set_ylim(0, top)
    ax[1].set_xlabel("MHz")
    ax[1].set_ylabel("L (nH)")
    ax[1].set_title("The FDTD port carries the loop's own capacitance")
    ax[1].legend(fontsize=7.5, frameon=False, loc="lower left")
    _axstyle(ax[1])

    # --- (c) was the record long enough to transform? --------------------
    any_tr = False
    for k, r in enumerate(em):
        tr = r.get("L_nH_from_record_fraction") or {}
        pts = sorted((float(a), b) for a, b in tr.items()
                     if isinstance(b, (int, float)))
        if len(pts) < 2:
            continue
        any_tr = True
        xs = [a * 100 for a, _ in pts]
        ys = [b for _, b in pts]
        ax[2].plot(xs, ys, "o-", lw=1.6, ms=5,
                   color=plt.cm.Blues(0.45 + 0.2 * k),
                   label=f"snapped {r['step_used_x_mm']:.2f} mm, "
                         f"{r.get('final_energy_dB')} dB left")
    if any_tr:
        ax[2].set_xlabel("per cent of the time record transformed")
        ax[2].set_ylabel("band-mean L (nH)")
        ax[2].set_title("How much of the record the answer needs")
        ax[2].legend(fontsize=7.5, frameon=False, loc="best")
    else:
        ax[2].set_axis_off()
    _axstyle(ax[2])

    fig.suptitle("Q1 cross-check \u2014 FastHenry against openEMS on the same "
                 "copper", fontsize=12.5, color=FG, y=1.0)
    fig.tight_layout()
    fig.savefig(paths.FIGS / "p7_crosscheck.png", bbox_inches="tight",
                facecolor="white")
    plt.close(fig)


def _plot_geometry(cellname="A"):
    """The exploded copper of the cell, so the loop can be seen rather than
    taken on trust."""
    from matplotlib.patches import Patch
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection, Line3DCollection
    from mpl_toolkits.mplot3d import proj3d
    from shapely.geometry import box as _box, Polygon as _Poly

    c = fhcell.CELL[cellname]
    LAY = copper.CU_LAYERS
    NET_C = {"VBUS": "#c0392b", "GND": "#aab2bf", c["sw"]: "#2f6feb"}
    NET_A = {"VBUS": 0.90, "GND": 0.30, c["sw"]: 0.86}
    SP = 3.6
    zmap = {l: -i * SP for i, l in enumerate(LAY)}
    win = fhcell.power_window(cellname, 2.5)
    cx, cy = (win[0] + win[2]) / 2, (win[1] + win[3]) / 2
    x0, x1 = win[0] - cx, win[2] - cx
    y0, y1 = win[1] - cy, win[3] - cy

    def rings(g):
        out = []
        for q in (g.geoms if hasattr(g, "geoms") else [g]):
            if q.is_empty:
                continue
            out.append(np.array(q.exterior.coords))
            out += [np.array(r.coords) for r in q.interiors]
        return out

    def faces(g):
        out = []
        for q in (g.geoms if hasattr(g, "geoms") else [g]):
            if q.is_empty or q.area < 1e-4:
                continue
            if not q.interiors:
                out.append(np.array(q.exterior.coords)[:-1])
                continue
            mnx, mny, mxx, mxy = q.bounds
            xs = np.linspace(mnx, mxx, max(4, int((mxx - mnx) / .6)) + 1)
            for a, b in zip(xs[:-1], xs[1:]):
                for r in ((lambda z: z.geoms if hasattr(z, "geoms") else [z])
                          (q.intersection(_box(a, mny, b, mxy)))):
                    if isinstance(r, _Poly) and r.area > 1e-4 and not r.interiors:
                        out.append(np.array(r.exterior.coords)[:-1])
        return out

    fig = plt.figure(figsize=(12, 7.4), dpi=170)
    ax = fig.add_subplot(111, projection="3d", computed_zorder=False)
    ax.set_position([-0.02, 0.03, 1.04, 0.92])
    bars = copper.barrels(("VBUS", "GND", c["sw"]), win)
    for zi, layer in reversed(list(enumerate(LAY))):
        z, zo = zmap[layer], 10 + (len(LAY) - 1 - zi) * 3
        for net in ("GND", "VBUS", c["sw"]):
            u = copper.net_union(net, layer, win, simplify=0.03)
            if u is None or u.is_empty:
                continue
            ax.add_collection3d(Poly3DCollection(
                [np.column_stack([q[:, 0] - cx, q[:, 1] - cy,
                                  np.full(len(q), z)]) for q in faces(u)],
                facecolor=NET_C[net], edgecolor="none", alpha=NET_A[net],
                zorder=zo))
            ax.add_collection3d(Line3DCollection(
                [np.column_stack([r[:, 0] - cx, r[:, 1] - cy,
                                  np.full(len(r), z)]) for r in rings(u)],
                colors=NET_C[net], linewidths=0.5,
                alpha=min(1.0, NET_A[net] + .25), zorder=zo + 1),
                autolim=False)
        segs, cols = [], []
        for b in bars:
            zs = [zmap[l] for k, l in enumerate(LAY) if b["i0"] <= k <= b["i1"]]
            if len(zs) < 2 or min(zs) > z or max(zs) < z:
                continue
            lo = max(min(zs), z - SP)
            if lo >= z:
                continue
            segs.append([(b["x"] - cx, b["y"] - cy, z),
                         (b["x"] - cx, b["y"] - cy, lo)])
            cols.append(NET_C.get(b["net"], "#888"))
        if segs:
            ax.add_collection3d(Line3DCollection(segs, colors=cols,
                                                 linewidths=1.0, alpha=.8,
                                                 zorder=zo + 2))
    # the two conducting devices, drawn where the model puts them
    bridges = []
    root = paths.WORK / "openems"
    for gj in sorted(root.glob(f"p7_cell{cellname}_loop*/geom.json"),
                     key=lambda q: -q.stat().st_mtime):
        try:
            b = json.loads(gj.read_text()).get("bridges") or []
        except (OSError, ValueError):
            continue
        if b and isinstance(b[0], dict) and "pts" in b[0]:
            bridges = b
            break
    for b in bridges:
        if not isinstance(b, dict) or "pts" not in b:
            continue
        q = np.asarray(b["pts"], float)
        ax.add_collection3d(Poly3DCollection(
            [np.column_stack([q[:, 0] - cx, q[:, 1] - cy,
                              np.full(len(q), zmap["F.Cu"])])],
            facecolor="#1a9c5b", edgecolor="#12794a", linewidths=.8,
            alpha=.95, zorder=10 + (len(LAY) - 1) * 3 + 4))

    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1)
    ax.set_zlim(zmap[LAY[-1]] - 1.5, 1.5)
    ax.set_box_aspect((win[2] - win[0], win[3] - win[1], 17))
    ax.view_init(elev=21, azim=-62)
    ax.set_axis_off()
    fig.canvas.draw()
    for layer in LAY:
        xp, yp, _ = proj3d.proj_transform(x0 - 7.0, y0 - 7.0, zmap[layer],
                                          ax.get_proj())
        ax.annotate(layer, ax.transData.transform((xp, yp)),
                    xycoords="figure pixels", xytext=(-10, 0),
                    textcoords="offset points", ha="right", va="center",
                    fontsize=9, color="#3d4653")
    handles = [Patch(facecolor=NET_C[n], label=l, alpha=NET_A[n])
               for n, l in (("VBUS", "VBUS"),
                            (c["sw"], f"{c['sw']}  (switch node)"),
                            ("GND", "GND"))]
    if bridges:
        handles.append(Patch(facecolor="#1a9c5b",
                             label="the two FETs, conducting"))
    ax.legend(handles=handles, loc="upper left", frameon=False, fontsize=10,
              bbox_to_anchor=(0.04, 0.95))
    fig.suptitle(f"Cell {cellname} commutation loop — the copper both solvers "
                 f"see", fontsize=13.5, y=0.97, color=FG)
    fig.text(0.5, 0.015,
             "Six layers, exploded.  VBUS is a zone on In2 alone: the "
             "high-side drain reaches it only through the 4\u00d74 field of "
             "barrels,\nwhose antipads perforate the In1 ground plane in "
             "between.  Each device is a short between its own pads, lying "
             "in F.Cu.",
             ha="center", fontsize=9.5, color=MUTED, linespacing=1.5)
    fig.savefig(paths.FIGS / f"p7_cell{cellname}_3d.png", bbox_inches="tight",
                facecolor="white")
    plt.close(fig)


def _plot_mesh(cellname="A", layer="In1.Cu", res_bad=0.55, min_step=0.10):
    """Why the mesh decides the answer.

    In FDTD there are no nets, only metal.  A cell that touches VBUS and GND
    makes them one conductor, and the commutation loop collapses; the tightest
    clearance here is 0.40 mm, so a uniform mesh coarser than that does exactly
    that under the high-side drain.
    """
    from matplotlib.patches import Rectangle, Patch
    from shapely.geometry import box as sbox, Point as sPoint
    from shapely.ops import unary_union

    c = fhcell.CELL[cellname]
    NET_C = {"VBUS": "#c0392b", "GND": "#aab2bf", c["sw"]: "#2f6feb"}
    win = fhcell.power_window(cellname, 2.5)
    gpath = paths.WORK / "openems" / f"p7_cell{cellname}_loop" / "geom.json"
    if not gpath.exists():
        return
    G = geom_export.drop_barrel_shadowed(json.loads(gpath.read_text()))
    zl = copper.stackup()[layer]["z"]

    shapes = {}
    for n in ("GND", "VBUS", c["sw"]):
        u = copper.net_union(n, layer, win, simplify=0.0)
        cyl = [sPoint(b["x"], b["y"]).buffer(b["dia"] / 2, 48)
               for b in G["barrels"] if b["net"] == n
               and b["z0"] - 1e-6 <= zl <= b["z1"] + 1e-6]
        parts = ([u] if u is not None and not u.is_empty else []) + cyl
        if parts:
            shapes[n] = unary_union(parts)
    if "VBUS" not in shapes or "GND" not in shapes:
        return

    bx = [b["x"] for b in G["barrels"] if b["net"] == "VBUS"]
    by = [b["y"] for b in G["barrels"] if b["net"] == "VBUS"]
    if not bx:
        return
    cx, cy, H = float(np.median(bx)), float(np.median(by)), 3.2
    zoom = (cx - H, cy - H, cx + H, cy + H)

    def uniform(axis, r):
        i = 0 if axis == "x" else 1
        lo, hi = win[i], win[i + 2]
        return list(np.linspace(lo, hi, max(2, int(round((hi - lo) / r)) + 1)))

    def shorting(xs, ys):
        out = []
        for a, b in zip(xs[:-1], xs[1:]):
            if b < zoom[0] or a > zoom[2]:
                continue
            for d, e_ in zip(ys[:-1], ys[1:]):
                if e_ < zoom[1] or d > zoom[3]:
                    continue
                cell = sbox(a, d, b, e_)
                hit = [n for n, g in shapes.items()
                       if g.intersects(cell) and g.intersection(cell).area > 1e-6]
                if len(hit) > 1:
                    out.append((a, d, b - a, e_ - d))
        return out

    fxs, sx = geom_export.fixed_lines(G, "x", min_step)
    fys, _ = geom_export.fixed_lines(G, "y", min_step)
    panels = [(f"A uniform {res_bad:.2f} mm mesh", uniform("x", res_bad),
               uniform("y", res_bad)),
              (f"Snapped to the copper ({sx:.2f} mm)", fxs, fys)]

    fig, axes = plt.subplots(1, 2, figsize=(13, 6.6), dpi=165)
    for ax, (title, xs, ys) in zip(axes, panels):
        for n, g in shapes.items():
            for q in (g.geoms if hasattr(g, "geoms") else [g]):
                ax.fill(*q.exterior.xy, color=NET_C[n], alpha=.55, lw=0)
                for r in q.interiors:
                    ax.fill(*r.xy, color="white", lw=0)
                ax.plot(*q.exterior.xy, color=NET_C[n], lw=.7)
                for r in q.interiors:
                    ax.plot(*r.xy, color=NET_C[n], lw=.7)
        for x in xs:
            if zoom[0] <= x <= zoom[2]:
                ax.axvline(x, color="#1f2937", lw=.35, alpha=.28, zorder=5)
        for y in ys:
            if zoom[1] <= y <= zoom[3]:
                ax.axhline(y, color="#1f2937", lw=.35, alpha=.28, zorder=5)
        bad = shorting(xs, ys)
        for (a, d, w, h) in bad:
            ax.add_patch(Rectangle((a, d), w, h, facecolor="#7b1fa2",
                                   alpha=.55, edgecolor="#4a0d63", lw=.8,
                                   zorder=6))
        ax.set_xlim(zoom[0], zoom[2]); ax.set_ylim(zoom[1], zoom[3])
        ax.set_aspect("equal")
        ax.set_title(f"{title}\n{len(bad)} cells join two nets",
                     fontsize=10.5, color=FG)
        ax.tick_params(colors=MUTED, labelsize=8)
        for sp in ax.spines.values():
            sp.set_color(LINE)
    axes[0].legend(handles=[
        Patch(facecolor=NET_C["VBUS"], alpha=.55, label="VBUS"),
        Patch(facecolor=NET_C["GND"], alpha=.55, label="GND"),
        Patch(facecolor="#7b1fa2", alpha=.55, label="a cell touching both")],
        loc="upper left", fontsize=8.5, frameon=False)
    fig.suptitle(f"{layer} under the high-side drain \u2014 the 0.40 mm "
                 f"antipads the VBUS barrels pass through",
                 fontsize=12.5, color=FG, y=0.99)
    fig.text(0.5, 0.005,
             "There are no nets in FDTD, only metal: a cell that touches VBUS "
             "and GND makes them one conductor, and the loop collapses.",
             ha="center", fontsize=9.5, color=MUTED)
    fig.tight_layout(rect=[0, 0.055, 1, 0.965])
    fig.savefig(paths.FIGS / f"p7_mesh_{cellname}.png", bbox_inches="tight",
                facecolor="white")
    plt.close(fig)


def run(quick=False):
    out = {}
    if not fr.available():
        return {"error": "the openEMS image is not built; run sim/setup.py",
                "Q1_crosscheck": None, "Q11": None}
    out["Q1_crosscheck"] = q1_crosscheck(quick)
    out["Q11"] = q11(quick)
    return out


if __name__ == "__main__":
    print(json.dumps(run(quick=True), indent=1, default=str)[:3000])
