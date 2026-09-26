#!/usr/bin/env python3
"""P2 -- the phase-cell circuit: Q2, Q3, Q4, Q5.

The cell netlist (spice/cell.py) is driven through one switching period with
both edges in it, and every sweep the spec asks for is run over it:

  Q2  V_ds overshoot and ringing at turn-off and at turn-on, over R_g,
      junction temperature, bus voltage and phase current
  Q3  whether an RC snubber is needed, and the smallest one that works
  Q4  the gate loop: V_gs induced on the off device by the complementary edge
  Q5  the sense chain after each edge, and what the ADC would see

Every criterion in SPEC.md sec.4 is evaluated here and reported as met or not.
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
from spice import devices, analyse                           # noqa: E402
from spice.cell import CellNetlist                           # noqa: E402
from spice.ngspice import NgSpice                            # noqa: E402

V_SILICON = 80.0
V_CRIT = board.P()["v_crit"]  # SPEC.md Q2's pass criterion (68 V on board A)
VB = board.P()["v_bus"]       # the design point's bus: 60 V board A, 48 V S
RELTOL = "1e-3"               # the cell converges here; see the note in run()


def _G():
    paths.import_tools()
    import geometry
    return geometry


def simulate(step=1e-10, maxstep=1e-9, **kw):
    """One cell transient.  If ngspice stops early -- this netlist is stiff at
    the turn-on edge -- the tolerance is loosened one notch and it is tried
    again, and whether the run completed is recorded with the result rather
    than hidden."""
    n = CellNetlist(**kw)
    base = n.text()
    ng = NgSpice()
    last = None
    for reltol, st, ms in ((RELTOL, step, maxstep),
                           ("3e-3", step, maxstep),
                           ("3e-3", step * 2, maxstep * 2),
                           ("1e-2", step * 2, maxstep * 2)):
        net = base.replace("reltol=1e-4", f"reltol={reltol}")
        try:
            ng.circuit(net)
            ng.command(f"tran {st:g} {n.t_stop:g} 0 {ms:g}")
            d = ng.all()
        except Exception as e:
            last = f"{e.__class__.__name__}: {e}"
            continue
        t = d.get("time")
        if t is not None and len(t) and t.max() >= n.t_stop * 0.98:
            d["_n"] = n
            d["_reltol"] = reltol
            d["_complete"] = True
            return d
        last = (f"stopped at {t.max() * 1e6:.3f} us of {n.t_stop * 1e6:.3f}"
                if t is not None and len(t) else "no data")
    d["_n"] = n
    d["_reltol"] = reltol
    d["_complete"] = False
    d["_why"] = last
    return d


def measure(d):
    n = d["_n"]
    t = d["time"]
    if not d.get("_complete", True) or len(t) < 10:
        return {"incomplete": True, "reason": d.get("_why"),
                "t_reached_us": float(t.max() * 1e6) if len(t) else 0.0,
                "pass_Q2": False, "exceeds_silicon": None,
                "Vds_low_peak_at_turn_on": float("nan"),
                "Vds_high_peak_at_turn_off": float("nan"),
                "ring_turn_off": {}, "ring_turn_on": {}}
    sw, hbp, hbn = d["sw"], d["hb_p"], d["hb_n"]
    vds_lo = sw - hbn
    vds_hi = hbp - sw
    t_on, t_off = n.t_turn_on, n.t_turn_off
    out = {}
    out["Vds_low_peak_at_turn_on"], out["t_peak_on"] = analyse.peak(
        t, vds_lo, t_on - 0.3e-6, t_on + 1.2e-6)
    out["Vds_high_peak_at_turn_off"], out["t_peak_off"] = analyse.peak(
        t, vds_hi, t_off - 0.3e-6, t_off + 1.5e-6)
    out["Vds_high_peak_at_turn_on"] = analyse.peak(
        t, vds_hi, t_on - 0.3e-6, t_on + 1.2e-6)[0]
    # how fast the switch node actually moves, which is what sets both the
    # overshoot and the Miller current
    wsw = analyse.window(t, t_on - 0.2e-6, t_on + 0.6e-6)
    out["dVdt_switch_node_kV_per_us"] = (
        float(np.max(np.abs(np.gradient(sw, t)[wsw]))) / 1e9
        if wsw.any() else float("nan"))
    out["phase_peak"] = float(np.max(d["phase"]))
    out["sw_min"] = float(np.min(sw))
    out["ring_turn_off"] = analyse.ring(t, vds_hi, t_off, t_off + 1.5e-6,
                                        settle=n.vbus)
    out["ring_turn_on"] = analyse.ring(t, vds_lo, t_on, t_on + 1.2e-6,
                                       settle=n.vbus)
    # gate behaviour (Q4).  What turns a device on is the voltage across its
    # own two terminals, gate to source -- not gate to the driver's reference,
    # which differs from it by the common-source inductance's L di/dt.  Both
    # are measured: the die pair decides, the driver-referenced pair is what a
    # probe on the board would show.
    gi1 = devices.gate_internal("g1", "Q1").lower()
    gi2 = devices.gate_internal("g2", "Q2").lower()
    if gi1 in d and gi2 in d:
        vgs_hi = d[gi1] - d["s1"]
        vgs_lo = d[gi2] - d["s2"]
        vgs_hi_drv = d["g1"] - sw
        vgs_lo_drv = d["g2"] - hbn
        out["Vgs_high_max"] = float(np.max(vgs_hi))
        out["Vgs_high_min"] = float(np.min(vgs_hi))
        out["Vgs_low_max"] = float(np.max(vgs_lo))
        out["Vgs_low_min"] = float(np.min(vgs_lo))
        # the off device during the complementary edge
        out["Vgs_low_induced_at_turn_on"] = analyse.excursion(
            t, vgs_lo, t_on - 0.1e-6, t_on + 0.5e-6)
        out["Vgs_high_induced_at_turn_off"] = analyse.excursion(
            t, vgs_hi, t_off + 0.1e-6, t_off + 1.0e-6)
        out["Vgs_low_induced_at_driver"] = analyse.excursion(
            t, vgs_lo_drv, t_on - 0.1e-6, t_on + 0.5e-6)
        out["Vgs_high_induced_at_driver"] = analyse.excursion(
            t, vgs_hi_drv, t_off + 0.1e-6, t_off + 1.0e-6)
    # avalanche: the device breaks down at V(BR)DSS rather than letting V_ds
    # run away, so what matters is how long it spends there and how much
    # energy it takes while it does
    for tag, v_ds, branch in (("low", vds_lo, "ld2#branch"),
                              ("high", vds_hi, "ld1#branch")):
        m = v_ds > 79.0
        out[f"t_above_79V_{tag}_ns"] = (float(np.sum(np.diff(t) * m[:-1]))
                                        * 1e9)
        if branch in d and m.any():
            i = d[branch]
            p_ = np.where(m, v_ds * np.abs(i), 0.0)
            out[f"E_avalanche_{tag}_uJ"] = float(
                np.trapezoid(p_, t)) * 1e6
        else:
            out[f"E_avalanche_{tag}_uJ"] = 0.0
    # current slope actually achieved
    if "ld1#branch" in d:
        out["di_dt_A_per_ns"] = analyse.di_dt(
            t, d["ld1#branch"], t_off - 0.1e-6, t_off + 0.3e-6) / 1e9
    # sense chain (Q5)
    if "isense" in d:
        iso = d["isense"]
        out["isense_final"] = float(np.median(iso[t > t_off + 2e-6])) \
            if (t > t_off + 2e-6).any() else None
        out["isense_settle_after_on_ns"] = (analyse.settling(
            t, iso, t_on, t_max=(t_off - t_on) * 0.95) or 0) * 1e9
        out["isense_settle_after_off_ns"] = (analyse.settling(
            t, iso, t_off, t_max=(n.t_low_on - t_off) * 0.95) or 0) * 1e9
        out["isense_excursion_on_V"] = analyse.excursion(
            t, iso, t_on, t_on + 1e-6, ref=float(np.median(iso[(t > t_on - 1e-6)
                                                               & (t < t_on)])))
    # SPEC.md Q2 has two criteria: the peak, and that the ringing decays to
    # under 5 % within five cycles.
    cyc = [((out.get(k) or {}).get("cycles_to_5pct"))
           for k in ("ring_turn_off", "ring_turn_on")]
    cyc = [c for c in cyc if c is not None]
    out["cycles_to_5pct_worst"] = max(cyc) if cyc else None
    out["pass_ring_decay"] = bool(cyc and max(cyc) <= 5.0)
    out["pass_peak"] = bool(max(out["Vds_low_peak_at_turn_on"],
                                out["Vds_high_peak_at_turn_off"]) <= V_CRIT)
    out["pass_Q2"] = bool(out["pass_peak"] and out["pass_ring_decay"])
    out["exceeds_silicon"] = bool(max(out["Vds_low_peak_at_turn_on"],
                                      out["Vds_high_peak_at_turn_off"])
                                  > V_SILICON)
    return out


# =================================================================== Q2 =====
def q2(quick=False):
    t0 = time.time()
    base = dict(vbus=VB, i_phase=28.28, tj=25.0, rg=2.2)
    rows = []
    rg_list = [2.2, 4.7, 10.0, 22.0]
    tj_list = [25.0, 125.0]
    for rg in (rg_list[:2] if quick else rg_list):
        for tj in tj_list:
            d = simulate(**dict(base, rg=rg, tj=tj))
            m = measure(d)
            m.update({"R_g": rg, "T_j": tj, "V_bus": VB, "I_phase": 28.28})
            rows.append(m)
    # the operating envelope
    env = []
    for vb in ([VB] if quick else list(board.P()["v_sweep"])):
        for ip in ([28.28] if quick else [0.0, 7.07, 14.14, 28.28]):
            d = simulate(**dict(base, vbus=vb, i_phase=ip))
            m = measure(d)
            m.update({"R_g": 2.2, "T_j": 25.0, "V_bus": vb, "I_phase": ip})
            env.append(m)
    # a stronger driver: the spec's named escape
    fast = []
    for scale in ([2.0] if quick else [1.0, 2.0]):
        d = simulate(**dict(base, drive_scale=scale))
        m = measure(d)
        m["drive_scale"] = scale
        fast.append(m)

    # the driver's output impedance, which the product page does not pin down:
    # 1.0 reads I_O+/I_O- as a resistance at the 12 V drive, 0.1 is a strong
    # output stage.  It sets the edge rate, so it sets the overshoot.
    drv = []
    for rs in ([1.0] if quick else [1.0, 0.3, 0.1]):
        d = simulate(**dict(base, drv_r_scale=rs, tj=125.0))
        m = measure(d)
        m["drv_r_scale"] = rs
        m["R_g"] = base["rg"]
        m["T_j"] = 125.0
        m["V_bus"] = base["vbus"]
        m["I_phase"] = base["i_phase"]
        drv.append(m)

    # the datasheet's maximum capacitances, which slow every edge: the model's
    # switching times at "typ" come out 30-45 % faster than the datasheet's own
    # figures, so this corner bounds the answer from the slow side
    corner = []
    for c in ("typ", "max"):
        d = simulate(**dict(base, fet_corner=c))
        m = measure(d)
        m["fet_corner"] = c
        corner.append(m)

    def _pk(r):
        a = r.get("Vds_low_peak_at_turn_on", float("nan"))
        b = r.get("Vds_high_peak_at_turn_off", float("nan"))
        a = -1e9 if a != a else a
        b = -1e9 if b != b else b
        return max(a, b)

    worst = max(rows + env + drv, key=_pk)
    _plot_edges(base)
    return {
        "question": "Q2",
        "estimate_geometry_py": {"overshoot_V": _G().commutation_loop()["overshoot"],
                                 "v_peak_V": _G().commutation_loop()["v_peak"],
                                 "ring_MHz_estimate": 100.0},
        "criterion": {"V_ds_peak_max_V": V_CRIT,
                      "reason": board.P()["v_crit_reason"]},
        "rg_tj_sweep": rows,
        "envelope_sweep": env,
        "drive_strength": fast,
        "driver_impedance": drv,
        "capacitance_corner": corner,
        "worst_case": worst,
        "worst_V_ds_V": _pk(worst),
        "pass": bool(all(r["pass_Q2"] for r in rows + env + drv)),
        "incomplete_runs": sum(1 for r in rows + env + fast + corner + drv
                               if r.get("incomplete")),
        "figure": "p2_edges.png",
        "seconds": round(time.time() - t0, 1),
        "note": ("Both edges are measured.  The turn-OFF number is the one "
                 "SPEC.md Q2 names; the turn-ON number is the low-side "
                 "device's V_ds while its own body diode reverse-recovers, "
                 "which on this board is the larger of the two."),
    }


def _plot_edges(base):
    d = simulate(step=2e-11, maxstep=2e-10, **base)
    n = d["_n"]
    t = d["time"] * 1e6
    vth = jsonio.model("bsc030n08ns5")["V_GS_th"]["min"]
    fig, ax = plt.subplots(3, 2, figsize=(12, 10), dpi=150)
    for col, (tc, name) in enumerate(((n.t_turn_on, "high-side turn-on"),
                                      (n.t_turn_off, "high-side turn-off"))):
        wide = (t > (tc - 0.2e-6) * 1e6) & (t < (tc + 1.2e-6) * 1e6)
        near = (t > (tc - 0.05e-6) * 1e6) & (t < (tc + 0.25e-6) * 1e6)
        for row, w, tag in ((0, wide, ""), (1, near, " (300 ns)")):
            a = ax[row, col]
            a.plot(t[w], (d["hb_p"] - d["sw"])[w], lw=1, label="V_ds high")
            a.plot(t[w], (d["sw"] - d["hb_n"])[w], lw=1, label="V_ds low")
            a.axhline(80, color="r", ls="--", lw=.8, label="80 V silicon")
            a.axhline(68, color="orange", ls=":", lw=.8, label="68 V criterion")
            a.set_title(name + tag, fontsize=9)
            a.set_ylabel("V")
            a.grid(alpha=.3)
            if row == 0:
                a.legend(fontsize=6)
        a = ax[2, col]
        gi1 = devices.gate_internal("g1", "Q1").lower()
        gi2 = devices.gate_internal("g2", "Q2").lower()
        a.plot(t[near], (d[gi1] - d["s1"])[near], lw=1, label="V_gs high, die")
        a.plot(t[near], (d[gi2] - d["s2"])[near], lw=1, label="V_gs low, die")
        a.plot(t[near], (d["g2"] - d["hb_n"])[near], lw=.8, ls=":",
               label="V_gs low, at the pin")
        a.axhline(vth, color="r", ls="--", lw=.8,
                  label=f"V_gs(th) min {vth:g} V")
        a.set_xlabel("us"); a.set_ylabel("V")
        a.legend(fontsize=6); a.grid(alpha=.3)
    fig.suptitle(f"cell A, {VB:.0f} V, 28.3 A, Rg 2.2 Ohm, Tj 25 C -- the gate row "
                 "is the polysilicon gate against the source, with the "
                 "package pin dotted", fontsize=10)
    fig.tight_layout()
    fig.savefig(paths.FIGS / "p2_edges.png")
    plt.close(fig)


# =================================================================== Q3 =====
def q3(quick=False, need=None):
    """The snubber sweep.  Run whether or not Q2 passed, because the answer
    'none needed' is itself a result that has to be shown."""
    t0 = time.time()
    rows = []
    Rs = [2.2, 4.7, 10.0, 22.0]
    Cs = [0.47e-9, 1e-9, 2.2e-9, 4.7e-9]
    if quick:
        Rs, Cs = [4.7, 10.0], [1e-9, 2.2e-9]
    for r in Rs:
        for c in Cs:
            d = simulate(vbus=VB, i_phase=28.28, tj=25.0, rg=2.2,
                         snubber=(r, c))
            m = measure(d)
            # snubber loss: the capacitor is charged and discharged twice per
            # period, so P = C V^2 f whatever R is, plus the ring it damps
            pk = [m.get("Vds_low_peak_at_turn_on", float("nan")),
                  m.get("Vds_high_peak_at_turn_off", float("nan"))]
            pk = [x for x in pk if x == x]
            m.update({"R": r, "C_nF": c * 1e9,
                      "P_R_W": c * VB ** 2 * 20e3,
                      "V_peak_V": max(pk) if pk else float("nan")})
            rows.append(m)
    ok = [r for r in rows if r["V_peak_V"] == r["V_peak_V"]
          and r["V_peak_V"] <= V_CRIT and r["P_R_W"] <= 0.25]
    best = min(ok, key=lambda r: (r["C_nF"], r["R"])) if ok else None
    return {
        "question": "Q3",
        "sweep": [{k: r.get(k) for k in ("R", "C_nF", "V_peak_V", "P_R_W",
                                         "Vds_low_peak_at_turn_on",
                                         "Vds_high_peak_at_turn_off",
                                         "incomplete")}
                  for r in rows],
        "smallest_that_passes": ({k: best[k] for k in ("R", "C_nF", "V_peak_V",
                                                       "P_R_W")} if best else None),
        "criterion": f"V_ds,peak <= {V_CRIT:g} V with P_R <= 0.25 W",
        "seconds": round(time.time() - t0, 1),
        "note": ("P_R = C.V^2.f is the whole snubber loss at 20 kHz and does "
                 "not depend on R; R only sets how hard it damps and how the "
                 "loss splits between R and the FETs."),
    }


# =================================================================== Q4 =====
def q4(quick=False):
    t0 = time.time()
    M = jsonio.model("bsc030n08ns5")
    vth_min = M["V_GS_th"]["min"]
    rows = []
    # The EG2103 product page gives I_O+/I_O- but not the condition they are
    # measured at, so the output-stage impedance is bracketed: 1.0 reads the
    # ratings as a resistance at the 12 V drive (40/20 ohm), 0.1 is a strong
    # output stage (4/2 ohm).  The bracket sets the edge rate, so every answer
    # that depends on dV/dt is reported across it rather than at one point.
    combos = ([(s_, r_, c_, dr_) for s_ in (1.0, 2.0)
               for r_ in (2.2, 4.7, 10.0) for c_ in ("typ", "max")
               for dr_ in (1.0, 0.1)]
              if not quick else [(1.0, 2.2, "typ", 1.0)])
    for scale, rg, fc, drv in combos:
        if True:
            d = simulate(vbus=VB, i_phase=28.28, tj=125.0, rg=rg,
                         drive_scale=scale, fet_corner=fc, drv_r_scale=drv,
                         step=5e-11, maxstep=5e-10)
            m = measure(d)
            m.setdefault("dVdt_switch_node_kV_per_us", float("nan"))
            m.update({"R_g": rg, "drive_scale": scale, "fet_corner": fc,
                      "drv_r_scale": drv})
            m["induced_Vgs_limit"] = 0.7 * vth_min
            m.setdefault("Vgs_low_induced_at_turn_on", float("nan"))
            m.setdefault("Vgs_high_induced_at_turn_off", float("nan"))
            m.setdefault("Vgs_low_induced_at_driver", float("nan"))
            m.setdefault("Vgs_low_max", float("nan"))
            m["pass_miller"] = bool(m["Vgs_low_induced_at_turn_on"]
                                    <= 0.7 * vth_min)
            m["Vgs_overshoot_pct"] = (m["Vgs_low_max"] - 12.0) / 12.0 * 100
            m["pass_overshoot"] = bool(abs(m["Vgs_overshoot_pct"]) <= 20.0)
            rows.append(m)
    # what it would take to meet the criterion.  The induced step is set by
    # the charge divider Q_gd / C_iss, so the lever is C_iss: a capacitor
    # across the FET's own gate and source pins.  Swept until the die-side
    # excursion drops under the criterion, at the worst edge in the sweep
    # above (the strong-driver corner).
    remedy = []
    worst_row = max(rows, key=lambda r: r["Vgs_low_induced_at_turn_on"])
    for c_gs in ([0.0] if quick else [0.0, 2.2e-9, 4.7e-9, 10e-9, 22e-9]):
        d = simulate(vbus=VB, i_phase=28.28, tj=125.0,
                     rg=worst_row["R_g"], drive_scale=worst_row["drive_scale"],
                     fet_corner=worst_row["fet_corner"],
                     drv_r_scale=worst_row["drv_r_scale"],
                     c_gs_ext=c_gs, step=5e-11, maxstep=5e-10)
        m = measure(d)
        remedy.append({
            "C_gs_ext_nF": c_gs * 1e9,
            "Vgs_low_induced_at_turn_on": m.get("Vgs_low_induced_at_turn_on"),
            "Vgs_low_induced_at_driver": m.get("Vgs_low_induced_at_driver"),
            "Vgs_high_max": m.get("Vgs_high_max"),
            "Vds_low_peak_at_turn_on": m.get("Vds_low_peak_at_turn_on"),
            "Vds_high_peak_at_turn_off": m.get("Vds_high_peak_at_turn_off"),
            "pass_miller": bool((m.get("Vgs_low_induced_at_turn_on")
                                 or float("inf")) <= 0.7 * vth_min),
            "pass_below_Vth_min": bool((m.get("Vgs_low_induced_at_turn_on")
                                        or float("inf")) <= vth_min),
        })
    # Does the induced excursion actually conduct?  The criterion is a
    # voltage, but what matters is charge through the channel of the device
    # that is supposed to be off.  Measured by difference: the same edge run
    # twice, once with the low-side device at the bottom of the datasheet's
    # V_GS(th) distribution and once with a device that cannot turn on at all
    # (threshold pushed out of reach) but has identical capacitances and
    # charges.  Whatever the high side carries extra in the first run is
    # cross-conduction and nothing else.
    fm0 = devices.fet_model(tj=125.0, corner=worst_row["fet_corner"])
    vto_fit = fm0["params"]["Vto"]
    cross = []
    for tag, shift in (("V_GS(th) min", vth_min - vto_fit),
                       ("V_GS(th) typ", 0.0),
                       ("cannot conduct", 50.0)):
        d = simulate(vbus=VB, i_phase=28.28, tj=125.0,
                     rg=worst_row["R_g"], drive_scale=worst_row["drive_scale"],
                     fet_corner=worst_row["fet_corner"],
                     drv_r_scale=worst_row["drv_r_scale"],
                     vto_shift_low=shift, step=2e-11, maxstep=1e-10)
        m = measure(d)
        n, t = d["_n"], d["time"]
        w = (t > n.t_turn_on - 0.1e-6) & (t < n.t_turn_on + 0.6e-6)
        i_hi = d.get("ld1#branch")
        q = (float(np.trapezoid(i_hi[w], t[w])) if i_hi is not None
             and w.any() else float("nan"))
        cross.append({
            "corner": tag, "Vto_V": vto_fit + shift,
            "Vgs_low_induced_at_turn_on": m.get("Vgs_low_induced_at_turn_on"),
            "i_high_peak_A": (float(np.max(i_hi[w])) if i_hi is not None
                              and w.any() else float("nan")),
            "charge_through_high_side_nC": q * 1e9,
        })
    ref = next((c for c in cross if c["corner"] == "cannot conduct"), None)
    for c in cross:
        c["cross_conduction_nC"] = (c["charge_through_high_side_nC"]
                                    - ref["charge_through_high_side_nC"]
                                    if ref else float("nan"))
        c["cross_conduction_uJ"] = c["cross_conduction_nC"] * 1e-9 * VB * 1e6

    smallest = next((r for r in remedy if r["pass_miller"]), None)
    smallest_below_vth = next((r for r in remedy if r["pass_below_Vth_min"]),
                              None)
    return {
        "question": "Q4",
        "V_GS_th_min_V": vth_min,
        "remedy_C_gs": remedy,
        "cross_conduction": cross,
        "remedy_at": {k: worst_row[k] for k in
                      ("R_g", "drive_scale", "fet_corner", "drv_r_scale")},
        "smallest_C_gs_that_meets_criterion_nF": (
            smallest["C_gs_ext_nF"] if smallest else None),
        "smallest_C_gs_that_stays_below_Vth_min_nF": (
            smallest_below_vth["C_gs_ext_nF"] if smallest_below_vth else None),
        "criterion": ("induced V_gs,peak <= 0.7 x V_GS(th),min at 1.2 kV/us "
                      "and at twice that; V_gs overshoot <= 20 %"),
        "sweep": [{k: r[k] for k in
                   ("R_g", "drive_scale", "fet_corner", "drv_r_scale",
                    "dVdt_switch_node_kV_per_us",
                    "Vgs_low_induced_at_turn_on", "Vgs_high_induced_at_turn_off",
                    "Vgs_low_induced_at_driver",
                    "Vgs_low_max", "Vgs_overshoot_pct", "pass_miller",
                    "pass_overshoot")} for r in rows],
        "note": ("V_gs is measured across the device's own two terminals -- "
                 "the polysilicon gate to the source -- because that is what "
                 "decides whether the channel conducts.  The gate PIN is "
                 "reported alongside it as Vgs_low_induced_at_driver: the "
                 "Miller current's I*R_g(int) drop separates the two, and a "
                 "probe on the board would see the pin."),
        "pass": bool(all(r["pass_miller"] and r["pass_overshoot"] for r in rows)),
        "seconds": round(time.time() - t0, 1),
    }


# =================================================================== Q5 =====
def q5(quick=False):
    t0 = time.time()
    A = jsonio.model("ina241a3")
    sh = jsonio.model(board.P()["shunt"])
    lsb = 3.3 / 4096.0
    rows = []
    for corner in (["typ"] if quick else ["min", "typ", "max"]):
        for ip in ([28.28] if quick else [-28.28, 0.0, 28.28]):
            d = simulate(vbus=VB, i_phase=ip, tj=25.0, rg=2.2,
                         shunt_esl_corner=corner, step=5e-11, maxstep=5e-10)
            m = measure(d)
            n = d["_n"]
            t = d["time"]
            if m.get("incomplete") or "isense" not in d:
                m.update({"shunt_esl_corner": corner, "I_phase": ip})
                rows.append(m)
                continue
            iso = d["isense"]
            pre_on = float(np.median(iso[(t > n.t_turn_on - 0.8e-6)
                                         & (t < n.t_turn_on - 0.1e-6)]))
            pre_off = float(np.median(iso[(t > n.t_turn_off - 0.8e-6)
                                          & (t < n.t_turn_off - 0.1e-6)]))
            # each edge is measured only up to the next one: searching past
            # it picks up the other edge's hold and reports it as this edge's
            # settling time
            span_on = (n.t_turn_off - n.t_turn_on) * 0.95
            span_off = (n.t_low_on - n.t_turn_off) * 0.95
            m["settle_on_ns"] = (analyse.settling(t, iso, n.t_turn_on,
                                                  final=pre_on, tol=lsb,
                                                  t_max=span_on) or 0) * 1e9
            m["settle_off_ns"] = (analyse.settling(t, iso, n.t_turn_off,
                                                   final=pre_off, tol=lsb,
                                                   t_max=span_off) or 0) * 1e9
            m["err_peak_on_LSB"] = analyse.excursion(
                t, iso, n.t_turn_on, n.t_turn_on + span_on, ref=pre_on) / lsb
            m["err_peak_off_LSB"] = analyse.excursion(
                t, iso, n.t_turn_off, n.t_turn_off + span_off,
                ref=pre_off) / lsb
            # the raw differential across the tap, before the amplifier
            if "nsnsp" in d and "nsnsn" in d:
                vdiff = d["nsnsp"] - d["nsnsn"]
                m["V_tap_peak_mV"] = analyse.excursion(
                    t, vdiff, n.t_turn_off, n.t_turn_off + 0.5e-6) * 1e3
            m.update({"shunt_esl_corner": corner, "I_phase": ip,
                      "L_esl_nH": sh["L_esl"][corner] * 1e9})
            rows.append(m)
    # What fraction of free-running samples lands in a disturbed window.
    # The spec's phrase is "corrupted", but that is not what this chain does:
    # the amplifier holds its pre-edge output, so a sample taken inside the
    # hold is *stale*, by at most the hold time, rather than wrong.  Both are
    # reported, and the error is bounded in LSB.
    got = [r for r in rows if "settle_on_ns" in r]
    worst_settle = (max(max(r["settle_on_ns"], r["settle_off_ns"])
                        for r in got) if got else float("nan"))
    worst_err_LSB = (max(max(r["err_peak_on_LSB"], r["err_peak_off_LSB"])
                         for r in got) if got else float("nan"))
    hold = A["pwm_hold"]["value"]
    adc = jsonio.model("rp2350_adc")
    t_ap = [adc["t_aperture"]["min"], adc["t_aperture"]["max"]]
    period = 1 / 20e3
    # six switch-node edges per period, each holding the output for `hold`
    frac = [(6 * (hold + ta) / period) for ta in t_ap]
    return {
        "question": "Q5",
        "criterion": ("ISENSE settles to +-1 LSB (0.806 mV) within 500 ns of "
                      "the switch-node edge; corrupted-sample fraction <= 6 %"),
        "LSB_mV": lsb * 1e3,
        "sweep": [{k: r[k] for k in
                   ("shunt_esl_corner", "L_esl_nH", "I_phase", "settle_on_ns",
                    "settle_off_ns", "err_peak_on_LSB", "err_peak_off_LSB",
                    "V_tap_peak_mV") if k in r} for r in rows],
        "worst_settle_ns": worst_settle,
        "worst_error_LSB": worst_err_LSB,
        "ina241_hold_ns": hold * 1e9,
        "stale_fraction_free_running": frac,
        "stale_fraction_synchronised": 0.0,
        "pass_settling": bool(worst_settle <= 500.0),
        "pass_error_bound_LSB": bool(worst_err_LSB <= 8.0),
        "pass_corruption": bool(max(frac) <= 0.06),
        "seconds": round(time.time() - t0, 1),
        "note": ("The INA241A3 holds its output for 1 us after a large "
                 "common-mode dV/dt (SBOSA30D sec.7.3.1.1), so the output "
                 "cannot settle inside 500 ns by construction: for that window "
                 "it is not wrong, it is stale.  Both facts are reported; the "
                 "consequence for the current loop is Q8, in P6.  The "
                 "'corrupted-sample fraction' the spec asks for is replaced "
                 "by two numbers that mean something for this part: what "
                 "fraction of free-running samples lands inside a hold "
                 "window, and how far from the truth the output ever gets. "
                 "Synchronised sampling at the counter zero lands in no hold "
                 "window at all, because every edge is half a period away."),
    }


# =================================================================== Q12 ====
def q12(quick=False):
    """Common mode: what the switch node's dV/dt pushes through the motor's
    winding-to-frame capacitance, and what the frame does about it.

    The four M3 motor mounts are NPTH -- no copper, not a ground bond -- so
    on this board the motor frame is tied to board GND only through whatever
    the enclosure provides.  That is the question, so the bond is a swept
    parameter: floating, bonded through the six M2.5 bosses, or through a Y
    capacitor.
    """
    t0 = time.time()
    rows = []
    cwfs = [100e-12, 500e-12, 2e-9] if not quick else [500e-12]
    # The board itself provides no bond: the motor mounts are NPTH.  These are
    # the enclosure's options, not the board's.
    bonds = [("open", 0.0),                 # what the board gives you today
             ("strap", 20e-9),              # a wire from frame to board GND
             ("strap", 2e-9),               # a short, wide strap
             ("ycap", 4.7e-9)]              # a Y capacitor instead
    if quick:
        bonds = bonds[:2]
    # everything here is driven by the switch node's dV/dt, which the driver's
    # unspecified output impedance brackets (Q2), so the sweep is run at both
    # ends of that bracket rather than at one point
    for c_wf in cwfs:
        for bond in bonds:
          for drv in ([1.0] if quick else [1.0, 0.1]):
            d = simulate(vbus=VB, i_phase=28.28, tj=25.0, rg=2.2,
                         c_wf=c_wf, frame_bond=bond, drv_r_scale=drv,
                         step=5e-11, maxstep=5e-10)
            m = measure(d)
            if m.get("incomplete"):
                rows.append({"C_wf_pF": c_wf * 1e12, "bond": bond[0],
                             "drv_r_scale": drv, "incomplete": True})
                continue
            n = d["_n"]
            t = d["time"]
            frame = d.get("frame")
            icm = d.get("vwf#branch")
            e = {"C_wf_pF": c_wf * 1e12, "bond": bond[0],
                 "bond_value": bond[1], "drv_r_scale": drv,
                 "dVdt_switch_node_kV_per_us":
                     m.get("dVdt_switch_node_kV_per_us"),
                 "label": (f"{bond[0]}"
                           + (f" {bond[1] * 1e9:g} nH" if bond[0] == "strap"
                              else (f" {bond[1] * 1e9:g} nF"
                                    if bond[0] == "ycap" else "")))}
            if frame is not None:
                e["V_frame_pk_V"] = float(np.max(np.abs(frame - d["hb_n"])))
            if icm is not None:
                e["I_cm_pk_A"] = float(np.max(np.abs(icm)))
                e["I_cm_rms_A"] = analyse.rms(t, icm)
                # three phases switch, not one, so the board's own total is
                # up to three times this in the worst alignment
                e["I_cm_rms_three_phase_A"] = e["I_cm_rms_A"] * math.sqrt(3)
            e["pass_frame_10V"] = bool(e.get("V_frame_pk_V", 1e9) <= 10.0)
            rows.append(e)
    return {
        "question": "Q12",
        "criterion": "reported; frame-to-GND excursion <= 10 V peak",
        "sweep": rows,
        "worst_V_frame_V": max((r.get("V_frame_pk_V") or 0) for r in rows),
        "pass": bool(all(r.get("pass_frame_10V", False) for r in rows)),
        "board_provides": ("nothing: with the mounts NPTH the frame floats, "
                           "and the first row of the sweep is what the board "
                           "gives you today"),
        "mount_note": ("the four Dia 3.2 motor mounts are NPTH and carry no "
                       "copper (SPEC.md sec.1.2), so the motor frame has no "
                       "bond to board A's ground except through the six M2.5 "
                       "perimeter bosses and whatever the enclosure adds"),
        "seconds": round(time.time() - t0, 1),
    }


def run(quick=False):
    out = {}
    fit = devices.fit_fet()
    out["fet_fit"] = {k: fit[k] for k in
                      ("part", "datasheet", "simulated", "error",
                       "worst_error", "pass_20pct", "curve", "note",
                       "R_d_fitted_Ohm", "switching_times_simulated_ns",
                       "switching_times_datasheet_ns")}
    fit_max = devices.fit_fet(corner="max")
    out["fet_fit_max_corner"] = {
        k: fit_max[k] for k in ("datasheet", "simulated", "error",
                                "worst_error", "switching_times_simulated_ns")}
    # written from the fit rather than typed, so it cannot go stale
    ds = fit["switching_times_datasheet_ns"]
    t_typ = fit["switching_times_simulated_ns"]
    t_max = fit_max["switching_times_simulated_ns"]
    fast = [k for k in ds if t_typ[k] < ds[k]]
    worst = max(ds, key=lambda k: abs(t_typ[k] / ds[k] - 1))
    slower = all(t_max[k] > t_typ[k] for k in ds)
    straddle = (min(t_max[k] / ds[k] for k in ds) < 1
                < max(t_max[k] / ds[k] for k in ds))
    out["fet_fit"]["edge_rate_note"] = (
        f"Against the datasheet's own switching test the fitted model is "
        f"faster on {len(fast)} of its {len(ds)} times at the typical "
        f"capacitances, by up to "
        f"{abs(t_typ[worst] / ds[worst] - 1) * 100:.0f} % "
        f"({worst.replace('_ns', '')} {t_typ[worst]:.1f} vs "
        f"{ds[worst]:.0f} ns)."
        + (" At the maximum capacitances every one of them is slower than at "
           "the typical" if slower else
           " At the maximum capacitances they are not uniformly slower")
        + (", and they straddle the datasheet's figures rather than sitting "
           "on one side of them" if straddle else "")
        + ", so the two corners bracket the edge rate rather than pointing at "
          "one. Every edge-rate-sensitive answer is run at both.")
    out["fet_fit"]["params"] = fit["model"]["params"]
    out["fet_fit"]["Cgd"] = fit["model"]["Cgd"]
    out["fet_fit"]["Cds"] = fit["model"]["Cds"]
    _plot_fet(fit)
    out["Q2"] = q2(quick)
    out["Q3"] = q3(quick)
    out["Q4"] = q4(quick)
    out["Q5"] = q5(quick)
    try:
        out["Q12"] = q12(quick)
    except Exception as ex:
        out["Q12"] = {"error": f"{ex.__class__.__name__}: {ex}"}
    out["solver_note"] = (
        f"ngspice through libngspice.so.0 (there is no CLI on this machine); "
        f"reltol={RELTOL}, gear integration.  The default reltol of 1e-4 stalls "
        f"on this netlist at the turn-on edge; the timestep convergence check "
        f"in results shows the answers do not depend on the step.")
    return out


def _plot_fet(fit):
    c = fit["curve"]
    fig, ax = plt.subplots(figsize=(5.5, 4), dpi=150)
    ax.loglog(c["V"][1:], c["C_iss_pF"][1:], label="C_iss")
    ax.loglog(c["V"][1:], c["C_oss_pF"][1:], label="C_oss")
    ax.loglog(c["V"][1:], c["C_gd_pF"][1:], label="C_rss = C_gd")
    for v, y, lab in ((40, fit["datasheet"]["C_iss_40V_pF"], None),
                      (40, fit["datasheet"]["C_oss_40V_pF"], None),
                      (40, fit["datasheet"]["C_rss_40V_pF"], "datasheet, 40 V")):
        ax.plot([v], [y], "ko", ms=5, label=lab)
    ax.set_xlabel("V_DS (V)"); ax.set_ylabel("pF")
    ax.set_title("BSC030N08NS5: fitted capacitances", fontsize=9)
    ax.legend(fontsize=7); ax.grid(alpha=.3, which="both")
    fig.tight_layout()
    fig.savefig(paths.FIGS / "p2_fet_caps.png")
    plt.close(fig)


if __name__ == "__main__":
    print(json.dumps(run(quick=True), indent=1, default=str)[:3000])
