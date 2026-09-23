#!/usr/bin/env python3
"""geometry.json -> shapely copper, per net per layer.

Everything downstream (the FastHenry mesher, the L5 rasteriser, the FastCap
panels, the magpylib current paths) wants the same thing: "give me the copper
belonging to net N on layer L, inside this window, as a polygon".  This is the
one place that assembles it out of zones, pads and tracks.

Frame: mm, origin at the shaft axis, +y up -- the tools' frame.
"""
import json
import math
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon, MultiPolygon, box, LineString, Point
from shapely.ops import unary_union
from shapely import prepared

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import paths                                        # noqa: E402

CU_LAYERS = ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu"]


@lru_cache(maxsize=1)
def load(path=None):
    return json.loads(Path(path or paths.GEOMETRY).read_text())


def stackup(g=None):
    """-> {layer: {"z": mid-plane mm, "t": thickness mm}} plus the dielectrics."""
    g = g or load()
    out = {}
    for l in g["stackup"]["layers"]:
        if l["kind"] == "copper":
            out[l["name"]] = {"z": l["z_mid"], "t": l["thickness"],
                              "z_top": l["z_top"]}
    return out


def dielectrics(g=None):
    g = g or load()
    return [l for l in g["stackup"]["layers"] if l["kind"] == "dielectric"]


def _poly(entry):
    pts = entry["pts"]
    if len(pts) < 3:
        return None
    holes = entry.get("holes") or []
    try:
        p = Polygon(pts, [h for h in holes if len(h) >= 3])
        if not p.is_valid:
            p = p.buffer(0)
        return p if not p.is_empty else None
    except Exception:
        return None


def _track_poly(t):
    (x0, y0), (x1, y1) = t["start"], t["end"]
    if t.get("kind") == "arc" and "mid" in t:
        pts = _arc_points(t["start"], t["mid"], t["end"])
        line = LineString(pts)
    elif abs(x1 - x0) < 1e-9 and abs(y1 - y0) < 1e-9:
        return Point(x0, y0).buffer(t["width"] / 2, quad_segs=8)
    else:
        line = LineString([(x0, y0), (x1, y1)])
    return line.buffer(t["width"] / 2, cap_style=1, quad_segs=8)


def _arc_points(p0, pm, p1, n=24):
    (x0, y0), (xm, ym), (x1, y1) = p0, pm, p1
    # circumcentre of the three points
    d = 2 * (x0 * (ym - y1) + xm * (y1 - y0) + x1 * (y0 - ym))
    if abs(d) < 1e-12:
        return [p0, p1]
    ux = ((x0 ** 2 + y0 ** 2) * (ym - y1) + (xm ** 2 + ym ** 2) * (y1 - y0)
          + (x1 ** 2 + y1 ** 2) * (y0 - ym)) / d
    uy = ((x0 ** 2 + y0 ** 2) * (x1 - xm) + (xm ** 2 + ym ** 2) * (x0 - x1)
          + (x1 ** 2 + y1 ** 2) * (xm - x0)) / d
    r = math.hypot(x0 - ux, y0 - uy)
    a0 = math.atan2(y0 - uy, x0 - ux)
    am = math.atan2(ym - uy, xm - ux)
    a1 = math.atan2(y1 - uy, x1 - ux)
    # go the way that passes through the mid point
    def unwrap(a, ref):
        while a - ref > math.pi:
            a -= 2 * math.pi
        while a - ref < -math.pi:
            a += 2 * math.pi
        return a
    am_u = unwrap(am, a0)
    a1_u = unwrap(a1, am_u)
    ts = np.linspace(a0, a1_u, n)
    return [(ux + r * math.cos(t), uy + r * math.sin(t)) for t in ts]


