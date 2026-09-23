#!/usr/bin/env python3
"""Real copper -> a FastHenry segment mesh (PEEC).

FastHenry's built-in uniform ground plane is a rectangle with round or
rectangular holes.  The copper on this board is an annular sector perforated by
a 4 x 4 via field, so instead every conducting layer is meshed explicitly: a
graded rectilinear grid is laid over the window, a node is placed wherever that
grid point sits on copper of the net, and neighbouring nodes are joined by a
rectangular bar whose width is the half-cell on each side and whose height is
the layer's copper thickness.

A plated barrel (a PCB via or one of the 16-via FET arrays, which are footprint
pads with drills) becomes a chain of vertical bars of equal cross-section,
joined to the in-plane mesh only on the layers where its net actually has
copper -- which is what makes the antipads in the In1 return plane show up in
the answer instead of being assumed away (SPEC.md H1).

Sanity: a uniform sheet meshed this way reproduces its sheet resistance
exactly, because an x-bar from (i,j) to (i+1,j) has R = dx / (sigma * w_j * t)
and the rows sit in parallel.  tests/kat.py checks it.
"""
import math
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import Point, LineString, box
from shapely import prepared

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from extract import copper                                   # noqa: E402
from fasthenry import runner                                 # noqa: E402

CU_LAYERS = copper.CU_LAYERS


def grid_lines(lo, hi, required=(), target=0.7, min_gap=0.12):
    """A rectilinear axis: every required coordinate, then filled so that no
    gap exceeds `target`, then lines closer than `min_gap` merged."""
    req = sorted({float(r) for r in required if lo - 1e-9 <= r <= hi + 1e-9}
                 | {float(lo), float(hi)})
    out = []
    for a, b in zip(req[:-1], req[1:]):
        out.append(a)
        gap = b - a
        if gap > target:
            n = int(math.ceil(gap / target))
            out.extend(a + gap * k / n for k in range(1, n))
    out.append(req[-1])
    merged = [out[0]]
    for v in out[1:]:
        if v - merged[-1] >= min_gap:
            merged.append(v)
    if hi - merged[-1] > 1e-9:
        merged[-1] = hi
    return np.array(merged)


def half_widths(lines):
    """For each grid line, the width of the strip it represents."""
    w = np.zeros(len(lines))
    w[1:-1] = (lines[2:] - lines[:-2]) / 2.0
    w[0] = (lines[1] - lines[0]) / 2.0
    w[-1] = (lines[-1] - lines[-2]) / 2.0
    return w


class Conductor:
    """One net on one layer, meshed."""

    def __init__(self, mesh, tag, net, layer, geom):
        self.mesh = mesh
        self.tag = tag
        self.net = net
        self.layer = layer
        self.geom = geom
        self.prep = prepared.prep(geom) if geom is not None else None
        self.nodes = {}          # (i, j) -> node name
        self.n_seg = 0
        self._xy = None
        self._keys = None

    def name(self, i, j):
        return f"N{self.tag}_{i}_{j}"


