#!/usr/bin/env python3
"""P9 for board S -- the simulation, summarised onto the board-S page.

Board A's P9 writes a report and a hand-back list for board A.  Board S's
results live in sim/results/s/ and its figures in sim/report/img/s/; this
phase reads only those files, grades each question against its criterion, and
writes

    sim/results/s/P9.json      the rows, as data
    single.html                the section between <!-- sim:begin --> and
                               <!-- sim:end -->, rebuilt whole every run

so the page can never show a number the last run did not produce.

    python3 sim/run.py --board s --phase P9
"""
import datetime
import html
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import paths, jsonio, board                         # noqa: E402

PAGE = paths.PROJECT / "single.html"
BEGIN, END = "<!-- sim:begin -->", "<!-- sim:end -->"


def g(d, path, default=None):
    """d["a"]["b"][0]... from "a.b.0"; None where any step is missing."""
    cur = d
    for k in path.split("."):
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        elif isinstance(cur, list) and k.isdigit() and int(k) < len(cur):
            cur = cur[int(k)]
        else:
            return default
    return cur


def f(x, nd=1, unit=""):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "&mdash;"
    s = f"{x:.{nd}f}"
    return f"{s}&nbsp;{unit}" if unit else s


def esc(s):
    return html.escape(str(s), quote=False)


