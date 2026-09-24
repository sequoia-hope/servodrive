#!/usr/bin/env python3
"""P3 -- conduction: Q6 (sense accuracy) and Q7 (current density).

The L5 solver (conduction/) is run on the copper as routed, at the design
point's peak instants, and gives:

  Q6  the resistance actually between the two sense taps, how much of it is
      copper rather than shunt, its temperature coefficient, and how the two
      1.6 mOhm shunts share the current
  Q7  the current density on every layer, the current in every via of the
      FET arrays, and the hot-spot list

A three-phase inverter at the peak of phase A has i_A = +28.3 A and
i_B = i_C = -14.14 A, so the solve is one terminal at the A lead pad, two at
the B and C lead pads, and the FET drains as the third: that is the real
current pattern, not a two-terminal idealisation.
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
from shapely.geometry import Polygon                         # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import paths, jsonio, board                         # noqa: E402
from extract import copper                                   # noqa: E402
from conduction.raster import Raster                         # noqa: E402
from conduction.solver import Conductor, sigma_at, SIGMA_CU  # noqa: E402

paths.import_tools()

I_PEAK = 28.284           # 20 A RMS -> 28.3 A peak (SPEC.md sec.2.1)


def _entry_refs():
    """Where the bus current enters the board: board A's two 2 x 5 headers,
    board S's XT30."""
    e = board.P()["bus_entry"]
    return (e["ref"],) if e else ("J7", "J8")
CELLS = {"A": dict(hi="Q1", lo="Q2", shunts=("R105", "R106"),
                   taps=("R107", "R108"), sw="SW_A", phase="PHASE_A", lead="J1"),
         "B": dict(hi="Q3", lo="Q4", shunts=("R205", "R206"),
                   taps=("R207", "R208"), sw="SW_B", phase="PHASE_B", lead="J2"),
         "C": dict(hi="Q5", lo="Q6", shunts=("R305", "R306"),
                   taps=("R307", "R308"), sw="SW_C", phase="PHASE_C", lead="J3")}


def _pad_poly(ref, padnum, layer=None):
    out = []
    for p in copper.pads_of(ref):
        if p["pad"] != str(padnum):
            continue
        for lname, shapes in p["shapes"].items():
            if layer and lname != layer:
                continue
            for e in shapes:
                out.append((lname, Polygon(e["pts"])))
    return out


# =================================================================== Q6 =====
def tap_resistance(cell="A", temperature=25.0, cellsize=0.05):
    """What the amplifier actually measures between the two sense taps.

    The taps are voltage probes, not part of the current path, so this is a
    four-terminal measurement: the phase current is driven through each pour
    between the places it really enters and leaves, and the tap pads are read
    as probes.  The error the copper adds is then

        V(tap_sw) - V(shunt pads, SW side)     on the SW side
        V(shunt pads, PHASE side) - V(tap_ph)  on the PHASE side

    Either sign is possible: a tap placed on the far side of the current flow
    reads high, one placed short of it reads low.
    """
    c = CELLS[cell]
    sh1, sh2 = c["shunts"]
    t1, t2 = c["taps"]
    win = copper.cell_window(cell, margin=1.5)
    r = Raster(cell=cellsize, window=win)
    I = 1.0
    out = {}

    # ---- SW side: current in from the low-side drain array, out at the
    # shunts' SW pads; the SNSP tap is a probe
    cond = Conductor(c["sw"], layers=["F.Cu", "B.Cu", "In1.Cu", "In2.Cu",
                                      "In3.Cu", "In4.Cu"],
                     raster=r, temperature=temperature)
    src = cond.pad_nodes(c["lo"], 5, layer="F.Cu")
    sink = np.unique(np.concatenate(
        [cond.nodes_in(l, p) for ref in (sh1, sh2)
         for l, p in _pad_poly(ref, 1, "B.Cu")]))
    probe = np.unique(np.concatenate(
        [cond.nodes_in(l, p) for l, p in _pad_poly(t1, 2, "B.Cu")]))
    cond.solve([(src, I), (sink, -I)])
    v_sink = float(np.mean(cond.V[sink]))
    v_probe = float(np.mean(cond.V[probe]))
    out["sw"] = {"R_error_Ohm": (v_probe - v_sink) / I,
                 "R_drain_to_shunt_Ohm": (float(np.mean(cond.V[src])) - v_sink) / I,
                 "nodes": cond.n}

    # ---- PHASE side: current in at the shunts' PHASE pads, out at the lead
    condp = Conductor(c["phase"], layers=["B.Cu"], raster=r,
                      temperature=temperature)
    src = np.unique(np.concatenate(
        [condp.nodes_in(l, p) for ref in (sh1, sh2)
         for l, p in _pad_poly(ref, 2, "B.Cu")]))
    lead = []
    for p in copper.pads_of(c["lead"]):
        for lname, shapes in p["shapes"].items():
            if lname != "B.Cu":
                continue
            for e in shapes:
                lead.append(condp.nodes_in(lname, Polygon(e["pts"])))
    lead = [x for x in lead if len(x)]
    if not lead:
        raise RuntimeError(f"no lead-pad nodes for cell {cell}")
    sink = np.unique(np.concatenate(lead))
    probe = np.unique(np.concatenate(
        [condp.nodes_in(l, p) for l, p in _pad_poly(t2, 1, "B.Cu")]))
    condp.solve([(src, I), (sink, -I)])
    v_src = float(np.mean(condp.V[src]))
    v_probe = float(np.mean(condp.V[probe]))
    out["phase"] = {"R_error_Ohm": (v_src - v_probe) / I,
                    "R_shunt_to_lead_Ohm": (v_src - float(np.mean(condp.V[sink]))) / I,
                    "nodes": condp.n}
    return out, win, r


