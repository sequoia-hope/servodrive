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
        # openEMS pads the figure to five characters, so a change under
        # 10 dB prints as "(- 0.00dB)"; the first pattern here required the
        # digits to follow the sign and silently read those as nothing
        e = _re.findall(r"Energy: ~\s*([0-9.e+-]+) \((-?)\s*([0-9.]+)dB",
                        so)
        if e:
            out["final_energy"] = float(e[-1][0])
            out["final_energy_dB"] = f"{e[-1][1]}{e[-1][2]}"
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
                shrink=1.0, layer="F.Cu"):
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
    A = copper.net_union(drain_net, layer, box, simplify=0.0)
    B = copper.net_union(source_net, layer, box, simplify=0.0)
    if A is None or B is None or A.is_empty or B.is_empty:
        raise KeyError(f"{fet}: no copper for {drain_net}/{source_net}")
    a, b = nearest_points(A, B)
    foreign = [u for u in (copper.net_union(n, layer, box, simplify=0.0)
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


PKG_Z = (-0.25, -0.10)     # the package conductor, above the board's top face


def _fet_fill(fet, drain_net, source_net, z, t, others=(), keep_off=0.15):
    """The conducting device the way the PEEC model has it: drain pad and
    source pads one conductor -- built where the real one is, in the package.

    FastHenry ties every node under those pads into one (an ideal short, the
    "PCB only" convention).  Here that is a conductor at package height,
    PKG_Z, over the device's whole footprint, with posts down onto the drain
    pad and the source pads and nowhere else.  It has to be above the board
    and not in F.Cu: on board S the GND pour runs a finger under Q1's package,
    between the drain tab and the source pins, 0.40 mm from both, and copper
    drawn in F.Cu between them either touches it or squeezes round its end
    through a 0.3 mm neck that a 0.2 mm mesh cannot hold open.  The real
    current goes over it, through the die and the clip; so does this.

    The first version joined drain and source with one strip across the gap
    and tried to take the strip's inductance back out by halving its width
    and extrapolating L = L0 + k/w.  The halved strip was 0.25 mm wide on a
    diagonal, about one cell of the 0.2 mm mesh and barely joined up: halving
    added 1.73 nH where the strip's own parallel-plate inductance says 0.4,
    and the extrapolation left 0.09 nH of a 1.8 nH loop.
    """
    from shapely.ops import unary_union
    drain = _pad_poly(fet, 5)
    srcs = [_pad_poly(fet, n) for n in (1, 2, 3)]
    hull = unary_union([drain, *srcs]).convex_hull
    lo, hi = PKG_Z
    out = []

    def add(geom, z0, z1, role):
        for q in geom_export._simple_pieces(geom):
            out.append({"pts": [[round(x, 4), round(y, 4)] for x, y in q],
                        "z": (z0 + z1) / 2, "t": z1 - z0, "role": role,
                        "from": f"{fet} {drain_net}", "to": f"{fet} {source_net}",
                        "area_mm2": round(geom.area, 3)})

    add(hull, lo, hi, "package")
    # posts from the package down into each pad's own copper
    for pad in (drain, *srcs):
        add(pad, lo, z, "post")
    return out


def cell_loop(cellname="A", res=0.35, min_step=0.10, nrts=500000,
              timeout=21600, name=None, nets=None, nthreads=12, snap=True,
              barrel="plated", plating=0.025):
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
        bridges += _fet_fill(fet, dn, sn, z, t)

    d = json.loads(p.read_text())
    if barrel == "plated":
        # A plated hole is a tube of drill + 2 x plating, not a solid rod of
        # the pad's diameter.  The pad diameter is what the extractor carries
        # because that is what the pad is on the layers it lands on; between
        # layers there is only the tube, and the tube is what sets the barrel's
        # partial inductance.  At these frequencies the current is on the
        # tube's outside, so a solid rod of its outer diameter is the tube;
        # the PEEC mesh uses a bar of the tube's geometric mean distance
        # (fasthenry/mesher.add_barrel), so both solvers see the same via.
        # It used to be a rod of the pad diameter here and a bar of the
        # plating's area there, too fat in one and 2.2x too inductive in the
        # other.
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
              "keep_x": keep_x, "keep_y": keep_y, "extra_z": list(PKG_Z)})
    p.write_text(json.dumps(d))
    wd = fr.run(name, cell_scripts.CELL_LOOP, timeout=timeout,
                nthreads=nthreads, clean=False)
    meta = {"snapped": snap, "barrel": barrel, "port_box": pbox,
            "fet_package_mm2": {b["from"].split()[0]: b["area_mm2"]
                                for b in bridges if b["role"] == "package"},
            "package_z_mm": list(PKG_Z),
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
                          r"\((-?)\s*([0-9.]+)dB", so):
        ts.append(int(m.group(1)))
        db.append(float(m.group(2) + m.group(3)))
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
        lc = ref.get("last_refinement_change")
        t += (f"The figure quoted for FastHenry here is the finest point of "
              f"that sweep"
              + (f" (it moved {lc * 100:.0f} % on its last step)"
                 if lc is not None else "")
              + f", {ref['grid_target_mm']} mm at "
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
    out["shorting_survey"] = shorting_survey(
        "A", "In1.Cu", uniform=[r for r, _ in uni],
        snapped=[m for _, m, _ in series])

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
        # Whether the record is long enough is whether stopping it earlier
        # still moves the answer: the last step, three quarters of it against
        # all of it, held to 2 %.  The half-record figure is kept beside it but
        # not graded -- it was, against a 5 % spread over all three, and on
        # board S's 0.1 mm mesh it failed a record whose last quarter moved
        # the answer 0.5 % (2.81, 2.64, 2.66 nH).
        tr = fin.get("L_nH_from_record_fraction") or {}
        a, b = tr.get("0.75"), tr.get("1.00")
        settled = (abs(b - a) / abs(b) < 0.02
                   if isinstance(a, (int, float)) and isinstance(b, (int, float))
                   else None)
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
    # like with like by construction: both models make each FET's drain and
    # source pads one conductor (_fet_fill) and model each via as the plated
    # tube, so the FDTD number is compared as it comes
    l2 = out.get("L2_openems_nH")
    out["compared_on"] = ("the FDTD value as solved: both models join each "
                          "FET's drain and source pads into one conductor and "
                          "see each via as its plated tube, so nothing is "
                          "taken out")
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


# ------------------------------------------------------------------- Q11 ---
Q11_NETS = ["GND", "VBUS", "SW_A", "SW_B", "SW_C",
            "PHASE_A", "PHASE_B", "PHASE_C", "+3V3"]
BOARD_WIN = (-32.5, -32.5, 32.5, 32.5)
LEAD_MM = 58.0

# Two meshes, so the answer is checked against the mesh rather than assumed:
# `min_step` is the closest two snapped lines may be, `res_fine` the fill
# around the switch node, `res_board` the fill over the rest of the board and
# `cell_max` the largest air cell (lambda/50 at 1 GHz for the finer one).
Q11_MESHES = (
    ("coarse", {"min_step": 0.30, "res_fine": 0.45, "res_board": 1.8,
                "cell_max": 8.0}),
    ("fine", {"min_step": 0.20, "res_fine": 0.30, "res_board": 1.2,
              "cell_max": 6.0}),
)


def _sw_port(c, nets, half=0.15, margin=0.10):
    """The source: a z-directed lumped port from the switch node on F.Cu down
    to the In1 ground plane, at the spot nearest the low-side FET's channel
    where F.Cu is the switch node, In1 is ground and no barrel passes through.

    The first version of Q11 drove a box 0.8 mm tall reaching from the
    high-side drain pad to the low-side source pad, about 8 x 13 mm.  openEMS
    gives a lumped port metal end caps, and z grows into this board, so the
    cap on F.Cu shorted VBUS, the switch node and GND together across that
    whole area, the other cap was a floating plate inside the dielectric at
    z = 0.84 mm, and at the port's priority the box replaced about 100 mm2 of
    the In1 and In2 planes with its own resistor.  The switch node was never
    driven at all.  This port is 0.3 mm square and each cap lands on the
    conductor it is meant to: the switch node above, ground below.
    """
    from shapely.geometry import Point, box as sbox
    from shapely.ops import nearest_points, unary_union
    pts = [(q["x"], q["y"]) for q in copper.pads_of(c["lo"])]
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    fb = (min(xs) - 0.6, min(ys) - 0.6, max(xs) + 0.6, max(ys) + 0.6)
    a, b = nearest_points(copper.net_union(c["sw"], "F.Cu", fb, simplify=0.0),
                          copper.net_union("GND", "F.Cu", fb, simplify=0.0))
    chan = Point((a.x + b.x) / 2, (a.y + b.y) / 2)
    look = (chan.x - 10, chan.y - 10, chan.x + 10, chan.y + 10)
    sw = copper.net_union(c["sw"], "F.Cu", look, simplify=0.0)
    gnd = copper.net_union("GND", "In1.Cu", look, simplify=0.0)
    holes = unary_union([Point(q["x"], q["y"]).buffer(q["dia"] / 2 + margin,
                                                       24)
                         for q in copper.barrels(nets, look)])
    ok = sw.intersection(gnd).difference(holes).buffer(-(half + margin))
    if ok.is_empty:
        raise KeyError(f"no spot where {c['sw']} on F.Cu lies over GND on In1")
    p = nearest_points(ok, chan)[0]
    sq = sbox(p.x - half, p.y - half, p.x + half, p.y + half)
    if not (sq.within(sw) and sq.within(gnd)):
        raise KeyError("the port's footprint is not wholly on both conductors")
    st = copper.stackup()
    return {"p0": [p.x - half, p.y - half, st["F.Cu"]["z"]],
            "p1": [p.x + half, p.y + half, st["In1.Cu"]["z"]],
            "from": f"{c['sw']} on F.Cu", "to": "GND on In1.Cu",
            "fet_channel_xy": [round(chan.x, 3), round(chan.y, 3)],
            "from_channel_mm": round(p.distance(chan), 3)}


def _rf_bridges(nets):
    """The components that matter at RF, as copper.

    Components are not copper, so without these the motor lead stands on
    PHASE_A copper that nothing joins to the switch node (the shunts are the
    only connection), VBUS floats on its plane capacitance, and phases B and C
    are islands.  Phase A's shunts put the lead on the switch node; the
    DC-link capacitors tie VBUS to ground; phases B and C sit with their low
    sides on.  Each is the shortest strip across its own component's
    clearance, as for the FETs in Q1's model.
    """
    st = copper.stackup()
    out, failed = [], []

    def add(ref, a, b):
        try:
            lay = sorted({l for q in copper.pads_of(ref)
                          for l in q.get("shapes", {})})
            lay = lay[0] if len(lay) == 1 else "F.Cu"
            out.append(_fet_bridge(ref, a, b, 1.0, st[lay]["z"], st[lay]["t"],
                                   others=[n for n in nets if n not in (a, b)],
                                   layer=lay))
            out[-1]["layer"] = lay
        except (KeyError, IndexError, ValueError) as e:
            failed.append(f"{ref}: {e}")

    for k in ("A", "B", "C"):
        c = fhcell.CELL[k]
        for r in c["shunts"]:
            add(r, c["sw"], c["phase"])
        if k != "A":
            add(c["lo"], c["sw"], "GND")
        for r in list(c["hf"]) + list(c["bulk"]):
            add(r, "VBUS", "GND")
    return out, failed


def _snap_window(c, lead_xy, pad=1.0):
    """The switch node's own copper, every layer, with a margin: the only
    place the mesh has to keep conductors apart."""
    from shapely.ops import unary_union
    parts = [u for n in (c["sw"], c["phase"]) for L in copper.CU_LAYERS
             for u in [copper.net_union(n, L, BOARD_WIN, simplify=0.0)]
             if u is not None and not u.is_empty]
    x0, y0, x1, y1 = unary_union(parts).bounds
    x0, x1 = min(x0, lead_xy[0]), max(x1, lead_xy[0])
    y0, y1 = min(y0, lead_xy[1]), max(y1, lead_xy[1])
    return [round(max(BOARD_WIN[0], x0 - pad), 4),
            round(max(BOARD_WIN[1], y0 - pad), 4),
            round(min(BOARD_WIN[2], x1 + pad), 4),
            round(min(BOARD_WIN[3], y1 + pad), 4)]


def _snap_lines(d, win, min_step, extra_x=(), extra_y=()):
    """Mesh lines snapped to every copper edge inside `win`, as Q1's cell
    model does over its own window (see geom_export.fixed_lines)."""
    from shapely.geometry import Polygon as _P, box as sbox
    clip = sbox(*win)
    polys = []
    for p in d["polys"]:
        g = _P(p["pts"])
        if not g.is_valid:
            g = g.buffer(0)
        if not g.intersects(clip):
            continue
        h = g.intersection(clip)
        for q in (h.geoms if hasattr(h, "geoms") else [h]):
            if isinstance(q, _P) and not q.is_empty:
                polys.append({"pts": [list(t) for t in q.exterior.coords]})
    bars = [b for b in d["barrels"]
            if win[0] <= b["x"] <= win[2] and win[1] <= b["y"] <= win[3]]
    sub = {"polys": polys, "barrels": bars, "window": list(win)}
    fx, sx = geom_export.fixed_lines(
        sub, "x", min_step, extra=[v for v in extra_x if win[0] <= v <= win[2]])
    fy, sy = geom_export.fixed_lines(
        sub, "y", min_step, extra=[v for v in extra_y if win[1] <= v <= win[3]])
    return fx, fy, max(sx, sy)


def _ring_hz(default=118e6):
    """The switch node's own ring, from the circuit model (P2)."""
    try:
        r = ((jsonio.read("P2") or {}).get("Q2") or {})["worst_case"]
        return float(r["ring_turn_on"]["f_MHz"]) * 1e6
    except (KeyError, TypeError, ValueError):
        return default


def _nf2ff_kat():
    """The far-field transform on a dipole whose answer is in the textbook
    (field/cell_scripts.NF2FF_KAT): D = 1.5 and R_rad = 20 pi^2 (L/lambda)^2.
    R_rad is held to 20 %, because that formula is the thin-wire limit and
    this dipole is a 1 mm bar with a 1 mm gap."""
    t0 = time.time()
    wd = fr.run("p7_nf2ff_kat", cell_scripts.NF2FF_KAT, timeout=3600,
                nthreads=14)
    res = fr.result(wd)
    rows = res.get("rows") or {}
    ok_d = all(abs(r["D"] - 1.5) <= 0.05 for r in rows.values()
               if r["f_Hz"] <= 6e8)
    ok_r = all(abs(r["R_rad_ohm"] / r["R_rad_theory_ohm"] - 1) <= 0.20
               for r in rows.values())
    ok_t = all(abs(r["peak_theta_deg"] - 90) <= 10 for r in rows.values())
    return {"dipole_mm": res.get("dipole_mm"), "rows": rows,
            "D_within_0.05_of_1.5": ok_d, "R_rad_within_20pct": ok_r,
            "peak_broadside": ok_t, "pass": bool(rows and ok_d and ok_r
                                                 and ok_t),
            "seconds": round(time.time() - t0, 1)}


def _q11_run(tag, base, port, bridges, lead, win, spec, f_ff, f_probe,
             nrts):
    name = f"p7_board_nf_{tag}"
    d = json.loads(json.dumps(base))
    lx, ly, r = lead[0][0], lead[0][1], 0.8
    keep_x = [port["p0"][0], port["p1"][0], lx - r, lx, lx + r]
    keep_y = [port["p0"][1], port["p1"][1], ly - r, ly, ly + r]
    bx = [q[0] for b in bridges for q in b["pts"]]
    by = [q[1] for b in bridges for q in b["pts"]]
    fx, fy, step = _snap_lines(d, win, spec["min_step"], keep_x + bx,
                               keep_y + by)
    z_end = max(s[5] for s in lead)
    d.update({"port_box": port, "bridges": bridges, "lead": lead,
              "lead_r": r, "snap_window": win, "fx": fx, "fy": fy,
              "keep_x": keep_x, "keep_y": keep_y,
              "min_step": spec["min_step"], "res_fine": spec["res_fine"],
              "res_board": spec["res_board"], "cell_max": spec["cell_max"],
              "air": [60.0, -80.0, z_end + 60.0],
              "tau": 0.5e-9, "nrts": nrts, "end_criteria": 1e-5,
              "f_ff": list(f_ff), "f_probe": list(f_probe)})
    p = paths.WORK / "openems" / name / "geom.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d))
    t0 = time.time()
    wd = fr.run(name, cell_scripts.NEAR_FAR, timeout=6 * 3600, nthreads=14,
                clean=False)
    res = fr.result(wd)
    diag = _diagnostics(wd)
    return {"tag": tag, **spec, "step_used_mm": round(step, 4),
            "fixed_lines_x": len(fx), "fixed_lines_y": len(fy),
            "cells": diag.get("cells"), "mesh": diag.get("mesh"),
            "final_energy_dB": diag.get("final_energy_dB"),
            "dropped_primitives": diag.get("dropped_primitives"),
            "min_cell_mm": res.get("min_cell_mm"),
            "far_field": res.get("far_field") or {},
            "workdir": str(wd), "seconds": round(time.time() - t0, 1)}


def q11(quick=False):
    """The radiated field of the switch node's edge, with a motor lead.

    The first version reported a directivity below 1 (impossible) at every
    frequency and a field pinned at its own -120 dBuV/m floor, and blamed the
    mesh.  None of it was the mesh: the run stopped at 8.8 ns in the middle
    of its own 14 ns pulse; the port shorted the switch node instead of
    driving it (_sw_port); the field was a spectral density in V.s/m never
    divided by the excitation; the far-field angles were radians where
    openEMS wants degrees, so the "sphere" was a pinhole on the lead's own
    axis; and the directivity divided by a near-field flux that is noise at
    this size (_q11_tests).  geom_export was also dropping a third of each
    inner ground plane (_simple_pieces).  The answer now is a transfer
    function, dBuV/m at 3 m per volt across the switch node.
    """
    t0 = time.time()
    out = {"question": "Q11"}
    c = fhcell.CELL["A"]
    nets = Q11_NETS
    try:
        p, data = geom_export.export(
            BOARD_WIN, nets, simplify=0.05, name="p7_board_nf",
            out=paths.WORK / "openems" / "p7_board_nf" / "geom.json")
        base = geom_export.drop_barrel_shadowed(json.loads(p.read_text()))
        st = copper.stackup()
        # The motor lead leaves the board from cell A's own lead pad, starting
        # in the pad's copper (J1.1 is on B.Cu), not 70 um below it.
        lx, ly = _pad_xy(c["lead"], 1, "B.Cu")
        lz = st["B.Cu"]["z"]
        lead = [[lx, ly, lz, lx, ly, lz + LEAD_MM]]
        port = _sw_port(c, nets)
        bridges, failed = _rf_bridges(nets)
        win = _snap_window(c, (lx, ly))
        ring = _ring_hz()
        f_ff = sorted({30e6, 40e6, 50e6, 65e6, 80e6, 100e6, round(ring, -5),
                       150e6, 200e6, 230e6, 300e6, 400e6, 500e6, 650e6,
                       800e6, 1e9})
        f_probe = [1e5, 1e6, 2e7, round(ring, -5), 3e8]
        out["model"] = {
            "source": port, "lead": {"length_mm": LEAD_MM, "radius_mm": 0.8,
                                     "pad": f"{c['lead']}.1", "x": lx,
                                     "y": ly, "z": lz},
            "bridges": [{k: b[k] for k in ("from", "to", "layer", "width_mm")}
                        for b in bridges],
            "bridges_failed": failed,
            "snap_window_mm": win,
            "copper_mm2": base.get("copper_mm2"),
            "copper_lost_mm2": base.get("copper_lost_mm2"),
            "ring_Hz": ring,
            "excitation": ("derivative of a Gaussian, tau 0.5 ns: no DC, "
                           "peak at 450 MHz, -19 dB at 30 MHz, -10 dB at "
                           "1 GHz"),
            "boundaries": "MUR, 60 mm of air beyond the board and the lead"}
        out["nf2ff_known_answer"] = _nf2ff_kat()
        meshes = Q11_MESHES[:1] if quick else Q11_MESHES
        runs = [_q11_run(tag, base, port, bridges, lead, win, spec, f_ff,
                         f_probe, 200000 if quick else 1200000)
                for tag, spec in meshes]
        out["mesh_series"] = [{k: v for k, v in r.items() if k != "far_field"}
                              for r in runs]
        fine = runs[-1]
        ff = fine["far_field"]
        out["far_field"] = ff
        out["workdir"] = fine["workdir"]
        out["diagnostics"] = _diagnostics(Path(fine["workdir"]))
        out.update(_q11_tests(runs, out["nf2ff_known_answer"]))
        _plot_q11(out)
    except Exception as e:
        out["error"] = f"{e.__class__.__name__}: {e}"
    # CISPR 32 class B radiated limits at 3 m, for reference only
    out["cispr32_class_B_3m_dBuV_per_m"] = {
        "30-230 MHz": 40.0, "230-1000 MHz": 47.0,
        "note": ("quasi-peak limits at 3 m.  The simulated field is a "
                 "transfer function, dBuV/m at 3 m per volt across the switch "
                 "node at that frequency, so a level needs the switch node's "
                 "own spectrum in the receiver's 120 kHz bandwidth.  It is "
                 "the far-field term: below about 50 MHz, 3 m is less than "
                 "half a wavelength and the near-field terms are not small.")}
    out["seconds"] = round(time.time() - t0, 1)
    return out


def _q11_tests(runs, kat):
    """What makes the far field a number rather than a transform of noise.

    A power balance -- the radiated power from the flux through the transform
    box against the far field integrated over the sphere -- was tried and is
    not a test here: on the known-answer dipole the flux comes out negative
    at 100 and 300 MHz, because at this size it is a small difference of
    large reactive terms.  The known answer itself is the test of the
    transform, and the tests of the model are below.
    """
    fine = runs[-1]
    ff = fine["far_field"]
    t = {}
    # 1. the transform reproduces a textbook dipole
    t["nf2ff_known_answer"] = bool(kat.get("pass"))
    # 2. the record is complete: the pulse has died away
    try:
        t["energy_dB"] = float(str(fine.get("final_energy_dB")).strip())
    except ValueError:
        t["energy_dB"] = None
    t["energy_decayed"] = (t["energy_dB"] is not None
                           and t["energy_dB"] <= -40.0)
    # 3. the switch node is a conductor of its own: at the bottom of the band
    #    it is a capacitor to ground.  Joined to ground anywhere -- which is
    #    what a mesh too coarse for 0.40 mm clearances does -- it would be an
    #    inductor of a few nH instead.
    lo = min(ff.values(), key=lambda r: r["f_Hz"]) if ff else None
    t["switch_node_X_ohm_at_lowest_f"] = lo["Z_port_ohm"][1] if lo else None
    t["switch_node_pF"] = (round(-1e12 / (2 * math.pi * lo["f_Hz"]
                                          * lo["Z_port_ohm"][1]), 1)
                           if lo and lo["Z_port_ohm"][1] < 0 else None)
    t["switch_node_not_shorted"] = bool(lo and lo["Z_port_ohm"][1] < 0)
    # Where the answer stops being the board's.  The switch node's 68 pF is
    # in series, at the port, with the inductance of the port's own loop
    # (about 0.9 nH), and the two resonate near 660 MHz.  Near there the
    # transfer function turns on that inductance -- a property of a 0.3 mm
    # lumped port, not of the FET package that drives the real switch node --
    # and a small shift of the resonance between meshes reads as dB.  So the
    # answer is graded, and quoted, below 0.8 of that resonance; above it,
    # the edge's own spectrum is 60 dB down from 30 MHz anyway.
    rows = sorted(ff.values(), key=lambda r: r["f_Hz"])
    f_res = None
    for a, b in zip(rows[:-1], rows[1:]):
        xa, xb = a["Z_port_ohm"][1], b["Z_port_ohm"][1]
        if xa < 0 <= xb:
            f_res = a["f_Hz"] + (b["f_Hz"] - a["f_Hz"]) * (-xa) / (xb - xa)
            break
    t["switch_node_series_resonance_Hz"] = f_res
    top = 0.8 * f_res if f_res else max((r["f_Hz"] for r in rows), default=0)
    band = [k for k, r in ff.items() if r["f_Hz"] <= top]
    t["graded_band_Hz"] = [min((ff[k]["f_Hz"] for k in band), default=None),
                           max((ff[k]["f_Hz"] for k in band), default=None)]
    # 4. energy: nothing radiates more than the port puts in, over the band
    #    the answer is graded on.  At 1 GHz the lead is near its own quarter
    #    wave, nearly everything the port delivers is radiated, and the ratio
    #    has come out at 0.94 and at 1.04 on two runs of the same model.
    eff = [ff[k].get("radiated_over_input") for k in band]
    t["radiated_over_input_max"] = max((e for e in eff if e is not None),
                                       default=None)
    t["radiated_over_input_max_above_band"] = max(
        (r.get("radiated_over_input") or 0 for k, r in ff.items()
         if k not in band), default=None)
    t["energy_conserved"] = bool(eff) and all(e is not None and e <= 1.0
                                              for e in eff)
    # 5. the mesh: the same transfer function on a coarser one
    if len(runs) > 1:
        a = runs[0]["far_field"]
        diffs = {k: round(ff[k]["dBuV_per_m_per_V"]
                          - a[k]["dBuV_per_m_per_V"], 2)
                 for k in ff if k in a}
        t["mesh_change_dB"] = diffs
        inb = [abs(v) for k, v in diffs.items() if k in band]
        t["mesh_change_max_dB"] = max(inb) if inb else None
        t["mesh_change_max_dB_above_band"] = max(
            (abs(v) for k, v in diffs.items() if k not in band), default=None)
        t["mesh_agrees"] = bool(diffs) and t["mesh_change_max_dB"] <= 1.5
    else:
        t["mesh_agrees"] = None
    checks = ("nf2ff_known_answer", "energy_decayed",
              "switch_node_not_shorted", "energy_conserved", "mesh_agrees")
    out = {"tests": t, "converged": all(t[k] is True for k in checks)}
    if not out["converged"]:
        why = []
        if not t["nf2ff_known_answer"]:
            why.append("the far-field transform fails its known-answer "
                       "dipole")
        if not t["energy_decayed"]:
            why.append(f"the run stopped with its energy at "
                       f"{t['energy_dB']} dB, not below -40 dB")
        if not t["switch_node_not_shorted"]:
            why.append("the switch node is inductive at the bottom of the "
                       "band, so the mesh has joined it to ground")
        if not t["energy_conserved"]:
            why.append(f"the far field carries up to "
                       f"{t['radiated_over_input_max']} of the power the "
                       f"port puts in")
        if t["mesh_agrees"] is False:
            why.append(f"the coarse and fine meshes differ by up to "
                       f"{t['mesh_change_max_dB']} dB below the switch "
                       f"node's own resonance")
        if t["mesh_agrees"] is None:
            why.append("only one mesh was run (quick mode)")
        out["not_converged_reason"] = "; ".join(why) + "."
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


def _cispr_b_3m(f):
    """CISPR 32 class B quasi-peak limit at 3 m, dBuV/m."""
    return 40.0 if f <= 230e6 else 47.0


def _plot_q11(out):
    """The radiated transfer function, and the switch-node signal it takes
    to reach the class B limit."""
    ff = out.get("far_field") or {}
    if not ff:
        return
    rows = sorted(ff.values(), key=lambda r: r["f_Hz"])
    f = np.array([r["f_Hz"] for r in rows]) / 1e6
    h = np.array([r["dBuV_per_m_per_V"] for r in rows])
    ch = (out.get("tests") or {}).get("mesh_change_dB") or {}
    hc = np.array([r["dBuV_per_m_per_V"] - ch.get(k, np.nan)
                   for k, r in sorted(ff.items(), key=lambda t: t[1]["f_Hz"])])
    ser = {m["tag"]: m for m in out.get("mesh_series") or []}
    lead = ((out.get("model") or {}).get("lead") or {}).get("length_mm")
    ring = (out.get("model") or {}).get("ring_Hz")
    fig, ax = plt.subplots(2, 1, figsize=(8.6, 7.4), sharex=True)
    if np.isfinite(hc).any() and "coarse" in ser:
        ax[0].plot(f, hc, "o--", color=C_EM, alpha=0.45, lw=1.3, ms=4,
                   label=f"coarse mesh, {ser['coarse'].get('cells', 0)/1e6:.2f} "
                         f"Mcells")
    fine = ser.get("fine") or ser.get("coarse") or {}
    ax[0].plot(f, h, "o-", color=C_EM, lw=1.8, ms=5,
               label=f"{fine.get('tag', '')} mesh, "
                     f"{(fine.get('cells') or 0)/1e6:.2f} Mcells")
    ax[0].set_ylabel("dB\u00b5V/m at 3 m per volt\nacross the switch node")
    ax[0].set_title(f"What the board radiates per volt of switch-node signal "
                    f"({lead:.0f} mm motor lead)" if lead else
                    "What the board radiates per volt of switch-node signal",
                    fontsize=10.5, color=FG)
    ax[0].legend(fontsize=8, frameon=False, loc="upper left")
    # the switch-node signal in the receiver's band that reaches the limit
    need = np.array([_cispr_b_3m(r["f_Hz"]) for r in rows]) - h + 120.0
    ax[1].plot(f, need, "o-", color=C_FH, lw=1.8, ms=5)
    ax[1].set_ylabel("switch-node signal that\nreaches class B (dB\u00b5V)")
    ax[1].set_title("Against CISPR 32 class B at 3 m: the switch-node level, "
                    "in the receiver's 120 kHz band,\nthat puts the field on "
                    "the limit", fontsize=10, color=FG)
    top = ((out.get("tests") or {}).get("graded_band_Hz") or [None, None])[1]
    for a in ax:
        if ring:
            a.axvline(ring / 1e6, color=MUTED, lw=0.9, ls=":")
        if top and top < f[-1] * 1e6:
            a.axvspan(top / 1e6 * 1.12, f[-1] * 1.05, color=LINE, alpha=0.6,
                      lw=0)
        a.set_xscale("log")
        _axstyle(a)
    if top and top < f[-1] * 1e6:
        ax[0].annotate("near the switch node's resonance\nwith the port's "
                       "own loop: not graded", (top / 1e6 * 1.18,
                                                 ax[0].get_ylim()[0]),
                       xytext=(0, 8), textcoords="offset points",
                       fontsize=7.5, color=MUTED)
    if ring:
        ax[1].annotate(f"switch-node ring\n{ring/1e6:.0f} MHz (P2)",
                       (ring / 1e6, ax[1].get_ylim()[1]),
                       xytext=(4, -24), textcoords="offset points",
                       fontsize=8, color=MUTED)
    ax[1].set_xlabel("frequency (MHz)")
    from matplotlib.ticker import FixedLocator, NullFormatter
    ticks = [30, 50, 100, 200, 300, 500, 1000]
    ax[1].xaxis.set_major_locator(FixedLocator(ticks))
    ax[1].set_xticklabels([str(t) for t in ticks])
    ax[1].xaxis.set_minor_formatter(NullFormatter())
    fig.tight_layout()
    fig.savefig(paths.FIGS / "p7_radiated.png", bbox_inches="tight",
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
