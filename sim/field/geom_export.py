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


def _simple_pieces(geom, max_pts=400, _depth=0):
    """Split a shape into simple polygons openEMS can extrude, losing none
    of it.

    The first version cut a shape with holes into 1 mm vertical strips and
    dropped every strip piece that still had a hole inside it, and every piece
    over `max_pts` vertices.  A via antipad narrower than the strip lies wholly
    inside one more often than not, so on board S that threw away 900 of the
    2735 mm2 of each of the In1 and In4 ground planes -- a third of the plane,
    in 1 mm slots -- and 42 of the 412 mm2 of In1 under cell A's commutation
    loop, with nothing reported.  Now a piece that still has a hole is cut
    again through that hole, and an over-long one is halved across its longer
    side, until every piece is simple; `export` checks the areas afterwards.
    """
    out = []
    geoms = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    for g in geoms:
        if not isinstance(g, Polygon) or g.is_empty or g.area < 1e-4:
            continue
        n = len(g.exterior.coords) - 1
        if not g.interiors and n <= max_pts:
            if n >= 3:
                out.append(list(g.exterior.coords)[:-1])
            continue
        if _depth > 60:                # cannot happen on real copper; the
            continue                   # area check in export() would say so
        minx, miny, maxx, maxy = g.bounds
        if g.interiors:
            # a vertical cut through a point inside the hole opens it
            cx = Polygon(g.interiors[0]).representative_point().x
            halves = (box(minx - 1, miny - 1, cx, maxy + 1),
                      box(cx, miny - 1, maxx + 1, maxy + 1))
        elif maxx - minx >= maxy - miny:
            cx = (minx + maxx) / 2
            halves = (box(minx - 1, miny - 1, cx, maxy + 1),
                      box(cx, miny - 1, maxx + 1, maxy + 1))
        else:
            cy = (miny + maxy) / 2
            halves = (box(minx - 1, miny - 1, maxx + 1, cy),
                      box(minx - 1, cy, maxx + 1, maxy + 1))
        for h in halves:
            out += _simple_pieces(g.intersection(h), max_pts, _depth + 1)
    return out


def export(window, nets, layers=None, simplify=0.05, out=None, name="cell"):
    """Write the copper of `nets` inside `window` as polygons plus barrels."""
    stack = copper.stackup()
    layers = layers or copper.CU_LAYERS
    data = {"window": list(window), "stack": stack, "polys": [], "barrels": [],
            "nets": list(nets)}
    area_in = area_out = 0.0
    for net in nets:
        for layer in layers:
            u = copper.net_union(net, layer, window, simplify=simplify)
            if u is None or u.is_empty:
                continue
            pieces = _simple_pieces(u)
            area_in += u.area
            area_out += sum(Polygon(q).area for q in pieces)
            for pts in pieces:
                data["polys"].append({
                    "net": net, "layer": layer,
                    "z": stack[layer]["z"], "t": stack[layer]["t"],
                    "pts": [[round(x, 4), round(y, 4)] for x, y in pts]})
    # what the polygons the solver gets add up to, against the copper itself
    data["copper_mm2"] = round(area_in, 2)
    data["copper_lost_mm2"] = round(area_in - area_out, 3)
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
