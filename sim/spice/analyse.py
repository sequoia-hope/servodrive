#!/usr/bin/env python3
"""Measurements on the L3 waveforms.

Every number Q2, Q3, Q4 and Q5 ask for is extracted here, once, so that the
same definition is used in every sweep and the report can say what it means.
"""
import math

import numpy as np


def window(t, a, b):
    return (t >= a) & (t <= b)


def peak(t, v, a, b):
    m = window(t, a, b)
    if not m.any():
        return float("nan"), float("nan")
    i = int(np.argmax(v[m]))
    return float(v[m][i]), float(t[m][i])


def ring(t, v, t0, t1, settle=None):
    """Fit a damped sinusoid to v(t) over [t0, t1] about its settled value.

    Returns the ring frequency, the quality factor, the decay time constant,
    and how many cycles it takes to fall below 5 % of the first excursion --
    which is Q2's second criterion.
    """
    m = window(t, t0, t1)
    tt, vv = t[m], v[m]
    if len(tt) < 20:
        return {}
    base = float(settle if settle is not None else np.median(vv[-len(vv) // 5:]))
    x = vv - base
    # zero crossings of the ringing
    s = np.sign(x)
    idx = np.where(np.diff(s) != 0)[0]
    if len(idx) < 3:
        # it does not ring: there is a step and then nothing.  The amplitude
        # reported is what is left after the waveform first reaches its
        # settled value, not the step itself.
        after = (float(np.max(np.abs(x[idx[0]:]))) if len(idx) else 0.0)
        return {"f_MHz": None, "Q": None, "cycles_to_5pct": 0,
                "rings": False, "amplitude_V": after}
    tz = tt[idx] + (tt[idx + 1] - tt[idx]) * (-x[idx]) / (x[idx + 1] - x[idx])
    half = np.diff(tz)
    f = 1.0 / (2 * np.median(half))
    # envelope from successive extrema
    ext_t, ext_v = [], []
    for a, b in zip(idx[:-1], idx[1:]):
        seg = np.abs(x[a:b + 1])
        if len(seg) == 0:
            continue
        k = int(np.argmax(seg))
        ext_t.append(tt[a + k])
        ext_v.append(seg[k])
    ext_t = np.array(ext_t)
    ext_v = np.array(ext_v)
    out = {"f_MHz": f / 1e6, "rings": True,
           "amplitude_V": float(ext_v[0]) if len(ext_v) else None}
    if len(ext_v) >= 3 and ext_v[0] > 0:
        good = ext_v > ext_v[0] * 1e-3
        if good.sum() >= 3:
            p = np.polyfit(ext_t[good], np.log(ext_v[good]), 1)
            alpha = -p[0]
            out["tau_ns"] = 1e9 / alpha if alpha > 0 else None
            out["Q"] = math.pi * f / alpha if alpha > 0 else None
            below = np.where(ext_v <= 0.05 * ext_v[0])[0]
            out["cycles_to_5pct"] = (float(below[0]) / 2.0 if len(below)
                                     else float("inf"))
    return out


def settling(t, v, t_edge, final=None, tol=None, t_max=5e-6):
    """When does v settle to within `tol` of its final value after t_edge?"""
    m = (t >= t_edge) & (t <= t_edge + t_max)
    tt, vv = t[m], v[m]
    if len(tt) < 5:
        return None
    fin = float(final if final is not None else np.median(vv[-len(vv) // 5:]))
    tol = tol if tol is not None else 3.3 / 4096.0        # 1 LSB
    bad = np.where(np.abs(vv - fin) > tol)[0]
    if len(bad) == 0:
        return 0.0
    return float(tt[bad[-1]] - t_edge)


def excursion(t, v, t0, t1, ref=0.0):
    m = window(t, t0, t1)
    if not m.any():
        return float("nan")
    return float(np.max(np.abs(v[m] - ref)))


def rms(t, v):
    if len(t) < 3:
        return float("nan")
    dt = np.diff(t)
    mid = (v[:-1] ** 2 + v[1:] ** 2) / 2
    return float(math.sqrt(np.sum(mid * dt) / np.sum(dt)))


def dissipation(t, v, i, t0, t1):
    """Average power in an element from its voltage and current."""
    m = window(t, t0, t1)
    if m.sum() < 3:
        return float("nan")
    tt = t[m]
    p = v[m] * i[m]
    return float(np.trapezoid(p, tt) / (tt[-1] - tt[0]))


def di_dt(t, i, t0, t1):
    m = window(t, t0, t1)
    if m.sum() < 3:
        return float("nan")
    d = np.gradient(i[m], t[m])
    return float(d[np.argmax(np.abs(d))])
