#!/usr/bin/env python3
"""P4 -- the DC link and the board-to-board interconnect (Q13, and H2).

Three cells switching with centre-aligned sine PWM at m = 0.8 and 20 A RMS
per phase, the DC-link ceramics with the DC-bias derating from H2, board B's
bulk behind the header inductance FastHenry solved in P1.  What comes out:
the ripple-current split, the bus ripple at the FET drains, the impedance the
cell sees looking into its own DC link, and whether that impedance has a
resonance the switching can excite.
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
from spice import devices                                    # noqa: E402
from spice.ngspice import NgSpice                            # noqa: E402
from spice.cell import _p1                                   # noqa: E402

paths.import_tools()

FSW = 20e3
M_INDEX = 0.8


def dc_link_impedance(corner="typ", bias=60.0, with_bulk=True, p1=None):
    """Z(f) looking into the DC link from one cell's half-bridge."""
    p1 = p1 or _p1()
    L = np.array(p1["L_loop_nH"], float) * 1e-9
    Rl = np.array(p1.get("R_loop_mOhm", [4.0] * len(L)), float) * 1e-3
    caps = ["cap_100n_100v_0603", "cap_100n_100v_0603",
            "cap_2u2_100v_1206", "cap_2u2_100v_1206"]
    names = ["C103", "C104", "C101", "C102"]
    if p1.get("ports"):
        names = [p.upper().lstrip("P") for p in p1["ports"]]
        caps = ["cap_100n_100v_0603" if n in ("C103", "C104")
                else "cap_2u2_100v_1206" for n in names]
    S = ["dc link impedance"]
    S.append("Iac hb_p hb_n AC 1")
    S.append("Rhbn hb_n 0 1u")
    S.append(f"Chbp hb_p hb_n 80p")
    cvals = []
    for k, (nm, mdl) in enumerate(zip(names, caps)):
        S.append(f"L{nm}loop hb_p {nm}_i {L[k]:g}")
        S.append(f"R{nm}loop {nm}_i {nm}_t {Rl[min(k, len(Rl) - 1)]:g}")
        txt, cv = devices.cap_card(nm, f"{nm}_t", "hb_n", mdl, bias=bias,
                                   corner=corner)
        S.append(txt.rstrip())
        cvals.append(cv)
    if with_bulk:
        S.append(f"Llink hb_p nb {p1['L_link_nH'] * 1e-9:g}")
        S.append("Cbulk nb nbr 400u")
        S.append("Rbulk nbr hb_n 0.01")
        S.append("Ctvs nb hb_n 2n")
    S.append(".end")
    ng = NgSpice()
    ng.circuit("\n".join(S) + "\n")
    ng.command("ac dec 60 1e3 1e9")
    d = ng.all()
    f = np.real(d["frequency"])
    z = d["hb_p"]
    return f, z, float(sum(cvals))


