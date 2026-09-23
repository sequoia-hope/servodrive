#!/usr/bin/env python3
"""P8 (optional) -- the thermal path, checked against the design's own numbers.

Heat conduction on a multilayer board is the same Laplacian the L5 solver
already does: replace sigma by the thermal conductivity and current by power,
and the same raster gives a thermal resistance.  Copper is 385 W/m.K, FR4 is
about 0.3, and a plated barrel is copper of the same annulus the conduction
solver already knows how to build -- so the via array under a drain pad, which
`geometry.via_thermal()` estimates at 7.6 K/W, can be solved rather than
estimated.

Only the two numbers the design leans on are checked here: the via array and
the spreading into the ring.  A full three-dimensional thermal model of the
stack is out of scope (SPEC.md sec.4).
"""
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import paths, jsonio                                # noqa: E402
from extract import copper                                   # noqa: E402

paths.import_tools()

from thermal.model import K_CU, K_FR4, K_PLATED, via_array, spreading


def run(quick=False):
    t0 = time.time()
    import geometry as G
    losses = G.losses()
    out = {"question": "P8 (optional)",
           "losses_geometry_py_W": losses,
           "via_array": {ref: via_array(ref) for ref in ("Q1", "Q2")},
           "spreading": spreading(),
           "sink_needed": dict(zip(("r_sink_K_per_W", "area_cm2"),
                                   G.sink_needed())),
           "note": ("The board's own thermal model is in "
                    "tools/geometry.py: via_thermal(), thermal() and "
                    "sink_needed().  Only the via array and the lateral "
                    "spreading are re-solved here; the ring and the "
                    "convection coefficient are enclosure questions."),
           "seconds": round(time.time() - t0, 1)}
    p3 = jsonio.read("P3") or {}
    pa = (p3.get("Q7") or {}).get("phase_A") or {}
    out["joule_from_P3_W"] = {
        "phase_pour": pa.get("P_phase_pour_W"),
        "switch_node": pa.get("P_sw_node_W"),
        "gnd_return": pa.get("P_gnd_W"),
        "note": ("solved at the 28.3 A peak; at 20 A rms the same paths "
                 "dissipate half of it")}
    return out


if __name__ == "__main__":
    print(json.dumps(run(), indent=1, default=str))
