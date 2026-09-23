#!/usr/bin/env python3
"""Drive FastCap 2.0 (FastFieldSolvers fork, built by sim/setup.py).

FastCap is an electro-quasi-static boundary-element solver: conductors are
tiled with flat panels, and it returns the short-circuit capacitance matrix.

Geometry is in metres (FastCap is MKS) and the matrix comes back in picofarads.
Everything in this project is in millimetres, so `Model` takes millimetres and
converts on the way out -- one place, so it cannot be got wrong twice.

The matrix FastCap prints is the *Maxwell* capacitance matrix: the diagonal is
the total capacitance of a conductor to everything else, the off-diagonals are
negative.  The two-terminal capacitance between i and j is -C[i][j].
"""
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import paths                                        # noqa: E402

MM = 1e-3
EPS0 = 8.8541878128e-12


class Model:
    """Panels grouped by conductor.  Coordinates in mm."""

    def __init__(self, title, epsilon_r=1.0):
        self.title = title
        self.epsilon_r = epsilon_r
        self.conductors = {}          # name -> list of panels (each 3 or 4 xyz)

    def _cond(self, name):
        return self.conductors.setdefault(name, [])

    def quad(self, cond, p1, p2, p3, p4):
        self._cond(cond).append([p1, p2, p3, p4])

    def tri(self, cond, p1, p2, p3):
        self._cond(cond).append([p1, p2, p3])

    def rect_z(self, cond, x0, y0, x1, y1, z, nx=1, ny=1):
        """An axis-aligned rectangle in a z = const plane, tiled nx by ny.

        Tiling matters: FastCap's accuracy on a plate comes from having small
        panels near the edges, and a 1 x 1 plate is badly wrong.
        """
        xs = np.linspace(x0, x1, nx + 1)
        ys = np.linspace(y0, y1, ny + 1)
        for i in range(nx):
            for j in range(ny):
                self.quad(cond,
                          (xs[i], ys[j], z), (xs[i + 1], ys[j], z),
                          (xs[i + 1], ys[j + 1], z), (xs[i], ys[j + 1], z))

    def geometry_raster(self, cond, geom, z, cell=0.4):
        """Tile a shapely geometry with axis-aligned quads on a raster.

        A fan triangulation of a concave polygon produces panels outside the
        shape and panels that overlap, and FastCap answers those with negative
        potential coefficients and a capacitance matrix with positive
        off-diagonals -- which is how the first attempt at this failed.  A
        raster cannot do either.
        """
        from shapely.geometry import box as _box
        from shapely import prepared as _prep
        minx, miny, maxx, maxy = geom.bounds
        pr = _prep.prep(geom)
        nx = max(1, int(math.ceil((maxx - minx) / cell)))
        ny = max(1, int(math.ceil((maxy - miny) / cell)))
        n = 0
        for i in range(nx):
            for j in range(ny):
                x0 = minx + i * cell
                y0 = miny + j * cell
                b = _box(x0, y0, x0 + cell, y0 + cell)
                if pr.intersects(b):
                    inter = geom.intersection(b)
                    if inter.area < 0.35 * cell * cell:
                        continue
                    self.quad(cond, (x0, y0, z), (x0 + cell, y0, z),
                              (x0 + cell, y0 + cell, z), (x0, y0 + cell, z))
                    n += 1
        return n

    def polygon_z(self, cond, pts, z, max_edge=0.5):
        """A closed polygon in a z = const plane, triangulated and refined.

        Used for real copper: pad outlines, zone fragments.
        """
        from matplotlib.tri import Triangulation
        pts = np.asarray(pts, float)
        tri = _triangulate(pts, max_edge)
        for a, b, c in tri:
            self.tri(cond, (a[0], a[1], z), (b[0], b[1], z), (c[0], c[1], z))

    # ------------------------------------------------------------------ text
    @staticmethod
    def _degenerate(p, min_area=1e-6, min_edge=1e-4):
        """A panel with a zero-length edge or no area makes FastCap's
        potential coefficient go negative and the solve is then garbage, so
        such panels are dropped rather than emitted."""
        import numpy as _np
        a = _np.asarray(p, float)
        n = len(a)
        for i in range(n):
            if _np.linalg.norm(a[i] - a[(i + 1) % n]) < min_edge:
                return True
        if n == 3:
            ar = 0.5 * abs(_np.cross(a[1] - a[0], a[2] - a[0])[-1]
                           if a.shape[1] == 3 else
                           _np.cross(a[1] - a[0], a[2] - a[0]))
        else:
            ar = 0.5 * abs(sum(a[i][0] * a[(i + 1) % n][1]
                               - a[(i + 1) % n][0] * a[i][1] for i in range(n)))
        return ar < min_area

    def text(self):
        L = [f"0 {self.title}"]
        self.dropped = 0
        for k, (name, panels) in enumerate(self.conductors.items(), start=1):
            L.append(f"* conductor {k}: {name}")
            for p in panels:
                if self._degenerate(p):
                    self.dropped += 1
                    continue
                tag = "Q" if len(p) == 4 else "T"
                coords = " ".join(f"{c * MM:.9e}" for pt in p for c in pt)
                L.append(f"{tag} {k} {coords}")
        return "\n".join(L) + "\n"

    def names(self):
        return list(self.conductors)


