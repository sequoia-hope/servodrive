#!/usr/bin/env python3
"""Draw the extracted geometry, layer by layer, so that what the solvers see
can be checked by eye against img/layers/a/ (the P0 gate in SPEC.md sec.6).

    python3 sim/extract/plot.py             # all six layers, whole board
    python3 sim/extract/plot.py --cell A    # the cell-A crop
"""
import sys
import json
import argparse
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                             # noqa: E402
from matplotlib.patches import Polygon as MplPoly           # noqa: E402
from matplotlib.collections import PatchCollection          # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import paths                                       # noqa: E402

# One colour per net family, so that a wrong net shows up as a wrong colour.
NET_COLOUR = {
    "GND": "#5b6b7a", "VBUS": "#c0392b", "+12V": "#e67e22", "+5V": "#d4a017",
    "+3V3": "#2e86c1", "+3V3A": "#5dade2", "+1V1": "#48c9b0",
}
PHASE_COLOUR = {"A": "#8e44ad", "B": "#27ae60", "C": "#16a085"}


def net_colour(net):
    if net in NET_COLOUR:
        return NET_COLOUR[net]
    for pre, base in (("SW_", 0), ("PHASE_", 1)):
        if net.startswith(pre):
            c = PHASE_COLOUR.get(net[-1], "#888")
            return c if base == 0 else c + "cc"
    if net.startswith(("HIN", "LIN", "HO_", "LO_", "GH_", "GL_", "VB_")):
        return "#f39c12"
    if net.startswith(("SNS", "ISENSE", "ENC", "ADC")):
        return "#d35400"
    return "#7f8c8d"


def track_poly(t):
    """A track is a rectangle plus two round caps; the rectangle is enough for
    a picture (the rasteriser in conduction/ does the caps properly)."""
    (x0, y0), (x1, y1) = t["start"], t["end"]
    w = t["width"] / 2.0
    dx, dy = x1 - x0, y1 - y0
    L = np.hypot(dx, dy)
    if L < 1e-9:
        return None
    nx, ny = -dy / L * w, dx / L * w
    return [(x0 + nx, y0 + ny), (x1 + nx, y1 + ny),
            (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)]


def draw_layer(ax, g, layer, window=None, labels=False):
    patches, colours = [], []
    for z in g["zones"]:
        if z["layer"] != layer:
            continue
        patches.append(MplPoly(z["pts"], closed=True))
        colours.append(net_colour(z["net"]))
    for p in g["pads"]:
        sh = p["shapes"].get(layer)
        if not sh:
            continue
        for entry in sh:
            patches.append(MplPoly(entry["pts"], closed=True))
            colours.append(net_colour(p["net"]))
    for t in g["tracks"]:
        if t["layer"] != layer:
            continue
        pts = track_poly(t)
        if pts:
            patches.append(MplPoly(pts, closed=True))
            colours.append(net_colour(t["net"]))
    pc = PatchCollection(patches, facecolors=colours, edgecolors="none", alpha=0.85)
    ax.add_collection(pc)

    order = g["copper_layers"]
    li = order.index(layer)
    vx, vy, vc = [], [], []
    for v in g["vias"] + g["through_pads"]:
        top = order.index(v.get("top", "F.Cu"))
        bot = order.index(v.get("bottom", "B.Cu"))
        if "layers" in v:
            idx = [order.index(l) for l in v["layers"]]
            top, bot = min(idx), max(idx)
        if top <= li <= bot:
            vx.append(v["x"]); vy.append(v["y"]); vc.append(net_colour(v["net"]))
    ax.scatter(vx, vy, s=2.0, c=vc, marker="o", linewidths=0, zorder=5)

    th = np.linspace(0, 2 * np.pi, 361)
    ax.plot(32.5 * np.cos(th), 32.5 * np.sin(th), color="#333", lw=0.6)
    if labels:
        for p in g["parts"]:
            if window and not _inside(p["x"], p["y"], window):
                continue
            ax.text(p["x"], p["y"], p["ref"], fontsize=3.5, ha="center",
                    va="center", color="#111", zorder=9)
    if window:
        ax.set_xlim(window[0], window[1]); ax.set_ylim(window[2], window[3])
    else:
        ax.set_xlim(-34, 34); ax.set_ylim(-34, 34)
    ax.set_aspect("equal"); ax.set_title(layer, fontsize=8)
    ax.tick_params(labelsize=5)


def _inside(x, y, w):
    return w[0] <= x <= w[1] and w[2] <= y <= w[3]


def cell_window(g, cell):
    c = g["cells"][cell]
    a = np.radians(np.linspace(c["a0"], c["a1"], 64))
    xs = np.concatenate([c["r0"] * np.cos(a), c["r1"] * np.cos(a)])
    ys = np.concatenate([c["r0"] * np.sin(a), c["r1"] * np.sin(a)])
    m = 1.0
    return (xs.min() - m, xs.max() + m, ys.min() - m, ys.max() + m)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cell", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    g = json.loads(paths.GEOMETRY.read_text())
    win = cell_window(g, a.cell) if a.cell else None

    fig, axes = plt.subplots(2, 3, figsize=(15, 10), dpi=170)
    for ax, layer in zip(axes.ravel(), g["copper_layers"]):
        draw_layer(ax, g, layer, win, labels=bool(a.cell))
    fig.suptitle(f"servodrive A -- extracted copper"
                 + (f", cell {a.cell} crop" if a.cell else ""), fontsize=11)
    fig.tight_layout()
    out = Path(a.out) if a.out else (
        paths.FIGS / (f"extract_cell{a.cell}.png" if a.cell else "extract_board.png"))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    print("wrote", out)


if __name__ == "__main__":
    main()