def net_shapes(net, layer, window=None, g=None, include=("zone", "pad", "track")):
    """All copper of `net` on `layer`, as a list of shapely polygons."""
    g = g or load()
    win = box(*window) if window else None
    out = []
    if "zone" in include:
        for z in g["zones"]:
            if z["net"] != net or z["layer"] != layer:
                continue
            p = _poly(z)
            if p is not None:
                out.append(p)
    if "pad" in include:
        for pad in g["pads"]:
            if pad["net"] != net or layer not in pad["shapes"]:
                continue
            for entry in pad["shapes"][layer]:
                p = _poly(entry)
                if p is not None:
                    out.append(p)
    if "track" in include:
        for t in g["tracks"]:
            if t["net"] != net or t["layer"] != layer:
                continue
            out.append(_track_poly(t))
    if win is not None:
        out = [p.intersection(win) for p in out]
        out = [p for p in out if not p.is_empty]
    return out


def net_union(net, layer, window=None, g=None, simplify=None):
    shapes = net_shapes(net, layer, window, g)
    if not shapes:
        return None
    u = unary_union(shapes)
    if simplify:
        u = u.simplify(simplify, preserve_topology=True)
    return u


def nets_on(layer, g=None):
    g = g or load()
    s = {z["net"] for z in g["zones"] if z["layer"] == layer}
    s |= {p["net"] for p in g["pads"] if layer in p["shapes"]}
    s |= {t["net"] for t in g["tracks"] if t["layer"] == layer}
    return sorted(s)


def barrels(net=None, window=None, g=None):
    """Every plated hole -- PCB vias and plated footprint pads -- as
    {x, y, drill, dia, layers, net, ref}.  These are the only vertical
    conductors on the board."""
    g = g or load()
    order = {n: i for i, n in enumerate(CU_LAYERS)}
    out = []
    for v in g["vias"]:
        out.append({"x": v["x"], "y": v["y"], "drill": v["drill"],
                    "dia": v["dia"], "net": v["net"], "ref": "via",
                    "i0": order[v["top"]], "i1": order[v["bottom"]]})
    for p in g["through_pads"]:
        idx = [order[l] for l in p["layers"]]
        out.append({"x": p["x"], "y": p["y"], "drill": p["drill"],
                    "dia": p.get("dia", p["drill"] + 0.4), "net": p["net"],
                    "ref": f"{p['ref']}.{p['pad']}",
                    "i0": min(idx), "i1": max(idx)})
    if net is not None:
        nets = {net} if isinstance(net, str) else set(net)
        out = [b for b in out if b["net"] in nets]
    if window is not None:
        x0, y0, x1, y1 = window
        out = [b for b in out if x0 <= b["x"] <= x1 and y0 <= b["y"] <= y1]
    return out


def barrel_area(drill, plating=0.025):
    """Cross-sectional copper area of a plated barrel, mm^2."""
    r_i = drill / 2.0
    r_o = r_i + plating
    return math.pi * (r_o ** 2 - r_i ** 2)


def cell_window(cell="A", margin=1.0, g=None):
    g = g or load()
    c = g["cells"][cell]
    a = np.radians(np.linspace(c["a0"], c["a1"], 128))
    xs = np.concatenate([c["r0"] * np.cos(a), c["r1"] * np.cos(a)])
    ys = np.concatenate([c["r0"] * np.sin(a), c["r1"] * np.sin(a)])
    return (xs.min() - margin, ys.min() - margin,
            xs.max() + margin, ys.max() + margin)


def part(ref, g=None):
    g = g or load()
    for p in g["parts"]:
        if p["ref"] == ref:
            return p
    raise KeyError(ref)


def pads_of(ref, g=None):
    g = g or load()
    return [p for p in g["pads"] if p["ref"] == ref]


def pad_centre(ref, padnum, layer=None, g=None):
    """Centroid of one pad (there may be several entries for one pad number --
    the FET drain arrays -- in which case the centroid of all of them)."""
    ps = [p for p in pads_of(ref, g) if p["pad"] == str(padnum)]
    if layer:
        ps = [p for p in ps if layer in p["shapes"]]
    if not ps:
        raise KeyError(f"{ref} pad {padnum}")
    xs = [p["x"] for p in ps]
    ys = [p["y"] for p in ps]
    return float(np.mean(xs)), float(np.mean(ys))
