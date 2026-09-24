"""The board being simulated, and the design point that belongs to it.

Board A was the board SPEC.md was written for: a 60 V bus, board B's bulk
behind a 2 x 5 header, the 64 V clamps, 1.6 mOhm shunts.  Board S is the same
phase cells on one board: a 48 V operational maximum, its own bulk cans in the
middle of the board, the bus in on an XT30, the 54 V clamps, and the 2 x 2 mOhm
shunts sourcing forced (hardware/parts/lcsc.csv).  Every phase that used to
write one of board A's numbers inline asks for it here instead, so a board-S
run cannot quietly compute with a board-A constant.

    from lib import board
    B = board.P()          # the profile of the board paths.use_board() chose
"""
import math

from . import paths

PROFILES = {
    "a": {
        "key": "a", "name": "A",
        "v_bus": 60.0,                        # tools/geometry.py BUS_V
        "v_sweep": (20.0, 48.0, 60.0),        # SPEC.md sec.2.1
        "v_crit": 68.0,                       # SPEC.md Q2
        "v_crit_reason": ("the TVS breakdown minimum is 71.1 V and the "
                          "silicon is 80 V; SPEC.md Q2 asks for 68 V"),
        "tvs_phase": "tpsmf4l64a",
        "tvs_bus": "smdj64a",
        "shunt": "shunt_1m6_2010",
        "shunt_r": 0.8e-3,                    # two in parallel
        "sense_gain": 40e-3,                  # V/A at the ADC
        "bus_entry": None,                    # the 2 x 5 header, J7/J8
        "bulk": {"kind": "board_b", "uF": 400.0, "esr": 0.01},
        "guard": (63.0, 66.0),                # main.cpp VBUS_GUARD_V / _FULL_V
        "magnet_D": 8e-3, "gap_nom": 1.0,
        "geometry": {},
    },
    "s": {
        "key": "s", "name": "S",
        "v_bus": 48.0,                        # the 48 V operational maximum
        # 56 V is the top of the firmware fold-back: the highest bus the drive
        # is allowed to switch into, briefly, under regen
        "v_sweep": (20.0, 48.0, 56.0),
        # the same rule as board A's 68 V against a 71.1 V breakdown: stay
        # 3 V under the phase clamp's minimum breakdown, 60.0 V for the 54 V
        # grade, so the clamp never conducts on an edge
        "v_crit": 57.0,
        "v_crit_reason": ("the TPSMF4L54A's breakdown minimum is 60.0 V; the "
                          "criterion keeps board A's 3 V margin under it"),
        "tvs_phase": "tpsmf4l54a",
        "tvs_bus": "smdj54a",
        "shunt": "shunt_2m0_2010",
        "shunt_r": 1.0e-3,                    # 2 x 2 mOhm, geometry.SHUNT_R
        "sense_gain": 50e-3,                  # 1.0 mOhm x 50 V/V
        "bus_entry": {"ref": "J4", "VBUS": "2", "GND": "1"},   # the XT30
        "bulk": {"kind": "cans", "refs": ("C1001", "C1002"),
                 "model": "cap_100u_100v_polymer"},
        # The fold-back the board-S decision set: "firmware fold-back then
        # ~52-56 V" (the SMDJ54A breaks down at 60.0 V minimum)
        "guard": (52.0, 56.0),
        "magnet_D": 6e-3, "gap_nom": 1.5,     # the hand-back's Dia 6 magnet
        "geometry": {"BUS_V": 48.0},
    },
}


def P():
    return PROFILES[paths.BOARD_KEY]


def is_s():
    return paths.BOARD_KEY == "s"


_patched = set()


def patch_geometry():
    """tools/geometry.py is board A's arithmetic; for another board, set the
    constants that differ before any phase reads them.  Derived constants that
    geometry.py computes at import from a patched one are recomputed here."""
    key = paths.BOARD_KEY
    if key in _patched:
        return
    import geometry as G
    for k, v in P()["geometry"].items():
        setattr(G, k, v)
    _patched.add(key)


def i_peak():
    import geometry as G
    return G.I_PHASE * math.sqrt(2)
