#!/usr/bin/env python3
"""L5: DC and low-frequency conduction on the six copper layers plus vias.

The physics is Laplace's equation in a conductor, div(sigma grad V) = 0, with
current injected at one terminal and drawn at another.  On a square raster of
pitch h, a layer of thickness t has a link conductance between neighbouring
cells of

    G = sigma * t * f

where f is the harmonic mean of the two cells' copper coverage (measured at a
sub-pitch, so an edge that cuts a cell in half counts as half).  The pitch
cancels -- refining the grid does not change a plane's sheet resistance, it
only resolves the shapes better -- and the coverage weighting is what keeps a
3 mm band from being a half-cell too wide at each edge.  A plated barrel between two layers is a lumped conductance
sigma_plated * A_barrel / d, with d the distance between the two mid-planes.

Solved with conjugate gradients preconditioned by algebraic multigrid (pyamg),
which is what a 10^6-unknown Laplacian wants.

Out of it: the potential field, hence the current density on every layer, the
current in every barrel, the terminal-to-terminal resistance and the Joule
density -- Q6 and Q7, and the current paths that P5's Biot-Savart needs.
"""
import sys
from pathlib import Path

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from extract import copper                                   # noqa: E402
from conduction.raster import Raster                         # noqa: E402

SIGMA_CU = 5.8e7          # S/m at 20 C, annealed
SIGMA_PLATED = 4.7e7      # S/m, plated barrel
ALPHA_CU = 0.00393        # 1/K
MM = 1e-3
CU = copper.CU_LAYERS


def sigma_at(T, sigma20=SIGMA_CU):
    return sigma20 / (1.0 + ALPHA_CU * (T - 20.0))


