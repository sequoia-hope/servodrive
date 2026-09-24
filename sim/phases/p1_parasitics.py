#!/usr/bin/env python3
"""P1 -- parasitics of phase cell A, and of the board-to-board link.

Q1  commutation-loop L(f), R(f), and the H1 verdict
Q4  gate-loop inductance, both sides
Q5  shunt and sense-tap partial and mutual inductance, tap capacitance (H3, H4)
Q13 the 2 x 5 header and the six standoffs

Everything is solved on the copper as routed (sim/work/geometry.json).  What is
*not* in these numbers, and is added by the circuit model in P2, is stated with
each result: the FET packages, the capacitors' own ESL and ESR, the shunts'
own ESL.
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
from lib import paths, jsonio, board                         # noqa: E402
from extract import copper                                   # noqa: E402
from fasthenry import runner, mesher, cell as fhcell, gridjs  # noqa: E402
from fastcap import runner as fcrun                          # noqa: E402

paths.import_tools()

# Every FastHenry solve here is a single-threaded subprocess, independent of
# the others, and the finest meshes take hours on their own: they are
# submitted to a pool and collected afterwards, so the phase takes about as
# long as its slowest solve rather than the sum of them.  Same decks, same
# answers (runner.run caches a byte-identical deck).  pyplot is not
# thread-safe, so the figures are drawn under a lock.
import os
import threading
from concurrent.futures import ThreadPoolExecutor
FH = ThreadPoolExecutor(max_workers=int(os.environ.get("SIM_FH_JOBS", "16")))
# The two slowest solves -- the finest convergence mesh and the three-filament
# skin check -- get their own timeout: hours when run for a result, seconds
# when a run only wants the cached rest (the solve is then left to finish on
# its own and a later run picks it up from the cache).
SLOW = int(os.environ.get("SIM_FH_SLOW_TIMEOUT", str(6 * 3600)))
PLOT = threading.Lock()


def _LR(f, Z):
    w = 2 * np.pi * f[:, None, None]
    return np.imag(Z) / w, np.real(Z)


def _parallel_L(L):
    """The loop inductance the cell sees when every capacitor is a short:
    1 / (1' L^-1 1), which is the right way to parallel coupled inductors."""
    L = (L + L.T) / 2
    one = np.ones(len(L))
    return float(1.0 / (one @ np.linalg.inv(L) @ one))


# =================================================================== Q1 =====
def commutation(quick=False, cellname="A"):
    t0 = time.time()
    import geometry as G
    base = G.commutation_loop()

    # --- mesh convergence, one frequency -------------------------------
    conv = []
    # the series has to go fine enough to show where it is heading: a 1.2 mm
    # filament grid cannot let the return current concentrate under a leg that
    # runs 0.4 mm above it, and the answer falls with every refinement until
    # it can.  P7 cross-checks the end of this curve against a full-wave run.
    meshes = ([(1.8, 0.6), (1.4, 0.45)] if quick else
              [(1.8, 0.6), (1.4, 0.45), (1.1, 0.32), (0.9, 0.261),
               (0.75, 0.218), (0.62, 0.18)])
    ports = None
    conv_jobs = []
    for target, min_gap in meshes:
        m, ports, win, _ = fhcell.commutation_model(
            cellname, target=target, nhinc=1, min_gap=min_gap)
        conv_jobs.append((target, min_gap, m, FH.submit(
            runner.run, m.model, 1e6, 1e6,
            timeout=SLOW if target == meshes[-1][0] else 7200,
            workdir=paths.WORK / f"fh/comm_{cellname}_conv_{target}")))

    # --- the production model's sweep, the skin check and the current map
    # are submitted now too, and everything is collected below
    target, min_gap, nh = (1.4, 0.45, 1) if quick else (1.2, 0.36, 1)
    m, ports, win, _ = fhcell.commutation_model(
        cellname, target=target, nhinc=nh, min_gap=min_gap)
    freqs = [1e3, 1e5, 1e6, 1e7, 1e8, 3e8]
    sweep_jobs = [(fq, FH.submit(runner.run, m.model, fq, fq, ndec=1, timeout=1200,
                                 workdir=paths.WORK / f"fh/comm_{cellname}_f{fq:.0e}"))
                  for fq in freqs]
    skin_jobs = {}
    if not quick:
        for tag, nh3 in (("nhinc_1", 1), ("nhinc_3", 3)):
            mm, _, _, _ = fhcell.commutation_model(
                cellname, target=1.6, nhinc=nh3, min_gap=0.5)
            skin_jobs[tag] = (mm, FH.submit(
                runner.run, mm.model, 2e7, 2e7,
                workdir=paths.WORK / f"fh/comm_{cellname}_skin_{tag}",
                timeout=SLOW if nh3 > 1 else 1800))
    mj, _, winj, _ = fhcell.commutation_model(
        cellname, target=target, nhinc=nh, min_gap=min_gap)
    wdj = paths.WORK / f"fh/comm_{cellname}_J"
    j_job = FH.submit(runner.run, mj.model, 1e7, 1e7, workdir=wdj,
                      extra_args=("-d", "GRIDS", "-x",
                                  f"P{fhcell.CELL[cellname]['hf'][0]}"))

    for target_c, min_gap_c, mc, fut in conv_jobs:
        try:
            f, Z, order, _ = fut.result()
        except Exception as ex:
            # a mesh that did not finish is a row that says so, not a crash
            conv.append({"target_mm": target_c, "min_gap_mm": min_gap_c,
                         "segments": mc.n_seg, "nodes": mc.n_node,
                         "error": f"{ex.__class__.__name__}: {str(ex)[:160]}"})
            continue
        L, R = _LR(f, Z)
        conv.append({"target_mm": target_c, "min_gap_mm": min_gap_c,
                     "segments": mc.n_seg, "nodes": mc.n_node,
                     "ports": order, "f_Hz": float(f[0]),
                     "L_nH": (L[0] * 1e9).tolist(),
                     "R_mOhm": (R[0] * 1e3).tolist(),
                     "L_eff_nH": _parallel_L(L[0]) * 1e9})

    # --- the production model: the frequency sweep ----------------------
    # One filament through the thickness (nhinc = 1).  Below about 1 MHz the
    # skin depth exceeds 35 um of copper and that is exact; above it, R(f) is
    # underestimated because the current is not pushed to the surface within a
    # segment.  A separate nhinc = 3 run at one high frequency prices that
    # rather than leaving it as a caveat.
    # One FastHenry invocation per frequency, each with its own timeout.
    # A single `.freq` sweep is cheaper in principle -- the mesh is built once
    # -- but then one frequency that will not converge takes the whole sweep
    # with it, and on this model the high ones sometimes do.
    fl, Zl, order, failed = [], [], None, []
    for fq, fut in sweep_jobs:
        try:
            f1, Z1, o1, _ = fut.result()
            fl.append(float(f1[0]))
            Zl.append(Z1[0])
            order = order or o1
        except Exception as ex:
            failed.append({"f_Hz": fq,
                           "why": f"{ex.__class__.__name__}: {str(ex)[:120]}"})
    if not fl:
        raise RuntimeError("no frequency point of the commutation sweep "
                           "completed")
    f = np.array(fl)
    Z = np.array(Zl)
    L, R = _LR(f, Z)
    Leff = np.array([_parallel_L(L[k]) for k in range(len(f))])
    out = ""

    skin = {"note": ("one filament through the 35 um copper resolves the skin "
                     "effect exactly below about 1 MHz and not at all above "
                     "it; this is the same model with three filaments, at one "
                     "frequency, to price that")}
    if not quick:
        try:
            got = {}
            for tag, (mm, fut) in skin_jobs.items():
                f3, Z3, _, _ = fut.result()
                L3, R3 = _LR(f3, Z3)
                got[tag] = {"segments": mm.n_seg,
                            "L_eff_nH": _parallel_L(L3[0]) * 1e9,
                            "R_diag_mOhm": (np.diag(R3[0]) * 1e3).tolist()}
            skin.update(got)
            r1 = float(np.mean(got["nhinc_1"]["R_diag_mOhm"]))
            r3 = float(np.mean(got["nhinc_3"]["R_diag_mOhm"]))
            skin["R_ratio_3_over_1_at_20MHz"] = (r3 / r1) if r1 else None
            skin["L_ratio_3_over_1"] = (got["nhinc_3"]["L_eff_nH"]
                                        / got["nhinc_1"]["L_eff_nH"])
        except Exception as ex:
            skin["error"] = f"{ex.__class__.__name__}: {ex}"

    # --- where the return current goes, for the figure and for H1 -------
    jmaps = None
    try:
        j_job.result()
        with PLOT:
            jmaps = _plot_currents(wdj, winj, cellname)
    except Exception as e:
        jmaps = {"error": f"{e.__class__.__name__}: {e}"}

    i100k = int(np.argmin(abs(f - 1e5)))
    i10M = int(np.argmin(abs(f - 1e7)))
    L_pcb = float(Leff[i10M])

    # what the circuit sees once the parts are added
    fet = jsonio.model("bsc030n08ns5")
    c1206 = jsonio.model("cap_2u2_100v_1206")
    c0603 = jsonio.model("cap_100n_100v_0603")
    pkg = 2 * fet["L_source_package"]["typ"]
    esl_lo = 1.0 / (2 / c0603["ESL"]["min"] + 2 / c1206["ESL"]["min"])
    esl_hi = 1.0 / (2 / c0603["ESL"]["max"] + 2 / c1206["ESL"]["max"])
    di_dt = base["di_dt"]

    res = {
        "question": "Q1",
        "estimate_geometry_py": {
            "L_pcb_nH": base["l_pcb"] * 1e9,
            "L_total_nH": base["l_total"] * 1e9,
            "overshoot_V": base["overshoot"],
            "v_peak_V": base["v_peak"],
            "di_dt_A_per_ns": di_dt / 1e9,
        },
        "solved": {
            "method": "FastHenry2 3.0.1, PEEC on the routed copper",
            "cell": cellname,
            "window_mm": list(win),
            "ports": order,
            "segments": m.n_seg, "nodes": m.n_node,
            "nhinc": nh, "grid_target_mm": target,
            "f_Hz": f.tolist(),
            "frequencies_that_did_not_converge": failed,
            "L_matrix_nH": (L * 1e9).tolist(),
            "R_matrix_mOhm": (R * 1e3).tolist(),
            "L_eff_nH": (Leff * 1e9).tolist(),
            "L_pcb_nH_at_10MHz": L_pcb * 1e9,
            "L_pcb_nH_at_100kHz": float(Leff[i100k]) * 1e9,
            "R_loop_mOhm_at_10MHz": float(np.real(np.linalg.inv(
                np.linalg.inv(R[i10M]))).sum()) if False else float(
                    1.0 / (np.ones(len(R[i10M])) @ np.linalg.inv(R[i10M])
                           @ np.ones(len(R[i10M])))) * 1e3,
        },
        "total_with_parts": {
            "L_package_nH": pkg * 1e9,
            "L_cap_esl_nH": [esl_hi * 1e9, esl_lo * 1e9],
            "L_total_nH": [(L_pcb + pkg + esl_hi) * 1e9,
                           (L_pcb + pkg + esl_lo) * 1e9],
            "overshoot_V": [(L_pcb + pkg + esl_hi) * di_dt,
                            (L_pcb + pkg + esl_lo) * di_dt],
            "v_peak_V": [board.P()["v_bus"] + (L_pcb + pkg + esl_hi) * di_dt,
                         board.P()["v_bus"] + (L_pcb + pkg + esl_lo) * di_dt],
        },
        "H1": {
            "claim": ("the commutation loop is bigger than 0.39 nH, expected "
                      "0.8-1.5 nH for the PCB part and about 2.5-3 nH total"),
            "L_pcb_solved_nH": L_pcb * 1e9,
            "ratio_to_estimate": L_pcb / base["l_pcb"],
            "verdict": None,
        },
        "convergence": conv,
        "skin_effect_check": skin,
        "current_maps": jmaps,
        "seconds": round(time.time() - t0, 1),
    }
    r = res["H1"]["ratio_to_estimate"]
    res["H1"]["verdict"] = (
        f"CONFIRMED, and by more than the hypothesis expected: the PCB part is "
        f"{L_pcb * 1e9:.2f} nH, {r:.1f} times geometry.commutation_loop()'s "
        f"0.39 nH, because VBUS reaches the high-side drain from In2 (0.55 mm "
        f"below F.Cu) through the drain's via field, not from a plane 0.1 mm "
        f"away."
        if r > 1.5 else
        f"NOT CONFIRMED: the PCB part is {L_pcb * 1e9:.2f} nH, "
        f"{r:.2f} times the estimate.")
    with PLOT:
        _plot_LR(f, Leff, L, R, order, cellname)
    return res


def _plot_LR(f, Leff, L, R, order, cellname):
    fig, ax = plt.subplots(1, 2, figsize=(11, 4), dpi=150)
    for k, nm in enumerate(order):
        ax[0].semilogx(f, L[:, k, k] * 1e9, lw=1, label=nm)
        ax[1].loglog(f, R[:, k, k] * 1e3, lw=1, label=nm)
    ax[0].semilogx(f, Leff * 1e9, "k", lw=2, label="all four in parallel")
    ax[0].axhline(0.387, color="r", ls="--", lw=1,
                  label="geometry.commutation_loop() 0.39 nH")
    ax[0].set_xlabel("Hz"); ax[0].set_ylabel("L (nH)")
    ax[0].set_title(f"cell {cellname} commutation loop, PCB only")
    ax[0].legend(fontsize=6); ax[0].grid(alpha=.3)
    ax[1].set_xlabel("Hz"); ax[1].set_ylabel("R (m$\\Omega$)")
    ax[1].set_title("loop resistance"); ax[1].legend(fontsize=6)
    ax[1].grid(alpha=.3, which="both")
    fig.tight_layout()
    fig.savefig(paths.FIGS / f"p1_commutation_{cellname}.png")
    plt.close(fig)


def _plot_currents(wd, win, cellname):
    stack = copper.stackup()
    out = {}
    p = wd / "Jmag.mat"
    if not p.exists():
        return {"error": f"no {p}"}
    d = gridjs.read(p)
    per = gridjs.by_layer(d, stack)
    order = [l for l in copper.CU_LAYERS if l in per]
    if not order:
        return {"error": "no filaments landed on a copper layer"}
    fig, axes = plt.subplots(1, len(order), figsize=(3.4 * len(order), 3.6),
                             dpi=150, squeeze=False)
    for ax, layer in zip(axes[0], order):
        gd = gridjs.grid(per[layer], win, cell=0.25)
        im = ax.imshow(np.log10(np.maximum(gd.T, 1e-3)), origin="lower",
                       extent=win, cmap="magma")
        ax.set_title(layer, fontsize=8)
        ax.tick_params(labelsize=5)
        plt.colorbar(im, ax=ax, fraction=0.046, label="log10 |J| A/m$^2$")
        out[layer] = {"n_filaments": int(len(per[layer]["x"])),
                      "J_max_A_per_mm2": float(np.nanmax(
                          np.hypot(per[layer]["Jx"], per[layer]["Jy"])) / 1e6)}
    fig.suptitle(f"cell {cellname}: current density at 10 MHz, 1 A into the "
                 f"high-side 100 n", fontsize=9)
    fig.tight_layout()
    fig.savefig(paths.FIGS / f"p1_current_{cellname}.png")
    plt.close(fig)
    out["figure"] = f"p1_current_{cellname}.png"
    return out


def cells_bc(quick=False, target=1.4, min_gap=0.45):
    """Cells B and C, at one frequency, against cell A.

    SPEC.md sec.5.5: simulate A fully, and confirm B and C where the
    surrounding copper differs -- the signal-link wedge adjoins cell A at 0
    degrees and the power-link wedge, where VBUS and GND enter the board,
    adjoins cell C at 204.  If the three come out the same, the rotation is
    what it looks like; if they do not, the neighbourhood matters.
    """
    out = {}
    for cellname in ("A", "B", "C"):
        try:
            m, ports, win, _ = fhcell.commutation_model(
                cellname, target=target, nhinc=1, min_gap=min_gap)
            f, Z, order, _ = runner.run(
                m.model, 1e6, 1e6,
                workdir=paths.WORK / f"fh/comm_{cellname}_compare")
            L, R = _LR(f, Z)
            out[cellname] = {"segments": m.n_seg, "ports": order,
                             "L_nH": (np.diag(L[0]) * 1e9).tolist(),
                             "R_mOhm": (np.diag(R[0]) * 1e3).tolist(),
                             "L_eff_nH": _parallel_L(L[0]) * 1e9}
        except Exception as ex:
            out[cellname] = {"error": f"{ex.__class__.__name__}: {ex}"}
    good = {k: v["L_eff_nH"] for k, v in out.items() if "L_eff_nH" in v}
    if len(good) > 1:
        lo, hi = min(good.values()), max(good.values())
        out["spread_pct"] = (hi - lo) / ((hi + lo) / 2) * 100
        out["verdict"] = (
            f"the three cells agree to {out['spread_pct']:.1f} %: the "
            f"rotation is what it looks like, and cell C's neighbourhood -- "
            f"the power-link wedge where VBUS and GND enter the board -- does "
            f"not change its commutation loop"
            if out["spread_pct"] < 10 else
            f"the three cells differ by {out['spread_pct']:.1f} %, so the "
            f"neighbourhood does matter and cell A is not representative")
    out["note"] = ("one frequency, the convergence mesh; the point is the "
                   "comparison, not the absolute value")
    return out


# =================================================================== Q4 =====
def gate_loops(quick=False, cellname="A"):
    out = {}
    fet = jsonio.model("bsc030n08ns5")
    for side in ("high", "low"):
        m, win = fhcell.gate_model(cellname, side, target=1.0, nhinc=1)
        f, Z, order, _ = runner.run(
            m.model, 1e6, 1e8, ndec=1, timeout=3600,
            workdir=paths.WORK / f"fh/gate_{cellname}_{side}")
        L, R = _LR(f, Z)
        i = int(np.argmin(abs(f - 1e7)))
        out[side] = {
            "window_mm": list(win), "segments": m.n_seg,
            "f_Hz": f.tolist(),
            "L_nH": (L[:, 0, 0] * 1e9).tolist(),
            "R_mOhm": (R[:, 0, 0] * 1e3).tolist(),
            "L_nH_at_10MHz": float(L[i, 0, 0]) * 1e9,
            "R_mOhm_at_10MHz": float(R[i, 0, 0]) * 1e3,
        }
    # the gate loop's ring with C_iss, which is what a driver has to live with
    Ciss = fet["C_iss_40V"]["max"]
    for side in ("high", "low"):
        Lg = out[side]["L_nH_at_10MHz"] * 1e-9
        Rg_total = 2.2 + fet["R_G_internal"]["typ"]
        f0 = 1 / (2 * np.pi * math.sqrt(Lg * Ciss))
        Q = math.sqrt(Lg / Ciss) / Rg_total
        out[side].update({"gate_ring_MHz": f0 / 1e6, "gate_Q": Q,
                          "R_g_total_used_Ohm": Rg_total,
                          "C_iss_used_pF": Ciss * 1e12})
    out["question"] = "Q4"
    out["note"] = ("The gate resistor and the device's gate-source are shorted "
                   "in the electromagnetic model; the 2.2 Ohm and C_iss are put "
                   "back here and in P2.  L is the loop from the driver's "
                   "output pin to its own reference pin.")
    return out


# =================================================================== Q5 =====
def sense_loops(quick=False, cellname="A"):
    m, win, ports = fhcell.sense_model(cellname, target=0.3, nhinc=1)
    f, Z, order, _ = runner.run(
        m.model, 1e4, 1e8, ndec=1, timeout=3600,
        workdir=paths.WORK / f"fh/sense_{cellname}")
    L, R = _LR(f, Z)
    i1M = int(np.argmin(abs(f - 1e6)))
    i10M = int(np.argmin(abs(f - 1e7)))
    shunt_i = order.index("pshunt") if "pshunt" in order else 0
    tap_i = order.index("ptap") if "ptap" in order else 1
    v_signal_note = (
        "Z21 is the tap-to-tap voltage per amp through the copper alone: the "
        "shunt elements are shorted in the electromagnetic model and their "
        "0.8 mOhm and their own ESL are lumped back in P2.  The tap port is "
        "taken at the two 10 Ohm resistors' pour-side pads, which is where "
        "the copper is actually probed; the tracks onward to the amplifier "
        "carry no current and add capacitance rather than inductance, which "
        "is the FastCap question in Q5/H4.")

    G_ = copper.load()
    sh = jsonio.model(board.P()["shunt"])
    di_dt = 28.28 / 32.5e-9        # the design point's turn-off di/dt

    L_tt = float(L[i10M, tap_i, shunt_i])       # transfer inductance
    R_tt = float(R[i10M, tap_i, shunt_i])
    L_tap_self = float(L[i10M, tap_i, tap_i])
    L_shunt_self = float(L[i10M, shunt_i, shunt_i])

    v_signal = 28.28 * board.P()["shunt_r"]     # full-scale shunt voltage
    v_ldidt_pcb = L_tt * di_dt
    esl = sh["L_esl"]
    v_ldidt_part = [esl["min"] * di_dt, esl["typ"] * di_dt, esl["max"] * di_dt]

    out = {
        "question": "Q5",
        "window_mm": list(win), "segments": m.n_seg, "ports": order,
        "f_Hz": f.tolist(),
        "L_matrix_nH": (L * 1e9).tolist(),
        "R_matrix_mOhm": (R * 1e3).tolist(),
        "L_shunt_path_nH_at_10MHz": L_shunt_self * 1e9,
        "L_tap_loop_nH_at_10MHz": L_tap_self * 1e9,
        "L_transfer_tap_to_shunt_nH_at_10MHz": L_tt * 1e9,
        "R_transfer_uOhm_at_1MHz": float(R[i1M, tap_i, shunt_i]) * 1e6,
        "H3": {
            "claim": ("the shunt's ESL dominates the sense signal during "
                      "edges: 0.2-0.5 nH at 0.87 A/ns is 170-430 mV against "
                      "22.6 mV of signal"),
            "signal_full_scale_mV": v_signal * 1e3,
            "di_dt_A_per_ns": di_dt / 1e9,
            "V_Ldidt_from_pcb_mV": v_ldidt_pcb * 1e3,
            "V_Ldidt_from_part_ESL_mV": [v * 1e3 for v in v_ldidt_part],
            "ratio_to_signal_pcb": v_ldidt_pcb / v_signal,
            "ratio_to_signal_part": [v / v_signal for v in v_ldidt_part],
        },
        "definition": v_signal_note,
        "note": ("Z21 of this two-port IS the sense voltage per amp of phase "
                 "current, including the L.di/dt the tap loop picks up: the "
                 "port-2 (tap) voltage with 1 A into port 1 (the shunt path). "
                 "The shunt element's own ESL is a part property and is added "
                 f"from models/{board.P()['shunt']}.json; the PCB's share is "
                 "solved."),
    }
    out["H3"]["verdict"] = (
        "CONFIRMED" if max(out["H3"]["ratio_to_signal_part"]) > 3 else
        "NOT CONFIRMED")
    return out


def tap_asymmetry(cellname="A", max_panels=6000):
    """H4: the two sense taps do not take the same path to the amplifier, so a
    common-mode step on the switch node becomes a differential one.

    What decides that is how much capacitance each tap has to the node that is
    moving.  Solved with FastCap on the tap traces themselves, with the
    switch-node and phase pours that surround them and the In4 ground plane
    under them -- and only in the window those traces occupy, because a
    boundary-element solver's cost is the square of the panel count and the
    ground plane is most of the area.
    """
    from shapely.geometry import box as _box
    c = fhcell.CELL[cellname]
    refs = [*c["taps"], c["filt"], c["amp"]]
    pts = [(p["x"], p["y"]) for ref in refs for p in copper.pads_of(ref)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    w = (min(xs) - 1.5, min(ys) - 1.5, max(xs) + 1.5, max(ys) + 1.5)
    z = copper.stackup()
    m = fcrun.Model(f"cell {cellname} sense taps", epsilon_r=4.5)
    counts = {}
    # the taps themselves finely; everything around them coarsely
    plan = [(f"SNSP_{cellname}", "B.Cu", "snsp", 0.35),
            (f"SNSN_{cellname}", "B.Cu", "snsn", 0.35),
            (c["sw"], "B.Cu", "sw", 1.2),
            (c["phase"], "B.Cu", "phase", 1.2),
            ("GND", "In4.Cu", "gnd", 1.5)]
    for net, layer, name, edge in plan:
        u = copper.net_union(net, layer, w, simplify=0.05)
        if u is None or u.area < 1e-3:
            continue
        m.geometry_raster(name, u, z[layer]["z"], cell=edge)
        counts[name] = len(m.conductors.get(name, []))
    total = sum(len(v) for v in m.conductors.values())
    if total > max_panels:
        return {"question": "Q5/H4", "panels": total,
                "error": (f"{total} panels is past the {max_panels} this "
                          f"solver is worth running at; coarsen the plan")}
    C, labels, _ = fcrun.run(m, workdir=paths.WORK / f"fc/taps_{cellname}",
                             timeout=1800)
    names = list(m.conductors)
    idx = {n: i for i, n in enumerate(names)}
    out = {"question": "Q5/H4", "window_mm": list(w), "panels": total,
           "panels_per_conductor": counts,
           "conductors": names, "C_matrix_pF": (C * 1e12).tolist()}
    for other in ("sw", "phase", "gnd"):
        if "snsp" in idx and "snsn" in idx and other in idx:
            cp = fcrun.mutual(C, idx["snsp"], idx[other]) * 1e12
            cn = fcrun.mutual(C, idx["snsn"], idx[other]) * 1e12
            out[f"C_snsp_{other}_pF"] = cp
            out[f"C_snsn_{other}_pF"] = cn
            out[f"asymmetry_{other}_pF"] = cp - cn
    if "asymmetry_sw_pF" in out:
        dv = board.P()["v_bus"]
        # a common-mode step of dv on the switch node drives the difference
        # of the two taps' capacitances into the 10 ohm + 10 ohm tap network
        dq = abs(out["asymmetry_sw_pF"]) * 1e-12 * dv
        out["differential_charge_from_bus_step_pC"] = dq * 1e12
        out["verdict"] = (
            f"the two taps differ by {out['asymmetry_sw_pF']:+.3f} pF to the "
            f"switch node; a {dv:.0f} V common-mode step therefore injects "
            f"{dq * 1e12:.1f} pC of differential charge into the tap network, "
            f"which the 1 nF across it turns into "
            f"{dq / 1e-9 * 1e3:.3f} mV -- against "
            f"{28.28 * board.P()['shunt_r'] * 1e3:.1f} mV of full-scale "
            f"signal")
    return out


# ================================================================== Q13 =====
def interconnect(quick=False):
    """The board-to-board power link: two 2 x 5 headers (J7, J8) and the six
    M2.5 standoffs, as vertical conductors 11 mm long between the two boards."""
    g = copper.load()
    pads = [p for p in g["through_pads"] if p["ref"].startswith(("J7", "J8"))]
    if not pads:
        return {"question": "Q13", "error": "no J7/J8 plated pads found"}
    GAP = 11.0
    m = runner.Model("board A <-> board B power link")
    hdr = jsonio.model("header_2x5")
    side = hdr["pin_square"]["value"] * 1e3          # mm
    tops_v, tops_g = [], []
    bots_v, bots_g = [], []
    for k, p in enumerate(pads):
        a = m.node(f"h{k}a", p["x"], p["y"], 0.0)
        b = m.node(f"h{k}b", p["x"], p["y"], -GAP)
        m.segment(a, b, w=side, h=side, nwinc=3, nhinc=3)
        (tops_v if p["net"] == "VBUS" else tops_g).append(a)
        (bots_v if p["net"] == "VBUS" else bots_g).append(b)

    # the six M2.5 brass standoffs, in parallel with the ground pins
    bosses = [p for p in g["through_pads"]
              if p["ref"].startswith("H") and p["net"] == "GND"
              and p["drill"] > 2.0]
    SIGMA_BRASS = 1.5e7
    for k, p in enumerate(bosses):
        a = m.node(f"s{k}a", p["x"], p["y"], 0.0)
        b = m.node(f"s{k}b", p["x"], p["y"], -GAP)
        m.segment(a, b, w=4.0, h=4.0, nwinc=3, nhinc=3, sigma=SIGMA_BRASS)
        tops_g.append(a)
        bots_g.append(b)

    m.equiv(*tops_v); m.equiv(*tops_g)
    m.equiv(*bots_v); m.equiv(*bots_g)
    m.port("Plink", tops_v[0], tops_g[0])
    # board B shorts VBUS to GND for the loop measurement; the real bulk
    # impedance is put back in P4
    m.equiv(bots_v[0], bots_g[0])
    f, Z, order, _ = runner.run(m.model if hasattr(m, "model") else m,
                                1e4, 1e8, ndec=1, timeout=1800,
                                workdir=paths.WORK / "fh/interconnect")
    L, R = _LR(f, Z)
    i = int(np.argmin(abs(f - 1e6)))

    # the same again with the standoffs removed, to price them
    m2 = runner.Model("power link, header only")
    tv, tg, bv, bg = [], [], [], []
    for k, p in enumerate(pads):
        a = m2.node(f"h{k}a", p["x"], p["y"], 0.0)
        b = m2.node(f"h{k}b", p["x"], p["y"], -GAP)
        m2.segment(a, b, w=side, h=side, nwinc=3, nhinc=3)
        (tv if p["net"] == "VBUS" else tg).append(a)
        (bv if p["net"] == "VBUS" else bg).append(b)
    m2.equiv(*tv); m2.equiv(*tg); m2.equiv(*bv); m2.equiv(*bg)
    m2.port("Plink", tv[0], tg[0]); m2.equiv(bv[0], bg[0])
    f2, Z2, _, _ = runner.run(m2, 1e4, 1e8, ndec=1, timeout=1800,
                              workdir=paths.WORK / "fh/interconnect_nostand")
    L2, R2 = _LR(f2, Z2)

    return {
        "question": "Q13 (link parasitics)",
        "n_pins": len(pads),
        "n_vbus_pins": sum(1 for p in pads if p["net"] == "VBUS"),
        "n_gnd_pins": sum(1 for p in pads if p["net"] == "GND"),
        "n_standoffs": len(bosses),
        "gap_mm": GAP,
        "f_Hz": f.tolist(),
        "L_nH": (L[:, 0, 0] * 1e9).tolist(),
        "R_mOhm": (R[:, 0, 0] * 1e3).tolist(),
        "L_nH_at_1MHz": float(L[i, 0, 0]) * 1e9,
        "R_mOhm_at_1MHz": float(R[i, 0, 0]) * 1e3,
        "L_nH_at_1MHz_header_only": float(L2[i, 0, 0]) * 1e9,
        "standoff_share": 1 - float(L[i, 0, 0]) / float(L2[i, 0, 0]),
        "note": ("The mated pins are modelled as 0.64 mm square bars 11 mm "
                 "long -- the board-to-board gap, which is longer than the "
                 "6 mm mated length SPEC.md sec.3.4 assumes, because the pins "
                 "must also span the gap.  Brass standoffs at 1.5e7 S/m, "
                 "4 mm across, no contact resistance: an optimistic bound on "
                 "what they take."),
    }


# ============================================================ Q13, board S ==
def bulk_path(quick=False, cellname="A"):
    """Board S has no board-to-board link: its bulk is two polymer cans in the
    middle of the board, and what stands between them and a phase cell is the
    VBUS plane on In2 over the GND planes.  This is that path's inductance --
    the number board A's header supplied to the cell model (Llink).

    The port is at the cell's DC link, its four capacitors' VBUS pads tied
    together and their GND pads tied together; at the far end each can's two
    leads are shorted where the can stands (F.Cu), so the can's own ESL and
    ESR are left out here and put back in P2/P4 from the part's model."""
    B = board.P()
    refs = B["bulk"]["refs"]
    c = fhcell.CELL[cellname]
    pts = [(p["x"], p["y"]) for r in (*refs, *c["hf"], *c["bulk"])
           for p in copper.pads_of(r)]
    xs = [q[0] for q in pts]
    ys = [q[1] for q in pts]
    margin = 3.0
    win = (max(min(xs) - margin, -32.5), max(min(ys) - margin, -32.5),
           min(max(xs) + margin, 32.5), min(max(ys) + margin, 32.5))
    bar = copper.barrels(("VBUS", "GND"), win)
    rows, pend = [], []
    for target in ((2.0,) if quick else (2.0, 1.5, 1.2)):
        m = mesher.Mesh(f"board S bulk cans to cell {cellname}", win,
                        target=target, required_x=xs, required_y=ys,
                        nhinc=1, min_gap=0.45)
        m.add_conductor("vbF", "VBUS", "F.Cu")
        m.add_conductor("vb2", "VBUS", "In2.Cu")
        for tag, layer in (("gF", "F.Cu"), ("g1", "In1.Cu"),
                           ("g3", "In3.Cu"), ("g4", "In4.Cu")):
            m.add_conductor(tag, "GND", layer)
        vt = {"F.Cu": "vbF", "In2.Cu": "vb2"}
        gt = {"F.Cu": "gF", "In1.Cu": "g1", "In3.Cu": "g3", "In4.Cu": "g4"}
        lead = {}
        for b in bar:
            nm = m.add_barrel(b, vt if b["net"] == "VBUS" else gt)
            if b["ref"].split(".")[0] in refs:
                lead[b["ref"]] = f"NB{nm}_{b['i0']}"
        for r in refs:
            a, k = lead.get(f"{r}.1"), lead.get(f"{r}.2")
            if not (a and k):
                raise RuntimeError(f"bulk path: no barrels for {r}'s leads")
            m.model.equiv(a, k)                   # the can, as a short
        vn, gn = [], []
        for r in (*c["hf"], *c["bulk"]):
            vn += m.pad_equipotential("vbF", r, 1)
            gn += m.pad_equipotential("gF", r, 2)
        if not (vn and gn):
            raise RuntimeError("bulk path: no DC-link pad nodes in cell "
                               + cellname)
        m.model.equiv(*sorted(set(vn)))
        m.model.equiv(*sorted(set(gn)))
        m.model.port("Pbulk", vn[0], gn[0])
        m.pruned = m.model.prune_to_ports()
        pend.append((target, m, FH.submit(
            runner.run, m.model, 1e4, 1e8, ndec=1, timeout=7200,
            workdir=paths.WORK / f"fh/bulk_{cellname}_{target}")))
    for target, m, fut in pend:
        f, Z, order, _ = fut.result()
        L, R = _LR(f, Z)
        i = int(np.argmin(abs(f - 1e6)))
        rows.append({"target_mm": target, "segments": m.n_seg,
                     "f_Hz": f.tolist(),
                     "L_nH": (L[:, 0, 0] * 1e9).tolist(),
                     "R_mOhm": (R[:, 0, 0] * 1e3).tolist(),
                     "L_nH_at_1MHz": float(L[i, 0, 0]) * 1e9,
                     "R_mOhm_at_1MHz": float(R[i, 0, 0]) * 1e3})
    best = rows[-1]
    return {
        "question": "Q13 (board S: bulk cans to the cell, through the planes)",
        "cell": cellname, "cans": list(refs), "window_mm": list(win),
        "mesh_series": rows,
        "f_Hz": best["f_Hz"], "L_nH": best["L_nH"], "R_mOhm": best["R_mOhm"],
        "L_nH_at_1MHz": best["L_nH_at_1MHz"],
        "R_mOhm_at_1MHz": best["R_mOhm_at_1MHz"],
        "change_last_refinement_pct": (
            abs(rows[-1]["L_nH_at_1MHz"] / rows[-2]["L_nH_at_1MHz"] - 1) * 100
            if len(rows) > 1 else None),
        "note": ("VBUS on F.Cu and In2, GND on F.Cu, In1, In3 and In4, every "
                 "VBUS and GND barrel in the window; the cans shorted at their "
                 "leads, the port across the cell's four DC-link capacitors. "
                 "It replaces board A's header-and-standoffs inductance in "
                 "the cell and DC-link models."),
    }


def run(quick=False):
    # the questions are independent of each other: run them side by side
    top = ThreadPoolExecutor(max_workers=6)
    q13 = bulk_path if board.P()["bulk"]["kind"] == "cans" else interconnect
    jobs = {"Q1": top.submit(commutation, quick),
            "Q1_cells_BC": top.submit(cells_bc, quick),
            "Q4": top.submit(gate_loops, quick),
            "Q5": top.submit(sense_loops, quick),
            "Q5_H4": top.submit(tap_asymmetry),
            "Q13": top.submit(q13, quick)}
    out = {"Q1": jobs["Q1"].result()}          # Q1 failing fails the phase
    for k, fut in jobs.items():
        if k == "Q1":
            continue
        try:
            out[k] = fut.result()
        except Exception as e:
            out[k] = {"error": f"{e.__class__.__name__}: {e}"}
    return out


if __name__ == "__main__":
    print(json.dumps(run(quick=True), indent=1, default=str)[:4000])
