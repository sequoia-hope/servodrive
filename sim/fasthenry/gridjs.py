#!/usr/bin/env python3
"""Read FastHenry's filament-current dump.

With `-d GRIDS` FastHenry writes Jreal.mat / Jimag.mat / Jmag.mat: one line per
filament, `x y z Jx Jy Jz`, where J is the current density (the filament's
current divided by its area, times its unit direction vector).  That is the
only way to see where the return current actually goes -- which is the whole
point of Q1 and of hypothesis H1.
"""
import numpy as np


def read(path):
    a = np.loadtxt(path)
    if a.ndim == 1:
        a = a.reshape(1, -1)
    return {"x": a[:, 0], "y": a[:, 1], "z": a[:, 2],
            "Jx": a[:, 3], "Jy": a[:, 4], "Jz": a[:, 5]}


def by_layer(d, stack, tol=0.02):
    """Split the filaments by which copper layer their z falls in."""
    out = {}
    for name, s in stack.items():
        m = np.abs(d["z"] - s["z"]) <= (s["t"] / 2 + tol)
        if m.any():
            out[name] = {k: v[m] for k, v in d.items()}
    return out


def grid(layer_d, window, cell=0.2):
    """Bin |J| onto a raster for plotting, in A/m^2."""
    x0, y0, x1, y1 = window
    nx = int(np.ceil((x1 - x0) / cell))
    ny = int(np.ceil((y1 - y0) / cell))
    Jm = np.hypot(layer_d["Jx"], layer_d["Jy"])
    i = np.clip(((layer_d["x"] - x0) / cell).astype(int), 0, nx - 1)
    j = np.clip(((layer_d["y"] - y0) / cell).astype(int), 0, ny - 1)
    acc = np.zeros((nx, ny))
    cnt = np.zeros((nx, ny))
    np.add.at(acc, (i, j), Jm)
    np.add.at(cnt, (i, j), 1)
    with np.errstate(invalid="ignore"):
        out = np.where(cnt > 0, acc / np.maximum(cnt, 1), np.nan)
    return out