class Conductor:
    """One electrically-connected body of copper: given nets, on given layers,
    stitched by the plated barrels of those nets."""

    def __init__(self, nets, layers=None, cell=0.05, window=None,
                 temperature=25.0, raster=None, g=None, plating=0.025,
                 masks=None, stack=None, origin=(0.0, 0.0)):
        """`masks` and `stack` let a caller hand in synthetic geometry -- that
        is how tests/kat.py checks this solver against closed forms without a
        board."""
        self.synthetic = masks is not None
        if self.synthetic:
            self._init_synthetic(masks, stack, cell, origin, temperature)
            return
        self.g = g or copper.load()
        self.nets = [nets] if isinstance(nets, str) else list(nets)
        self.layers = list(layers or CU)
        self.cell = cell
        self.T = temperature
        self.plating = plating
        self.sigma = sigma_at(temperature, SIGMA_CU)
        self.sigma_plated = sigma_at(temperature, SIGMA_PLATED)
        self.r = raster or Raster(cell=cell, window=window, g=self.g)
        self.cell = self.r.cell
        self.stack = copper.stackup(self.g)
        self._build()

    def _init_synthetic(self, masks, stack, cell, origin, temperature):
        class _R:
            pass
        first = next(iter(masks.values()))
        r = _R()
        r.nx, r.ny = first.shape
        r.cell = cell
        r.x0, r.y0 = origin
        r.window = (origin[0], origin[1],
                    origin[0] + (r.nx - 1) * cell, origin[1] + (r.ny - 1) * cell)
        r.index = lambda x, y: (int(round((x - r.x0) / cell)),
                                int(round((y - r.y0) / cell)))
        self.r = r
        self.cell = cell
        self.g = None
        self.nets = ["synthetic"]
        self.layers = list(masks)
        self.T = temperature
        self.plating = 0.025
        self.sigma = sigma_at(temperature, SIGMA_CU)
        self.sigma_plated = sigma_at(temperature, SIGMA_PLATED)
        self.stack = stack
        self._masks_in = masks
        self._build(synthetic=True)

    # ------------------------------------------------------------- meshing
    def _build(self, synthetic=False):
        r = self.r
        self.mask, self.index, self.cov = {}, {}, {}
        n = 0
        for layer in self.layers:
            if synthetic:
                m = np.asarray(self._masks_in[layer], bool)
            else:
                m = np.zeros((r.nx, r.ny), bool)
                for net in self.nets:
                    m |= r.mask(net, layer)
            self.mask[layer] = m
            if synthetic:
                self.cov[layer] = m.astype(float)
            else:
                cov = np.zeros((r.nx, r.ny))
                for net in self.nets:
                    cov = np.maximum(cov, r.coverage(net, layer))
                self.cov[layer] = np.where(m, np.clip(cov, 1e-3, 1.0), 0.0)
            idx = np.full((r.nx, r.ny), -1, np.int64)
            k = int(m.sum())
            idx[m] = np.arange(n, n + k)
            self.index[layer] = idx
            n += k
        self.n = n
        if n == 0:
            raise RuntimeError(f"no copper for {self.nets} on {self.layers} "
                               f"in {r.window}")

        I, J, V = [], [], []
        self.plane_links = 0

        def add(a, b, gv):
            a = np.asarray(a).ravel()
            b = np.asarray(b).ravel()
            gv = np.broadcast_to(np.asarray(gv, float), a.shape).ravel()
            I.append(np.concatenate([a, b, a, b]))
            J.append(np.concatenate([a, b, b, a]))
            V.append(np.concatenate([gv, gv, -gv, -gv]))

        for layer in self.layers:
            idx = self.index[layer]
            cov = self.cov[layer]
            t = self.stack[layer]["t"] * MM
            G0 = self.sigma * t
            for sl_a, sl_b in ((np.s_[:-1, :], np.s_[1:, :]),
                               (np.s_[:, :-1], np.s_[:, 1:])):
                a, b = idx[sl_a], idx[sl_b]
                fa, fb = cov[sl_a], cov[sl_b]
                ok = (a >= 0) & (b >= 0)
                f = 2 * fa * fb / np.where(fa + fb > 0, fa + fb, 1.0)
                add(a[ok], b[ok], G0 * f[ok])
                self.plane_links += int(ok.sum())

        self.barrels = []
        for bb in ([] if synthetic else
                   copper.barrels(self.nets, self.r.window, self.g)):
            i, j = self.r.index(bb["x"], bb["y"])
            if not (0 <= i < r.nx and 0 <= j < r.ny):
                continue
            area = copper.barrel_area(bb["drill"], self.plating) * MM * MM
            touch = [CU[k] for k in range(bb["i0"], bb["i1"] + 1)
                     if CU[k] in self.layers and self.index[CU[k]][i, j] >= 0]
            links = []
            for l0, l1 in zip(touch[:-1], touch[1:]):
                d = abs(self.stack[l1]["z"] - self.stack[l0]["z"]) * MM
                G = self.sigma_plated * area / d
                a, b = self.index[l0][i, j], self.index[l1][i, j]
                add([a], [b], G)
                links.append((l0, l1, a, b, G))
            if links:
                self.barrels.append(dict(bb, i=i, j=j, layers=touch, area=area,
                                         links=links))
        for bb in (self._extra_barrels if hasattr(self, "_extra_barrels") else []):
            pass

        self.A = sp.coo_matrix(
            (np.concatenate(V), (np.concatenate(I), np.concatenate(J))),
            shape=(self.n, self.n)).tocsr()
        self.A.sum_duplicates()

    # ------------------------------------------------------------ terminals
    def nodes_in(self, layer, region):
        """Indices of the conductor's nodes inside a region.

        region: (x0, y0, x1, y1) box in mm, or a shapely geometry, or a
        callable(x, y) -> bool.
        """
        idx = self.index[layer]
        ii, jj = np.nonzero(idx >= 0)
        xs = self.r.x0 + ii * self.cell
        ys = self.r.y0 + jj * self.cell
        if callable(region):
            keep = np.array([region(x, y) for x, y in zip(xs, ys)])
        elif hasattr(region, "contains"):
            from shapely import prepared
            from shapely.geometry import Point
            pr = prepared.prep(region)
            keep = np.array([pr.contains(Point(x, y)) for x, y in zip(xs, ys)])
        else:
            x0, y0, x1, y1 = region
            keep = (xs >= x0) & (xs <= x1) & (ys >= y0) & (ys <= y1)
        return idx[ii[keep], jj[keep]]

    def pad_nodes(self, ref, padnum, layer=None):
        """Nodes under one component pad -- the natural terminal."""
        out = []
        for p in copper.pads_of(ref, self.g):
            if p["pad"] != str(padnum):
                continue
            for lname, shapes in p["shapes"].items():
                if lname not in self.layers:
                    continue
                if layer and lname != layer:
                    continue
                for entry in shapes:
                    from shapely.geometry import Polygon
                    poly = Polygon(entry["pts"])
                    out.append(self.nodes_in(lname, poly))
        out = [o for o in out if len(o)]
        if not out:
            raise RuntimeError(f"no conductor nodes under {ref}.{padnum}")
        return np.unique(np.concatenate(out))

    # ------------------------------------------------------------- solving
    def solve(self, terminals, tol=1e-11, verbose=False, maxiter=2000):
        """terminals: list of (node_indices, current_A).  Currents must sum to
        zero; each terminal is treated as an equipotential (a perfect pad)."""
        groups = [np.unique(np.asarray(t[0], np.int64)) for t in terminals]
        currents = [float(t[1]) for t in terminals]
        if abs(sum(currents)) > 1e-9 * max(1.0, max(abs(c) for c in currents)):
            raise ValueError("terminal currents must sum to zero")

        # tie each terminal's nodes together with a very stiff link to the
        # group's first node: a pad is an equipotential, not a point
        Gbig = float(abs(self.A.diagonal()).max()) * 1e4
        I, J, V = [], [], []
        for grp in groups:
            if len(grp) < 2:
                continue
            a = np.full(len(grp) - 1, grp[0])
            b = grp[1:]
            g = np.full(len(b), Gbig)
            I.append(np.concatenate([a, b, a, b]))
            J.append(np.concatenate([a, b, b, a]))
            V.append(np.concatenate([g, g, -g, -g]))
        A = self.A
        if I:
            A = (A + sp.coo_matrix(
                (np.concatenate(V), (np.concatenate(I), np.concatenate(J))),
                shape=A.shape)).tocsr()

        b = np.zeros(self.n)
        for grp, cur in zip(groups, currents):
            b[grp[0]] += cur

        # ground the last terminal's first node
        ref = groups[-1][0]
        A = A.tolil()
        A[ref, :] = 0
        A[:, ref] = 0
        A[ref, ref] = 1.0
        A = A.tocsr()
        b[ref] = 0.0

        try:
            import pyamg
            ml = pyamg.smoothed_aggregation_solver(A, max_coarse=500)
            M = ml.aspreconditioner()
            V_, info = spla.cg(A, b, rtol=tol, maxiter=maxiter, M=M)
            if info != 0:
                raise RuntimeError(f"cg did not converge, info={info}")
        except ImportError:
            V_ = spla.spsolve(A, b)
            info = 0

        self.V = V_
        res = float(np.linalg.norm(A @ V_ - b) / max(np.linalg.norm(b), 1e-30))
        if verbose:
            print(f"solved {self.n} unknowns, residual {res:.2e}")
        self.residual = res
        self.groups = groups
        self.currents = currents
        return V_

    # ------------------------------------------------------------- outputs
    def terminal_voltage(self, k):
        return float(self.V[self.groups[k][0]])

    def resistance(self, k=0, m=-1):
        """Terminal-to-terminal resistance from the solved potentials."""
        dv = self.terminal_voltage(k) - self.terminal_voltage(m)
        return dv / self.currents[k]

    def current_density(self, layer):
        """(Jx, Jy) in A/m^2 at every cell of `layer`, and |J|."""
        idx = self.index[layer]
        t = self.stack[layer]["t"] * MM
        h = self.cell * MM
        V = np.where(idx >= 0, self.V[np.clip(idx, 0, None)], np.nan)
        Jx = np.zeros_like(V)
        Jy = np.zeros_like(V)
        # central differences where both neighbours are copper
        ok = (idx[:-1, :] >= 0) & (idx[1:, :] >= 0)
        dv = np.where(ok, V[1:, :] - V[:-1, :], 0.0)
        jx = -self.sigma * dv / h                     # A/m^2
        Jx[:-1, :] += jx / 2
        Jx[1:, :] += jx / 2
        ok = (idx[:, :-1] >= 0) & (idx[:, 1:] >= 0)
        dv = np.where(ok, V[:, 1:] - V[:, :-1], 0.0)
        jy = -self.sigma * dv / h
        Jy[:, :-1] += jy / 2
        Jy[:, 1:] += jy / 2
        Jx[idx < 0] = np.nan
        Jy[idx < 0] = np.nan
        return Jx, Jy, np.hypot(Jx, Jy)

    def sheet_current(self, layer):
        """Linear current density in A/mm (what a copper-width rule is about)."""
        Jx, Jy, Jm = self.current_density(layer)
        t = self.stack[layer]["t"] * MM
        return Jm * t / 1e3

    def barrel_currents(self):
        """Current in every plated hole, A (positive = downwards in the stack)."""
        out = []
        for b in self.barrels:
            tot = 0.0
            per = []
            for (l0, l1, a, bb_, G) in b["links"]:
                i = G * (self.V[a] - self.V[bb_])
                per.append({"from": l0, "to": l1, "I": float(i)})
                tot = max(tot, abs(i))
            out.append({"ref": b["ref"], "net": b["net"],
                        "x": b["x"], "y": b["y"], "drill": b["drill"],
                        "I_max": float(tot), "links": per})
        return out

    def joule(self):
        """Total dissipation, W, from the solved field."""
        return float(self.V @ (self.A @ self.V))

    def joule_map(self, layer):
        """Volumetric Joule density, W/m^3, per cell."""
        Jx, Jy, Jm = self.current_density(layer)
        return Jm ** 2 / self.sigma
