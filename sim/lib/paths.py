"""Where everything lives.  Every other module imports these; no module
builds a path out of string concatenation of its own."""
from pathlib import Path
import sys

SIM = Path(__file__).resolve().parent.parent
PROJECT = SIM.parent

TOOLS = PROJECT / "tools"
HARDWARE = PROJECT / "hardware" / "motor_board"
BOARD_SRC = HARDWARE / "servodrive_A.kicad_pcb"

WORK = SIM / "work"
BOARD = WORK / "servodrive_A.kicad_pcb"          # the copy; never the original
GEOMETRY = WORK / "geometry.json"

MODELS = SIM / "models"
RESULTS = SIM / "results"
REPORT = SIM / "report"
FIGS = REPORT / "img"

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