def q13(quick=False):
    t0 = time.time()
    import geometry as G
    p1 = _p1()
    base = G.interconnect(10)

    out = {"question": "Q13",
           "estimate_geometry_py": {
               "i_bus_A": base["i_bus"], "i_ripple_A_rms": base["i_ripple"],
               "share_to_board_B": base["share"], "i_link_A_rms": base["i_link"],
               "capacity_A": base["capacity"], "ok": bool(base["ok"]),
               "assumed_ceramic_uF": 13.0},
           "p1_source": p1.get("source")}

    # --- H2: what the ceramics are really worth at 60 V ----------------
    c1206 = jsonio.model("cap_2u2_100v_1206")
    c0603 = jsonio.model("cap_100n_100v_0603")
    h2 = {}
    for corner in ("min", "typ", "max"):
        c = 6 * c1206["C_nominal"]["value"] * c1206["retention_at_60V"][corner] \
            + 6 * c0603["C_nominal"]["value"] * c0603["retention_at_60V"][corner]
        h2[corner] = c
    out["H2"] = {
        "claim": ("the DC-link ceramics are not 13 uF at 60 V; a 100 V X7R "
                  "1206 typically keeps 40-60 % of its value at 60 % of "
                  "rating"),
        "nominal_total_uF": (6 * 2.2 + 6 * 0.1),
        "at_60V_uF": {k: v * 1e6 for k, v in h2.items()},
        "geometry_py_assumed_uF": 13.0,
        "verdict": None,
        "source_note": (c1206["note"]),
    }
    out["H2"]["verdict"] = (
        f"CONFIRMED as a direction, bracketed in size: at 60 V the six 2.2 uF "
        f"and six 100 nF are worth {h2['min'] * 1e6:.1f}-{h2['max'] * 1e6:.1f} uF "
        f"against the 13.2 uF nominal that geometry.interconnect() assumes. "
        f"No manufacturer DC-bias curve could be retrieved for an unchosen "
        f"part, so this is a swept bracket, not a measurement.")

    # --- the ripple split, recomputed with the derated ceramics ---------
    split = {}
    for corner, cval in h2.items():
        z_cer = 1 / (2 * math.pi * FSW * cval)
        z_bulk = 0.2          # the spec's ESR-dominated electrolytic case
        z_bulk_poly = 0.03    # 4 x 100 uF polymer hybrid, ESR 20-40 mOhm each
        for name, zb in (("electrolytic_0R2", z_bulk),
                         ("polymer_0R03", z_bulk_poly)):
            share = z_cer / (z_cer + zb)
            i_link = math.hypot(base["i_bus"], base["i_ripple"] * share)
            split.setdefault(name, {})[corner] = {
                "ceramic_uF": cval * 1e6, "Z_ceramic_mOhm": z_cer * 1e3,
                "share_crossing": share, "i_link_A_rms": i_link,
                "capacity_A": base["capacity"],
                "pass_80pct": bool(i_link <= 0.8 * base["capacity"])}
    out["ripple_split"] = split

    # --- the impedance the cell sees -----------------------------------
    curves = {}
    for corner in ("min", "typ", "max"):
        f, z, ctot = dc_link_impedance(corner=corner, p1=p1)
        mag = np.abs(z)
        i_res = int(np.argmax(mag[(f > 1e4) & (f < 1e9)])) \
            + int(np.searchsorted(f, 1e4))
        # the parallel resonance: where |Z| peaks
        fr = float(f[i_res])
        zr = float(mag[i_res])
        # Q of that peak, from the -3 dB width
        half = zr / math.sqrt(2)
        lo = f[:i_res][mag[:i_res] < half]
        hi = f[i_res:][mag[i_res:] < half]
        q = (fr / (hi[0] - lo[-1])) if len(lo) and len(hi) else None
        curves[corner] = {
            "f_Hz": f[::6].tolist(), "Z_ohm": mag[::6].tolist(),
            "Z_at_20kHz_mOhm": float(np.interp(2e4, f, mag)) * 1e3,
            "Z_at_118MHz_mOhm": float(np.interp(1.18e8, f, mag)) * 1e3,
            "f_resonance_Hz": fr, "Z_peak_ohm": zr, "Q_resonance": q,
            "pass_no_resonance_below_1MHz": bool(not (fr < 1e6 and q and q > 3)),
        }
    out["impedance"] = curves
    _plot_z(curves)
    out["figure"] = "p4_dclink_z.png"

    # --- the link parasitics P1 solved ----------------------------------
    r1 = jsonio.read("P1") or {}
    out["link_parasitics"] = r1.get("Q13", {"note": "P1 has not been run"})
    out["seconds"] = round(time.time() - t0, 1)
    return out


def _plot_z(curves):
    fig, ax = plt.subplots(figsize=(6.5, 4.4), dpi=150)
    for corner, c in curves.items():
        ax.loglog(c["f_Hz"], c["Z_ohm"], lw=1.2,
                  label=f"ceramics at DC bias, {corner}")
    ax.axvline(2e4, color="k", ls=":", lw=.8)
    ax.text(2.1e4, 1e-3, "20 kHz", fontsize=7)
    ax.set_xlabel("Hz"); ax.set_ylabel("|Z| (ohm)")
    ax.set_title("DC-link impedance seen by one cell's half-bridge", fontsize=9)
    ax.grid(alpha=.3, which="both"); ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(paths.FIGS / "p4_dclink_z.png")
    plt.close(fig)


