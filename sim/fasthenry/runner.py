#!/usr/bin/env python3
"""Drive FastHenry 3.0.1 (FastFieldSolvers fork, built by sim/setup.py).

FastHenry is a partial-element-equivalent-circuit (PEEC) magneto-quasi-static
solver: it discretises every conductor into filaments, fills the partial
inductance and resistance matrices, and returns the terminal impedance Z(f) of
each port.  L(f) = Im(Z)/omega, R(f) = Re(Z).

This module owns three things and nothing else:
  * `Model` -- an object you add nodes, segments, planes and ports to, which
    writes a `.inp` file.  Units are always written explicitly (SPEC.md sec.9).
  * `run()` -- invoke the binary in a scratch directory and parse Zc.mat.
  * the conductivity and unit conventions.

The one unit trap: `.units mm` makes every length millimetres, and then
`sigma` must be given in 1/(mm.Ohm), i.e. S/m divided by 1000.
"""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import paths                                        # noqa: E402

SIGMA_CU = 5.8e7            # S/m, annealed copper (SPEC.md sec.7)
SIGMA_CU_PLATED = 4.7e7     # S/m, plated via barrel / ED foil
MU0 = 4e-7 * np.pi


def sigma_mm(sigma_si):
    """S/m -> 1/(mm.Ohm), which is what `.units mm` wants."""
    return sigma_si / 1000.0


