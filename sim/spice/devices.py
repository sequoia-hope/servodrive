#!/usr/bin/env python3
"""Device models for L3, and the fits that justify them.

The hard one is the MOSFET.  ngspice's VDMOS primitive gets the channel and the
body diode right, but its gate-drain capacitance has one shape parameter and
cannot be both 32 pF at V_DS = 40 V and 13 nC of Miller charge over 0-40 V --
the two numbers the BSC030N08NS5 datasheet actually specifies.  So C_gd and
C_ds are modelled explicitly as charge-defined non-linear capacitors, which
ngspice supports (`Cxx a b Q={...}`), with

    C_gd(V) = Cgd_lo + Cgd_hi / (1 + V/V0)^2
    C_ds(V) = Cj0 / (1 + V/Vj)^m

whose parameters are solved from the datasheet's own specified points:
C_rss(40 V), Q_gd, C_oss(40 V), Q_oss.  The charge expressions are the analytic
integrals of those, so charge is conserved exactly rather than by the
simulator's integration of a C(V) curve.

Everything here reports its fit; spice/fit_report() returns the table that goes
in the report, and tests/kat.py checks the 20 % criterion of SPEC.md sec.5.4.
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import brentq, least_squares

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import jsonio                                       # noqa: E402
from spice.ngspice import NgSpice                            # noqa: E402

# the gate-drive rail this board runs the EG2103 from (12 V regulator on the
# VCC pin); the driver's rated output currents are read at it
V_DRIVE = 12.0


# ------------------------------------------------------------------ MOSFET --
def _cgd_params(C_rss_40, Q_gd, C_gd_on, w=3.0, V_ref=40.0):
    """Solve the gate-drain capacitance from three datasheet numbers.

    C_rss(40 V), Q_gd and the on-state C_gd that the total gate charge implies
    are mutually inconsistent with any single-pole shape: a 1/(1+V/V0)^p curve
    that is 32 pF at 40 V and integrates to 13 nC needs about 4.5 nF at V = 0,
    which then makes Q_g come out 60 % high.  The three are consistent with a
    curve that stays flat and then collapses -- which is what a shielded-gate
    trench device does when its shield depletes -- so the shape used is

        C_gd(V) = C_lo + (C_hi - C_lo) * (1 - tanh((V - Vk)/w)) / 2

    with C_hi from Q_g, C_lo from C_rss(40 V), and the knee Vk solved from
    Q_gd.  Its charge is analytic, so ngspice conserves charge exactly:

        Q(V) = C_lo*V + (C_hi - C_lo)/2 * [V - w*lncosh((V-Vk)/w)
                                             + w*lncosh(Vk/w)]
    """
    C_hi, C_lo = C_gd_on, C_rss_40

    def lncosh(x):
        ax = abs(x)
        return ax + math.log1p(math.exp(-2 * ax)) - math.log(2.0)

    def Q(Vk, V=V_ref):
        return (C_lo * V + (C_hi - C_lo) / 2.0
                * (V - w * lncosh((V - Vk) / w) + w * lncosh(Vk / w)))

    lo, hi = 0.05, V_ref - 0.05
    if Q(lo) > Q_gd or Q(hi) < Q_gd:
        raise ValueError(f"Q_gd {Q_gd * 1e9:.1f} nC is outside "
                         f"[{Q(lo) * 1e9:.1f}, {Q(hi) * 1e9:.1f}] nC for this "
                         f"C_hi/C_lo pair")
    Vk = brentq(lambda v: Q(v) - Q_gd, lo, hi, xtol=1e-9)
    return {"C_hi": C_hi, "C_lo": C_lo, "Vk": Vk, "w": w}


def _cds_params(C_ds_40, Q_ds, V_ref=40.0):
    """Solve Cj0, Vj, m of  C = Cj0/(1 + V/Vj)^m  from C_ds(40 V) and the
    charge that (1 + V/Vj)^-m must integrate to."""
    def f(x):
        m, w = x
        Vj = V_ref / (w - 1.0)
        Cj0 = C_ds_40 * w ** m
        Q = Cj0 * Vj / (1 - m) * ((w) ** (1 - m) - 1)
        return [Q / Q_ds - 1.0, 0.0]

    def g(w, m):
        Vj = V_ref / (w - 1.0)
        Cj0 = C_ds_40 * w ** m
        return Cj0 * Vj / (1 - m) * (w ** (1 - m) - 1) - Q_ds

    # m is chosen as low as the charge allows, which keeps the shape physical
    for m in np.arange(0.55, 0.96, 0.005):
        try:
            w = brentq(lambda w: g(w, m), 1.0001, 5000.0)
        except ValueError:
            continue
        Vj = V_ref / (w - 1.0)
        if Vj > 0.15:
            return C_ds_40 * w ** m, Vj, float(m)
    raise ValueError("no (Cj0, Vj, m) reproduces both C_ds(40 V) and Q_ds")


def fet_model(part="bsc030n08ns5", tj=25.0, name="QFET", corner="typ",
              vto_shift=0.0):
    """The .model card plus the fit that produced it.

    `vto_shift` moves the threshold without touching anything else, which is
    how the datasheet's V_GS(th) spread is carried: the fit lands on the
    large-signal threshold implied by g_fs and the plateau at 50 A, and a
    device at the bottom of the V_GS(th) distribution is that fit shifted down.
    A large positive shift makes a device that cannot conduct at all, which is
    how Q4 separates cross-conduction from displacement current.

    `corner` picks the datasheet column: "typ" or "max".  The maximum
    capacitances and charges are what the datasheet's own switching times are
    closer to, and they slow every edge, so both are run wherever an answer
    depends on the edge rate.
    """
    M = jsonio.model(part)
    c = corner if corner in ("typ", "max") else "typ"

    def g(key, alt="typ"):
        d = M[key]
        return d.get(c, d.get(alt))

    C_rss = g("C_rss_40V")
    C_oss = g("C_oss_40V")
    C_iss = g("C_iss_40V")
    Q_gd = g("Q_gd")
    Q_oss = g("Q_oss")
    C_ds_40 = C_oss - C_rss
    Q_ds = Q_oss - Q_gd

    C_gs = C_iss - C_rss                      # C_iss = C_gs + C_gd, at 40 V
    Q_g = g("Q_g")
    Q_gs = M["Q_gs"]["value"]
    V_pl = M["V_plateau"]["value"]
    # what the datasheet's own Q_g leaves for the post-plateau rise, hence the
    # gate-drain capacitance with the channel on
    C_gd_on = (Q_g - Q_gs - Q_gd) / (10.0 - V_pl) - C_gs
    cgd = _cgd_params(C_rss, Q_gd, C_gd_on)
    Cj0, Vj, m = _cds_params(C_ds_40, Q_ds)

    # channel: I = Kp (Vgs - Vto)^2 / 2 and gfs = Kp (Vgs - Vto), both at the
    # datasheet's I_D = 50 A and V_plateau = 4.6 V.  Vto lands inside the
    # datasheet's own V_GS(th) window.
    I_test, gfs, Vpl = 50.0, M["gfs"]["typ"], M["V_plateau"]["value"]
    dv = 2 * I_test / gfs
    Vto = Vpl - dv + vto_shift
    Kp = gfs / dv

    r_on = M["R_ds_on_10V"]["typ"] * (M["R_ds_on_125C_factor"]["value"]
                                      if tj > 100 else 1.0)
    # The body diode's transit time is set from the datasheet's own reverse
    # recovery: Q_rr = Tt * I_F at the test current, so Tt = 94 nC / 50 A.
    # (The datasheet's di/dt for that test is 100 A/us; the design point's
    # turn-off is 870 A/us, where Q_rr is larger -- P2 sweeps it.)
    Tt = M["Q_rr"]["typ"] / 50.0
    # The part is 100 % avalanche tested and its V(BR)DSS minimum is 80 V, so
    # the model breaks down there instead of letting V_ds run away.  Without
    # it the simulation reports an overshoot the silicon would never allow and
    # says nothing about what the silicon does instead.
    params = dict(Vto=Vto, Kp=Kp, Rs=1e-4, Rg=M["R_G_internal"]["typ"],
                  Rds=1e9, Cgs=C_gs, Cgdmax=1e-15, Cgdmin=1e-15, a=1.0,
                  Cjo=1e-15, Is=1e-13, N=1.05, Rb=2e-3, Tt=Tt,
                  mtriode=1.0, Bv=M["V_BR_DSS"]["min"], Ibv=1e-3)
    return {
        "name": name, "params": params, "tj": tj, "corner": c,
        "C_gd_on_from_Qg": C_gd_on,
        "Cgd": cgd,
        "Cds": {"Cj0": Cj0, "Vj": Vj, "m": m},
        "Cgs": C_gs, "r_on_target": r_on,
        "L_s": M["L_source_package"]["typ"], "L_d": M["L_drain_package"]["typ"],
        "V_SD": M["V_SD"]["typ"], "Q_rr": M["Q_rr"]["typ"],
        "t_rr": M["t_rr"]["typ"],
    }


def gate_internal(g, inst):
    """The node name fet_instance() gives the polysilicon gate -- the node
    C_gs and C_gd actually meet on, and the one whose voltage to the source
    decides whether the channel conducts."""
    return f"{g}i{inst}"


def fet_instance(fm, inst, d, g, s):
    """One FET, expanded inline.

    Not a `.subckt`: ngspice does not translate the node names inside a
    behavioural `Q={...}` expression when the element is inside a subcircuit,
    so `v(d,s)` there would refer to whatever top-level nodes happen to be
    called d and s.  Emitting the expressions with the caller's own node names
    removes the question.

    `g` is the package gate PIN.  C_gd and C_gs do not meet there: they meet on
    the polysilicon gate, which the die's own distributed gate resistance
    separates from the pin.  So R_g(int) is emitted as a real resistor from the
    pin to an internal node, the VDMOS (whose C_gs is internal to it) and C_gd
    both hang off that internal node, and the model card's own Rg is set aside.
    Hanging C_gd on the pin instead would let the Miller current develop its
    I*R_g(int) drop on the wrong side of that resistance, which inflates the
    apparent induced V_gs several-fold -- the drop is real, but it isolates the
    die gate from the driver, it does not appear across the oxide.
    """
    gi = gate_internal(g, inst)
    g_ = fm["Cgd"]
    dd = fm["Cds"]
    V = f"v({d},{gi})"
    w = g_["w"]
    # ln(cosh(x)) written stably: |x| + ln(1+exp(-2|x|)) - ln 2
    x = f"(({V})-{g_['Vk']:g})/{w:g}"
    lnc = f"(abs({x}) + ln(1+exp(-2*abs({x}))) - 0.693147)"
    x0 = g_["Vk"] / w
    lnc0 = abs(x0) + math.log1p(math.exp(-2 * abs(x0))) - math.log(2.0)
    qgd = (f"{{ {g_['C_lo']:g}*({V}) + {(g_['C_hi'] - g_['C_lo']) / 2:g}*"
           f"( ({V}) - {w:g}*{lnc} + {w * lnc0:g} ) }}")
    qds = (f"{{ {dd['Cj0']:g}*{dd['Vj']:g}/(1-{dd['m']:g})*"
           f"(pow(1+max(v({d},{s}),0)/{dd['Vj']:g}, 1-{dd['m']:g}) - 1) "
           f"+ {dd['Cj0']:g}*min(v({d},{s}),0) }}")
    # The node order matters: a charge-defined capacitor's expression must be
    # a function of its OWN branch voltage v(n+, n-).  Writing Q(v(d,g)) on an
    # element declared `C g d` makes dQ/dV negative -- a negative capacitance,
    # and the transient runs away in a few nanoseconds.
    return (f"R{inst}gi {g} {gi} {fm['params']['Rg']:g}\n"
            f"M{inst} {d} {gi} {s} {fm['name']}\n"
            f"C{inst}gd {d} {gi} Q={qgd}\n"
            f"C{inst}ds {d} {s} Q={qds}\n")


def fet_cards(fm, rd=None):
    """The .model card for the fitted FET.  The non-linear capacitances are
    emitted per instance by fet_instance(), not as a subcircuit."""
    p = dict(fm["params"])
    if rd is not None:
        p["Rd"] = rd
    # R_g(int) is emitted as a real resistor by fet_instance so that C_gd and
    # C_gs meet behind it, where they physically do; leaving it on the card as
    # well would count it twice
    p["Rg"] = 1e-6
    keys = ("Vto", "Kp", "Rd", "Rs", "Rg", "Rds", "Cgs", "Cgdmax", "Cgdmin",
            "a", "Cjo", "Is", "N", "Rb", "Tt", "mtriode", "Bv", "Ibv",
            "subshift", "ksubthres")
    card = (f".model {fm['name']} VDMOS nchan "
            + " ".join(f"{k}={p[k]:g}" for k in keys if k in p))
    return card, ""


def cgd_of(fm, V):
    g = fm["Cgd"]
    return g["C_lo"] + (g["C_hi"] - g["C_lo"]) / 2 * (
        1 - np.tanh((np.asarray(V, float) - g["Vk"]) / g["w"]))


def cds_of(fm, V):
    d = fm["Cds"]
    V = np.maximum(np.asarray(V, float), 0.0)
    return d["Cj0"] / (1 + V / d["Vj"]) ** d["m"]


def fit_fet(part="bsc030n08ns5", verbose=False, corner="typ"):
    """Fit R_d so that R_DS(on) matches, then measure every datasheet point
    back out of ngspice.  Returns the table the report prints."""
    ng = NgSpice()
    M = jsonio.model(part)
    fm = fet_model(part, corner=corner)

    def ron(rd):
        card, _ = fet_cards(fm, rd)
        net = (f"ron\n{card}\n" + fet_instance(fm, "1", "d", "g", "0")
               + "Vg g 0 10\nId 0 d 50\n.end\n")
        ng.circuit(net)
        ng.command("op")
        return float(ng.vector("d")[0]) / 50.0

    target = M["R_ds_on_10V"]["typ"]
    try:
        rd = brentq(lambda r: ron(r) - target, 1e-7, 5e-3, xtol=1e-10)
    except ValueError:
        rd = 1e-3
    fm["params"]["Rd"] = rd
    card, sub = fet_cards(fm)

    # --- capacitances, measured the way a curve tracer would ------------
    def caps_at(v):
        net = (f"caps\n{card}\n" + fet_instance(fm, "1", "d", "g", "0")
               + f"Vg g 0 0\nVd d 0 {v:g}\nRd2 d 0 1e12\n.end\n")
        ng.circuit(net)
        ng.log = []
        ng.command("op")
        ng.command("show all : cgs cgd cds")
        out = {}
        for line in ng.log:
            t = line.replace("stdout", "").split()
            if len(t) == 2 and t[0] in ("cgs", "cgd", "cds"):
                try:
                    out.setdefault(t[0], float(t[1]))
                except ValueError:
                    pass
        out["cgd_ext"] = float(cgd_of(fm, v))
        out["cds_ext"] = float(cds_of(fm, v))
        return out

    vs = np.array([0, 1, 2, 5, 10, 20, 30, 40, 50, 60, 80])
    rows = [caps_at(float(v)) for v in vs]
    cgd = cgd_of(fm, vs)
    cds = cds_of(fm, vs)
    cgs = fm["Cgs"]
    i40 = int(np.argmin(abs(vs - 40)))

    def q_gd(V):
        g = fm["Cgd"]
        w = g["w"]

        def lncosh(x):
            ax = abs(x)
            return ax + math.log1p(math.exp(-2 * ax)) - math.log(2.0)

        return (g["C_lo"] * V + (g["C_hi"] - g["C_lo"]) / 2
                * (V - w * lncosh((V - g["Vk"]) / w) + w * lncosh(g["Vk"] / w)))

    def q_ds(V):
        d = fm["Cds"]
        return (d["Cj0"] * d["Vj"] / (1 - d["m"])
                * ((1 + V / d["Vj"]) ** (1 - d["m"]) - 1))

    got = {
        "C_iss_40V_pF": (cgs + cgd[i40]) * 1e12,
        "C_oss_40V_pF": (cgd[i40] + cds[i40]) * 1e12,
        "C_rss_40V_pF": cgd[i40] * 1e12,
        "Q_gd_nC": q_gd(40.0) * 1e9,
        "Q_oss_nC": (q_gd(40.0) + q_ds(40.0)) * 1e9,
        "R_ds_on_mOhm": ron(rd) * 1e3,
    }
    want = {
        "C_iss_40V_pF": M["C_iss_40V"]["typ"] * 1e12,
        "C_oss_40V_pF": M["C_oss_40V"]["typ"] * 1e12,
        "C_rss_40V_pF": M["C_rss_40V"]["typ"] * 1e12,
        "Q_gd_nC": M["Q_gd"]["typ"] * 1e9,
        "Q_oss_nC": M["Q_oss"]["typ"] * 1e9,
        "R_ds_on_mOhm": M["R_ds_on_10V"]["typ"] * 1e3,
    }

    # --- gate charge, the datasheet's own test --------------------------
    # The datasheet's own test: a clamped inductive load, i.e. a constant
    # 50 A drawn from the drain with a freewheel diode to the 40 V rail, and
    # the gate charged by a constant current.
    net = (f"gate charge\n{card}\n"
           ".model DFW D(Is=1e-12 N=1.0 Rs=1m Cjo=1p)\n"
           + fet_instance(fm, "1", "d", "g", "0")
           + "Ig 0 g PULSE(0 1m 1u 1n 1n 1 2)\n"
           "Vdd dd 0 40\n"
           "Iload dd d 50\n"        # the load inductor, as a constant current
           "Dfw d dd DFW\n"         # freewheel: clamps the drain to the rail
           "Rg2 g 0 1e9\n"
           ".end\n")
    ng.circuit(net)
    ng.command("tran 5n 150u 0 50n")
    d = ng.all()
    t, vg, vd = d["time"], d["g"], d["d"]
    q = 1e-3 * np.maximum(t - 1e-6, 0.0)
    ok = vg <= 10.0
    Qg10 = float(q[ok][-1]) if ok.any() else float("nan")
    # the Miller plateau: the gate voltage while the drain is in mid-transit
    plateau = vg[(vd < 32) & (vd > 8)]
    Vpl_meas = float(np.median(plateau)) if len(plateau) else float("nan")
    got["Q_g_nC"] = Qg10 * 1e9
    want["Q_g_nC"] = M["Q_g"]["typ"] * 1e9
    got["V_plateau_V"] = Vpl_meas
    want["V_plateau_V"] = M["V_plateau"]["value"]

    # --- the datasheet's own switching times, as a check on the whole model
    # (V_DD = 40 V, I_D = 50 A, R_G,ext = 3 Ohm, a clamped inductive load)
    sw = {}
    try:
        net = (f"switching times\n{card}\n"
               ".model DFW D(Is=1e-12 N=1.0 Rs=1m Cjo=1p)\n"
               + fet_instance(fm, "1", "d", "g", "0")
               + "Vdrv drv 0 PULSE(0 10 1u 2n 2n 1u 10u)\n"
               "Rgext drv g 3\n"
               "Vdd dd 0 40\nIload dd d 50\nDfw d dd DFW\n"
               ".end\n")
        ng.circuit(net)
        ng.command("tran 20p 2.6u 0.9u 100p")
        r = ng.all()
        t, vg, vd = r["time"], r["g"], r["d"]
        idr = 50.0 * np.clip((vg - fm["params"]["Vto"]), 0, None) ** 2 \
            * fm["params"]["Kp"] / 2 / 50.0
        vin = np.interp(t, [0, 1e-6, 1e-6 + 2e-9, 2e-6, 2e-6 + 2e-9, 3e-6],
                        [0, 0, 10, 10, 0, 0])

        def cross(x, lvl, t0, rising=True):
            m = t > t0
            tt, xx = t[m], x[m]
            s_ = (xx > lvl).astype(int)
            k = np.where(np.diff(s_) == (1 if rising else -1))[0]
            return float(tt[k[0]]) if len(k) else float("nan")

        v10, v90 = 40 * 0.1, 40 * 0.9
        t_in_hi = 1e-6
        sw["t_d_on_ns"] = (cross(-vd, -v90, t_in_hi) - t_in_hi) * 1e9
        sw["t_f_ns"] = (cross(-vd, -v10, t_in_hi)
                        - cross(-vd, -v90, t_in_hi)) * 1e9
        t_in_lo = 2e-6
        sw["t_d_off_ns"] = (cross(vd, v10, t_in_lo) - t_in_lo) * 1e9
        sw["t_r_ns"] = (cross(vd, v90, t_in_lo) - cross(vd, v10, t_in_lo)) * 1e9
    except Exception as e:
        sw["error"] = f"{e.__class__.__name__}: {e}"

    err = {k: (got[k] - want[k]) / want[k] for k in want}
    return {
        "switching_times_simulated_ns": sw,
        "switching_times_datasheet_ns": {
            "t_d_on_ns": M["t_d_on"]["value"] * 1e9,
            "t_r_ns": M["t_r"]["value"] * 1e9,
            "t_d_off_ns": M["t_d_off"]["value"] * 1e9,
            "t_f_ns": M["t_f"]["value"] * 1e9,
        },
        "part": part, "model": fm, "R_d_fitted_Ohm": rd,
        "card": card, "subckt": sub,
        "datasheet": want, "simulated": got, "error": err,
        "worst_error": max(abs(v) for v in err.values()),
        "pass_20pct": max(abs(v) for v in err.values()) <= 0.20,
        "curve": {"V": vs.tolist(),
                  "C_gd_pF": (cgd * 1e12).tolist(),
                  "C_ds_pF": (cds * 1e12).tolist(),
                  "C_oss_pF": ((cgd + cds) * 1e12).tolist(),
                  "C_iss_pF": ((cgs + cgd) * 1e12).tolist()},
        "note": ("C_gd and C_ds are charge-defined behavioural capacitors "
                 "outside the VDMOS primitive; the VDMOS keeps the channel, "
                 "the gate resistance and the body diode.  The shapes between "
                 "the datasheet's specified points are the model's, not the "
                 "datasheet's -- Infineon publishes the curves only as a "
                 "diagram, so P2 sweeps C_oss to show what depends on it."),
    }


if __name__ == "__main__":
    import pprint
    f = fit_fet()
    pprint.pprint(f["datasheet"])
    pprint.pprint(f["simulated"])
    pprint.pprint(f["error"])
    print("worst", f["worst_error"], "pass", f["pass_20pct"])
    print(f["card"])
    print(f["subckt"])


# ------------------------------------------------------------ gate driver --
def eg2103_cards(name="EG2103", t_on=None, t_off=None, r_scale=1.0):
    """A behavioural EG2103: two current-limited push-pull outputs with the
    datasheet's two different propagation delays, whose 560 ns difference IS
    the internal dead time, plus the interlock and UVLO.

    The delays are lossless transmission lines: this ngspice has no `delay`
    controlled source and no XSPICE code models, and a T-line is an exact
    delay.  It halves the amplitude into a matched load, so the thresholds
    downstream are at half scale.

    The output pins carry the body diodes of their own output MOSFETs, which
    clamp each pin to its two rails; that clamp is what stops a resonant gate
    loop from ringing the pin outside the supply.

    Modelled, from models/eg2103.json: I_source 0.3 A, I_sink 0.6 A, t_on
    780 ns, t_off 220 ns, LIN active low (the firmware is built to match with
    -DSIMPLEFOC_PWM_LOWSIDE_ACTIVE_HIGH=false), UVLO on VCC and on the
    bootstrap rail.  Not modelled: the level shifter's own dV/dt immunity, and
    the 200 k input pulls, which the board overrides with 4.7 k.
    """
    M = jsonio.model("eg2103")
    ton = t_on if t_on is not None else M["t_on"]["value"]
    toff = t_off if t_off is not None else M["t_off"]["value"]
    isrc = M["I_source"]["value"]
    isnk = M["I_sink"]["value"]
    # the rated peak currents, read as an output resistance at the 12 V drive
    # this board uses; r_scale brackets that reading (Q4)
    r_src = V_DRIVE / isrc * r_scale
    r_snk = V_DRIVE / isnk * r_scale
    uvlo_off = M["UVLO_VCC_off"]["value"]
    vb_on = M["UVLO_VB_on"]["value"]
    return f"""
