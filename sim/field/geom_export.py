#!/usr/bin/env python3
"""Extracted copper -> a JSON the openEMS container can build from.

CSXCAD wants polygons with an elevation and a thickness; the extractor already
has exactly that.  Concave shapes and holes are handled by triangulating
nothing at all: openEMS's `AddLinPoly` takes a simple polygon, so a shape with
holes is cut into simple pieces by shapely first.
"""
import json
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import box, Point, Polygon, MultiPolygon
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import paths                                        # noqa: E402
from extract import copper                                   # noqa: E402


def _simple_pieces(geom, max_pts=400):
    """Split a polygon with holes into simple polygons openEMS can extrude."""
    out = []
    geoms = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    for g in geoms:
        if g.is_empty or g.area < 1e-4:
            continue
        if not g.interiors:
            out.append(list(g.exterior.coords)[:-1])
            continue
        # cut the shape into vertical strips so each piece is hole-free
        minx, miny, maxx, maxy = g.bounds
        n = max(4, int((maxx - minx) / 1.0))
        xs = np.linspace(minx, maxx, n + 1)
        for a, b in zip(xs[:-1], xs[1:]):
            piece = g.intersection(box(a, miny, b, maxy))
            if piece.is_empty:
                continue
            for q in (piece.geoms if hasattr(piece, "geoms") else [piece]):
                if q.area < 1e-4 or not isinstance(q, Polygon):
                    continue
                if q.interiors:
                    continue
                out.append(list(q.exterior.coords)[:-1])
    return [p for p in out if len(p) >= 3 and len(p) <= max_pts]


def export(window, nets, layers=None, simplify=0.05, out=None, name="cell"):
    """Write the copper of `nets` inside `window` as polygons plus barrels."""
    stack = copper.stackup()
    layers = layers or copper.CU_LAYERS
    data = {"window": list(window), "stack": stack, "polys": [], "barrels": [],
            "nets": list(nets)}
    for net in nets:
        for layer in layers:
            u = copper.net_union(net, layer, window, simplify=simplify)
            if u is None or u.is_empty:
                continue
            for pts in _simple_pieces(u):
                data["polys"].append({
                    "net": net, "layer": layer,
                    "z": stack[layer]["z"], "t": stack[layer]["t"],
                    "pts": [[round(x, 4), round(y, 4)] for x, y in pts]})
    for b in copper.barrels(nets, window):
        data["barrels"].append({
            "net": b["net"], "ref": b["ref"], "x": b["x"], "y": b["y"],
            "drill": b["drill"], "dia": b["dia"],
            "z0": stack[copper.CU_LAYERS[b["i0"]]]["z"],
            "z1": stack[copper.CU_LAYERS[b["i1"]]]["z"]})
    p = Path(out) if out else (paths.WORK / "openems" / name / "geom.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data))
    return p, data


def drop_barrel_shadowed(data, tol=0.02):
    """Remove pads that lie wholly inside a barrel cylinder.

    The barrels are built at a higher priority than the polygons, so a via pad
    no bigger than its own barrel never sets a single field component and
    openEMS reports it as an unused primitive.  There were 127 of those in the
    first cell-A model, and they were read as lost copper when they are in fact
    copper the cylinder already provides.  Dropping them here makes the
    solver's warning mean what it appears to mean: a feature the mesh missed.
    """
    keep, shadowed = [], 0
    for p in data["polys"]:
        g = Polygon(p["pts"])
        if g.area <= 0:
            continue
        if any(b["z0"] - 1e-6 <= p["z"] <= b["z1"] + 1e-6
               and g.difference(Point(b["x"], b["y"]).buffer(b["dia"] / 2, 48)
                                ).area < tol * g.area
               for b in data["barrels"]):
            shadowed += 1
            continue
        keep.append(p)
    data["polys"] = keep
    data["barrel_shadowed_polys"] = shadowed
    return data


def fixed_lines(data, axis, min_step=0.1, budget=340, extra=()):
    """Mesh lines snapped to the copper edges on one axis.

    In FDTD there are no nets, only metal: two conductors within one cell of
    each other are one conductor.  The tightest net-to-net clearance in this
    window, barrel walls included, is 0.40 mm, and a uniform mesh that does not
    stay well inside that joins VBUS to GND and reports a commutation loop
    several times too small; P7's uniform series shows it climbing as the
    cells shrink past that figure and settling once they are under it.
    Pinning a line to every polygon vertex and every barrel wall
    puts one inside each clearance by construction rather than by where the
    grid happens to fall, and costs cells only where there is something to
    resolve.

    Returns (lines, step_used): if the budget forces a coarser merge than
    `min_step`, the step actually used is reported rather than hidden.
    """
    i = 0 if axis == "x" else 1
    vals = [np.asarray(p["pts"], float)[:, i] for p in data["polys"]]
    for b in data["barrels"]:
        c, r = (b["x"] if i == 0 else b["y"]), b["dia"] / 2
        vals.append(np.array([c - r, c, c + r]))
    if extra:
        vals.append(np.asarray(extra, float).ravel())
    if not vals:
        return [], min_step
    v = np.unique(np.concatenate(vals))
    lo, hi = data["window"][i], data["window"][i + 2]
    v = v[(v >= lo - 1e-9) & (v <= hi + 1e-9)]

    step = min_step
    for _ in range(24):
        keep = []
        for x in v:
            if not keep or x - keep[-1] >= step:
                keep.append(float(x))
        if len(keep) <= budget:
            break
        step *= 1.25
    return keep, step


if __name__ == "__main__":
    from fasthenry.cell import power_window
    w = power_window("A", 2.5)
    p, d = export(w, ("VBUS", "GND", "SW_A"), name="cellA_loop")
    print(p, len(d["polys"]), "polygons", len(d["barrels"]), "barrels")
