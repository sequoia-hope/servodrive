#!/usr/bin/env python3
"""L6 -- the field at the MT6701 (magpylib, plus Biot-Savart on the solved
current distribution).

Three fields add at the sensor: the diametric magnet, the phase currents in
the board copper and in the motor leads, and whatever leaks past the rotor.
The angle the part reports is atan2 of the in-plane field, so the error the
current causes is the angle between the magnet's field alone and the sum.
"""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import paths                                        # noqa: E402
from extract import copper                                   # noqa: E402
from conduction.raster import Raster                         # noqa: E402
from conduction.solver import Conductor                      # noqa: E402

MU0 = 4e-7 * math.pi
I_PEAK = 28.284


def sensor_point(g=None):
    """The MT6701's sensing centre: the shaft axis, at the die.

    The part is on B.Cu at (0, 0); its die sits about 0.5 mm inside a 1.75 mm
    SOIC body whose face points at the magnet, so the field is evaluated on
    the axis 0.9 mm below the B.Cu plane (towards the motor).
    """
    st = copper.stackup(g)
    z_b = st["B.Cu"]["z"]                 # mm below the F.Cu surface
    return np.array([0.0, 0.0, (z_b + 0.9)]) * 1e-3


def magnet_field(gap_mm=1.0, off_axis_mm=0.0, Br=1.2, D=8e-3, H=2.5e-3,
                 angle_deg=0.0, g=None):
    """B at the sensor from the diametric magnet, in tesla.

    The magnet face sits `gap` above the IC package face; the package face is
    the motor-facing side of the board plus the 1.75 mm body.
    """
    import magpylib as magpy
    p = sensor_point(g)
    # the magnet's centre: the gap is measured from the IC surface, and the
    # IC surface is 1.75 mm of body below the die... the die is what senses,
    # so from the die the magnet centre is gap + body/2 + H/2 away
    body = 1.75e-3
    dz = (gap_mm * 1e-3) + body / 2 + H / 2
    a = math.radians(angle_deg)
    m = magpy.magnet.Cylinder(
        polarization=(Br * math.cos(a), Br * math.sin(a), 0.0),
        dimension=(D, H),
        position=(off_axis_mm * 1e-3, 0.0, float(p[2]) + dz))
    return np.asarray(m.getB(p), float)


def current_paths(cell="A", cellsize=0.12, current=I_PEAK, layers=("B.Cu",)):
    """Current elements on the real copper, from the L5 solution, as a list of
    (position, I*dl) pairs ready for Biot-Savart."""
    from phases.p3_conduction import CELLS, _pad_poly
    from shapely.geometry import Polygon
    c = CELLS[cell]
    win = copper.cell_window(cell, margin=1.5)
    r = Raster(cell=cellsize, window=win)
    cond = Conductor(c["phase"], layers=list(layers), raster=r)
    lead = []
    for p in copper.pads_of(c["lead"]):
        for lname, shapes in p["shapes"].items():
            if lname not in layers:
                continue
            for e in shapes:
                lead.append(cond.nodes_in(lname, Polygon(e["pts"])))
    lead = [x for x in lead if len(x)]
    sp = []
    for ref in c["shunts"]:
        for lname, poly in _pad_poly(ref, 2, "B.Cu"):
            if lname in layers:
                sp.append(cond.nodes_in(lname, poly))
    sp = [x for x in sp if len(x)]
    if not lead or not sp:
        raise RuntimeError(f"cell {cell}: no lead or shunt nodes")
    cond.solve([(np.unique(np.concatenate(sp)), current),
                (np.unique(np.concatenate(lead)), -current)])
    st = copper.stackup()
    pts, mom = [], []
    for layer in layers:
        Jx, Jy, _ = cond.current_density(layer)
        t = st[layer]["t"] * 1e-3
        h = cond.cell * 1e-3
        idx = cond.index[layer]
        ii, jj = np.nonzero(idx >= 0)
        z = st[layer]["z"] * 1e-3
        for i, j in zip(ii, jj):
            jx, jy = Jx[i, j], Jy[i, j]
            if not (np.isfinite(jx) and np.isfinite(jy)):
                continue
            # I*dl for this cell: J * (t*h) * h  -> A.m
            pts.append((cond.r.x0 * 1e-3 + i * h, cond.r.y0 * 1e-3 + j * h, z))
            mom.append((jx * t * h * h, jy * t * h * h, 0.0))
    return np.array(pts), np.array(mom)


def biot_savart(pts, mom, p):
    """B at p from a set of current elements I*dl at positions pts."""
    r = p[None, :] - pts
    d = np.linalg.norm(r, axis=1)
    ok = d > 1e-6
    r, m, d = r[ok], mom[ok], d[ok]
    cross = np.cross(m, r)
    return (MU0 / (4 * math.pi) * (cross / d[:, None] ** 3)).sum(axis=0)


def lead_field(p, i_a, i_b, i_c, length=0.25, r_lead=0.0295, z_out=0.02):
    """The three motor leads as straight wires leaving the lead pads and
    running away from the board, carrying the three phase currents."""
    import magpylib as magpy
    coll = []
    for k, I in enumerate((i_a, i_b, i_c)):
        ang = math.radians(34.0 + 68.0 * k)
        x, y = r_lead * math.cos(ang), r_lead * math.sin(ang)
        src = magpy.current.Polyline(
            current=float(I),
            vertices=[(x, y, float(p[2]) - 0.0),
                      (x, y, float(p[2]) - z_out),
                      (x, y, float(p[2]) - z_out - length)])
        coll.append(src)
    return np.sum([np.asarray(s.getB(p), float) for s in coll], axis=0)