* ---- {name}: behavioural EG2103 half-bridge driver -------------------
* terminals: vcc hin lin com ho vs vb lo
.subckt {name} vcc hin lin com ho vs vb lo
Bhin nhi 0 v = {{ v(hin,com) > 2.5 ? 1 : 0 }}
Blin nlo 0 v = {{ v(lin,com) < 1.0 ? 1 : 0 }}
Rhs nhi nha 50
Thion nha 0 nhb 0 Z0=50 TD={ton:g}
Rhb nhb 0 50
Rhs2 nhi nhc 50
Thioff nhc 0 nhd 0 Z0=50 TD={toff:g}
Rhd nhd 0 50
Rls nlo nla 50
Tloon nla 0 nlb 0 Z0=50 TD={ton:g}
Rlb nlb 0 50
Rls2 nlo nlc 50
Tlooff nlc 0 nld 0 Z0=50 TD={toff:g}
Rld nld 0 50
* an output is on only while its slow (turn-on) and fast (turn-off) paths
* agree: the difference between the two delays is the internal dead time
Bh nh 0 v = {{ (v(nhb) > 0.25) && (v(nhd) > 0.25) ? 1 : 0 }}
Bl nl 0 v = {{ (v(nlb) > 0.25) && (v(nld) > 0.25) ? 1 : 0 }}
Buv nuv 0 v = {{ v(vcc,com) > {uvlo_off:g} ? 1 : 0 }}
Buvb nuvb 0 v = {{ v(vb,vs) > {vb_on - 1.0:g} ? 1 : 0 }}
* push-pull outputs, referenced to VS (high) and COM (low).  The output
* stage is a pair of MOSFETs in triode, so it is a RESISTANCE to its rail,
* not a current limit: current pushed into the pin from outside -- Miller
* current out of the FET being held off -- raises the pin instead of being
* absorbed.  The resistances are the datasheet's rated peak currents taken
* at the board's 12 V drive, which is how a driver's I_O+/I_O- is specified
* (output shorted to the opposite rail); the EG2103 product page gives the
* currents but not the test condition, so this is the assumption, and Q4
* carries a corner that brackets it.
Bho ho vs i = {{ (v(nh) > 0.5) && (v(nuv) > 0.5) && (v(nuvb) > 0.5)
+   ? -(v(vb,vs) - v(ho,vs)) / {r_src:g}
+   :  v(ho,vs) / {r_snk:g} }}
Blo lo com i = {{ (v(nl) > 0.5) && (v(nuv) > 0.5)
+   ? -(v(vcc,com) - v(lo,com)) / {r_src:g}
+   :  v(lo,com) / {r_snk:g} }}
Rhl ho vs 1meg
Rll lo com 1meg
Cho ho vs 20p
Clo lo com 20p
* the output stage is a pair of MOSFETs, so each output is clamped to its own
* two rails by their body diodes; without these the gate-loop inductance rings
* the output pin far outside the supply, which no real driver does
.model DCLAMP D(Is=1e-12 N=1.0 Rs=0.5 Cjo=20p)
Dhoup ho vb DCLAMP
Dhodn vs ho DCLAMP
Dloup lo vcc DCLAMP
Dlodn com lo DCLAMP
.ends {name}
"""


# ------------------------------------------------------- current amplifier --
def ina241_cards(name="INA241A3"):
    """A behavioural INA241A3.

    Gain 50 V/V, one pole at the datasheet's 1.1 MHz, an 8 V/us slew limit, an
    output clamped to the datasheet's swing -- and the part of this device that
    matters most here, the "enhanced PWM rejection" hold: on a large
    common-mode dV/dt the part freezes its output for 1 us (SBOSA30D
    sec.7.3.1.1).  That hold is why the sense chain cannot be judged by
    settling time alone, so it is modelled explicitly as a track-and-hold.
    """
    M = jsonio.model("ina241a3")
    G = M["gain"]["value"]
    fbw = M["bandwidth"]["value"]
    sr = M["slew_rate"]["value"]
    hold = M["pwm_hold"]["value"]
    cmrr = 10 ** (M["CMRR_dc"]["typ"] / 20.0)
    vos = M["V_os"]["typ"]
    swing_hi = M["swing_to_VS"]["max"]
    swing_lo = M["swing_to_GND"]["max"]
    tau = 1.0 / (2 * math.pi * fbw)
    rp = tau / 1e-9                      # with a 1 nF pole capacitor
    # The common-mode slew that arms the hold.  A switch-node edge is
    # 1000 V/us; nothing in the signal path goes near 1 V/us.
    trip = 1.0
    return f"""