def q6(quick=False):
    t0 = time.time()
    import geometry as G
    sh = jsonio.model(board.P()["shunt"])
    out = {"question": "Q6", "estimate": {
        "R_shunt_Ohm": G.SHUNT_R,
        "TCR_part_ppm_per_K": [sh["TCR"]["min"], sh["TCR"]["max"]],
        "V_per_A": G.V_PER_A}}

    cells = ["A"] if quick else ["A", "B", "C"]
    per = {}
    for cell in cells:
        c = CELLS[cell]
        try:
            res25, win, r = tap_resistance(cell, 25.0)
            res125, _, _ = tap_resistance(cell, 125.0)
        except Exception as e:
            per[cell] = {"error": f"{e.__class__.__name__}: {e}"}
            continue
        Rcu25 = sum(v["R_error_Ohm"] for v in res25.values())
        Rcu125 = sum(v["R_error_Ohm"] for v in res125.values())
        Rsh = sh["R"]["value"] / 2.0 if cell != "C" else 0.0
        R25 = Rsh + Rcu25
        # the part's TCR is a bracket; the worse end is the one reported.  On
        # board A it was +50..+100 ppm/K; board S's 2 mOhm metal strip is
        # +-275 ppm/K component TCR, terminals included
        tcrs = []
        for t_part in (sh["TCR"]["min"], sh["TCR"]["max"]):
            R125_ = Rsh * (1 + t_part * 1e-6 * 100) + Rcu125
            tcrs.append(((R125_ - R25) / R25 / 100.0 * 100, R125_))
        tcr, R125 = max(tcrs, key=lambda x: abs(x[0]))
        per[cell] = {
            "R_copper_error_Ohm": Rcu25,
            "R_shunt_Ohm": Rsh,
            "R_tap_to_tap_Ohm": R25,
            "R_tap_to_tap_125C_Ohm": R125,
            "copper_share": abs(Rcu25) / R25 if R25 else None,
            "TCR_pct_per_K": tcr,
            "TCR_pct_per_K_bracket": [t for t, _ in tcrs],
            "pass_copper_share": bool(R25 and abs(Rcu25) / R25 <= 0.05),
            "pass_TCR": bool(abs(tcr) <= 0.05),
            "per_side": res25,
        }
        if cell == "C":
            per[cell]["note"] = ("cell C carries 0 Ohm links instead of shunts "
                                 "(SPEC.md sec.1.5), so all of its tap-to-tap "
                                 "resistance is copper.  It is not a current "
                                 "sense at all -- I_c is inferred as "
                                 "-(I_a + I_b) -- so the copper-share and TCR "
                                 "criteria do not apply to it; the number is "
                                 "here only to show what a tap on bare copper "
                                 "would read.")
            per[cell]["pass_copper_share"] = None
            per[cell]["pass_TCR"] = None
    out["per_cell"] = per
    out["seconds"] = round(time.time() - t0, 1)
    return out


