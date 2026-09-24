#!/usr/bin/env python3
"""Reproduce the whole servodrive electromagnetic simulation.

    python3 sim/run.py                 # every phase, in order
    python3 sim/run.py --phase P2      # one phase
    python3 sim/run.py --from P3       # P3 onwards
    python3 sim/run.py --list
    python3 sim/run.py --board s       # board S: results/s/, report/img/s/

Each phase writes `sim/results/P<n>.json` and figures into `sim/report/img/`;
the report is built from those files alone, so a re-run can never put
yesterday's number beside today's figure (SPEC.md sec.8.1).
"""
import argparse
import importlib
import os
import sys
import time
import traceback
from pathlib import Path


def _board_arg(argv):
    """--board has to be known before lib.paths is imported: every module
    reads its paths from there, and a subprocess inherits SIM_BOARD."""
    for i, a in enumerate(argv):
        if a == "--board" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--board="):
            return a.split("=", 1)[1]
    return None


if _board_arg(sys.argv):
    os.environ["SIM_BOARD"] = _board_arg(sys.argv).lower()

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import paths, jsonio                                # noqa: E402

PHASES = [
    ("P0", "setup, extraction, known-answer tests", "phases.p0_setup"),
    ("P1", "parasitics: FastHenry/FastCap on cell A", "phases.p1_parasitics"),
    ("P2", "cell circuit: ngspice", "phases.p2_cell"),
    ("P3", "conduction: the L5 solver", "phases.p3_conduction"),
    ("P4", "DC link and interconnect", "phases.p4_dclink"),
    ("P5", "encoder field", "phases.p5_encoder"),
    ("P5S", "encoder field, board S: the bulk cans in the centre", "phases.p5s_board_s"),
    ("P6", "system: inverter + motor + SimpleFOC", "phases.p6_system"),
    ("P7", "full wave: openEMS", "phases.p7_fullwave"),
    ("P8", "thermal (optional)", "phases.p8_thermal"),
    ("P9", "report and hand-back", "phases.p9_report"),
]

# Board S: P5S is its own encoder check, and its report is the summary the
# board-S page carries rather than board A's report and hand-back.
PHASES_S = [p for p in PHASES if p[0] != "P9"] + [
    ("P9", "board S summary: results/s -> single.html", "phases.p9s_summary"),
]


def run_phase(tag, module, **kw):
    mod = importlib.import_module(module)
    t0 = time.time()
    print(f"\n=== {tag}: {module} " + "=" * (52 - len(module)))
    res = mod.run(**kw)
    dt = time.time() - t0
    if res is not None:
        res.setdefault("_phase", {})["seconds"] = round(dt, 1)
        jsonio.write(tag, res)
    print(f"=== {tag} done in {dt / 60:.1f} min")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", nargs="*", help="run only these phases")
    ap.add_argument("--from", dest="start", help="run from this phase onwards")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--board", default=paths.BOARD_KEY,
                    help="which board: a (default) or s")
    ap.add_argument("--quick", action="store_true",
                    help="coarser sweeps; for development, never for a result")
    ap.add_argument("--keep-going", action="store_true",
                    help="record a phase failure as a finding and carry on")
    a = ap.parse_args()
    phases = PHASES_S if paths.BOARD_KEY == "s" else [
        p for p in PHASES if p[0] != "P5S"]
    print(f"board {paths.BOARD_KEY.upper()}: {paths.BOARD_SRC}")

    if a.list:
        for tag, desc, mod in phases:
            done = (paths.RESULTS / f"{tag}.json").exists()
            print(f"  {tag}  {'[done]' if done else '[    ]'}  {desc}")
        return

    paths.ensure_dirs()
    todo = phases
    if a.phase:
        todo = [p for p in phases if p[0] in a.phase]
    elif a.start:
        i = [p[0] for p in phases].index(a.start)
        todo = phases[i:]

    failures = []
    for tag, desc, mod in todo:
        try:
            run_phase(tag, mod, quick=a.quick)
        except Exception as e:
            traceback.print_exc()
            failures.append((tag, f"{e.__class__.__name__}: {e}"))
            if not a.keep_going:
                raise
    if failures:
        print("\nFAILED PHASES (recorded, not hidden):")
        for tag, msg in failures:
            print(f"  {tag}: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
