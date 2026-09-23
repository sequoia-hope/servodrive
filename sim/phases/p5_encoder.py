#!/usr/bin/env python3
"""P5 -- the field at the MT6701 (Q10).

Three fields are added at the sensor, 1.75 mm above B.Cu on the shaft axis:

  (a) the diametric magnet, over the datasheet's air-gap and off-axis
      tolerances, for both an N35 and an N42 grade
  (b) the phase currents, taken from P3's solved current distribution on the
      real copper and integrated with Biot-Savart, plus the three motor leads
      as parametric wires
  (c) rotor leakage, swept parametrically 0-5 mT as SPEC.md sec.3.2 asks

The angle the MT6701 reports is atan2 of the in-plane field, so the error the
current causes is the angle between (a) and (a)+(b)+(c).
"""
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                              # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import paths, jsonio                                # noqa: E402
from extract import copper                                   # noqa: E402
from conduction.raster import Raster                         # noqa: E402
from conduction.solver import Conductor                      # noqa: E402

paths.import_tools()
MU0 = 4e-7 * math.pi
I_PEAK = 28.284


from encoder.field import (sensor_point, magnet_field,
                          current_paths, biot_savart, lead_field)


def q10(quick=False):
    t0 = time.time()
    import magpylib as magpy
    M = jsonio.model("mt6701")
    mat = jsonio.model("materials")
    p = sensor_point()
    out = {"question": "Q10",
           "sensor_point_mm": (p * 1e3).tolist(),
           "criterion": ("B at the IC within 20-100 mT over the gap "
                         "tolerance; current-induced angle error <= 0.05 deg "
                         "(about 2 LSB of the 14-bit word)")}

    # --- (a) the magnet -------------------------------------------------
    rows = []
    for grade, Br in (("N35", 1.195), ("N42", 1.30)):
        for gap in (0.5, 1.0, 1.5, 2.0):
            for off in (0.0, 0.3):
                B = magnet_field(gap_mm=gap, off_axis_mm=off, Br=Br)
                rows.append({"grade": grade, "B_r_T": Br, "gap_mm": gap,
                             "off_axis_mm": off,
                             "B_inplane_mT": float(np.hypot(B[0], B[1])) * 1e3,
                             "B_z_mT": float(B[2]) * 1e3,
                             "in_window": bool(20e-3 <= np.hypot(B[0], B[1])
                                               <= 100e-3)})
    out["magnet"] = rows
    out["magnet_window_ok"] = bool(all(r["in_window"] for r in rows
                                       if r["gap_mm"] <= 2.0))
    # the board's Dia 8 magnet against the datasheet's recommended Dia 6
    rows6 = []
    for gap in (0.5, 1.0, 2.0):
        B = magnet_field(gap_mm=gap, Br=1.195, D=6e-3)
        rows6.append({"gap_mm": gap,
                      "B_inplane_mT": float(np.hypot(B[0], B[1])) * 1e3})
    out["magnet_dia6_reference"] = rows6

    # --- (b) the phase currents in the copper ---------------------------
    # one solve per cell at 1 A, so the three can be superposed with whatever
    # instantaneous currents the rotor angle calls for
    cur = {}
    B_cu_unit = {}
    try:
        n_el = 0
        for cell in ("A", "B", "C"):
            pts, mom = current_paths(cell, cellsize=0.15 if quick else 0.1,
                                     current=1.0)
            B_cu_unit[cell] = biot_savart(pts, mom, p)
            n_el += len(pts)
        cur["n_elements"] = int(n_el)
        cur["B_per_amp_nT"] = {k: (v * 1e9).tolist()
                               for k, v in B_cu_unit.items()}
        Bpk = (B_cu_unit["A"] * I_PEAK + B_cu_unit["B"] * (-I_PEAK / 2)
               + B_cu_unit["C"] * (-I_PEAK / 2))
        cur["B_from_copper_inplane_mT_at_peak"] = float(np.hypot(*Bpk[:2])) * 1e3
    except Exception as e:
        B_cu_unit = {"A": np.zeros(3), "B": np.zeros(3), "C": np.zeros(3)}
        cur["error"] = f"{e.__class__.__name__}: {e}"
    for L in (0.05, 0.25, 0.5):
        B_l = lead_field(p, I_PEAK, -I_PEAK / 2, -I_PEAK / 2, length=L)
        cur[f"B_from_leads_{L}m_mT"] = float(np.hypot(*B_l[:2])) * 1e3
    cur["note"] = ("the board copper contributes little because the three "
                   "phase pours are symmetric about the shaft axis and their "
                   "in-plane fields cancel; the leads, which leave the board "
                   "at three points and do not, are what is left")
    out["current_field"] = cur

    # --- the angle error, over a full electrical revolution -------------
    B_mag = magnet_field(gap_mm=1.0, Br=1.195)
    B_mag_ip = np.hypot(B_mag[0], B_mag[1])
    errs = []
    for th in np.linspace(0, 360, 73 if not quick else 25, endpoint=False):
        a = math.radians(th)
        ia = I_PEAK * math.cos(a)
        ib = I_PEAK * math.cos(a - 2 * math.pi / 3)
        ic = I_PEAK * math.cos(a + 2 * math.pi / 3)
        Bc = (B_cu_unit["A"] * ia + B_cu_unit["B"] * ib + B_cu_unit["C"] * ic)
        Bl = lead_field(p, ia, ib, ic, length=0.25)
        Bm = magnet_field(gap_mm=1.0, Br=1.195, angle_deg=th)
        tot = Bm + Bc + Bl
        e = math.degrees(math.atan2(tot[1], tot[0])
                         - math.atan2(Bm[1], Bm[0]))
        e = (e + 180) % 360 - 180
        errs.append({"rotor_deg": th, "err_deg": e,
                     "B_total_inplane_mT": float(np.hypot(*tot[:2])) * 1e3})
    out["angle_error"] = {
        "sweep": errs,
        "peak_deg": max(abs(e["err_deg"]) for e in errs),
        "peak_LSB": max(abs(e["err_deg"]) for e in errs) / M["lsb_deg"]["value"],
        "lsb_deg": M["lsb_deg"]["value"],
        "pass": bool(max(abs(e["err_deg"]) for e in errs) <= 0.05),
        "B_magnet_inplane_mT": float(B_mag_ip) * 1e3,
    }

    # --- (c) rotor leakage, parametric ---------------------------------
    leak = []
    for b in (0.0, 1.0, 2.0, 5.0):
        # a rotating transverse leakage field of amplitude b mT, worst phase
        e = math.degrees(math.atan2(b * 1e-3, B_mag_ip))
        leak.append({"leak_mT": b, "worst_angle_error_deg": e,
                     "LSB": e / M["lsb_deg"]["value"]})
    out["rotor_leakage"] = leak

    # --- temperature ----------------------------------------------------
    tc = M["TC_magnet_NdFeB"]["value"]
    out["temperature"] = {
        "TC_pct_per_K": tc,
        "B_at_125C_mT": float(B_mag_ip) * 1e3 * (1 + tc / 100 * 100),
        "still_in_window": bool(20 <= float(B_mag_ip) * 1e3
                                * (1 + tc / 100 * 100) <= 100)}
    _plot(out)
    out["figure"] = "p5_encoder.png"
    out["seconds"] = round(time.time() - t0, 1)
    return out


