"""Where everything lives.  Every other module imports these; no module
builds a path out of string concatenation of its own."""
import os
from pathlib import Path
import sys

SIM = Path(__file__).resolve().parent.parent
PROJECT = SIM.parent

TOOLS = PROJECT / "tools"
MODELS = SIM / "models"
REPORT = SIM / "report"

# Which board is simulated.  Board A's paths are the original ones; any other
# board keeps its copy, geometry, results and figures in a subdirectory of its
# own, so running one can never overwrite the other's numbers.  Chosen by
# `run.py --board`, which sets SIM_BOARD so that every module -- and every
# subprocess -- sees the same board.
BOARDS = {
    "a": ("motor_board", "servodrive_A.kicad_pcb"),
    "s": ("single_board", "servodrive_S.kicad_pcb"),
}


def use_board(key):
    global BOARD_KEY, HARDWARE, BOARD_SRC, WORK, BOARD, GEOMETRY, RESULTS, FIGS
    key = key.lower()
    if key not in BOARDS:
        raise SystemExit(f"unknown board {key!r}: one of {', '.join(BOARDS)}")
    BOARD_KEY = key
    hw, pcb = BOARDS[key]
    HARDWARE = PROJECT / "hardware" / hw
    BOARD_SRC = HARDWARE / pcb
    sub = "" if key == "a" else key
    WORK = SIM / "work" / sub if sub else SIM / "work"
    BOARD = WORK / pcb                              # the copy; never the original
    GEOMETRY = WORK / "geometry.json"
    RESULTS = SIM / "results" / sub if sub else SIM / "results"
    FIGS = REPORT / "img" / sub if sub else REPORT / "img"
    os.environ["SIM_BOARD"] = key


use_board(os.environ.get("SIM_BOARD", "a"))

FASTHENRY = SIM / "fasthenry"
FASTCAP = SIM / "fastcap"
SPICE = SIM / "spice"
FIELD = SIM / "field"
CONDUCTION = SIM / "conduction"
LOOP = SIM / "loop"
ENCODER = SIM / "encoder"
THERMAL = SIM / "thermal"
TESTS = SIM / "tests"

FINDINGS = SIM / "FINDINGS.md"
HANDBACK = SIM / "HANDBACK.md"

# External solvers built by sim/setup.py (no sudo on this machine).
OPT = Path.home() / "opt"
FASTHENRY_BIN = OPT / "FastHenry2" / "bin" / "fasthenry"
FASTCAP_BIN = OPT / "FastCap2" / "bin" / "fastcap"
OPENEMS_IMAGE = "servodrive-openems:latest"


def ensure_dirs():
    for d in (WORK, MODELS, RESULTS, REPORT, FIGS, FASTHENRY, FASTCAP, SPICE,
              FIELD, CONDUCTION, LOOP, ENCODER, THERMAL, TESTS):
        d.mkdir(parents=True, exist_ok=True)


def import_tools():
    """`import geometry as G` the way SPEC.md sec.9 asks: as a module, so that
    `python3 tools/geometry.py`'s SVG rewrite never happens."""
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    from lib import board
    board.patch_geometry()