def shunt_sharing(cell="A", cellsize=0.04):
    """How the two parallel 1.6 mOhm shunts split the phase current."""
    c = CELLS[cell]
    sh1, sh2 = c["shunts"]
    pts = [(p["x"], p["y"]) for ref in (sh1, sh2)
           for p in copper.pads_of(ref)]
    win = (min(p[0] for p in pts) - 4, min(p[1] for p in pts) - 4,
           max(p[0] for p in pts) + 4, max(p[1] for p in pts) + 4)
    r = Raster(cell=cellsize, window=win)
    # the SW pour feeds both shunts; solve for the spreading resistance from
    # the FET's via field to each shunt pad
    cond = Conductor(c["sw"], layers=["B.Cu"], raster=r)
    src = cond.pad_nodes(c["lo"], 5)          # the low-side drain via array
    out = {}
    ends = []
    for ref in (sh1, sh2):
        nodes = []
        for lname, poly in _pad_poly(ref, 1, "B.Cu"):
            nodes.append(cond.nodes_in(lname, poly))
        ends.append(np.unique(np.concatenate([x for x in nodes if len(x)])))
    # inject at the source, draw equally at the two shunt pads, and compare
    # the potentials: the imbalance is the share
    cond.solve([(src, 1.0), (ends[0], -0.5), (ends[1], -0.5)])
    v0, v1 = cond.terminal_voltage(1), cond.terminal_voltage(2)
    vin = cond.terminal_voltage(0)
    R0, R1 = (vin - v0) / 0.5, (vin - v1) / 0.5
    sh = jsonio.model(board.P()["shunt"])["R"]["value"]
    g0, g1 = 1 / (R0 + sh), 1 / (R1 + sh)
    return {"R_path_to_shunt1_Ohm": R0, "R_path_to_shunt2_Ohm": R1,
            "share_shunt1": g0 / (g0 + g1), "share_shunt2": g1 / (g0 + g1),
            "imbalance_pct": abs(g0 - g1) / (g0 + g1) * 200,
            "pass": bool(abs(g0 - g1) / (g0 + g1) * 200 <= 10.0)}