def link_network(corner="typ", bias=60.0, n_cells=3, p1=None, bulk_esr=0.03,
                 bulk_uF=400.0):
    """The whole DC link: three cells' worth of ceramics on board A, the header
    and standoff inductance P1 solved, and board B's bulk behind it.  Two AC
    measurements come out of it -- the impedance the inverter sees, and how
    much of the inverter's ripple current crosses the header."""
    p1 = p1 or _p1()
    L = np.array(p1["L_loop_nH"], float) * 1e-9
    Rl = np.array(p1.get("R_loop_mOhm", [4.0] * len(L)), float) * 1e-3
    names = ["C103", "C104", "C101", "C102"]
    if p1.get("ports"):
        names = [q.upper().lstrip("P") for q in p1["ports"]]
    caps = ["cap_100n_100v_0603" if n in ("C103", "C104", "C203", "C204",
                                          "C303", "C304")
            else "cap_2u2_100v_1206" for n in names]
    S = ["dc link network"]
    S.append("Iac hb_p hb_n AC 1")
    S.append("Rhbn hb_n 0 1u")
    S.append(f"Chbp hb_p hb_n {80e-12 * n_cells:g}")
    ctot = 0.0
    for cell in range(n_cells):
        for k, (nm, mdl) in enumerate(zip(names, caps)):
            tag = f"{nm}x{cell}"
            S.append(f"L{tag}loop hb_p {tag}_i {L[k]:g}")
            S.append(f"R{tag}loop {tag}_i {tag}_t {Rl[min(k, len(Rl) - 1)]:g}")
            txt, cv = devices.cap_card(tag, f"{tag}_t", "hb_n", mdl,
                                       bias=bias, corner=corner)
            S.append(txt.rstrip())
            ctot += cv
    S.append(f"Llink hb_p nlink {p1['L_link_nH'] * 1e-9:g}")
    S.append("Vlink nlink nb DC 0")          # an ammeter in the link branch
    S.append(f"Cbulk nb nbr {bulk_uF * 1e-6:g}")
    S.append(f"Rbulk nbr hb_n {bulk_esr:g}")
    S.append("Ctvs nb hb_n 2n")
    S.append("Rleak nb hb_n 100k")
    S.append(".end")
    ng = NgSpice()
    ng.circuit("\n".join(S) + "\n")
    ng.command("ac dec 40 1e2 1e9")
    d = ng.all()
    f = np.real(d["frequency"])
    z = d["hb_p"]                              # 1 A injected -> V = Z
    i_link = d["vlink#branch"]                 # the current crossing to board B
    return f, z, i_link, ctot


