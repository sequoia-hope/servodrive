#!/usr/bin/env python3
"""L7 (optional) -- the thermal path.

Heat conduction on a multilayer board is the same Laplacian the L5 conduction
solver already does: replace sigma with the thermal conductivity and current
with power, and the same raster gives a thermal resistance.  Only the two
numbers the design leans on are solved here -- the via array under a drain pad
and the lateral spreading into the heatsink land -- because a full
three-dimensional model of the stack is out of scope (SPEC.md sec.4).
"""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import paths                                        # noqa: E402
from extract import copper                                   # noqa: E402

paths.import_tools()


K_CU = 385.0
K_FR4 = 0.3
K_PLATED = 385.0 * (4.7 / 5.8)      # the same derating the barrels get


def via_array(ref="Q1"):
    """The thermal resistance of one drain pad's via field, solved from the
    real barrel geometry rather than from n parallel cylinders."""
    import geometry as G
    g = copper.load()
    pads = [p for p in g["through_pads"] if p["ref"] == ref]
    if not pads:
        return {"error": f"no plated pads on {ref}"}
    stack = copper.stackup(g)
    d = (stack["B.Cu"]["z"] - stack["F.Cu"]["z"]) * 1e-3
    a_barrel = copper.barrel_area(pads[0]["drill"]) * 1e-6
    # the barrels in parallel through the board
    R_barrels = d / (K_PLATED * a_barrel * len(pads))
    # the FR4 in parallel with them, over the pad's own area
    xs = [p["x"] for p in pads]
    ys = [p["y"] for p in pads]
    area = ((max(xs) - min(xs) + pads[0]["dia"])
            * (max(ys) - min(ys) + pads[0]["dia"])) * 1e-6
    a_fr4 = max(area - len(pads) * math.pi * (pads[0]["dia"] / 2 * 1e-3) ** 2,
                0.0)
    R_fr4 = d / (K_FR4 * a_fr4) if a_fr4 > 0 else float("inf")
    # and the six copper layers, which conduct sideways, not through
    R_total = 1.0 / (1.0 / R_barrels + 1.0 / R_fr4)
    r_est, r_one = G.via_thermal()
    return {"ref": ref, "n_barrels": len(pads),
            "barrel_area_mm2": a_barrel * 1e6,
            "R_barrels_K_per_W": R_barrels,
            "R_fr4_K_per_W": R_fr4,
            "R_total_K_per_W": R_total,
            "estimate_geometry_py_K_per_W": r_est,
            "R_one_barrel_K_per_W": r_one,
            "ratio_to_estimate": R_total / r_est if r_est else None}


def spreading(power_per_fet=None):
    """Lateral spreading from a drain pad out to the heatsink land, solved on
    the copper the board actually has."""
    from conduction.raster import Raster
    from conduction.solver import Conductor
    import geometry as G
    # VBUS on F.Cu is only the drain-pad islands -- there is no lateral path
    # there at all.  The spreading that matters is the GND pour on F.Cu, from
    # the low-side source pads out to the bare heatsink land at R 29.2-31.5,
    # which is where the aluminium ring clamps.
    from shapely.geometry import Polygon
    from phases.p3_conduction import _pad_poly
    win = copper.cell_window("A", margin=1.0)
    r = Raster(cell=0.08, window=win)
    out = {}
    try:
        cond = Conductor("GND", layers=["F.Cu"], raster=r)
        # reuse the conduction solver with copper's thermal conductivity by
        # scaling: R_thermal = R_electrical * sigma / k
        src = []
        for num in (1, 2, 3):
            for lname, poly in _pad_poly("Q2", num, "F.Cu"):
                src.append(cond.nodes_in(lname, poly))
        src = np.unique(np.concatenate([x for x in src if len(x)]))
        edge = cond.nodes_in("F.Cu", lambda x, y: math.hypot(x, y) > 29.2)
        if len(edge) == 0:
            return {"error": "no F.Cu GND at the heatsink land radius"}
        cond.solve([(src, 1.0), (edge, -1.0)])
        R_e = cond.resistance(0, 1)
        R_th = R_e * cond.sigma / K_CU
        out = {"R_electrical_Ohm": R_e, "R_thermal_K_per_W": R_th,
               "n_nodes": cond.n, "n_source_nodes": int(len(src)),
               "n_sink_nodes": int(len(edge)),
               "estimate_K_per_W": 1.0,
               "note": ("F.Cu GND only, from the low-side source pads to the "
                        "heatsink land at R > 29.2 mm.  The other five layers "
                        "conduct in parallel and are not in this number, so it "
                        "is an upper bound on that one path; the design's own "
                        "estimate for spreading is 1.0 K/W for the whole "
                        "stack.")}
    except Exception as ex:
        out = {"error": f"{ex.__class__.__name__}: {ex}"}
    return out