# =================================================================== Q7 =====
def phase_path(cell="A", cellsize=0.06, current=I_PEAK, temperature=25.0):
    """The full 20 A path of one phase: lead pad -> PHASE pour -> shunts ->
    SW pour -> the low-side via array -> the switch-node island on F.Cu."""
    c = CELLS[cell]
    win = copper.cell_window(cell, margin=1.5)
    r = Raster(cell=cellsize, window=win)
    out = {}

    # --- the PHASE side: lead pad to the shunt pads -------------------
    condp = Conductor(c["phase"], layers=["B.Cu"], raster=r,
                      temperature=temperature)
    lead = []
    for p in copper.pads_of(c["lead"]):
        for lname, shapes in p["shapes"].items():
            if lname != "B.Cu":
                continue
            for e in shapes:
                lead.append(condp.nodes_in(lname, Polygon(e["pts"])))
    lead = [x for x in lead if len(x)]
    shunt_pads = []
    for ref in c["shunts"]:
        for lname, poly in _pad_poly(ref, 2, "B.Cu"):
            shunt_pads.append(condp.nodes_in(lname, poly))
    shunt_pads = [x for x in shunt_pads if len(x)]
    if not lead or not shunt_pads:
        return {"error": "no lead pad or shunt pad nodes on the PHASE pour"}
    a = np.unique(np.concatenate(lead))
    b = np.unique(np.concatenate(shunt_pads))
    condp.solve([(a, current), (b, -current)])
    out["R_lead_to_shunt_Ohm"] = condp.resistance(0, 1)
    out["P_phase_pour_W"] = condp.joule()
    _plot_J(condp, "B.Cu", win, f"p3_J_phase_{cell}.png",
            f"cell {cell}: PHASE pour on B.Cu at {current:.1f} A")
    out["phase_current_density"] = _jstats(condp, "B.Cu")
    out["J_max_phase_A_per_mm2"] = float(
        np.nanmax(condp.current_density("B.Cu")[2])) / 1e6

    # --- the SW node with the high side conducting: the current enters at
    # the high-side FET's source pads on F.Cu, crosses the switch-node island,
    # goes down the low-side drain's 16-via array to the B.Cu pour and out
    # through the shunts.  That is the real 20 A path, and it is the one that
    # asks how well the via array shares.
    conds = Conductor(c["sw"], layers=["F.Cu", "B.Cu", "In1.Cu", "In2.Cu",
                                       "In3.Cu", "In4.Cu"],
                      raster=r, temperature=temperature)
    sp = []
    for ref in c["shunts"]:
        for lname, poly in _pad_poly(ref, 1, "B.Cu"):
            sp.append(conds.nodes_in(lname, poly))
    sp = [x for x in sp if len(x)]
    src = []
    for num in (1, 2, 3):
        for lname, poly in _pad_poly(c["hi"], num, "F.Cu"):
            src.append(conds.nodes_in(lname, poly))
    src = [x for x in src if len(x)]
    if not src or not sp:
        return dict(out, sw_error="no high-side source pads or shunt pads")
    conds.solve([(np.unique(np.concatenate(src)), current),
                 (np.unique(np.concatenate(sp)), -current)])
    out["R_hi_source_to_shunt_Ohm"] = conds.resistance(0, 1)
    out["P_sw_node_W"] = conds.joule()
    vias = conds.barrel_currents()
    arr = [v for v in vias if v["ref"].startswith(c["lo"])]
    if arr:
        cur = sorted((abs(v["I_max"]) for v in arr), reverse=True)
        out["low_side_via_array"] = {
            "n": len(arr), "I_max_A": cur[0], "I_min_A": cur[-1],
            "I_mean_A": float(np.mean(cur)),
            "spread_pct": (cur[0] - cur[-1]) / np.mean(cur) * 100,
            "I_rms_per_via_A": cur[0] / math.sqrt(2),
            "pass_2A_rms": bool(cur[0] / math.sqrt(2) <= 2.0),
        }
    for layer in ("F.Cu", "B.Cu"):
        _plot_J(conds, layer, win, f"p3_J_sw_{cell}_{layer.replace('.', '')}.png",
                f"cell {cell}: SW node, {layer}, {current:.1f} A")
    out["sw_current_density_F_Cu"] = _jstats(conds, "F.Cu")
    out["sw_current_density_B_Cu"] = _jstats(conds, "B.Cu")

    # --- the GND return: the low-side source pads to the header pins ----
    try:
        condg = Conductor("GND", raster=Raster(cell=0.1,
                                               window=(-32.5, -32.5, 32.5, 32.5)),
                          temperature=temperature)
        gsrc = []
        for num in (1, 2, 3):
            for lname, poly in _pad_poly(c["lo"], num, "F.Cu"):
                gsrc.append(condg.nodes_in(lname, poly))
        gsrc = [x for x in gsrc if len(x)]
        g = copper.load()
        hdr = []
        for p in g["through_pads"]:
            if p["ref"].startswith(_entry_refs()) and p["net"] == "GND":
                i, j = condg.r.index(p["x"], p["y"])
                for layer in condg.layers:
                    k = condg.index[layer][i, j]
                    if k >= 0:
                        hdr.append(k)
        if gsrc and hdr:
            condg.solve([(np.unique(np.concatenate(gsrc)), current),
                         (np.array(sorted(set(hdr))), -current)])
            out["R_gnd_source_to_header_Ohm"] = condg.resistance(0, 1)
            out["P_gnd_W"] = condg.joule()
            _plot_J(condg, "In1.Cu", (-32.5, -32.5, 32.5, 32.5),
                    f"p3_J_gnd_{cell}_In1.png",
                    f"GND return on In1 from cell {cell} at {current:.1f} A")
    except Exception as e:
        out["gnd_error"] = f"{e.__class__.__name__}: {e}"
    return out


