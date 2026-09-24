#!/usr/bin/env python3
"""P6 -- the system: Q8 (sampling), Q9 (dead time), Q10's consequence, Q14.

The L4 loop (sim/loop/) puts the real switching inverter, the motor as a
parameter set, SimpleFOC's own control law and the RP2350's ADC together, and
every question here is answered by running it and measuring.
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
from lib import paths, jsonio, board                                # noqa: E402
from loop import control                                     # noqa: E402
from loop.motor import Motor, BENCH, DESIGN, with_ke_for     # noqa: E402
from loop.system import Inverter, ADC, Run                   # noqa: E402

paths.import_tools()
I_RMS = 20.0
I_PEAK = I_RMS * math.sqrt(2)
VB = board.P()["v_bus"]            # 60 V board A, 48 V board S
SG = board.P()["sense_gain"]       # V/A at the ADC: 40 mV/A A, 50 mV/A S


def design_motor(rated_rpm=2000.0, pp=11, R=0.040, L=60e-6, v_bus=VB):
    p = dict(DESIGN, pole_pairs=pp, R=R, L=L)
    return with_ke_for(p, v_bus=v_bus, rated_rpm=rated_rpm)


def _thd(i, t, f_e):
    """Total harmonic distortion of one phase current about its fundamental."""
    n = len(t)
    if n < 64:
        return float("nan")
    tt = np.linspace(t[0], t[-1], 4096)
    ii = np.interp(tt, t, i)
    ii = ii - ii.mean()
    F = np.fft.rfft(ii * np.hanning(len(ii)))
    f = np.fft.rfftfreq(len(tt), tt[1] - tt[0])
    k = int(np.argmin(abs(f - f_e)))
    if k < 3:
        return float("nan")          # too few electrical periods to resolve
    band = slice(max(k - 2, 0), k + 3)
    fund = np.sqrt(np.sum(np.abs(F[band]) ** 2))
    tot = np.sqrt(np.sum(np.abs(F[1:]) ** 2))
    rest = math.sqrt(max(tot ** 2 - fund ** 2, 0.0))
    return rest / fund if fund else float("nan")


# =================================================================== Q8 =====
def q8(quick=False):
    """Free-running round-robin sampling against PWM-synchronised sampling."""
    t0 = time.time()
    adcm = jsonio.model("rp2350_adc")
    p = design_motor()
    g = control.tuned_gains(p, 1000.0)
    w = 2 * math.pi * p["rated_rpm"] / 60.0
    rows = []
    n_runs = 3 if quick else 8
    for sync in (False, True):
        for seed in range(n_runs):
            adc = ADC(n_channels=4, synchronised=sync, seed=seed)
            inv = Inverter(v_bus=VB, dead_zone=0.02)
            # the free-running ADC is not locked to the PWM, so where its
            # conversions land is arbitrary: each run draws a different phase
            ph = (0.0 if sync else (seed / n_runs) * 4 * 2e-6)
            r = Run(p, inv, adc, g, v_bus=VB, sense_gain=SG, i_q_target=I_PEAK,
                    omega0=w, mechanical=False, adc_phase=ph)
            rec = r.run(0.02)
            tail = rec["t"] > 0.01
            iq_true = _iq_of(rec)
            n = len(rec["iq_meas"])
            # the loop should regulate the AVERAGE current over a PWM period,
            # not the instantaneous value: a sample taken at an arbitrary
            # point in the ripple is not wrong about the instant, it is wrong
            # about what the loop wants to know.  That is the comparison.
            avg = _period_average(rec, iq_true, inv.T, n)
            meas = rec["iq_meas"][n // 2:]
            err = meas - avg[n // 2:]
            # where the error lives in frequency: the loop can filter what is
            # above its bandwidth and cannot touch what is below it
            spec = np.abs(np.fft.rfft(err - err.mean())) * 2 / len(err)
            fbin = np.fft.rfftfreq(len(err), inv.T)
            band = [(0, 200), (200, 1000), (1000, 5000), (5000, 10000)]
            in_band = {f"{a}-{b}Hz": float(np.sqrt(np.sum(
                spec[(fbin >= a) & (fbin < b)] ** 2) / 2)) for a, b in band}
            rows.append({
                "synchronised": sync, "seed": seed, "adc_phase_us": ph * 1e6,
                "error_spectrum_A_rms": in_band,
                "iq_mean_measured_A": float(np.mean(meas)),
                "iq_mean_true_A": float(np.mean(avg[n // 2:])),
                "bias_A": float(np.mean(err)),
                "noise_A_rms": float(np.std(err)),
                "noise_LSB": float(np.std(err)) / (3.3 / 4096 / SG),
                "i_rms_A": float(np.sqrt(np.mean(rec["i"][tail] ** 2))),
            })
    free = [r for r in rows if not r["synchronised"]]
    sync = [r for r in rows if r["synchronised"]]
    nf = float(np.mean([r["noise_A_rms"] for r in free]))
    ns = float(np.mean([r["noise_A_rms"] for r in sync]))
    out = {
        "question": "Q8",
        "adc": {"n_channels": 4, "t_conv_us": 2.0,
                "channel_refresh_us": 8.0,
                "resolution_LSB_mA": 3.3 / 4096 / SG * 1e3,
                "aperture_bracket_ns": [adcm["t_aperture"]["min"] * 1e9,
                                        adcm["t_aperture"]["max"] * 1e9]},
        "runs": rows,
        "free_running_noise_A_rms": nf,
        "synchronised_noise_A_rms": ns,
        "improvement_factor": (nf / ns) if ns else None,
        "free_running_bias_A": float(np.mean([r["bias_A"] for r in free])),
        "synchronised_bias_A": float(np.mean([r["bias_A"] for r in sync])),
        "error_spectrum_A_rms": {
            "free_running": {k: float(np.mean([r["error_spectrum_A_rms"][k]
                                               for r in free]))
                             for k in free[0]["error_spectrum_A_rms"]},
            "synchronised": {k: float(np.mean([r["error_spectrum_A_rms"][k]
                                               for r in sync]))
                             for k in sync[0]["error_spectrum_A_rms"]},
            "note": ("the current loop's own bandwidth is about 1 kHz, so "
                     "error below that is what it will try to follow and "
                     "error above it is what the low-pass takes out")},
        "criterion": ("synchronised sampling shown to cut corrupted samples "
                      "to zero and noise by a stated factor"),
        "seconds": round(time.time() - t0, 1),
    }
    _plot_q8(out)
    out["figure"] = "p6_sampling.png"
    return out


def _period_average(rec, iq_true, T, n):
    """The mean of i_q over each PWM period, time-weighted."""
    t = rec["t"]
    out = np.zeros(n)
    dt = np.diff(np.append(t, t[-1] + (t[-1] - t[-2] if len(t) > 1 else T)))
    for k in range(n):
        m = (t >= k * T) & (t < (k + 1) * T)
        if m.any():
            out[k] = float(np.sum(iq_true[m] * dt[m]) / np.sum(dt[m]))
        elif k:
            out[k] = out[k - 1]
    return out


def _iq_of(rec):
    ia = rec["i"][:, 0]
    ib = rec["i"][:, 1]
    al = ia
    be = (ia + 2 * ib) / math.sqrt(3.0)
    th = rec["theta"]
    return -al * np.sin(th) + be * np.cos(th)


def _plot_q8(out):
    fig, ax = plt.subplots(figsize=(6.2, 4), dpi=150)
    for sync, lab in ((False, "free-running, round-robin"),
                      (True, "PWM-synchronised at the counter zero")):
        v = [r["noise_A_rms"] for r in out["runs"] if r["synchronised"] == sync]
        ax.plot(range(len(v)), v, marker="o", ms=4, lw=1, label=lab)
    ax.set_xlabel("run (different sampling phase)")
    ax.set_ylabel("i_q measurement error, A rms")
    ax.set_title("what the current loop is given to work with", fontsize=9)
    ax.legend(fontsize=7); ax.grid(alpha=.3)
    fig.tight_layout()
    fig.savefig(paths.FIGS / "p6_sampling.png")
    plt.close(fig)


# =================================================================== Q9 =====
def q9(quick=False):
    t0 = time.time()
    import geometry as G
    out = {"question": "Q9",
           "estimate": {
               "V_distortion_with_software_dead_zone_V": VB * 1.5e-6 / 50e-6,
               "V_distortion_hardware_only_V": VB * 560e-9 / 50e-6,
               "note": ("V_bus x t_dead / T.  The software dead_zone of 0.02 "
                        "is 1 us per edge and the EG2103 adds 560 ns, so the "
                        "two together are about 1.56 us of the 50 us period")}}
    rows = []
    dz_list = [0.0, 0.005, 0.01, 0.02] if not quick else [0.0, 0.02]
    speeds = [500.0, 2000.0] if not quick else [2000.0]
    currents = [I_PEAK / 4, I_PEAK] if not quick else [I_PEAK]
    p = design_motor()
    g = control.tuned_gains(p, 1000.0)
    for dz in dz_list:
        for rpm in speeds:
            for iq in currents:
                inv = Inverter(v_bus=VB, dead_zone=dz)
                adc = ADC(n_channels=4)
                w = 2 * math.pi * rpm / 60.0
                r = Run(p, inv, adc, g, v_bus=VB, sense_gain=SG, i_q_target=iq,
                        omega0=w, mechanical=False)
                # the THD is measured on the second half of the run, so the
                # run needs eight electrical periods for that half to hold
                # four -- enough for the fundamental to be a resolved bin
                t_run = max(0.03 if not quick else 0.02, 8.0 / max(
                    w * p["pole_pairs"] / (2 * math.pi), 1.0))
                rec = r.run(t_run)
                tail = rec["t"] > rec["t"][-1] * 0.5
                f_e = w * p["pole_pairs"] / (2 * math.pi)
                T = rec["torque"][tail]
                rows.append({
                    "dead_zone": dz,
                    "t_dead_sw_us": dz * 50.0,
                    "rpm": rpm, "iq_target_A": iq,
                    "f_electrical_Hz": f_e,
                    "iq_measured_A": float(np.mean(rec["iq_meas"][-100:])),
                    "iq_true_A": float(np.mean(_iq_of(rec)[tail])),
                    "i_rms_A": float(np.sqrt(np.mean(rec["i"][tail] ** 2))),
                    "THD": _thd(rec["i"][tail, 0], rec["t"][tail], f_e),
                    "torque_mean_Nm": float(np.mean(T)),
                    "torque_ripple_pct": float(np.std(T) / max(abs(np.mean(T)),
                                                               1e-9) * 100),
                    "u_q_V": float(np.mean(rec["uq"][-50:])),
                })
    out["sweep"] = rows
    base = [r for r in rows if r["dead_zone"] == 0.0]
    worst = [r for r in rows if r["dead_zone"] == max(dz_list)]
    if base and worst:
        out["torque_loss_from_software_dead_zone_pct"] = float(
            (np.mean([r["torque_mean_Nm"] for r in base])
             - np.mean([r["torque_mean_Nm"] for r in worst]))
            / max(np.mean([r["torque_mean_Nm"] for r in base]), 1e-9) * 100)
    # the duty extremes the minimum pulse width makes unreachable
    inv = Inverter(v_bus=VB, dead_zone=0.0)
    reach = []
    for d in np.linspace(0.0, 0.06, 31):
        w_ = inv.windows([d, d, d])[0]
        reach.append({"duty": float(d), "reachable": w_ is not None})
    first = next((r["duty"] for r in reach if r["reachable"]), None)
    out["min_duty_reachable"] = first
    out["min_duty_note"] = (
        f"below {first * 100:.2f} % duty the commanded pulse is shorter than "
        f"the EG2103's 560 ns minimum and the high side never turns on; the "
        f"same applies by symmetry at the top of the range")
    out["recommendation"] = None
    out["seconds"] = round(time.time() - t0, 1)
    _plot_q9(rows)
    out["figure"] = "p6_deadtime.png"
    return out


def _plot_q9(rows):
    fig, ax = plt.subplots(1, 2, figsize=(11, 4), dpi=150)
    dz = sorted({r["dead_zone"] for r in rows})
    for rpm in sorted({r["rpm"] for r in rows}):
        sel = [r for r in rows if r["rpm"] == rpm
               and r["iq_target_A"] == max(x["iq_target_A"] for x in rows)]
        sel.sort(key=lambda r: r["dead_zone"])
        ax[0].plot([r["dead_zone"] * 50 for r in sel],
                   [r["THD"] * 100 for r in sel], marker="o", ms=4, lw=1,
                   label=f"{rpm:.0f} rpm")
        ax[1].plot([r["dead_zone"] * 50 for r in sel],
                   [r["torque_ripple_pct"] for r in sel], marker="o", ms=4,
                   lw=1, label=f"{rpm:.0f} rpm")
    ax[0].set_xlabel("software dead zone (us per edge)")
    ax[0].set_ylabel("phase-current THD (%)")
    ax[1].set_xlabel("software dead zone (us per edge)")
    ax[1].set_ylabel("torque ripple (% rms)")
    for a in ax:
        a.grid(alpha=.3); a.legend(fontsize=7)
    fig.suptitle("dead time: the software dead_zone on top of the EG2103's "
                 "560 ns", fontsize=9)
    fig.tight_layout()
    fig.savefig(paths.FIGS / "p6_deadtime.png")
    plt.close(fig)


# ================================================================== Q10 =====
def q10_consequence(quick=False):
    """What the angle error P5 found does to the torque."""
    r5 = jsonio.read("P5") or {}
    err_deg = ((r5.get("Q10") or {}).get("angle_error") or {}).get("peak_deg")
    out = {"question": "Q10 (consequence)",
           "angle_error_peak_deg": err_deg}
    if err_deg is None:
        out["error"] = "P5 has not been run"
        return out
    # a constant angle error e reduces torque by cos(e) and puts current on d
    e = math.radians(err_deg)
    out["torque_loss_pct"] = (1 - math.cos(e)) * 100
    out["i_d_error_A"] = I_PEAK * math.sin(e)
    # the error is periodic in the electrical angle, so it makes ripple at the
    # electrical frequency and its harmonics
    sweep = ((r5.get("Q10") or {}).get("angle_error") or {}).get("sweep", [])
    if sweep:
        e_t = np.array([s["err_deg"] for s in sweep])
        out["torque_ripple_from_angle_pct"] = float(
            np.std(np.cos(np.radians(e_t))) * 100)
        out["angle_error_rms_deg"] = float(np.sqrt(np.mean(e_t ** 2)))
    out["note"] = ("a current-dependent angle error is a torque error that "
                   "moves with the current, so it shows up as a gain error in "
                   "the torque loop rather than as noise")
    return out


# ================================================================== Q14 =====
def q14(quick=False):
    """Regeneration: a decel event pumping the bus, and what the firmware's
    fold-back (63-66 V on board A, 52-56 V on board S) can and cannot catch."""
    t0 = time.time()
    B = board.P()
    tvs = jsonio.model(B["tvs_bus"])
    g0, g1 = B["guard"]
    out = {"question": "Q14",
           "criterion": "reported; no brake chopper exists on this board",
           "V_bus_V": VB, "TVS": tvs["part"],
           "TVS_V_BR_V": [tvs["V_BR"]["min"], tvs["V_BR"]["max"]]}
    if B["bulk"]["kind"] == "cans":
        can = jsonio.model(B["bulk"]["model"])
        built = len(B["bulk"]["refs"]) * can["C_nominal"]["value"] * 1e6
        bulks = [built] if quick else [built, 300.0, 470.0, 1000.0]
        out["C_bulk_as_built_uF"] = built
    else:
        bulks = [300.0] if quick else [100.0, 300.0, 400.0, 1000.0]
    rows = []
    for c_bulk_uF in bulks:
        for J in ([1e-3] if quick else [1e-4, 1e-3, 1e-2]):
            # a decel from rated speed at the design-point current
            p = design_motor()
            w0 = 2 * math.pi * p["rated_rpm"] / 60.0
            ke = p["ke"]
            pp = p["pole_pairs"]
            # braking torque at -I_PEAK on the q axis
            T = 1.5 * pp * ke * I_PEAK
            # mechanical energy available
            E_mech = 0.5 * J * w0 ** 2
            # what the bulk can absorb between the bus and the TVS breakdown
            v_br = tvs["V_BR"]["min"]
            C = c_bulk_uF * 1e-6
            E_cap = 0.5 * C * (v_br ** 2 - VB ** 2)
            E_guard = 0.5 * C * (g0 ** 2 - VB ** 2)
            # the decel time and the power fed back
            t_dec = J * w0 / T if T else float("inf")
            P_regen = T * w0                       # at the start of the decel
            dvdt = P_regen / (C * VB)              # V/s while the bus rises
            t_to_tvs = (v_br - VB) / dvdt if dvdt else float("inf")
            t_to_guard = (g0 - VB) / dvdt if dvdt else float("inf")
            rows.append({
                "C_bulk_uF": c_bulk_uF, "J_kgm2": J,
                "omega0_rad_s": w0, "T_brake_Nm": T,
                "E_mech_J": E_mech, "E_cap_to_TVS_J": E_cap,
                "E_cap_to_guard_J": E_guard,
                "P_regen_start_W": P_regen,
                "dV_dt_V_per_s": dvdt,
                "t_to_guard_ms": t_to_guard * 1e3,
                "t_to_TVS_ms": t_to_tvs * 1e3,
                "t_decel_ms": t_dec * 1e3,
                "TVS_energy_J": max(E_mech - E_cap, 0.0),
                "bus_guard_catches": bool(t_to_tvs - t_to_guard > 50e-6),
                "guard_reaction_us": 50.0,
            })
    out["sweep"] = rows
    out["bus_guard"] = {
        "fold_start_V": g0, "fold_full_V": g1,
        "runs_at_Hz": 20e3,
        "reaction_time_us": 50.0,
        "note": (f"the firmware folds current_limit to zero across "
                 f"{g0:.0f}-{g1:.0f} V and runs that check every loop() at "
                 f"about the FOC rate, so it reacts in one 50 us period plus "
                 f"the current loop's own settling")}
    out["brake_chopper"] = ("the sibling has a fourth leg with a 15 Ohm / "
                            "100 W resistor and a 300 J budget; servodrive "
                            "has no fourth leg, so the only places for regen "
                            "energy are the bulk and the TVSs -- or the "
                            "battery, when one is connected and accepts "
                            "charge")
    out["seconds"] = round(time.time() - t0, 1)
    return out


# ============================================================ the L4 KAT ====
def control_kat():
    """SPEC.md sec.5.4's L4 test, in two parts.

    First the discretisation itself, against hand arithmetic: SimpleFOC's PID
    uses a Tustin integral and its low-pass uses alpha = Tf/(Tf + dt), and
    both are checked term by term.  Then the closed loop on the RL plant with
    the sibling's own tuned gains, measured with tune.py's own step metrics --
    which is where the disagreement SPEC.md sec.3.1 flags shows up as a number.
    """
    disc = {}
    pid = control.PID(2.0, 10.0, 0.0, float("inf"), float("inf"), 1e-3)
    got = [pid(1.0) for _ in range(3)]
    want = []
    integ = 0.0
    eprev = 0.0
    for _ in range(3):
        integ = integ + 10.0 * 1e-3 * 0.5 * (1.0 + eprev)
        want.append(2.0 * 1.0 + integ)
        eprev = 1.0
    disc["pid_tustin"] = {"got": got, "want": want,
                          "max_abs_error": max(abs(a - b)
                                               for a, b in zip(got, want))}
    lpf = control.LPF(5e-3, 1e-3)
    gotf = [lpf(1.0) for _ in range(3)]
    a = 5e-3 / (5e-3 + 1e-3)
    y = 0.0
    wantf = []
    for _ in range(3):
        y = a * y + (1 - a) * 1.0
        wantf.append(y)
    disc["lpf_alpha"] = {"got": gotf, "want": wantf,
                         "max_abs_error": max(abs(x - y_)
                                              for x, y_ in zip(gotf, wantf))}
    disc["pass"] = bool(disc["pid_tustin"]["max_abs_error"] < 1e-12
                        and disc["lpf_alpha"]["max_abs_error"] < 1e-12)
    R, L = 0.35, 1.0e-3
    Ts = 1 / 20e3
    pid = control.PID(0.36, 1.8, 0.0, float("inf"), 4.0, Ts)
    lpf = control.LPF(0.005, Ts)
    target = 0.3
    n = int(0.5 / Ts)
    i = 0.0
    t = np.arange(n) * Ts
    y = np.zeros(n)
    for k in range(n):
        meas = lpf(i)
        u = pid(target - meas)
        a = math.exp(-R / L * Ts)
        i = u / R + (i - u / R) * a
        y[k] = i
    peak = float(np.max(y))
    over = (peak - target) / target * 100
    k80 = np.argmax(y >= 0.8 * target)
    rise = float(t[k80]) * 1e3 if y.max() >= 0.8 * target else float("nan")
    band = 0.05 * target
    out_of = np.where(np.abs(y - target) > band)[0]
    settle = float(t[min(out_of[-1] + 1, n - 1)]) * 1e3 if len(out_of) else 0.0
    tail = y[int(n * 0.8):]
    ss = abs(float(np.mean(tail)) - target) / target * 100
    return {
        "discretisation": disc,
        "gains": {"P": 0.36, "I": 1.8, "Tf": 0.005},
        "plant": {"R": R, "L": L, "note": "the bench bracket's mid point"},
        "metrics": {"overshoot_pct": over, "rise_time_ms": rise,
                    "settle_time_ms": settle, "ss_error_pct": ss},
        "metric_definitions": ("identical to tune.py's analyze_step: 80 % "
                               "rise, 5 % settling band, steady state from "
                               "the last 20 %"),
        "pass": bool(disc["pass"]),
        "implied_bandwidth_rad_s": 1.8 / R,
        "note": ("the tuner's rule is Kp = omega_c.L, Ki = omega_c.R; with "
                 "Ki = 1.8 and R in the 0.25-0.5 Ohm bracket that is "
                 "omega_c = 3.6-7.2 rad/s, two orders below the 200 rad/s "
                 "tune.py aims for -- which is the disagreement SPEC.md "
                 "sec.3.1 flags between the two ways of reading R"),
    }


def run(quick=False):
    out = {"L4_known_answer": control_kat()}
    out["Q8"] = q8(quick)
    out["Q9"] = q9(quick)
    out["Q10"] = q10_consequence(quick)
    out["Q14"] = q14(quick)
    return out


if __name__ == "__main__":
    print(json.dumps(run(quick=True), indent=1, default=str)[:3000])