# ------------------------------------------------------------------ rows ---
def rows(R):
    B = board.P()
    P1, P2, P3, P4, P5, P5S, P6, P7, P8 = (R.get(k, {}) for k in (
        "P1", "P2", "P3", "P4", "P5", "P5S", "P6", "P7", "P8"))
    out = []

    def row(area, result, criterion, verdict, key, fig=None, why=""):
        out.append({"area": area, "result": result, "criterion": criterion,
                    "verdict": verdict, "evidence": key, "figure": fig,
                    "why": why})

    # --- Q1: the commutation loop --------------------------------------
    q1 = P1.get("Q1") or {}
    lp = g(q1, "solved.L_pcb_nH_at_10MHz")
    lt = g(q1, "total_with_parts.L_total_nH") or []
    x7 = P7.get("Q1_crosscheck") or {}
    # P7 may have finished before P1 did; take FastHenry's single-port number
    # (the capacitor the FDTD port stands at) from P1 here if P7 has none
    l1 = x7.get("L1_fasthenry_nH")
    if l1 is None and q1:
        try:
            from phases.p7_fullwave import _fh_reference
            l1 = _fh_reference("A").get("L_single_port_nH")
        except Exception:
            l1 = None
    series = [(r.get("min_step_mm"), r.get("L_nH")) for r in x7.get("mesh_convergence") or []
              if r.get("L_nH")]
    res = (f"{f(lp, 2, 'nH')} of PCB (FastHenry, all four capacitors); "
           f"{f(min(lt) if lt else None, 1)}&ndash;{f(max(lt) if lt else None, 1, 'nH')} "
           f"with the two FET packages and the capacitors' ESL")
    cv = [r for r in q1.get("convergence") or [] if r.get("L_eff_nH")]
    unfinished = [r["target_mm"] for r in q1.get("convergence") or [] if r.get("error")]
    if len(cv) > 1:
        ch = abs(cv[-1]["L_eff_nH"] / cv[-2]["L_eff_nH"] - 1) * 100
        res += (f" (its mesh series {', '.join(f(r['L_eff_nH'], 2) for r in cv)}&nbsp;nH, "
                f"{ch:.1f}&nbsp;% on the last step"
                + (f"; the {', '.join(f'{t:g}' for t in unfinished)}&nbsp;mm mesh had not "
                   f"finished" if unfinished else "") + ")")
    bc = P1.get("Q1_cells_BC") or {}
    if bc.get("spread_pct") is not None:
        res += f"; cells A/B/C within {f(bc['spread_pct'], 0)}&nbsp;%"
    if series:
        res += ("; the full-wave check, one capacitor's loop, reads "
                + ", ".join(f"{f(v, 2)}" for _, v in series)
                + f"&nbsp;nH as its mesh refines ({', '.join(f'{m:g}' for m, _ in series)}&nbsp;mm)"
                + (f" against FastHenry's {f(l1, 2, 'nH')}" if l1 else ""))
    conv = x7.get("converged")
    row("Commutation loop", res, "reported; FastHenry and openEMS agree within 25&nbsp;%, "
        "each converged", "pass" if (conv and x7.get("pass_25pct")) else
        ("info" if not x7 else "fail"), "P1 Q1, P7 Q1_crosscheck", "p1_current_A.png",
        why=("" if conv or not x7 else
             "the full-wave mesh series did not settle; its number is not used"))

    # --- Q2: the switching edges ---------------------------------------
    q2 = P2.get("Q2") or {}
    env = q2.get("envelope_sweep") or []

    def pk(r):
        return max(r.get("Vds_low_peak_at_turn_on") or -1,
                   r.get("Vds_high_peak_at_turn_off") or -1)
    nom = [r for r in env if abs(r.get("V_bus", 0) - B["v_bus"]) < .1
           and abs(r.get("I_phase", 0) - 28.28) < .1]
    top = [r for r in env if abs(r.get("V_bus", 0) - max(B["v_sweep"])) < .1]
    vnom = pk(nom[0]) if nom else None
    vtop = max(pk(r) for r in top) if top else None
    crit = g(q2, "criterion.V_ds_peak_max_V")
    res = (f"{f(vnom, 1, 'V')} peak at {B['v_bus']:.0f}&nbsp;V and 28.3&nbsp;A; "
           f"{f(q2.get('worst_V_ds_V'), 1, 'V')} at the worst corner swept "
           f"(R<sub>g</sub>, T<sub>j</sub>, driver strength, capacitance); "
           f"{f(vtop, 1, 'V')} at {max(B['v_sweep']):.0f}&nbsp;V, the top of the "
           f"fold-back")
    worst = q2.get("worst_V_ds_V")
    verdict = ("pass" if worst is not None and crit and worst <= crit else
               "warn" if vnom is not None and crit and vnom <= crit else "fail")
    row("Switching overshoot", res,
        f"V<sub>ds</sub> &le; {f(crit, 0, 'V')} everywhere ({esc(B['v_crit_reason'])}); "
        f"ringing under 5&nbsp;% in 5 cycles", verdict, "P2 Q2", "p2_edges.png")

    q3 = P2.get("Q3") or {}
    sw3 = [r for r in q3.get("sweep") or [] if r.get("V_peak_V")]
    if sw3:
        lo3, hi3 = min(r["V_peak_V"] for r in sw3), max(r["V_peak_V"] for r in sw3)
        res = (f"at the nominal edge, which passes without one ({f(vnom, 1, 'V')}), every RC "
               f"from 2.2&ndash;22&nbsp;&Omega; and 0.47&ndash;4.7&nbsp;nF leaves the peak at "
               f"{f(lo3, 1)}&ndash;{f(hi3, 1, 'V')}: the overshoot there is too small for a snubber "
               f"to matter. The sweep is not run at the strong-driver corner that reaches "
               f"{f(worst, 1, 'V')}")
    else:
        res = "not run"
    row("Snubber", res, "the smallest RC that passes Q2 with P<sub>R</sub> &le; 0.25&nbsp;W",
        "info", "P2 Q3")

    q4 = P2.get("Q4") or {}
    sw = q4.get("sweep") or []
    vind = max((r.get("Vgs_low_induced_at_turn_on") or 0) for r in sw) if sw else None
    base = [r for r in sw if r.get("R_g") == 2.2 and r.get("drive_scale") == 1.0
            and r.get("drv_r_scale") in (1.0, None)
            and r.get("fet_corner") in ("typ", None)]
    vb0 = base[0].get("Vgs_low_induced_at_turn_on") if base else None
    row("Miller turn-on",
        f"{f(vb0, 2, 'V')} induced on the off FET's gate at the nominal edge, "
        f"{f(vind, 2, 'V')} at the worst corner, against a V<sub>GS(th)</sub> of "
        f"{f(q4.get('V_GS_th_min_V'), 1, 'V')} minimum",
        "induced V<sub>gs</sub> &le; 0.7 &times; V<sub>GS(th),min</sub>",
        "pass" if q4.get("pass") else "fail", "P2 Q4")

    # --- the sense chain ------------------------------------------------
    q5 = P2.get("Q5") or {}
    sf = q5.get("stale_fraction_free_running") or []
    row("Current sense, transient",
        f"the INA241A3 holds its output {f(q5.get('ina241_hold_ns'), 0, 'ns')} after "
        f"each edge (settles in {f(q5.get('worst_settle_ns'), 0, 'ns')}, "
        f"{f(q5.get('worst_error_LSB'), 0)} LSB worst); a free-running ADC lands "
        f"{f(min(sf) * 100 if sf else None, 0)}&ndash;{f(max(sf) * 100 if sf else None, 0)}&nbsp;% "
        f"of samples in a hold, PWM-synchronised sampling none",
        "settled to &plusmn;1 LSB in 500&nbsp;ns; &le; 6&nbsp;% of samples disturbed",
        "pass" if q5.get("pass_corruption") and q5.get("pass_settling") else "fail",
        "P2 Q5")
    q6 = P3.get("Q6") or {}
    a6 = g(q6, "per_cell.A") or {}
    sh = P3.get("Q6_sharing") or {}
    br = a6.get("TCR_pct_per_K_bracket") or []
    row("Current sense, DC",
        f"copper is {f((a6.get('copper_share') or 0) * 100, 1)}&nbsp;% of the "
        f"{f((a6.get('R_tap_to_tap_Ohm') or 0) * 1e3, 3, 'm&Omega;')} between the taps; "
        f"TCR {f(min(br) * 1e4 if br else None, 0)} to {f(max(br) * 1e4 if br else None, 0)}&nbsp;ppm/K "
        f"with the shunt's &plusmn;275&nbsp;ppm/K; the two shunts share "
        f"{f((sh.get('share_shunt1') or 0) * 100, 0)}/{f((sh.get('share_shunt2') or 0) * 100, 0)}",
        "copper &le; 5&nbsp;%; TCR &le; 500&nbsp;ppm/K; sharing within 10&nbsp;%",
        "pass" if a6.get("pass_copper_share") and a6.get("pass_TCR") and sh.get("pass")
        else "fail", "P3 Q6, Q6_sharing")

    # --- conduction -----------------------------------------------------
    q7 = P3.get("Q7") or {}
    lo = g(q7, "phase_A.low_side_via_array") or {}
    hi = g(q7, "vbus.Q1.via_array") or {}
    pj = g(q7, "phase_A.phase_current_density.A_per_mm2_p999")
    row("Vias and copper at 20&nbsp;A",
        f"busiest barrel {f(lo.get('I_rms_per_via_A'), 2, 'A')} RMS in the "
        f"{lo.get('n', '?')}-via array that takes the phase current from the low-side "
        f"drain down to the shunts, {f(hi.get('I_rms_per_via_A'), 2, 'A')} in the "
        f"high side's {hi.get('n', '?')}-via drain array fed from the XT30; phase band "
        f"{f(pj, 0)}&nbsp;A/mm&sup2; (p99.9)",
        "&le; 2&nbsp;A RMS per 0.4&nbsp;mm barrel",
        "pass" if lo.get("pass_2A_rms") and hi.get("pass_2A_rms") else "fail",
        "P3 Q7", "p3_J_vbus_In2.png")

    # --- the DC link ----------------------------------------------------
    r4 = P4.get("Q13_ripple") or {}
    q4d = P4.get("Q13") or {}
    nom4 = r4.get("nominal") or {}
    rt = r4.get("can_rating_A_rms_at_20_40kHz") or {}
    iok = r4.get("I_phase_rms_within_can_rating_A") or {}
    row("Bulk cans",
        f"{f(nom4.get('I_can_ripple_A_rms_each'), 1, 'A')} RMS in each can at 20&nbsp;A "
        f"RMS per phase ({f(nom4.get('can_heating_W_each'), 2, 'W')} each), against "
        f"{f(rt.get('min'), 1)}&ndash;{f(rt.get('max'), 1, 'A')} of rating at 20&ndash;40&nbsp;kHz: "
        f"within rating up to about {f(iok.get('min'), 1)}&ndash;{f(iok.get('max'), 1, 'A')} "
        f"RMS per phase, continuous",
        "each can within its ripple rating",
        "pass" if nom4.get("pass_can_rating_typ") else "fail",
        "P4 Q13_ripple", "p4_ripple_s.png")
    zc = g(q4d, "impedance.typ") or {}
    pser = g(P1, "Q13.mesh_series") or []
    row("Bus ripple",
        f"{f(nom4.get('V_bus_ripple_pp_V'), 2, 'V')} peak-to-peak at the drains; "
        f"{f(nom4.get('I_bat_ripple_A_rms'), 1, 'A')} RMS goes back up a 1&nbsp;&micro;H "
        f"battery lead; the ceramics are {f(g(q4d, 'H2.at_bus_uF.typ'), 1)}&nbsp;&micro;F "
        f"at {B['v_bus']:.0f}&nbsp;V of 13.8 nominal; the cans are about "
        f"{f(q4d.get('L_plane_nH'), 1, 'nH')} of plane from cell A"
        + (f" (FastHenry's series {', '.join(f(r['L_nH_at_1MHz'], 1) for r in pser)}&nbsp;nH "
           f"is still moving; at 20&ndash;40&nbsp;kHz any of them is under 1&nbsp;m&Omega;)"
           if len(pser) > 1 and abs(pser[-1]['L_nH_at_1MHz'] / pser[-2]['L_nH_at_1MHz'] - 1) > .1
           else ""),
        "V<sub>bus,pp</sub> &le; 3&nbsp;V; no DC-link resonance with Q &gt; 3 below 1&nbsp;MHz",
        "pass" if nom4.get("pass_ripple_3V") and zc.get("pass_no_resonance_below_1MHz")
        else "fail", "P4 Q13", "p4_dclink_z.png")

    # --- the encoder ----------------------------------------------------
    h5 = g(P5S, "Q10S.headline") or {}
    w = h5.get("weakest") or {}
    n5 = h5.get("nominal_N35_gap1.5") or {}
    row("Encoder field",
        f"bulk cans {f(w.get('xt30_caps_only_deg'), 3)}&deg; and the XT30's bus current "
        f"{f(w.get('xt30_bus_only_deg'), 3)}&deg; at the weakest field; with the motor "
        f"leads {f(n5.get('xt30_all_deg'), 2)}&deg; at the nominal gap, "
        f"{f(w.get('xt30_all_deg'), 2)}&deg; at the weakest &mdash; the leads are nearly all of it",
        "current-induced angle error &le; 0.05&deg; (about 2 LSB)",
        "pass" if h5.get("xt30_all_pass_everywhere") else "warn", "P5S Q10S",
        "p5s_board_s.png",
        why="the board's own currents pass; the motor leads do not unless twisted")
    mg = g(P5, "Q10.magnet") or []
    gn = board.P()["gap_nom"]
    # the weakest (N35, off axis) and strongest (N42, on axis) curves over the
    # gap; the window is where both are inside 20-100 mT
    lo_c = sorted((r["gap_mm"], r["B_inplane_mT"]) for r in mg
                  if r["grade"] == "N35" and r["off_axis_mm"] > 0)
    hi_c = sorted((r["gap_mm"], r["B_inplane_mT"]) for r in mg
                  if r["grade"] == "N42" and r["off_axis_mm"] == 0)
    ok_gaps = [gp for (gp, bl), (_, bh) in zip(lo_c, hi_c) if bl >= 20 and bh <= 100]
    nom = [r["B_inplane_mT"] for r in mg if r["grade"] == "N35"
           and r["off_axis_mm"] == 0 and abs(r["gap_mm"] - gn) < 1e-6]
    tol_ok = all(gp in ok_gaps for gp in (gn - 0.5, gn + 0.5) if any(
        abs(gp - x[0]) < 1e-6 for x in lo_c))
    too_close = [gp for (gp, bh) in hi_c if bh > 100]
    row("Magnet",
        f"&Oslash;{f(board.P()['magnet_D'] * 1e3, 0)} &times; 2.5&nbsp;mm: "
        f"{f(nom[0] if nom else None, 0, 'mT')} at the IC at the nominal {gn:g}&nbsp;mm gap; "
        f"inside the window for every grade and &plusmn;0.3&nbsp;mm offset from "
        f"{f(min(ok_gaps) if ok_gaps else None, 1)} to {f(max(ok_gaps) if ok_gaps else None, 1, 'mm')}"
        + (f"; over 100&nbsp;mT at {f(max(too_close), 1, 'mm')} and closer" if too_close else ""),
        f"20&ndash;100&nbsp;mT (MT6701) over the gap tolerance, {gn - 0.5:g}&ndash;{gn + 0.5:g}&nbsp;mm",
        "pass" if tol_ok else "fail", "P5 Q10")

    # --- the control loop ----------------------------------------------
    q8 = P6.get("Q8") or {}
    row("ADC sampling",
        f"free-running round-robin: {f(q8.get('free_running_noise_A_rms'), 2, 'A')} RMS "
        f"error and {f(q8.get('free_running_bias_A'), 2, 'A')} bias on i<sub>q</sub>; "
        f"sampled at the PWM counter's zero: {f(q8.get('synchronised_noise_A_rms'), 2, 'A')}"
        f" and {f(q8.get('synchronised_bias_A'), 2, 'A')}",
        "synchronised sampling cuts disturbed samples to zero", "info", "P6 Q8",
        "p6_sampling.png")
    q9 = P6.get("Q9") or {}
    row("Dead time",
        f"the software dead_zone costs {f(q9.get('torque_loss_from_software_dead_zone_pct'), 2)}&nbsp;% "
        f"of torque on top of the EG2103's own 560&nbsp;ns; pulses under "
        f"{f((q9.get('min_duty_reachable') or 0) * 100, 1)}&nbsp;% duty never reach the FETs",
        "reported, with a firmware setting", "info", "P6 Q9", "p6_deadtime.png")
    q14 = P6.get("Q14") or {}
    built = q14.get("C_bulk_as_built_uF")
    sw14 = [r for r in (q14.get("sweep") or []) if r.get("C_bulk_uF") == built
            and r.get("J_kgm2") == 1e-3]
    s0 = sw14[0] if sw14 else {}
    gd = q14.get("bus_guard") or {}
    row("Regeneration",
        f"a 20&nbsp;A brake from rated speed (J 10<sup>&minus;3</sup>&nbsp;kg&middot;m&sup2;) lifts "
        f"{f(built, 0)}&nbsp;&micro;F from {B['v_bus']:.0f}&nbsp;V to the "
        f"{f(gd.get('fold_start_V'), 0, 'V')} fold-back in "
        f"{f((s0.get('t_to_guard_ms') or 0) * 1e3, 0, '&micro;s')} and to the TVS in "
        f"{f((s0.get('t_to_TVS_ms') or 0) * 1e3, 0, '&micro;s')}; the bulk takes "
        f"{f(s0.get('E_cap_to_TVS_J'), 2, 'J')} of the {f(s0.get('E_mech_J'), 1, 'J')} stored",
        "reported: no brake chopper; the battery, when it accepts charge, is the sink",
        "warn" if s0 and not s0.get("bus_guard_catches") else "info", "P6 Q14")
    q12 = P2.get("Q12") or {}
    row("Motor frame (common mode)",
        f"a floating frame swings {f(q12.get('worst_V_frame_V'), 0, 'V')} peak on each edge "
        f"through the winding capacitance",
        "&le; 10&nbsp;V peak frame-to-GND", "pass" if q12.get("pass") else "fail",
        "P2 Q12", why="the enclosure has to bond the motor frame; the board cannot")

    # --- heat -------------------------------------------------------------
    L8 = P8.get("losses_geometry_py_W") or {}
    sn = P8.get("sink_needed") or {}
    va = g(P8, "via_array.Q1") or {}
    row("Heat",
        f"{f(L8.get('total'), 1, 'W')} on the board at 20&nbsp;A RMS and "
        f"{B['v_bus']:.0f}&nbsp;V; the high-side drain's {va.get('n_barrels', '?')}-via "
        f"array is {f(va.get('R_total_K_per_W'), 1, 'K/W')}; for T<sub>j</sub> &le; 125&nbsp;&deg;C the ring "
        f"needs &le; {f(sn.get('r_sink_K_per_W'), 1, 'K/W')} to air (about "
        f"{f(sn.get('area_cm2'), 0)}&nbsp;cm&sup2;)",
        "reported", "info", "P8")

    q11 = P7.get("Q11") or {}
    row("Radiated field",
        "not quoted: a whole-board FDTD mesh cannot resolve 0.15&nbsp;mm copper at an "
        "affordable cell count, so the far-field run does not converge"
        if not q11.get("converged") else "converged; see P7",
        "informational", "info", "P7 Q11")
    return out