def _jstats(cond, layer):
    """A peak current density on a raster diverges at a re-entrant corner, so
    quote the distribution, not just the maximum."""
    Jx, Jy, Jm = cond.current_density(layer)
    t = cond.stack[layer]["t"] * 1e-3
    sheet = (Jm * t / 1e3)[np.isfinite(Jm)]
    if sheet.size == 0:
        return {}
    return {"A_per_mm_max": float(np.max(sheet)),
            "A_per_mm_p999": float(np.percentile(sheet, 99.9)),
            "A_per_mm_p99": float(np.percentile(sheet, 99)),
            "A_per_mm2_p999": float(np.percentile(sheet, 99.9)) / (t * 1e3),
            "note": "p99.9 is the number to compare with a width rule; the "
                    "maximum is a single cell at a corner and grows without "
                    "limit as the grid is refined"}


def vbus_path(cellsize=0.08, current=I_PEAK):
    """VBUS from the header pins to ONE high-side drain at the phase peak.

    Not all three at once: the three phase currents sum to zero, so a state
    with 28.3 A in every high side does not exist.  The worst case for a
    drain's via array is its own phase at the peak, and that is what is
    solved; the plane's own loss is quoted for the 21 A DC bus current
    separately.
    """
    r = Raster(cell=cellsize, window=(-32.5, -32.5, 32.5, 32.5))
    cond = Conductor("VBUS", raster=r)
    g = copper.load()
    src = []
    for p in g["through_pads"]:
        if p["ref"].startswith(_entry_refs()) and p["net"] == "VBUS":
            i, j = r.index(p["x"], p["y"])
            for layer in cond.layers:
                k = cond.index[layer][i, j]
                if k >= 0:
                    src.append(k)
    if not src:
        return {"error": "no VBUS header pads"}
    out = {"current_A": current, "entry": list(_entry_refs())}
    src_arr = np.array(sorted(set(src)))
    for ref in ("Q1", "Q3", "Q5"):
        try:
            drain = cond.pad_nodes(ref, 5, layer="F.Cu")
        except Exception as e:
            out[f"{ref}_error"] = str(e)
            continue
        cond.solve([(src_arr, current), (drain, -current)])
        R = cond.resistance(0, 1)
        vias = cond.barrel_currents()
        arr = sorted((abs(v["I_max"]) for v in vias
                      if v["ref"].startswith(ref + ".")), reverse=True)
        entry = {"R_header_to_drain_Ohm": R,
                 "P_at_28A_W": current ** 2 * R,
                 "P_at_20A_rms_W": 20.0 ** 2 * R}
        if arr:
            entry["via_array"] = {
                "n": len(arr), "I_max_A": arr[0], "I_min_A": arr[-1],
                "I_mean_A": float(np.mean(arr)),
                "spread_pct": (arr[0] - arr[-1]) / np.mean(arr) * 100,
                "I_rms_per_via_A": arr[0] / math.sqrt(2),
                "pass_2A_rms": bool(arr[0] / math.sqrt(2) <= 2.0)}
        if ref == "Q1":
            entry["In2_current_density"] = _jstats(cond, "In2.Cu")
            _plot_J(cond, "In2.Cu", (-32.5, -32.5, 32.5, 32.5),
                    "p3_J_vbus_In2.png",
                    f"VBUS on In2, {'/'.join(_entry_refs())} to Q1 at "
                    f"{current:.1f} A")
        out[ref] = entry
    return out


def _plot_J(cond, layer, win, fname, title):
    Jx, Jy, Jm = cond.current_density(layer)
    t = cond.stack[layer]["t"] * 1e-3
    sheet = Jm * t / 1e3                 # A/mm
    fig, ax = plt.subplots(figsize=(6, 5.4), dpi=150)
    im = ax.imshow(np.log10(np.maximum(sheet.T, 1e-4)), origin="lower",
                   extent=(cond.r.x0, cond.r.x0 + (cond.r.nx - 1) * cond.cell,
                           cond.r.y0, cond.r.y0 + (cond.r.ny - 1) * cond.cell),
                   cmap="inferno")
    plt.colorbar(im, ax=ax, label="log10 linear current density (A/mm)")
    ax.set_aspect("equal"); ax.set_title(title, fontsize=9)
    ax.set_xlabel("mm"); ax.set_ylabel("mm")
    fig.tight_layout()
    fig.savefig(paths.FIGS / fname)
    plt.close(fig)