def _triangulate(pts, max_edge):
    """Fan-triangulate a convex-ish polygon, then split long edges.

    Good enough for pads and small zone fragments; anything concave is handled
    by shapely upstream (extract/), which hands us convex pieces.
    """
    import numpy as np
    out = []
    c = pts.mean(axis=0)
    n = len(pts)
    for i in range(n):
        out.append((pts[i], pts[(i + 1) % n], c))
    # refine
    changed = True
    while changed:
        changed = False
        nxt = []
        for a, b, cc in out:
            e = [(np.linalg.norm(b - a), 0), (np.linalg.norm(cc - b), 1),
                 (np.linalg.norm(a - cc), 2)]
            e.sort(reverse=True)
            if e[0][0] > max_edge:
                changed = True
                which = e[0][1]
                if which == 0:
                    m = (a + b) / 2
                    nxt += [(a, m, cc), (m, b, cc)]
                elif which == 1:
                    m = (b + cc) / 2
                    nxt += [(a, b, m), (a, m, cc)]
                else:
                    m = (cc + a) / 2
                    nxt += [(a, b, m), (m, b, cc)]
            else:
                nxt.append((a, b, cc))
        out = nxt
        if len(out) > 200000:
            break
    return out


_MAT = re.compile(r"CAPACITANCE MATRIX, (\w+)")


def parse_matrix(text):
    """-> (C [n, n] in farads, conductor labels)."""
    i = text.rindex("CAPACITANCE MATRIX")
    unit = _MAT.search(text[i:]).group(1).lower()
    scale = {"picofarads": 1e-12, "farads": 1.0,
             "nanofarads": 1e-9, "femtofarads": 1e-15}[unit]
    lines = text[i:].splitlines()[1:]
    rows, labels = [], []
    for ln in lines:
        if not ln.strip():
            if rows:
                break
            continue
        parts = ln.split()
        if not re.match(r"^-?\d", parts[-1]):
            continue
        # "<label> <index> v v v"
        try:
            idx = next(k for k, p in enumerate(parts) if re.match(r"^-?\d+$", p))
        except StopIteration:
            continue
        if idx == 0:
            continue            # the column-number header line has no label
        vals = parts[idx + 1:]
        if not vals:
            continue
        try:
            row = [float(v) for v in vals]
        except ValueError:
            continue
        labels.append(" ".join(parts[:idx]))
        rows.append(row)
    return np.array(rows) * scale, labels


def run(model, workdir=None, order=2, depth=None, keep=True, timeout=7200):
    """Tile, solve, and return the capacitance matrix."""
    if not paths.FASTCAP_BIN.exists():
        raise FileNotFoundError(
            f"fastcap not built at {paths.FASTCAP_BIN}; run sim/setup.py")
    wd = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="fc_"))
    wd.mkdir(parents=True, exist_ok=True)
    qui = wd / "model.qui"
    qui.write_text(model.text())
    # The permittivity of the (uniform) medium goes on the C line of a list file.
    lst = wd / "model.lst"
    lst.write_text(f"* {model.title}\nC model.qui {model.epsilon_r:.6g} 0 0 0\n")
    cmd = [str(paths.FASTCAP_BIN), f"-o{order}"]
    if depth:
        cmd.append(f"-d{depth}")
    cmd.append("-l" + lst.name)
    p = subprocess.run(cmd, cwd=wd, capture_output=True, text=True, timeout=timeout)
    if "CAPACITANCE MATRIX" not in p.stdout:
        raise RuntimeError(f"fastcap failed in {wd}\n{p.stdout[-3000:]}\n{p.stderr[-2000:]}")
    (wd / "fastcap.out").write_text(p.stdout)
    C, labels = parse_matrix(p.stdout)
    if not keep:
        shutil.rmtree(wd, ignore_errors=True)
    return C, labels, p.stdout


def mutual(C, i, j):
    """Two-terminal capacitance between conductors i and j, in farads."""
    return -C[i, j]