class Mesh:
    def __init__(self, title, window, xs=None, ys=None, target=0.7,
                 required_x=(), required_y=(), sigma=runner.SIGMA_CU,
                 nhinc=3, nwinc=1, min_gap=0.12):
        self.model = runner.Model(title, sigma=sigma)
        self.window = window
        x0, y0, x1, y1 = window
        self.xs = np.asarray(xs) if xs is not None else grid_lines(
            x0, x1, required_x, target, min_gap)
        self.ys = np.asarray(ys) if ys is not None else grid_lines(
            y0, y1, required_y, target, min_gap)
        self.wx = half_widths(self.xs)
        self.wy = half_widths(self.ys)
        self.stack = copper.stackup()
        self.nhinc, self.nwinc = nhinc, nwinc
        self.conductors = {}
        self.n_seg = 0
        self.n_node = 0

    # ------------------------------------------------------------- layers --
    def add_conductor(self, tag, net, layer, geom=None, nhinc=None, sigma=None):
        """Mesh `net` on `layer` inside the window."""
        if geom is None:
            geom = copper.net_union(net, layer, self.window)
        c = Conductor(self, tag, net, layer, geom)
        self.conductors[tag] = c
        if geom is None or geom.is_empty:
            return c
        z = self.stack[layer]["z"]
        t = self.stack[layer]["t"]
        nh = nhinc if nhinc is not None else self.nhinc

        occ = np.zeros((len(self.xs), len(self.ys)), bool)
        for i, x in enumerate(self.xs):
            for j, y in enumerate(self.ys):
                if c.prep.contains(Point(x, y)):
                    occ[i, j] = True
        c.occ = occ

        # nodes
        for i in range(len(self.xs)):
            for j in range(len(self.ys)):
                if occ[i, j]:
                    self.model.node(c.name(i, j), float(self.xs[i]),
                                    float(self.ys[j]), z)
                    c.nodes[(i, j)] = self.model._nname(c.name(i, j))
                    self.n_node += 1

        # bars: only where the copper is continuous between the two nodes
        for i in range(len(self.xs)):
            for j in range(len(self.ys)):
                if not occ[i, j]:
                    continue
                if i + 1 < len(self.xs) and occ[i + 1, j]:
                    if c.prep.contains(LineString([(self.xs[i], self.ys[j]),
                                                   (self.xs[i + 1], self.ys[j])])):
                        self.model.segment(c.name(i, j), c.name(i + 1, j),
                                           w=float(self.wy[j]), h=t,
                                           nwinc=self.nwinc, nhinc=nh,
                                           sigma=sigma)
                        self.n_seg += 1
                if j + 1 < len(self.ys) and occ[i, j + 1]:
                    if c.prep.contains(LineString([(self.xs[i], self.ys[j]),
                                                   (self.xs[i], self.ys[j + 1])])):
                        self.model.segment(c.name(i, j), c.name(i, j + 1),
                                           w=float(self.wx[i]), h=t,
                                           nwinc=self.nwinc, nhinc=nh,
                                           sigma=sigma)
                        self.n_seg += 1
        return c

    # ------------------------------------------------------------- tracks --
    def add_tracks(self, tag, net, layers=("F.Cu", "In3.Cu", "B.Cu"),
                   nhinc=None, tol=0.2, g=None, pad_merge=1.2):
        """A net's tracks as segments, one per PCB track.

        A 0.15 mm gate track cannot be rasterised on a grid coarse enough to
        mesh a ground plane: the grid misses it and the net comes out as a
        string of islands, which makes FastHenry's mesh matrix singular and
        every impedance NaN.  A track already is a segment with a width, so
        it is used as one, with its endpoints merged to the pads they land on.
        """
        gg = g or copper.load()
        c = Conductor(self, tag, net, "|".join(layers), None)
        self.conductors[tag] = c
        c.track_nodes = {}
        c.nodes = {}
        pts = {}

        c.node_layer = {}

        # Nearest-neighbour merging, not bucketing: two track ends that meet
        # at a corner can straddle a bucket boundary and end up as separate
        # nodes, which breaks the net into pieces that FastHenry then answers
        # with NaN.
        by_layer_pts = {}

        def nid(x, y, layer):
            lst = by_layer_pts.setdefault(layer, [])
            best, bd = None, 1e30
            for (xx, yy, nm) in lst:
                d = math.hypot(xx - x, yy - y)
                if d < bd:
                    best, bd = nm, d
            if best is not None and bd <= tol:
                return best
            z = self.stack[layer]["z"]
            nm = f"NT{tag}_{len(pts)}"
            self.model.node(nm, float(x), float(y), z)
            key = self.model._nname(nm)
            pts[(layer, len(pts))] = key
            lst.append((x, y, key))
            c.nodes[(len(pts), 0)] = key
            c.node_layer[key] = layer
            self.n_node += 1
            return key

        n = 0
        for t in gg["tracks"]:
            if t["net"] != net or t["layer"] not in layers:
                continue
            a = nid(t["start"][0], t["start"][1], t["layer"])
            b = nid(t["end"][0], t["end"][1], t["layer"])
            if a == b:
                continue
            self.model.segment(a, b, w=max(t["width"], 0.05),
                               h=self.stack[t["layer"]]["t"],
                               nwinc=self.nwinc,
                               nhinc=nhinc if nhinc is not None else self.nhinc)
            self.n_seg += 1
            n += 1
        # Pads become nodes too, but a track does not always end exactly at
        # the pad centre -- SPEC.md sec.9 warns that one can stop 0.05 mm
        # inside its pad -- so a pad reuses the nearest track end within its
        # own size rather than making a node of its own and leaving the net
        # in two pieces.
        by_layer = {k: [(x, y, nm) for (x, y, nm) in v]
                    for k, v in by_layer_pts.items()}
        for p in gg["pads"]:
            if p["net"] != net:
                continue
            for layer in p["shapes"]:
                if layer not in layers:
                    continue
                cand = by_layer.get(layer, [])
                best, bd = None, 1e30
                for xx, yy, nm in cand:
                    d = math.hypot(xx - p["x"], yy - p["y"])
                    if d < bd:
                        best, bd = nm, d
                a = best if (best is not None and bd <= pad_merge) \
                    else nid(p["x"], p["y"], layer)
                c.track_nodes[(p["ref"], p["pad"], layer)] = a
        c._xy = None
        c.n_tracks = n
        return c

    def pad_node(self, tag, ref, padnum, layer=None):
        """The node a pad of a track-meshed net sits on."""
        c = self.conductors[tag]
        for (r, pn, ly), nm in getattr(c, "track_nodes", {}).items():
            if r == ref and pn == str(padnum) and (layer is None or ly == layer):
                return nm
        return None

    # ------------------------------------------------------------ barrels --
    def add_barrel(self, b, tag_for_layer, plating=0.025,
                   sigma=runner.SIGMA_CU_PLATED, name=None):
        """A plated hole.  `tag_for_layer` maps a layer name to the conductor
        tag the barrel should join there (or None to pass through)."""
        area = copper.barrel_area(b["drill"], plating)
        side = math.sqrt(area)                    # equal-area square bar
        nm = name or f"{b['ref']}_{b['x']:.2f}_{b['y']:.2f}".replace(".", "p").replace("-", "m")
        prev = None
        for k in range(b["i0"], b["i1"] + 1):
            layer = CU_LAYERS[k]
            z = self.stack[layer]["z"]
            nname = f"NB{nm}_{k}"
            if nname not in self.model.nodes:
                self.model.node(nname, b["x"], b["y"], z)
                self.n_node += 1
            if prev is not None:
                self.model.segment(prev, nname, w=side, h=side,
                                   nwinc=3, nhinc=3, sigma=sigma)
                self.n_seg += 1
            prev = nname
            tag = tag_for_layer.get(layer)
            if tag and tag in self.conductors:
                near = self.nearest(tag, b["x"], b["y"], max_dist=1.2,
                                    layer=layer)
                if near:
                    self.model.equiv(nname, near)
        return nm

    # ---------------------------------------------------------- utilities --
    def nearest(self, tag, x, y, max_dist=None, layer=None):
        """The meshed node of `tag` closest to (x, y)."""
        c = self.conductors[tag]
        if not c.nodes:
            return None
        if getattr(c, "track_nodes", None) is not None:
            best, bd = None, 1e30
            for nm in set(c.nodes.values()):
                if layer is not None and c.node_layer.get(nm) != layer:
                    continue
                xx, yy, _ = self.model.nodes[nm]
                d = math.hypot(xx - x, yy - y)
                if d < bd:
                    best, bd = nm, d
            if max_dist is not None and bd > max_dist:
                return None
            return best
        if getattr(c, "_xy", None) is None:
            keys = list(c.nodes)
            c._keys = keys
            c._xy = np.array([(self.xs[i], self.ys[j]) for i, j in keys])
        d = np.hypot(c._xy[:, 0] - x, c._xy[:, 1] - y)
        k = int(np.argmin(d))
        if max_dist is not None and d[k] > max_dist:
            return None
        return c.nodes[c._keys[k]]

    def pad_equipotential(self, tag, ref, padnum, layer=None, g=None):
        """Tie every meshed node under a component pad into one node.

        A pad is a solid piece of copper with a solder joint on it: modelling
        it as a single mesh point adds a spreading inductance that is not
        there, which the strip-over-plane known-answer test showed to be worth
        20 percent on a sub-nanohenry loop.
        """
        from shapely.geometry import Polygon
        from shapely import prepared as _prep
        c = self.conductors[tag]
        got = []
        for p in copper.pads_of(ref, g):
            if p["pad"] != str(padnum):
                continue
            for lname, shapes in p["shapes"].items():
                if layer and lname != layer:
                    continue
                if lname != c.layer:
                    continue
                for entry in shapes:
                    poly = Polygon(entry["pts"])
                    pr = _prep.prep(poly)
                    for (i, j), nm in c.nodes.items():
                        if pr.contains(Point(self.xs[i], self.ys[j])):
                            got.append(nm)
        got = sorted(set(got))
        if len(got) > 1:
            self.model.equiv(*got)
        return got

    def terminal(self, name, tag, x, y, max_dist=1.5):
        """Name a point on a conductor so a port or a short can attach there."""
        n = self.nearest(tag, x, y, max_dist)
        if n is None:
            raise RuntimeError(f"no {tag} copper within {max_dist} mm of "
                               f"({x:.2f}, {y:.2f}) for terminal {name}")
        return n

    def stats(self):
        return {"nodes": self.n_node, "segments": self.n_seg,
                "grid_x": len(self.xs), "grid_y": len(self.ys),
                "conductors": {k: {"net": c.net, "layer": c.layer,
                                   "nodes": len(c.nodes)}
                               for k, c in self.conductors.items()}}
