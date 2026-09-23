#!/usr/bin/env python3
"""geometry.json -> a per-layer, per-net raster of copper.

Same idea as `tools/finish.py`'s router grid (PIL polygon fill on a fixed
pitch), but it keeps net identity per cell instead of free space per net,
because the conduction solver has to know which copper is which.

    r = Raster(cell=0.05, window=(x0, y0, x1, y1), nets=("PHASE_A", "SW_A"))
    r.mask("SW_A", "B.Cu")      # bool array [nx, ny]
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from extract import copper                                   # noqa: E402

CU = copper.CU_LAYERS


def synthetic_geometry(polys_by_layer, stack=None):
    """A geometry.json-shaped dict from shapely polygons, so that the solver's
    real code path can be checked against closed forms (tests/kat.py)."""
    zones = []
    for layer, polys in polys_by_layer.items():
        if hasattr(polys, "geoms"):
            polys = list(polys.geoms)
        elif not isinstance(polys, (list, tuple)):
            polys = [polys]
        for p in polys:
            zones.append({"net": "TEST", "layer": layer, "kind": "zone",
                          "pts": [list(c) for c in p.exterior.coords],
                          "holes": [[list(c) for c in r.coords]
                                    for r in p.interiors]})
    layers = []
    z = 0.0
    for name, d in (stack or {}).items():
        layers.append({"kind": "copper", "name": name, "z_top": d["z"] - d["t"] / 2,
                       "thickness": d["t"], "z_mid": d["z"]})
    return {"zones": zones, "pads": [], "tracks": [], "vias": [],
            "through_pads": [], "parts": [], "nets": ["TEST"],
            "stackup": {"layers": layers, "total_thickness": 1.6},
            "copper_layers": CU, "cells": {}}


class Raster:
    def __init__(self, cell=0.05, window=None, nets=None, layers=None, g=None,
                 sub=4):
        """`sub` is the sub-pitch used to measure how much of each cell is
        copper: point sampling alone biases a plane's width by half a cell at
        each edge, which is a percent or two on a 3 mm band."""
        self.g = g or copper.load()
        self.cell = cell
        self.sub = int(sub)
        if window is None:
            window = (-32.5, -32.5, 32.5, 32.5)
        self.window = window
        x0, y0, x1, y1 = window
        self.nx = int(np.ceil((x1 - x0) / cell)) + 1
        self.ny = int(np.ceil((y1 - y0) / cell)) + 1
        self.x0, self.y0 = x0, y0
        self.layers = list(layers or CU)
        self.nets = list(nets) if nets else None
        self._cache = {}

    # ------------------------------------------------------------- mapping
    def px(self, x, y):
        """(mm, mm) -> (column, row) in the image, row 0 at y = y0."""
        return ((x - self.x0) / self.cell, (y - self.y0) / self.cell)

    def xy(self, i, j):
        return (self.x0 + i * self.cell, self.y0 + j * self.cell)

    def index(self, x, y):
        return (int(round((x - self.x0) / self.cell)),
                int(round((y - self.y0) / self.cell)))

    def extent(self):
        return (self.x0, self.x0 + (self.nx - 1) * self.cell,
                self.y0, self.y0 + (self.ny - 1) * self.cell)

    # -------------------------------------------------------------- masks
    def coverage(self, net, layer):
        """Float array [nx, ny] in [0, 1]: what fraction of the cell centred on
        this grid point is copper of `net` on `layer`?

        Measured by rasterising `sub` times finer and box-averaging, so that a
        plane edge or a narrow trace carries the right cross-section instead of
        being rounded to a whole cell.
        """
        key = ("cov", net, layer)
        if key in self._cache:
            return self._cache[key]
        s = self.sub
        img = Image.new("1", (self.nx * s, self.ny * s), 0)
        d = ImageDraw.Draw(img)
        # sub-pixel k of cell i covers [i - 0.5 + (k + 0.5) / s] cells
        def spx(x, y):
            return (((x - self.x0) / self.cell + 0.5) * s - 0.5,
                    ((y - self.y0) / self.cell + 0.5) * s - 0.5)

        def poly(pts, fill=1):
            p = [spx(x, y) for x, y in pts]
            if len(p) >= 3:
                d.polygon(p, fill=fill)

        for z in self.g["zones"]:
            if z["net"] == net and z["layer"] == layer:
                poly(z["pts"], 1)
                for h in z.get("holes", []):
                    poly(h, 0)
        for pad in self.g["pads"]:
            if pad["net"] != net or layer not in pad["shapes"]:
                continue
            for entry in pad["shapes"][layer]:
                poly(entry["pts"], 1)
                for h in entry.get("holes", []):
                    poly(h, 0)
        for t in self.g["tracks"]:
            if t["net"] != net or t["layer"] != layer:
                continue
            w = t["width"] / self.cell * s
            a = spx(*t["start"])
            b = spx(*t["end"])
            d.line([a, b], fill=1, width=max(1, int(round(w))))
            for pt in (a, b):
                r = w / 2
                d.ellipse([pt[0] - r, pt[1] - r, pt[0] + r, pt[1] + r], fill=1)
        fine = np.array(img, dtype=np.uint8).T          # [nx*s, ny*s]
        cov = fine.reshape(self.nx, s, self.ny, s).mean(axis=(1, 3))
        self._cache[key] = cov
        return cov

    def mask(self, net, layer, threshold=0.25):
        """Bool array [nx, ny]: is there copper of `net` on `layer` here?"""
        key = ("mask", net, layer, threshold)
        if key in self._cache:
            return self._cache[key]
        m = self.coverage(net, layer) >= threshold
        self._cache[key] = m
        return m

    def _unused_mask(self, net, layer):
        img = Image.new("1", (self.nx, self.ny), 0)
        d = ImageDraw.Draw(img)

        def poly(pts, fill=1):
            p = [self.px(x, y) for x, y in pts]
            if len(p) >= 3:
                d.polygon(p, fill=fill)

        for z in self.g["zones"]:
            if z["net"] == net and z["layer"] == layer:
                poly(z["pts"], 1)
                for h in z.get("holes", []):
                    poly(h, 0)
        for pad in self.g["pads"]:
            if pad["net"] != net or layer not in pad["shapes"]:
                continue
            for entry in pad["shapes"][layer]:
                poly(entry["pts"], 1)
                for h in entry.get("holes", []):
                    poly(h, 0)
        for t in self.g["tracks"]:
            if t["net"] != net or t["layer"] != layer:
                continue
            w = t["width"] / self.cell
            a = self.px(*t["start"])
            b = self.px(*t["end"])
            d.line([a, b], fill=1, width=max(1, int(round(w))))
            for pt in (a, b):
                r = w / 2
                d.ellipse([pt[0] - r, pt[1] - r, pt[0] + r, pt[1] + r], fill=1)

        m = np.array(img, dtype=bool).T           # [nx, ny]
        self._cache[key] = m
        return m

    def area(self, net, layer):
        """True copper area, from the sub-pitch coverage."""
        return float(self.coverage(net, layer).sum()) * self.cell ** 2
