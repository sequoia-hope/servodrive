#!/usr/bin/env python3
"""P0 -- toolchain, geometry extraction, known-answer tests.

Gate (SPEC.md sec.6): every sec.5.4 test within tolerance, geometry.json
written, the cell-A crop drawn.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import paths                                        # noqa: E402


def run(quick=False):
    import setup as setup_mod
    from extract import board as extract_board
    from extract import plot as extract_plot
    from tests import kat

    out = {}
    out["toolchain"] = setup_mod.check()

    data = extract_board.main()
    out["geometry"] = {
        "zones": len(data["zones"]), "pads": len(data["pads"]),
        "through_pads": len(data["through_pads"]),
        "tracks": len(data["tracks"]), "vias": len(data["vias"]),
        "parts": len(data["parts"]), "nets": len(data["nets"]),
        "stackup_total_mm": data["stackup"]["total_thickness"],
        "file": str(paths.GEOMETRY),
    }

    # the P0 eyeball gate: the whole board and the cell-A crop, per layer
    for args in ([], ["--cell", "A"]):
        sys.argv = ["plot"] + args
        extract_plot.main()
    out["figures"] = ["extract_board.png", "extract_cellA.png"]

    tests = kat.run_tests()
    out["known_answer_tests"] = tests
    out["gate"] = {
        "all_pass": all(t["status"] == "PASS" for t in tests.values()),
        "n_pass": sum(1 for t in tests.values() if t["status"] == "PASS"),
        "n_total": len(tests),
        "failed": [k for k, t in tests.items() if t["status"] != "PASS"],
    }
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=1, default=str))