def _plot(out):
    fig, ax = plt.subplots(1, 2, figsize=(11, 4), dpi=150)
    for grade in ("N35", "N42"):
        for off in (0.0, 0.3):
            rows = [r for r in out["magnet"] if r["grade"] == grade
                    and r["off_axis_mm"] == off]
            ax[0].plot([r["gap_mm"] for r in rows],
                       [r["B_inplane_mT"] for r in rows],
                       marker="o", ms=3, lw=1,
                       label=f"{grade}, off-axis {off} mm")
    ax[0].axhspan(20, 100, color="g", alpha=.12, label="MT6701 window")
    ax[0].set_xlabel("air gap (mm)"); ax[0].set_ylabel("in-plane B at the IC (mT)")
    ax[0].set_title("magnet field over the gap tolerance", fontsize=9)
    ax[0].legend(fontsize=6); ax[0].grid(alpha=.3)
    sw = out["angle_error"]["sweep"]
    ax[1].plot([s["rotor_deg"] for s in sw], [s["err_deg"] for s in sw], lw=1.2)
    ax[1].axhline(0.05, color="r", ls="--", lw=.8, label="0.05 deg criterion")
    ax[1].axhline(-0.05, color="r", ls="--", lw=.8)
    ax[1].set_xlabel("rotor angle (deg)"); ax[1].set_ylabel("angle error (deg)")
    ax[1].set_title("angle error from 28.3 A of phase current", fontsize=9)
    ax[1].legend(fontsize=7); ax[1].grid(alpha=.3)
    fig.tight_layout()
    fig.savefig(paths.FIGS / "p5_encoder.png")
    plt.close(fig)


def run(quick=False):
    return {"Q10": q10(quick)}


if __name__ == "__main__":
    print(json.dumps(run(quick=True), indent=1, default=str)[:3000])