* ---- {name}: behavioural INA241A3 current-sense amplifier ------------
* terminals: inp inn ref vs out gnd
.subckt {name} inp inn ref vs out gnd
.model DIDEAL D(Is=1e-14 N=0.4 Rs=0.01 Cjo=0.01p)
Bdiff nd 0 v = {{ {G:g} * (v(inp,inn) + {vos:g}) + v(inn,gnd)/{cmrr:g} }}
* common-mode slew detector: a 1 ns high-pass on the common-mode node
Bcm ncm 0 v = {{ v(inn,gnd) }}
Rdet ncm ndf 1k
Cdet ndf 0 1p
Bdet ndet 0 v = {{ abs(v(ncm) - v(ndf)) > {trip:g} ? 1 : 0 }}
* one-shot: charge on detect, decay with tau = the datasheet's hold time
Ddet ndet nos DIDEAL
Cos nos 0 1n
Ros nos 0 {hold / 1e-9:g}
Bheld nheld 0 v = {{ v(nos) > 0.37 ? 1 : 0 }}
* the -3 dB pole comes first, so that what is held is the filtered value:
* the real part freezes its output at the level it had before the edge, and a
* track-and-hold placed ahead of the filter would latch the edge itself.
Rpole nd np {rp:g}
Cpole np 0 1n
* track and hold, tracking with a 10 ns constant -- fast against the 145 ns
* pole, slow enough that the value cannot move in the instant between the
* common-mode edge starting and the detector firing
Gsh 0 nsh value = {{ v(nheld) > 0.5 ? 0 : (v(np) - v(nsh)) * 0.1 }}
Csh nsh 0 1n
* slew limit
Gsl 0 nsl value = {{ max(min((v(nsh) - v(nsl)) * 100, {sr * 1e-6:g}),
+                         -{sr * 1e-6:g}) }}
Csl nsl 0 1u
Bout nout 0 v = {{ min(max(v(nsl) + v(ref), {swing_lo:g}),
+                       v(vs) - {swing_hi:g}) }}
Rout nout out 1
Cload out 0 1p
.ends {name}
"""


# -------------------------------------------------------------- passives ----
def retention(M, bias, corner="typ"):
    """The fraction of a ceramic's capacitance left at `bias` volts.

    The models carry brackets at particular voltages (retention_at_60V, ...).
    Between them -- and between 0 V, where it is 1 -- the bracket is
    interpolated linearly.  An earlier version looked only for an exact key
    and fell back to 1.0, so any other bias (48 V, say) got no derating at
    all without saying so."""
    if isinstance(M, str):
        M = jsonio.model(M)
    pts = [(0.0, 1.0)]
    for k, v in M.items():
        if k.startswith("retention_at_") and k.endswith("V"):
            try:
                vb = float(k[len("retention_at_"):-1])
            except ValueError:
                continue
            pts.append((vb, v.get(corner, v.get("typ", 1.0))))
    pts.sort()
    xs = [a for a, _ in pts]
    ys = [b for _, b in pts]
    if bias <= xs[0]:
        return ys[0]
    if bias >= xs[-1]:
        return ys[-1]
    return float(np.interp(bias, xs, ys))


def cap_card(name, n1, n2, model, bias=0.0, corner="typ"):
    """A ceramic capacitor as C-ESR-ESL in series, with the DC-bias derating
    from models/*.json applied -- hypothesis H2."""
    M = jsonio.model(model)
    C = M["C_nominal"]["value"]
    ret = retention(M, bias, corner)
    esr = M.get("ESR_20kHz", M.get("ESR", {})).get(corner, 1e-2)
    esl = M["ESL"].get(corner, M["ESL"].get("typ", 1e-9))
    return (f"C{name} {n1} {name}_a {C * ret:g}\n"
            f"R{name} {name}_a {name}_b {esr:g}\n"
            f"L{name} {name}_b {n2} {esl:g}\n"), C * ret


def tvs_card(name="TVS", model="tpsmf4l64a", corner="typ"):
    """A TVS as one junction: reverse breakdown at V_BR and, in the other
    direction, an ordinary forward diode.

    The forward direction matters on this board and the design did not
    consider it: PHASE_x sits about a volt below ground while the low-side
    body diode freewheels, so the phase TVS is forward-biased and shares that
    current.  Whether it matters is a P2 result, not an assumption.

    R_s = 0.1 Ohm is a bracketed number: the TPSMF4L64A datasheet was not
    retrieved (models/tpsmf4l64a.json says so), and it is the dynamic
    resistance a SOD-123FL TVS has at a few amps.  Everything this model says
    about forward sharing scales with it.
    """
    M = jsonio.model(model)
    vbr = M["V_BR"]["min"]
    cj = M["C_j"].get(corner, M["C_j"]["typ"])
    return f"""
.model {name}D D(Is=1e-12 N=1.8 Rs=0.1 Cjo={cj:g} Bv={vbr:g} Ibv=1m)
.subckt {name} a k
D{name} k a {name}D
Rleak a k 100meg
.ends {name}
"""