class Model:
    """A FastHenry input deck.

    Coordinates are in mm in the tools' frame (x, y as in geometry.json, z
    downwards from the top of F.Cu, matching the stackup in geometry.json).
    """

    def __init__(self, title, sigma=SIGMA_CU, units="mm"):
        self.title = title
        self.units = units
        self.sigma = sigma
        self.nodes = {}          # name -> (x, y, z)
        self.segments = []       # (name, n1, n2, kwargs)
        self.planes = []         # dicts
        self.equivs = []         # tuples of node names shorted together
        self.ports = []          # (name, n_plus, n_minus)
        self._n = 0

    # ---------------------------------------------------------------- nodes
    # FastHenry infers the kind of an object from the first letter of its name:
    # N for nodes, E for segments, G for planes.  Everything below enforces it
    # so that a caller's "q1_drain" cannot silently become an unknown line.
    @staticmethod
    def _nname(name):
        return name if name[0] in "Nn" else "N_" + name

    @staticmethod
    def _ename(name):
        return name if name[0] in "Ee" else "E_" + name

    @staticmethod
    def _gname(name):
        return name if name[0] in "Gg" else "G_" + name

    def node(self, name, x, y, z):
        name = self._nname(name)
        if name in self.nodes:
            raise KeyError(f"duplicate node {name}")
        self.nodes[name] = (x, y, z)
        return name

    def auto_node(self, x, y, z, prefix="N"):
        self._n += 1
        return self.node(f"{prefix}{self._n}", x, y, z)

    def has(self, name):
        return self._nname(name) in self.nodes

    # ------------------------------------------------------------- segments
    def segment(self, n1, n2, w, h, nwinc=3, nhinc=3, sigma=None, name=None):
        """A rectangular bar from node n1 to n2, width w, height h (mm).

        nwinc/nhinc are the filament counts across width and height: they set
        how well skin and proximity effect are resolved.  FastHenry wants an
        odd count; it grades them geometrically by default.
        """
        if name is None:
            self._n += 1
            name = f"E{self._n}"
        name, n1, n2 = self._ename(name), self._nname(n1), self._nname(n2)
        self.segments.append((name, n1, n2, dict(
            w=w, h=h, nwinc=nwinc, nhinc=nhinc,
            sigma=None if sigma is None else sigma_mm(sigma))))
        return name

    # ---------------------------------------------------------------- plane
    def plane(self, name, corners, thick, seg1, seg2, nhinc=1,
              holes=(), nodes=(), sigma=None, rh=None, rw=None):
        """A uniform ground plane.

        corners: three (x, y, z) points -- origin, then the two edges.
        holes:   ("rect", x1, y1, x2, y2) | ("circle", x, y, r) | ("point", x, y)
                 in plane coordinates, which for a flat plane are just x, y.
        nodes:   ((nodename, x, y, z), ...) points tied into the plane mesh.
        """
        name = self._gname(name)
        nodes = [(self._nname(nd[0]), *nd[1:4]) for nd in nodes]
        self.planes.append(dict(name=name, corners=corners, thick=thick,
                                seg1=seg1, seg2=seg2, nhinc=nhinc,
                                holes=list(holes), nodes=list(nodes),
                                sigma=None if sigma is None else sigma_mm(sigma),
                                rh=rh, rw=rw))
        for nd in nodes:
            self.nodes.setdefault(nd[0], tuple(nd[1:4]))
        return name

    # ------------------------------------------------------- equiv and port
    def equiv(self, *names):
        self.equivs.append(tuple(self._nname(n) for n in names))

    def port(self, name, n_plus, n_minus):
        self.ports.append((name, self._nname(n_plus), self._nname(n_minus)))

    # ----------------------------------------------------------- pruning --
    def prune_to_ports(self):
        """Drop every piece of copper that has no path to a port.

        A meshed layer often contains islands -- a pad with no via in the
        window, a fragment of pour cut off by a clearance.  FastHenry's mesh
        analysis is singular on those, and the whole impedance matrix comes
        back as NaN rather than as an error, so they are removed here and
        counted.
        """
        parent = {}

        def find(a):
            parent.setdefault(a, a)
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for _, n1, n2, _ in self.segments:
            union(n1, n2)
        for grp in self.equivs:
            for n in grp[1:]:
                union(grp[0], n)
        keep = set()
        split = []
        for nm, a, b in self.ports:
            ra, rb = find(a), find(b)
            if ra != rb:
                split.append(nm)
            keep.add(ra)
            keep.add(rb)
        if split:
            raise RuntimeError(
                f"port(s) {split} have their two terminals on separate pieces "
                f"of copper: the loop is open and every impedance would come "
                f"back as NaN. Check the mesh resolution against the narrowest "
                f"track in the model.")
        if not keep:
            return {"removed_segments": 0, "removed_nodes": 0}
        segs = [s for s in self.segments if find(s[1]) in keep]
        removed = len(self.segments) - len(segs)
        self.segments = segs
        live = {n for s in self.segments for n in (s[1], s[2])}
        live |= {n for grp in self.equivs for n in grp if find(n) in keep}
        for p in self.planes:
            live |= {nd[0] for nd in p["nodes"]}
        dropped = [n for n in self.nodes if n not in live]
        for n in dropped:
            del self.nodes[n]
        self.equivs = [tuple(n for n in grp if n in live)
                       for grp in self.equivs]
        self.equivs = [g for g in self.equivs if len(g) > 1]
        return {"removed_segments": removed, "removed_nodes": len(dropped)}

    # ----------------------------------------------------------------- text
    def text(self, fmin, fmax, ndec=1):
        L = [f"* {self.title}", f".units {self.units}",
             f".default sigma={sigma_mm(self.sigma):.6g}", ""]
        for nm, (x, y, z) in self.nodes.items():
            if any(nm == nd[0] for p in self.planes for nd in p["nodes"]):
                continue                      # plane nodes are declared inline
            L.append(f"{nm} x={x:.6f} y={y:.6f} z={z:.6f}")
        L.append("")
        for nm, n1, n2, kw in self.segments:
            extra = " ".join(f"{k}={v:.6g}" for k, v in kw.items() if v is not None)
            L.append(f"{nm} {n1} {n2} {extra}")
        L.append("")
        for p in self.planes:
            (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = p["corners"]
            L.append(f"{p['name']} x1={x1:.6f} y1={y1:.6f} z1={z1:.6f}")
            L.append(f"+ x2={x2:.6f} y2={y2:.6f} z2={z2:.6f}")
            L.append(f"+ x3={x3:.6f} y3={y3:.6f} z3={z3:.6f}")
            L.append(f"+ thick={p['thick']:.6f} seg1={p['seg1']} seg2={p['seg2']}")
            if p["nhinc"] and p["nhinc"] > 1:
                L.append(f"+ nhinc={p['nhinc']}")
            if p["rh"]:
                L.append(f"+ rh={p['rh']:.6f}")
            if p["rw"]:
                L.append(f"+ rw={p['rw']:.6f}")
            if p["sigma"]:
                L.append(f"+ sigma={p['sigma']:.6g}")
            for h in p["holes"]:
                if h[0] == "rect":
                    L.append(f"+ hole rect ({h[1]:.4f},{h[2]:.4f},{h[3]:.4f},{h[4]:.4f})")
                elif h[0] == "circle":
                    L.append(f"+ hole circle ({h[1]:.4f},{h[2]:.4f},{h[3]:.4f})")
                elif h[0] == "point":
                    L.append(f"+ hole point ({h[1]:.4f},{h[2]:.4f})")
            for nd in p["nodes"]:
                L.append(f"+ {nd[0]} ({nd[1]:.6f},{nd[2]:.6f},{nd[3]:.6f})")
            L.append("")
        # FastHenry reads with fgets into a fixed buffer and silently
        # truncates anything longer, which turns a pad equipotential of a
        # hundred mesh nodes into a corrupt netlist.  Chain each group
        # through its first node, a few names per line.
        for grp in self.equivs:
            grp = list(dict.fromkeys(grp))
            if len(grp) < 2:
                continue
            head, rest = grp[0], grp[1:]
            for k in range(0, len(rest), 8):
                L.append(".equiv " + head + " " + " ".join(rest[k:k + 8]))
        for nm, a, b in self.ports:
            L.append(f".external {a} {b} {nm}")
        L.append(f".freq fmin={fmin:g} fmax={fmax:g} ndec={ndec:g}")
        L.append(".end")
        return "\n".join(L) + "\n"


# ----------------------------------------------------------------- running --
_ZC_HEADER = re.compile(r"Impedance matrix for frequency = ([0-9.eE+-]+)\s+(\d+)\s*x\s*(\d+)")
_ROW = re.compile(r"^Row\s+(\d+):\s*([^\s,]+)\s+to\s+([^\s,]+)(?:\s*,\s*port name:\s*(\S+))?")
_CPLX = re.compile(r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*([-+])\s*(\d*\.?\d+(?:[eE][-+]?\d+)?)j")


def parse_zc(text):
    """Zc.mat -> (freqs [Hz], Z [nfreq, n, n] complex, port names).

    The file lists the port order once, as `Row k: na to nb, port name: p`,
    and then one `Impedance matrix for frequency = f n x n` block per
    frequency whose rows are bare `re +imj` pairs.
    """
    order, freqs, mats = [], [], []
    cur, n = None, 0
    for line in text.splitlines():
        m = _ROW.match(line.strip())
        if m:
            order.append(m.group(4) or f"port{m.group(1)}")
            continue
        m = _ZC_HEADER.search(line)
        if m:
            if cur:
                mats.append(np.array(cur))
            freqs.append(float(m.group(1)))
            n = int(m.group(2))
            cur = []
            continue
        if cur is not None:
            vals = _CPLX.findall(line)
            if vals:
                cur.append([float(a) + (1 if s_ == "+" else -1) * float(b) * 1j
                            for a, s_, b in vals])
    if cur:
        mats.append(np.array(cur))
    Z = np.array(mats)
    if Z.ndim == 2:                      # a single port collapses a dimension
        Z = Z.reshape(len(freqs), n, n)
    return np.array(freqs), Z, order


def run(model, fmin, fmax, ndec=1, workdir=None, keep=True, extra_args=(),
        timeout=7200, cache=True):
    """Write the deck, run fasthenry, return (freqs, Z, ports, stdout).

    `cache` reuses a previous result when the deck in the working directory is
    byte-identical to the one about to be written.  Identical input, identical
    output -- so this changes how long a re-run takes and not what it says.
    Delete `sim/work/fh/` (or pass cache=False) to force the solve.
    """
    if not paths.FASTHENRY_BIN.exists():
        raise FileNotFoundError(
            f"fasthenry not built at {paths.FASTHENRY_BIN}; run sim/setup.py")
    wd = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="fh_"))
    wd.mkdir(parents=True, exist_ok=True)
    inp = wd / "model.inp"
    deck = model.text(fmin, fmax, ndec)
    zc = wd / "Zc.mat"
    if (cache and inp.exists() and zc.exists() and zc.stat().st_size > 0
            and inp.read_text() == deck):
        f, Z, order = parse_zc(zc.read_text())
        return f, Z, order, "(cached)"
    inp.write_text(deck)
    cmd = [str(paths.FASTHENRY_BIN), *extra_args, inp.name]
    p = subprocess.run(cmd, cwd=wd, capture_output=True, text=True,
                       timeout=timeout)
    if not zc.exists():
        raise RuntimeError(
            f"fasthenry produced no Zc.mat in {wd}\n"
            f"--- stdout ---\n{p.stdout[-4000:]}\n--- stderr ---\n{p.stderr[-2000:]}")
    f, Z, order = parse_zc(zc.read_text())
    if not keep:
        shutil.rmtree(wd, ignore_errors=True)
    return f, Z, order, p.stdout


def LR(f, Z, i=0, j=None):
    """Series L(f) [H] and R(f) [Ohm] of one port (or a port pair)."""
    j = i if j is None else j
    z = Z[:, i, j]
    return np.imag(z) / (2 * np.pi * f), np.real(z)