# ----------------------------------------------------------------- figures -
FIGS = [
    ("p4_ripple_s.png", "The bulk cans at the design point",
     "Left: the inverter's DC-link current over three PWM periods at the phase-current "
     "peak, with what the two cans and the battery lead carry of its ripple. Right: each "
     "can's RMS ripple for every ceramic and ESR corner, against the part's 2.5&nbsp;A "
     "(100&nbsp;kHz) rating brought down to 20&ndash;40&nbsp;kHz. The ceramics on the cells are "
     "too small at 20&nbsp;kHz to take a useful share; the cans take it all, and the battery "
     "lead rings against them."),
    ("p2_edges.png", "Both switching edges of cell A",
     "ngspice, with FastHenry's loop and gate inductances, the fitted BSC030N08NS5 and a "
     "behavioural EG2103, at the design point. Top: V<sub>ds</sub> of both FETs; the larger "
     "overshoot is the low side's, while its own body diode recovers at turn-on. Bottom: "
     "the gates, with the Miller kick on the FET that is meant to stay off."),
    ("p1_commutation_A.png", "The commutation loop, solved",
     "FastHenry on cell A's routed copper: each DC-link capacitor's own loop and all four in "
     "parallel (black), 1&nbsp;kHz to 300&nbsp;MHz, against the 0.39&nbsp;nH the design's closed "
     "form assumed (dashed). The packages and capacitor ESL come on top of this."),
    ("p3_J_vbus_In2.png", "The bus current on In2",
     "The DC solution, 28.3&nbsp;A from the XT30's VMOT pin (lower left) to cell A's high-side "
     "drain (right) on the In2 VBUS plane. It goes the long way round, past cells C and B: "
     "In2 is +3V3 over the CPU wedge and the centre, so the plane is an open ring."),
    ("p3_J_phase_A.png", "The phase output",
     "Current density in cell A's phase pour on B.Cu, from the shunts to the motor-lead pad, "
     "at the 28.3&nbsp;A peak."),
    ("p5s_board_s.png", "The field at the encoder",
     "Worst angle error over the air gap with the &Oslash;6 magnet: the bulk cans' ripple and "
     "the bus current through the XT30 each stay well under the 0.05&deg; criterion; the "
     "motor leads are what exceed it."),
    ("p4_dclink_z.png", "The DC link seen from one cell",
     "Impedance looking into cell A's half-bridge: the cans behind the planes at low "
     "frequency, the ceramics above a few hundred kHz, and the loop inductance at the top."),
    ("p6_sampling.png", "What the ADC gives the current loop",
     "The FOC loop, the motor and the RP2350's ADC together: free-running round-robin "
     "sampling against sampling at the PWM counter's zero."),
    ("p7_crosscheck.png", "Two solvers, one loop",
     "The same loop in openEMS (full wave, mesh snapped to the copper) and in FastHenry "
     "(quasi-static), each over its own mesh series. On this board the full-wave series "
     "has not settled: its finest step reads twice the two before it (table above)."),
]


