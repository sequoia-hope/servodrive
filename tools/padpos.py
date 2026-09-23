"""padpos.py -- SMD/THT pad centres of a placed Part, in board polar coordinates.

Reproduces what gen_boards.py + KiCad do with (at x y ang) so the floorplan can
be reasoned about numerically without writing a board first.
"""
import re
from math import cos, sin, radians, hypot, atan2, degrees
import placement as PL

_PADS = {}
def pads(spec):
    if spec in _PADS: return _PADS[spec]
    t = PL.fp_path(spec).read_text()
    out = []
    for m in re.finditer(r'\(pad\s+"([^"]*)"\s+(smd|thru_hole|np_thru_hole)\s+\w+\s*'
                         r'\(at ([-\d.]+) ([-\d.]+)(?: [-\d.]+)?\)\s*\(size ([\d.]+) ([\d.]+)\)', t, re.S):
        out.append((m.group(1), float(m.group(3)), float(m.group(4)), float(m.group(5)), float(m.group(6))))
    _PADS[spec] = out
    return out

def pad_xy(part):
    """[(number, x, y)] in geometry (y-up) board mm, following gen_boards: KiCad
    (at X Y A) with the library pad at (px, py) y-down; back side mirrors y."""
    out = []
    A = radians(part.ang)
    for num, px, py, w, h in pads(part.fp):
        if part.layer.startswith("B."):
            py = -py
        # KiCad rotates pads by +A counter-clockwise on screen (y down):
        rx = px * cos(A) + py * sin(A)
        ry = -px * sin(A) + py * cos(A)
        # KiCad page coords: (X + rx, Y + ry); geometry y-up: y = -(Y + ry - CY)
        out.append((num, part.x + rx, part.y - ry, w, h))
    return out

def polar(part, axis):
    """[(number, r, s, w, h)] with s the tangential offset from `axis` (deg)."""
    res = []
    for num, x, y, w, h in pad_xy(part):
        r = hypot(x, y); a = degrees(atan2(y, x))
        da = (a - axis + 540) % 360 - 180
        res.append((num, r, radians(da) * r, w, h))
    return res