def bus_ripple(quick=False):
    """Three cells switching with centre-aligned sine PWM: the DC-link current
    waveform, the ripple it puts on the bus, and how much of it crosses the
    header.  The split is measured with the real impedances rather than the
    two-impedance divider geometry.interconnect() uses."""
    t0 = time.time()
    p1 = _p1()
    fs = 40e6
    T = 1 / 50.0
    n = int(T * fs)
    t = np.arange(n) / fs
    f_e = 50.0
    I = 20.0 * math.sqrt(2)
    ia = I * np.sin(2 * math.pi * f_e * t)
    ib = I * np.sin(2 * math.pi * f_e * t - 2 * math.pi / 3)
    ic = -(ia + ib)
    carrier = 2 * np.abs(((t * FSW) % 1.0) - 0.5)
    d_ = [0.5 + M_INDEX / 2 * np.sin(2 * math.pi * f_e * t - k * 2 * math.pi / 3)
          for k in range(3)]
    s_ = [(dd > carrier).astype(float) for dd in d_]
    i_dc = s_[0] * ia + s_[1] * ib + s_[2] * ic
    i_mean = float(np.mean(i_dc))
    i_rms = float(np.sqrt(np.mean(i_dc ** 2)))
    i_ripple = float(np.sqrt(max(i_rms ** 2 - i_mean ** 2, 0.0)))

    F = np.fft.rfftfreq(n, 1 / fs)
    Idc = np.fft.rfft(i_dc) * 2 / n
    Idc[0] /= 2
    keep = F <= 5e6
    out = {"question": "Q13 (bus ripple)",
           "modulation": {"m": M_INDEX, "f_electrical_Hz": f_e,
                          "f_sw_Hz": FSW, "I_phase_A_rms": 20.0},
           "i_dc_mean_A": i_mean, "i_dc_rms_A": i_rms,
           "i_ripple_A_rms": i_ripple,
           "i_ripple_geometry_py_A_rms": None}
    import geometry as G
    out["i_ripple_geometry_py_A_rms"] = G.interconnect(10)["i_ripple"]

    res = {}
    for corner in ("min", "typ", "max"):
        for bulk_name, esr, uF in (("polymer_4x100uF", 0.0075, 400.0),
                                   ("electrolytic_3x100uF", 0.2, 300.0)):
            fz, z, il, ctot = link_network(corner=corner, p1=p1,
                                           bulk_esr=esr, bulk_uF=uF)
            # complex transfer functions, so the ripple waveform can be
            # reconstructed with its phases instead of summing magnitudes
            Zc = (np.interp(F[keep], fz, np.real(z))
                  + 1j * np.interp(F[keep], fz, np.imag(z)))
            Hl = (np.interp(F[keep], fz, np.real(il))
                  + 1j * np.interp(F[keep], fz, np.imag(il)))
            V = np.zeros(len(F), complex)
            V[keep] = Idc[keep] * Zc
            V[0] = 0.0
            v_t = np.fft.irfft(V * n / 2, n)
            vpp = float(np.ptp(v_t))
            v_rms = float(np.sqrt(np.mean(v_t ** 2)))
            IL = np.zeros(len(F), complex)
            IL[keep] = Idc[keep] * Hl
            IL[0] = 0.0
            il_t = np.fft.irfft(IL * n / 2, n)
            i_link_ripple = float(np.sqrt(np.mean(il_t ** 2)))
            res[f"{corner}/{bulk_name}"] = {
                "ceramic_total_uF": ctot * 1e6,
                "V_bus_ripple_pp_V": vpp,
                "V_bus_ripple_rms_V": v_rms,
                "i_link_ripple_A_rms": i_link_ripple,
                "i_link_total_A_rms": float(math.hypot(i_mean, i_link_ripple)),
                "share_crossing": (i_link_ripple / i_ripple
                                   if i_ripple else None),
                "Z_at_20kHz_mOhm": float(np.interp(2e4, fz, np.abs(z))) * 1e3,
                "pass_ripple_3V": bool(vpp <= 3.0),
                "pass_link_80pct": bool(math.hypot(i_mean, i_link_ripple)
                                        <= 0.8 * 30.0),
            }
    out["per_case"] = res
    out["criterion"] = ("I_link <= 80 % of the 30 A the ten-pin header is "
                        "worth; V_bus,pp <= 3 V at the drains; no DC-link "
                        "resonance with Q > 3 below 1 MHz")
    out["note"] = ("The DC-link current waveform is synthesised from the "
                   "switching functions of three centre-aligned sine-PWM legs "
                   "at m = 0.8, transformed, multiplied by the complex "
                   "impedance ngspice gives for the real link, and "
                   "transformed back -- so V_bus,pp is a waveform's "
                   "peak-to-peak, not a sum of harmonic magnitudes.")
    out["seconds"] = round(time.time() - t0, 1)
    return out


def run(quick=False):
    out = {}
    out["Q13"] = q13(quick)
    try:
        out["Q13_ripple"] = bus_ripple(quick)
    except Exception as e:
        out["Q13_ripple"] = {"error": f"{e.__class__.__name__}: {e}"}
    return out


if __name__ == "__main__":
    print(json.dumps(run(quick=True), indent=1, default=str)[:3500])