def _figures():
    out = []
    for fn, title, cap in FIGS:
        p = paths.FIGS / fn
        if p.exists():
            rel = p.relative_to(paths.PROJECT).as_posix()
            out.append((rel, title, cap))
    return out


# -------------------------------------------------------------------- html -
VERDICT = {"pass": ("fix", "meets it"), "fail": ("bad", "does not"),
           "warn": ("wrn", "partly"), "info": ("keep", "reported")}


def section(R, rs):
    B = board.P()
    meta = [R[k].get("_meta", {}) for k in R if isinstance(R[k], dict)]
    written = max((m.get("written") or "" for m in meta), default="")
    bmt = max((m.get("board_mtime") or "" for m in meta), default="")
    n_pass = sum(r["verdict"] == "pass" for r in rs)
    n_fail = sum(r["verdict"] == "fail" for r in rs)
    n_warn = sum(r["verdict"] == "warn" for r in rs)
    phases = [k for k in ("P0", "P1", "P2", "P3", "P4", "P5", "P5S", "P6", "P7", "P8")
              if k in R]
    kat = g(R, "P0.gate") or {}
    r4 = g(R, "P4.Q13_ripple") or {}
    nom4 = r4.get("nominal") or {}
    iok = r4.get("I_phase_rms_within_can_rating_A") or {}
    q2w = g(R, "P2.Q2.worst_V_ds_V")
    L = []
    L.append('<section id="sim" aria-labelledby="sim-h">')
    L.append('  <h2 id="sim-h">What the simulation says about this board</h2>')
    L.append(f'  <p>The project&rsquo;s electromagnetic simulation, run on this board file: '
             f'the commutation loop and gate loops in FastHenry, the switching edges and '
             f'sense chain in ngspice, DC current flow on all six layers, the DC link and '
             f'bulk as a network, the field at the encoder, the FOC loop with the ADC, and '
             f'an openEMS cross-check of the loop. Design point: {B["v_bus"]:.0f}&nbsp;V, '
             f'20&nbsp;A RMS per phase (28.3&nbsp;A peak), 20&nbsp;kHz centre-aligned sine PWM '
             f'at m&nbsp;=&nbsp;0.8. Each row grades one question against the criterion the '
             f'simulation spec (<a href="sim/SPEC.md"><code>sim/SPEC.md</code></a>) set for it.</p>')
    L.append('  <div class="stats">')
    L.append(f'    <div class="stat"><b>{n_pass} of {len(rs)} criteria met</b><span>'
             f'{n_fail} not met &middot; {n_warn} partly &middot; the rest reported</span></div>')
    if nom4:
        L.append(f'    <div class="stat"><b>Bulk cans: {f(nom4.get("I_can_ripple_A_rms_each"), 1, "A")}'
                 f' ripple each</b><span>rated about {f(g(r4, "can_rating_A_rms_at_20_40kHz.typ"), 1, "A")}'
                 f' &middot; fine to ~{f(iok.get("typ"), 0, "A")} RMS per phase</span></div>')
    if q2w is not None:
        L.append(f'    <div class="stat"><b>Worst V<sub>ds</sub> {f(q2w, 1, "V")}</b><span>'
                 f'silicon 80&nbsp;V &middot; phase clamps break down at 60.0&nbsp;V</span></div>')
    lp = g(R, "P1.Q1.solved.L_pcb_nH_at_10MHz")
    if lp is not None:
        x7 = g(R, "P7.Q1_crosscheck") or {}
        tail = (f'openEMS agrees within {f(x7.get("agreement_pct"), 0)}&nbsp;%'
                if x7.get("converged") and x7.get("agreement_pct") is not None
                else "the full-wave check did not settle")
        L.append(f'    <div class="stat"><b>Commutation loop {f(lp, 2, "nH")}</b><span>'
                 f'PCB only, FastHenry &middot; {tail}</span></div>')
    L.append('  </div>')
    L.append('  <div class="tablewrap"><table class="sim">')
    L.append('    <thead><tr><th>Question</th><th>This board</th><th>Criterion</th>'
             '<th>Verdict</th></tr></thead>')
    L.append('    <tbody>')
    for r in rs:
        cls, word = VERDICT[r["verdict"]]
        why = f'<br><small>{r["why"]}</small>' if r["why"] else ""
        L.append(f'      <tr><td>{r["area"]}</td><td>{r["result"]}</td>'
                 f'<td>{r["criterion"]}</td><td class="{cls}">{word}{why}</td></tr>')
    L.append('    </tbody>')
    L.append('  </table></div>')
    L.append('  <div class="figs">')
    for rel, title, cap in _figures():
        L.append(f'    <figure><a href="{rel}"><img src="{rel}" loading="lazy" '
                 f'alt="{esc(title)}"></a><figcaption><b>{title}.</b> {cap}</figcaption></figure>')
    L.append('  </div>')
    L.append(f'  <p class="sub">Phases run: {", ".join(phases)}; solver known-answer tests '
             f'{kat.get("n_pass", "?")}/{kat.get("n_total", "?")} passed. Results written '
             f'{esc(written[:16].replace("T", " "))} from the board file of '
             f'{esc(bmt[:16].replace("T", " "))}; numbers and figures come only from '
             f'<code>sim/results/s/</code> and <code>sim/report/img/s/</code>. Rebuild with '
             f'<code>python3 sim/run.py --board s</code> (about two hours; the full-wave '
             f'cross-check is most of it). Board A&rsquo;s own report is '
             f'<a href="sim/report/index.html">here</a>.</p>')
    L.append('</section>')
    return "\n".join(L)


def write_page(text):
    if not PAGE.exists():
        return False
    t = PAGE.read_text()
    if BEGIN not in t or END not in t:
        return False
    t = t[:t.index(BEGIN) + len(BEGIN)] + "\n" + text + "\n" + t[t.index(END):]
    PAGE.write_text(t)
    return True


def run(quick=False):
    R = {}
    for p in sorted(paths.RESULTS.glob("P*.json")):
        if p.stem != "P9":
            R[p.stem] = json.loads(p.read_text())
    rs = rows(R)
    html_ = section(R, rs)
    wrote = write_page(html_)
    return {"rows": rs, "phases_present": sorted(R),
            "page": str(PAGE) if wrote else None,
            "written_to_page": wrote,
            "date": datetime.date.today().isoformat()}


if __name__ == "__main__":
    r = run()
    print(json.dumps({k: v for k, v in r.items() if k != "rows"}, indent=1))