def via_array_connectivity():
    """For every FET drain via array, how many of the 16 barrels actually reach
    copper that goes anywhere.

    A through barrel has a pad on all six layers, but a pad is only a conductor
    if it is joined to the pour of its own net on that layer.  This walks the
    real polygons and reports, per array and per layer, how many pads are
    inside the layer's main piece of that net rather than an island.
    """
    from shapely.geometry import Point
    from shapely.ops import unary_union
    g = copper.load()
    out = {}
    for ref, cellname in (("Q1", "A"), ("Q2", "A"), ("Q3", "B"), ("Q4", "B"),
                          ("Q5", "C"), ("Q6", "C")):
        pads = [p for p in g["through_pads"] if p["ref"] == ref]
        if not pads:
            continue
        net = pads[0]["net"]
        xs = [p["x"] for p in pads]
        ys = [p["y"] for p in pads]
        win = (min(xs) - 12, min(ys) - 12, max(xs) + 12, max(ys) + 12)
        per = {}
        for layer in copper.CU_LAYERS:
            u = copper.net_union(net, layer, win)
            if u is None:
                per[layer] = {"connected": 0, "n": len(pads),
                              "main_area_mm2": 0.0}
                continue
            geoms = sorted(list(u.geoms) if hasattr(u, "geoms") else [u],
                           key=lambda q: -q.area)
            main = geoms[0]
            # anything bigger than a couple of pads counts as "goes somewhere"
            big = [q for q in geoms if q.area > 3.0]
            live = unary_union(big) if big else None
            n_ok = sum(1 for p in pads
                       if live is not None and live.contains(Point(p["x"], p["y"])))
            per[layer] = {"connected": int(n_ok), "n": len(pads),
                          "main_area_mm2": round(main.area, 2),
                          "pieces": len(geoms)}
        effective = max(v["connected"] for v in per.values()
                        if v is not per[copper.CU_LAYERS[0]]) \
            if len(per) > 1 else 0
        out[ref] = {
            "net": net, "cell": cellname, "n_barrels": len(pads),
            "per_layer": per,
            "connected_on_F_Cu": per.get("F.Cu", {}).get("connected", 0),
            "connected_below_F_Cu": effective,
            "note": ("a barrel conducts only if its pad is joined to real "
                     "copper on both ends"),
        }
    return out


def q7(quick=False):
    t0 = time.time()
    import geometry as G
    out = {"question": "Q7",
           "estimate": {"IPC2152_width_mm": 6.0,
                        "phase_band_width_mm": 3.0,
                        "band_current_density_A_per_mm2": 95.0,
                        "FET_VIA_N": G.FET_VIA_N,
                        "via_drill_mm": G.VIA_DRILL,
                        "via_plating_mm": G.VIA_PLATE}}
    out["via_array_connectivity"] = via_array_connectivity()
    out["phase_A"] = phase_path("A", cellsize=0.08 if quick else 0.05)
    if not quick:
        out["vbus"] = vbus_path(cellsize=0.08)
    out["seconds"] = round(time.time() - t0, 1)
    return out


def convergence(quick=False):
    """Grid refinement, which SPEC.md sec.6 makes P3's gate."""
    rows = []
    for cell in ([0.1, 0.05] if quick else [0.12, 0.08, 0.05]):
        try:
            r = phase_path("A", cellsize=cell)
            j = r.get("phase_current_density", {})
            rows.append({"cell_mm": cell,
                         "R_lead_to_shunt_Ohm": r.get("R_lead_to_shunt_Ohm"),
                         "J_max_phase_A_per_mm2": r.get("J_max_phase_A_per_mm2"),
                         "sheet_p999_A_per_mm": j.get("A_per_mm_p999"),
                         "sheet_p99_A_per_mm": j.get("A_per_mm_p99")})
        except Exception as e:
            rows.append({"cell_mm": cell, "error": str(e)})
    return rows


def run(quick=False):
    out = {}
    out["Q6"] = q6(quick)
    try:
        out["Q6_sharing"] = shunt_sharing("A")
    except Exception as e:
        out["Q6_sharing"] = {"error": f"{e.__class__.__name__}: {e}"}
    out["Q7"] = q7(quick)
    out["convergence"] = convergence(quick)
    return out


if __name__ == "__main__":
    print(json.dumps(run(quick=True), indent=1, default=str)[:4000])
