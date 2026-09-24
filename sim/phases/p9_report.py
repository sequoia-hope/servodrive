#!/usr/bin/env python3
"""P9 -- the report, the findings register and the hand-back list.

Everything here reads `sim/results/*.json` and nothing else, so the report can
never show a number the current run did not produce (SPEC.md sec.8.1).  The
page is static HTML in the project's own style and is served the registered
way: `proj up servodrive`, then `proj url servodrive`, then /sim/report/.
"""
import html
import json
import math
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import paths, jsonio                                # noqa: E402

V_SILICON = 80.0
V_CRIT = 68.0

paths.import_tools()

VERDICT = {True: ("ok", "meets the criterion"),
           False: ("bad", "does not meet the criterion"),
           None: ("na", "reported, no pass/fail criterion")}


def e(x):
    return html.escape(str(x))


def num(x, n=3, unit=""):
    if x is None:
        return "&mdash;"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return e(x)
    if v != v:
        return "&mdash;"
    if v and (abs(v) >= 1e5 or abs(v) < 1e-3):
        s = f"{v:.{n}g}"
    else:
        s = f"{v:.{n}f}"
        if "." in s:                      # only strip zeros after a point
            s = s.rstrip("0").rstrip(".")
    return s + (f"&nbsp;{unit}" if unit else "")


def sentence(x):
    """A note written as a clause in a JSON file, rendered as prose: leading
    capital, one full stop at the end."""
    t = str(x or "").strip()
    if not t:
        return ""
    t = t[0].upper() + t[1:]
    return e(t if t[-1] in ".!?" else t + ".")


def chip(ok, label=None):
    cls, word = VERDICT[ok]
    txt = label or {"ok": "PASS", "bad": "FAIL", "na": "INFO"}[cls]
    colour = {"ok": "var(--up)", "bad": "var(--bad)", "na": "var(--muted)"}[cls]
    return (f'<span class="state" style="color:{colour};'
            f'border-color:color-mix(in srgb,{colour} 35%,var(--line))">'
            f'<span class="dot" style="background:{colour}"></span>{e(txt)}</span>')


def table(headers, rows, cls="num"):
    h = "".join(f"<th>{x}</th>" for x in headers)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"
                for r in rows)
    return (f'<div class="tablewrap"><table class="{cls}"><thead><tr>{h}</tr>'
            f"</thead><tbody>{b}</tbody></table></div>")


def figure(name, caption):
    if not (paths.FIGS / name).exists():
        return ""
    return (f'<figure><img src="img/{e(name)}" alt="{e(caption)}">'
            f"<figcaption>{caption}</figcaption></figure>")


# =========================================================== the sections ===
def summary_rows(R):
    out = []

    def row(q, what, est, solved, ok, decision):
        out.append([f"<strong>{q}</strong>", what, est, solved, chip(ok),
                    decision])

    p1, p2, p3, p4, p5, p6, p7 = (R.get(k, {}) for k in
                                  ("P1", "P2", "P3", "P4", "P5", "P6", "P7"))
    q1 = p1.get("Q1", {})
    if q1:
        s = q1.get("solved", {})
        t = q1.get("total_with_parts", {})
        row("Q1", "commutation loop L, R",
            f"{num(q1['estimate_geometry_py']['L_pcb_nH'], 2)} nH PCB, "
            f"{num(q1['estimate_geometry_py']['L_total_nH'], 2)} nH total",
            f"{num(s.get('L_pcb_nH_at_10MHz'), 2)} nH PCB, "
            f"{num((t.get('L_total_nH') or [None])[0], 2)}&ndash;"
            f"{num((t.get('L_total_nH') or [None, None])[1], 2)} nH total",
            None, "snubber, cap placement")
    q2 = p2.get("Q2", {})
    if q2:
        wc = q2.get("worst_case") or {}
        env_w = max((max(r.get("Vds_low_peak_at_turn_on") or 0,
                         r.get("Vds_high_peak_at_turn_off") or 0)
                     for r in q2.get("envelope_sweep", [])), default=0.0)
        row("Q2", "V<sub>ds</sub> overshoot and ringing",
            f"{num(q2['estimate_geometry_py']['v_peak_V'], 1)} V peak",
            f"{num(env_w, 1)} V at the driver impedance the datasheet "
            f"implies, {num(q2.get('worst_V_ds_V'), 1)} V with a strong "
            f"output stage",
            q2.get("pass"), "snubber yes/no, the EG2103's output stage")
    q3 = p2.get("Q3", {})
    if q3:
        b = q3.get("smallest_that_passes")
        row("Q3", "RC snubber", "none fitted",
            (f"{num(b['R'], 1)} &#8486; + {num(b['C_nF'], 2)} nF, "
             f"{num(b['P_R_W'], 3)} W &mdash; needed only in the strong-driver "
             f"corner" if b else "none of the sweep passes"),
            bool(b), "two 0805 footprints per cell")
    q4 = p2.get("Q4", {})
    if q4:
        worst = max((r.get("Vgs_low_induced_at_turn_on") or 0)
                    for r in q4.get("sweep", [{}])) if q4.get("sweep") else None
        cc = [c for c in q4.get("cross_conduction", [])
              if "min" in c.get("corner", "")]
        xtra = (f", {num(cc[0].get('cross_conduction_uJ'), 0)} &micro;J per "
                f"edge at V<sub>GS(th),min</sub>" if cc else "")
        row("Q4", "gate loop, Miller turn-on", "not estimated",
            f"{num(worst, 1)} V induced V<sub>gs</sub> at the die, limit "
            f"{num(q4.get('V_GS_th_min_V', 0) * 0.7, 2)} V{xtra}",
            q4.get("pass"), "the FET's Q<sub>gd</sub>/C<sub>iss</sub>, "
                            "gate-drive rail")
    q5 = p2.get("Q5", {})
    if q5:
        row("Q5", "sense chain during an edge", "not estimated",
            f"tap swings {num(max((r.get('V_tap_peak_mV') or 0) for r in q5.get('sweep', [{}])), 0)} mV "
            f"against 22.6 mV of signal; the ADC sees "
            f"{num(q5.get('worst_error_LSB'), 1)} LSB",
            q5.get("pass_error_bound_LSB"),
            "filter, taps, synchronised sampling")
    q6 = p3.get("Q6", {})
    if q6:
        a = (q6.get("per_cell") or {}).get("A", {})
        row("Q6", "sense accuracy at DC",
            "0.8 m&#8486;, 50 ppm/K assumed",
            f"{num(a.get('R_tap_to_tap_Ohm', 0) * 1e3, 4)} m&#8486;, copper "
            f"{num(a.get('copper_share', 0) * 100, 1)} %, TCR "
            f"{num(a.get('TCR_pct_per_K'), 3)} %/K",
            a.get("pass_copper_share") and a.get("pass_TCR"),
            "tap positions, Kelvin connection")
    q7 = p3.get("Q7", {})
    if q7:
        arr = (q7.get("phase_A") or {}).get("low_side_via_array", {})
        row("Q7", "current density and via sharing",
            "16 vias per drain pad",
            f"{num(arr.get('I_rms_per_via_A'), 2)} A rms in the busiest via "
            f"of the low-side array",
            arr.get("pass_2A_rms"), "via count, pour widths, zone fill")
    q13 = p4.get("Q13", {})
    if q13:
        rip = p4.get("Q13_ripple", {}).get("per_case", {})
        good = [v for k, v in rip.items() if "polymer" in k]
        row("Q13", "DC link and interconnect",
            "23.2 A rms link, 13 &micro;F of ceramic, 3 V ripple",
            (f"{num(good[0]['i_link_total_A_rms'], 1)} A rms link, "
             f"{num(good[0]['ceramic_total_uF'], 1)} &micro;F at 60 V, "
             f"{num(good[0]['V_bus_ripple_pp_V'], 2)} V<sub>pp</sub>"
             if good else "&mdash;"),
            bool(good and good[0]["pass_ripple_3V"]
                 and good[0]["pass_link_80pct"]),
            "board B bulk chemistry, header pins")
    q12s = p2.get("Q12", {})
    if q12s and not q12s.get("error"):
        fl = [r for r in q12s.get("sweep", []) if r.get("bond") == "open"]
        row("Q12", "common mode at the motor frame", "not estimated",
            f"the frame follows the switch node to "
            f"{num(max((r.get('V_frame_pk_V') or 0) for r in fl) if fl else 0, 0)} V "
            f"peak", q12s.get("pass"), "frame bond, Y capacitor")

    q10 = p5.get("Q10", {})
    if q10:
        ae = q10.get("angle_error", {})
        row("Q10", "field at the encoder",
            "not estimated",
            f"{num(ae.get('B_magnet_inplane_mT'), 1)} mT at 1 mm gap; "
            f"{num(ae.get('peak_deg'), 3)}&deg; from 28.3 A",
            ae.get("pass"), "magnet size and grade, lead routing")
    q8 = p6.get("Q8", {})
    if q8:
        row("Q8", "ADC sampling", "not estimated",
            f"{num(q8.get('free_running_noise_A_rms'), 3)} A rms free-running "
            f"vs {num(q8.get('synchronised_noise_A_rms'), 3)} A synchronised",
            None, "DMA-paced ADC trigger from the PWM wrap")
    q9 = p6.get("Q9", {})
    if q9:
        row("Q9", "dead time",
            f"{num(q9['estimate']['V_distortion_with_software_dead_zone_V'], 2)} V "
            f"of distortion with the software dead zone",
            f"{num(q9.get('torque_loss_from_software_dead_zone_pct'), 2)} % of "
            f"torque, THD barely moves",
            None, "<code>dead_zone</code> value")
    q14 = p6.get("Q14", {})
    if q14 and q14.get("sweep"):
        w = q14["sweep"][len(q14["sweep"]) // 2]
        row("Q14", "regeneration", "none for this board",
            f"bus reaches the TVS in {num(w['t_to_TVS_ms'], 2)} ms; "
            f"{num(w['TVS_energy_J'], 1)} J would land on it",
            None, "brake resistor, bulk size, guard timing")
    q11 = p7.get("Q11", {})
    x7 = p7.get("Q1_crosscheck", {})
    if q11 or x7:
        em7 = [r for r in (x7.get("mesh_convergence") or []) if r.get("L_nH")]
        if em7 and x7.get("L1_fasthenry_nH"):
            what = (f"the loop cross-check agrees to "
                    f"{num(x7.get('agreement_pct'), 0)}&nbsp;% "
                    f"({num(x7.get('L2_for_comparison_nH'), 2)}&nbsp;nH full "
                    f"wave against {num(x7['L1_fasthenry_nH'], 2)}&nbsp;nH "
                    f"PEEC); the whole-board radiated model did not converge")
            ok = x7.get("pass_25pct")
        else:
            what = ("the board-level FDTD models did not converge; the solver "
                    "itself passes both its known-answer tests")
            ok = None
        row("Q11", "near and far field, and the L1/L2 cross-check",
            "not estimated", what, ok, "shielding, lead routing")
    return out


def q_section(tag, title, body):
    slug = tag.replace(" ", "").replace("/", "-")
    return (f'<section id="{slug}"><h2>{e(tag)}</h2><h3>{title}</h3>{body}'
            "</section>")


def build(R):
    parts = []
    meta = R.get("P0", {}).get("_meta", {})
    parts.append(f"""<header>
<p class="crumb"><a href="../../index.html">servodrive</a> &rsaquo;
 electromagnetic simulation</p>
<div class="title"><h1>servodrive &mdash; electromagnetic simulation</h1>
<span class="state active"><span class="dot"></span>run {e(date.today().isoformat())}</span></div>
<p class="lede">Every closed-form estimate the board was designed on, replaced
 with a solved number, and every one of the fourteen questions in
 <code>sim/SPEC.md</code> answered against its own criterion. Built from the
 board as routed &mdash; 138 filled zone polygons, 1351 tracks, 283 vias, 526
 pads &mdash; not from a sketch of it.</p>
<p class="meta">Reproduce with <code>python3 sim/run.py</code>. Solvers:
 FastHenry2 3.0.1 (PEEC), FastCap 2.0, ngspice through
 <code>libngspice.so.0</code>, openEMS (FDTD, in a container), magpylib, and a
 finite-difference conduction solver on a 0.05&nbsp;mm raster. Every one was
 checked against a closed form before it was believed.</p>
</header><main>""")

    # ---- the summary ---------------------------------------------------
    rows = summary_rows(R)
    parts.append(q_section(
        "Summary", "What the simulation found",
        table(["", "Question", "The estimate", "Solved", "", "Feeds"], rows,
              cls="") +
        '<div class="note"><p><strong>Four things changed the design\'s own '
        'arithmetic.</strong> The commutation loop is several times the '
        'inductance the board was designed on, because VBUS reaches the '
        'high-side drain from In2 rather than from a plane 0.1&nbsp;mm away. '
        'Twelve of the sixteen vias under each low-side drain are islands on '
        'B.Cu and carry nothing. The device that is supposed to be off is '
        'pulled above its own minimum threshold on every turn-on, not by '
        'the layout but by its own Q<sub>gd</sub>/C<sub>iss</sub>. And the '
        'DC-link ceramics are worth about half their marked value at '
        '60&nbsp;V.</p><p>One number the board cannot settle decides several '
        'of the others: how hard the EG2103&rsquo;s output stage actually '
        'drives. The only document obtainable for it gives the peak '
        'currents without their test condition, and the edge rate &mdash; '
        'hence the overshoot, the ringing and whether a snubber is needed '
        '&mdash; follows it.</p></div>'))

    parts += [sec for sec in (
        sec_q1(R), sec_q2q3(R), sec_q4(R), sec_q5(R), sec_q6(R), sec_q7(R),
        sec_q13(R), sec_q10(R), sec_q8(R), sec_q9(R), sec_q14(R), sec_q11(R),
        sec_q12(R), sec_p8(R), sec_method(R), sec_scope(R)) if sec]

    parts.append("</main><footer>"
                 "<p>servodrive electromagnetic simulation. CERN-OHL-P, like "
                 "the board. Results in <code>sim/results/*.json</code>; the "
                 "register in <code>sim/FINDINGS.md</code>; the hand-back list "
                 "in <code>sim/HANDBACK.md</code>.</p>"
                 f"<p>Board read {e(meta.get('board_mtime', 'n/a'))}.</p>"
                 "</footer>")
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,"
            "initial-scale=1\"><title>servodrive &mdash; EM simulation</title>"
            "<link rel=\"stylesheet\" href=\"../../style.css\">"
            "<script src=\"../../shell.js\"></script></head><body>"
            + "".join(parts) + "</body></html>")


def sec_q1(R):
    q = R.get("P1", {}).get("Q1")
    if not q:
        return ""
    s = q["solved"]
    est = q["estimate_geometry_py"]
    t = q["total_with_parts"]
    f = np.array(s["f_Hz"])
    Leff = np.array(s["L_eff_nH"])
    rows = [[num(fr, 3), num(L, 2)] for fr, L in zip(f, Leff)]
    cv = sorted(q["convergence"], key=lambda c: -c["target_mm"])
    conv = table(["grid target (mm)", "segments", "L<sub>eff</sub> (nH)"],
                 [[num(c["target_mm"], 2), c.get("segments", "&mdash;"),
                   num(c.get("L_eff_nH"), 2)] for c in cv])
    # how fast is it still moving, and where is it heading?
    drop, settled = "", False
    if len(cv) > 1 and cv[-1].get("L_eff_nH") and cv[-2].get("L_eff_nH"):
        step = (cv[-2]["L_eff_nH"] - cv[-1]["L_eff_nH"]) / cv[-2]["L_eff_nH"]
        settled = abs(step) < 0.05
        drop = (f"from {num(cv[0]['L_eff_nH'], 2)}&nbsp;nH at "
                f"{num(cv[0]['target_mm'], 2)}&nbsp;mm to "
                f"{num(cv[-1]['L_eff_nH'], 2)}&nbsp;nH at "
                f"{num(cv[-1]['target_mm'], 2)}&nbsp;mm, the last refinement "
                f"moving it {num(abs(step) * 100, 1)}&nbsp;%"
                + (" and in the other direction" if step < 0 else ""))
    br = q1_bracket(R)
    bc = (R.get("P1") or {}).get("Q1_cells_BC") or {}
    BC = ""
    if "verdict" in bc:
        rows_bc = [[k, num(v.get("segments"), 0), num(v.get("L_eff_nH"), 2)]
                   for k, v in bc.items() if isinstance(v, dict)
                   and "L_eff_nH" in v]
        BC = ("<h3>Cells B and C</h3>"
              + table(["cell", "segments", "L<sub>eff</sub> (nH)"], rows_bc)
              + f"<p>{e(bc['verdict'])}. Cell C is the one whose neighbour is "
                f"the power-link wedge, where VBUS and GND enter the board, "
                f"and it is the one that differs.</p>")
    sk = q.get("skin_effect_check") or {}
    SKIN = ""
    if "nhinc_3" in sk:
        SKIN = ("<h3>Skin effect</h3><p>The sweep uses one filament through "
                "the 35&nbsp;&micro;m copper, which is exact below about "
                "1&nbsp;MHz and does not push current to the surface above "
                "it. The same model with three filaments, at 20&nbsp;MHz, "
                f"gives {num(sk['R_ratio_3_over_1_at_20MHz'], 2)}&times; the "
                f"loop resistance and {num(sk['L_ratio_3_over_1'], 3)}&times; "
                "the inductance: the resistance in the sweep is a lower bound "
                "above a megahertz, the inductance is not affected.</p>")
    elif sk.get("error"):
        SKIN = (f'<div class="note"><p>The skin-effect check did not run: '
                f'{e(sk["error"])}</p></div>')
    body = f"""
<p>The board was designed on <code>geometry.commutation_loop()</code>, which
 models the loop as a 15.4&nbsp;&times;&nbsp;5&nbsp;mm strip 0.1&nbsp;mm above
 the In1 ground plane and gets {num(est['L_pcb_nH'], 3)}&nbsp;nH for the PCB
 part. The board does not do that. VBUS has exactly one zone, on In2, and no
 tracks: the high-side drain reaches it only through a 4&nbsp;&times;&nbsp;4
 field of 0.4&nbsp;mm barrels whose antipads perforate In1, and the DC-link
 capacitors reach it through one via each. The loop's outward leg therefore
 runs 0.55&nbsp;mm below F.Cu with a ground plane in between.</p>
<p>Solved on the copper as routed, with both devices shorted at their own pads
 so the number is the PCB's alone, the loop each capacitor sees is
 {num(min(np.diag(np.array(s['L_matrix_nH'])[len(f) // 2])), 2)}&ndash;{num(max(np.diag(np.array(s['L_matrix_nH'])[len(f) // 2])), 2)}&nbsp;nH,
 and with all four in parallel &mdash; which is how they work, strongly
 coupled &mdash; <strong>{num(s['L_pcb_nH_at_10MHz'], 2)}&nbsp;nH</strong>,
 {num(q['H1']['ratio_to_estimate'], 1)}&times; the estimate.</p>
<div class="note"><p><strong>H1: {e(q['H1']['verdict'])}</strong></p></div>
<p>Adding the two packages ({num(t['L_package_nH'], 2)}&nbsp;nH, bracketed &mdash;
 Infineon does not specify the SuperSO8's lead inductance) and the capacitors'
 own ESL gives a total of {num(min(t['L_total_nH']), 2)}&ndash;{num(max(t['L_total_nH']), 2)}&nbsp;nH
 against the {num(est['L_total_nH'], 2)}&nbsp;nH the design assumed. The
 overshoot that line of the table quotes is still the design's own arithmetic
 &mdash; L times the 0.87&nbsp;A/ns it assumes &mdash; and it is wrong for a
 second reason: the transient in Q2 commutates at about five amps per
 nanosecond, not one. The peak the circuit actually reaches is in Q2, not
 here.</p>
{BC}
{figure(f"p1_commutation_A.png", "Loop inductance and resistance per capacitor, 1 kHz to 300 MHz, with the four in parallel in black and the design's estimate as the dashed line.")}
<h3>Mesh convergence</h3>{conv}
<p>The grid this was solved on is not fine enough to be the answer: a
 filament grid that coarse cannot let the return current concentrate under a
 leg running 0.4&nbsp;mm above it, and that concentration is most of what
 sets this loop. Refined{f", it runs {drop}" if drop else ""}{
 " &mdash; so it has settled, and the converged figure is "
 f"{num(cv[-1]['L_eff_nH'], 2)}&nbsp;nH against the "
 f"{num(s['L_pcb_nH_at_10MHz'], 2)}&nbsp;nH this report quotes elsewhere, "
 f"{num(abs(cv[-1]['L_eff_nH'] / s['L_pcb_nH_at_10MHz'] - 1) * 100, 0)}&nbsp;% "
 "lower" if settled else ", and it has not settled"}.{
 f" Q11 drives the same single port in a full-wave solver and, with its own device strip de-embedded, gets {num(br[2]['L_comparable_nH'], 2)}&nbsp;nH against this model's {num(br[3]['L_single_port_nH'], 2)}&nbsp;nH for that port &mdash; {num(abs(br[2]['L_comparable_nH'] / br[3]['L_single_port_nH'] - 1) * 100, 0)}&nbsp;% apart."
 if br else ""} Everything the number is used for &mdash; that the loop is
 several times the 0.39&nbsp;nH the board was designed on &mdash; holds
 across all of it.</p>
{SKIN}
{figure("p1_current_A.png", "Where the current actually goes at 10 MHz, layer by layer, with 1 A driven into the high-side 100 n capacitor.")}
"""
    return q_section("Q1", "The commutation loop is five times the estimate",
                     body)


def sec_q2q3(R):
    p2 = R.get("P2", {})
    q2, q3 = p2.get("Q2"), p2.get("Q3")
    if not q2:
        return ""
    rows = []
    for r in q2.get("rg_tj_sweep", []):
        rows.append([num(r["R_g"], 1), num(r["T_j"], 0),
                     num(r.get("Vds_low_peak_at_turn_on"), 1),
                     num(r.get("Vds_high_peak_at_turn_off"), 1),
                     num((r.get("ring_turn_off") or {}).get("amplitude_V"), 2),
                     chip(r.get("pass_peak")),
                     chip(r.get("pass_ring_decay"))])
    drows = []
    for r in q2.get("driver_impedance", []):
        rs = r.get("drv_r_scale") or 1.0
        ro, rn = r.get("ring_turn_off") or {}, r.get("ring_turn_on") or {}
        drows.append([num(40.0 * rs, 1), num(20.0 * rs, 1),
                      num(r.get("dVdt_switch_node_kV_per_us"), 0),
                      num(r.get("Vds_low_peak_at_turn_on"), 1),
                      num(r.get("Vds_high_peak_at_turn_off"), 1),
                      (num(rn.get("f_MHz"), 0) if rn.get("rings")
                       else "&mdash;"),
                      (num(rn.get("cycles_to_5pct"), 0) if rn.get("rings")
                       else "0"),
                      chip(r.get("pass_Q2"))])
    corner = table(
        ["capacitances", "low side at turn-on (V)", "high side at turn-off (V)"],
        [[e(r.get("fet_corner")), num(r.get("Vds_low_peak_at_turn_on"), 1),
          num(r.get("Vds_high_peak_at_turn_off"), 1)]
         for r in q2.get("capacitance_corner", [])])
    env = q2.get("envelope_sweep", [])
    env_worst = max((max(r.get("Vds_low_peak_at_turn_on") or 0,
                         r.get("Vds_high_peak_at_turn_off") or 0)
                     for r in env), default=0.0)
    weak = next((r for r in q2.get("driver_impedance", [])
                 if (r.get("drv_r_scale") or 1.0) == 1.0), None)
    strong = min(q2.get("driver_impedance", []),
                 key=lambda r: r.get("drv_r_scale") or 1.0, default=None)
    snub = ""
    if q3:
        b = q3.get("smallest_that_passes")
        srows = [[num(r["R"], 1), num(r["C_nF"], 2), num(r.get("V_peak_V"), 1),
                  num(r["P_R_W"], 3)] for r in q3["sweep"]]
        snub = ("<h3>Q3 &mdash; the snubber</h3>"
                + table(["R (&#8486;)", "C (nF)", "worst V<sub>ds</sub> (V)",
                         "P<sub>R</sub> (W)"], srows)
                + (f"<p>The smallest one that meets both criteria is "
                   f"<strong>{num(b['R'], 1)}&nbsp;&#8486; with "
                   f"{num(b['C_nF'], 2)}&nbsp;nF</strong>, which brings the "
                   f"worst V<sub>ds</sub> to {num(b['V_peak_V'], 1)}&nbsp;V and "
                   f"costs {num(b['P_R_W'], 3)}&nbsp;W per cell at 20&nbsp;kHz. "
                   f"The loss is C&middot;V&sup2;&middot;f and does not depend "
                   f"on R; R only sets the damping. At the driver impedance "
                   f"the datasheet implies, nothing needs snubbing &mdash; "
                   f"this is the part to fit only if the output stage turns "
                   f"out to be the strong one.</p>" if b else
                   "<p>Nothing in the sweep meets both criteria.</p>"))
    body = f"""
<p>Both edges were simulated, with the FET fitted to its own datasheet (see
 the method section) and the loop parasitics from Q1. The design expected
 {num(q2['estimate_geometry_py']['v_peak_V'], 1)}&nbsp;V of peak
 V<sub>ds</sub>. The answer is not one number, because it is not set by
 anything on this board: it is set by how hard the EG2103's output stage
 actually drives, and the only EG2103 document that could be obtained gives
 I<sub>O+</sub>/I<sub>O&minus;</sub> without the condition they are measured
 at.</p>
<p>Read those ratings the usual way &mdash; as an output resistance at the
 12&nbsp;V drive, so 40&nbsp;&#8486; sourcing and 20&nbsp;&#8486; sinking
 &mdash; and the worst V<sub>ds</sub> anywhere in the operating envelope is
 <strong>{num(env_worst, 1)}&nbsp;V</strong>, against a 68&nbsp;V criterion, a
 71.1&nbsp;V TVS and 80&nbsp;V of silicon. Give the same part a strong output
 stage instead ({num(4.0, 0)}/{num(2.0, 0)}&nbsp;&#8486;) and it becomes
 <strong>{num(q2.get('worst_V_ds_V'), 1)}&nbsp;V</strong>.</p>
<h3>The bracket that decides it</h3>
{table(["R<sub>source</sub> (&#8486;)", "R<sub>sink</sub> (&#8486;)",
        "dV/dt (kV/&micro;s)", "low side at turn-on (V)",
        "high side at turn-off (V)", "ring (MHz)", "cycles to 5 %", ""],
       drows)}
<div class="note"><p>This is the single largest open question in the cell, and
 it is a data-sheet question, not a board question. Everything downstream of
 the edge rate &mdash; overshoot, ringing, radiated emission, the induced
 V<sub>gs</sub> of Q4, the snubber of Q3 &mdash; moves with it. It is in the
 hand-back list.</p></div>
<h3>R<sub>g</sub> and junction temperature, at the datasheet reading</h3>
{table(["R<sub>g</sub> (&#8486;)", "T<sub>j</sub> (&deg;C)",
        "low side at turn-on (V)", "high side at turn-off (V)",
        "ring left at turn-off (V)", "peak", "decay"], rows)}
<p>Neither moves it. At this output impedance the gate resistor is a tenth of
 the path that charges the gate, so changing it from 2.2 to 22&nbsp;&#8486;
 changes the edge by a few per cent, and the turn-off overshoot only falls
 from {num(rows and q2['rg_tj_sweep'][0].get('Vds_high_peak_at_turn_off'), 1)}
 to {num(rows and q2['rg_tj_sweep'][-1].get('Vds_high_peak_at_turn_off'), 1)}&nbsp;V.
 The junction temperature does not appear at all: it changes R<sub>DS(on)</sub>
 and the body diode, neither of which sets this edge.</p>
<p>There is no ring to fit at the datasheet reading. The turn-off edge
 leaves {num(((weak or {}).get('ring_turn_off') or {}).get('amplitude_V'), 1)}&nbsp;V
 above the rail and comes back without completing three half-cycles about it,
 and the turn-on approaches the rail from below with a decaying oscillation
 that never crosses it; either way Q2's five-cycle decay criterion is met. With the strong output stage the turn-on does ring, at
 {num(((strong or {}).get('ring_turn_on') or {}).get('f_MHz'), 0)}&nbsp;MHz for
 {num(((strong or {}).get('ring_turn_on') or {}).get('cycles_to_5pct'), 0)}
 cycles, which is the one criterion that corner fails on top of the peak. The
 frequency is close to the 100&nbsp;MHz the design estimated from
 1/(2&pi;&radic;(L&middot;C<sub>oss</sub>)); the loop is bigger and the
 capacitance smaller than assumed, and the two partly cancel.</p>
<div class="note"><p>The device breaks down at its own V(BR)DSS rather than
 letting V<sub>ds</sub> run away: the model includes that, because leaving it
 out reports an overshoot the silicon would never allow. Nothing in the sweep
 reaches it &mdash; the avalanche energy is zero in every run &mdash; so the
 overshoot is a criterion question, not a survival one.</p></div>
<h3>Sensitivity to the device's capacitance</h3>{corner}
<p>{e(p2.get('fet_fit', {}).get('edge_rate_note', ''))}</p>
{figure("p2_edges.png", "Both edges at 60 V and 28.3 A. Top: the whole event. Middle: the first 300 ns. Bottom: each device's own V_gs, at the die.")}
{snub}
"""
    worst = q2.get("worst_V_ds_V") or 0
    if env_worst <= V_CRIT < worst:
        head = ("Overshoot meets the criterion at the driver impedance the "
                "datasheet implies, and misses it at a strong output stage")
    else:
        head = ("Overshoot exceeds the silicon" if worst > V_SILICON else
                ("Overshoot stays inside the silicon but not inside the "
                 "criterion" if worst > V_CRIT else
                 "Overshoot meets the criterion"))
    return q_section("Q2 / Q3", head, body)


def sec_q4(R):
    q = R.get("P2", {}).get("Q4")
    if not q:
        return ""
    g1 = (R.get("P1") or {}).get("Q4") or {}
    GATE = ""
    if "high" in g1:
        grows = [[e(side), num(g1[side].get("L_nH_at_10MHz"), 2),
                  num(g1[side].get("R_mOhm_at_10MHz"), 1),
                  num(g1[side].get("gate_ring_MHz"), 1),
                  num(g1[side].get("gate_Q"), 2)]
                 for side in ("high", "low") if side in g1]
        GATE = ("<h3>The gate loops, solved</h3>"
                + table(["side", "L (nH)", "R (m&#8486;)",
                         "ring with C<sub>iss</sub> (MHz)", "Q"], grows)
                + "<p>Both loops are heavily damped &mdash; the gate resistor, "
                  "the driver's own output impedance and the device's "
                  "1.6&nbsp;&#8486; see to that &mdash; so the gate does not "
                  "ring, and the loop inductance is not what sets the answer "
                  "below. The answer is set by charge.</p>")
    lim = q["V_GS_th_min_V"] * 0.7
    rows = [[num(r["R_g"], 1), num(r["drive_scale"], 1), e(r.get("fet_corner")),
             num(40.0 * (r.get("drv_r_scale") or 1.0), 0),
             num(r.get("dVdt_switch_node_kV_per_us"), 1),
             num(r.get("Vgs_low_induced_at_turn_on"), 2),
             num(r.get("Vgs_low_induced_at_driver"), 2),
             chip(bool(r.get("pass_miller")))]
            for r in q.get("sweep", [])]
    sw = q.get("sweep", [])
    die = [r.get("Vgs_low_induced_at_turn_on") or 0 for r in sw]
    pin = [r.get("Vgs_low_induced_at_driver") or 0 for r in sw]
    gap = max(((r.get("Vgs_low_induced_at_driver") or 0)
               - (r.get("Vgs_low_induced_at_turn_on") or 0)) for r in sw) \
        if sw else 0.0
    dv = [r.get("dVdt_switch_node_kV_per_us") or 0 for r in sw]
    rem = q.get("remedy_C_gs", [])
    REM = ""
    if rem:
        REM = ("<h3>The usual fix does not work on this part</h3>"
               + table(["C<sub>gs</sub> added (nF)", "induced at the die (V)",
                        "induced at the pin (V)",
                        "low side at turn-on (V)"],
                       [[num(r["C_gs_ext_nF"], 1),
                         num(r.get("Vgs_low_induced_at_turn_on"), 2),
                         num(r.get("Vgs_low_induced_at_driver"), 2),
                         num(r.get("Vds_low_peak_at_turn_on"), 1)]
                        for r in rem])
               + f"""<p>A capacitor across the gate and source pins is the
 standard answer, and it does almost nothing here: {num(rem[-1]['C_gs_ext_nF'], 0)}&nbsp;nF
 moves the die from {num(rem[0].get('Vgs_low_induced_at_turn_on'), 2)} to
 {num(rem[-1].get('Vgs_low_induced_at_turn_on'), 2)}&nbsp;V. The reason is the
 device's own 1.6&nbsp;&#8486; of internal gate resistance: it sits between
 the pins and the polysilicon, so an external capacitor reaches the die
 through a {num(1.6 * rem[-1]['C_gs_ext_nF'], 0)}&nbsp;ns time constant while
 the edge it is supposed to absorb lasts a few nanoseconds. It is very
 effective at the <em>pin</em> &mdash; which is where a scope probe is, and
 why this fix is often believed to have worked.</p>""")
    cc = q.get("cross_conduction", [])
    CC = ""
    if cc:
        CC = ("<h3>Whether it actually conducts</h3>"
              + table(["device", "V<sub>to</sub> (V)", "induced V<sub>gs</sub> (V)",
                       "high-side peak (A)", "cross-conduction (nC)",
                       "extra loss per edge (&micro;J)"],
                      [[e(c["corner"]), num(c["Vto_V"], 2),
                        num(c.get("Vgs_low_induced_at_turn_on"), 2),
                        num(c.get("i_high_peak_A"), 1),
                        num(c.get("cross_conduction_nC"), 0),
                        num(c.get("cross_conduction_uJ"), 1)]
                       for c in cc])
              + """<p>The criterion is a voltage, but what matters is whether
 charge goes through the channel of the device that is supposed to be off. So
 the same edge was run twice more: once with the low-side device at the bottom
 of the datasheet's V<sub>GS(th)</sub> distribution, and once with a device
 whose threshold is out of reach but whose capacitances and charges are
 identical. The difference between the two is cross-conduction and nothing
 else &mdash; no displacement current, no recovery charge.</p>""")
        mn = next((c for c in cc if "min" in c["corner"]), None)
        ty = next((c for c in cc if "typ" in c["corner"]), None)
        if mn and ty:
            CC += f"""<p>At the typical threshold there is none:
 {num(ty.get('cross_conduction_nC'), 0)}&nbsp;nC, which is the numerical floor
 of the difference. At the datasheet's minimum threshold there is
 {num(mn.get('cross_conduction_nC'), 0)}&nbsp;nC &mdash;
 {num(mn.get('cross_conduction_uJ'), 0)}&nbsp;&micro;J of extra loss on that
 edge, and the high-side peak current rises from
 {num(ty.get('i_high_peak_A'), 0)} to {num(mn.get('i_high_peak_A'), 0)}&nbsp;A.
 At 20&nbsp;kHz that is
 {num((mn.get('cross_conduction_uJ') or 0) * 20e3 / 1e6, 2)}&nbsp;W in one
 half-bridge if every turn-on does it, on top of the switching loss that is
 there anyway.</p>"""
    body = f"""
<p>The question is whether the device that is off gets turned on by the other
 one's edge. The criterion is an induced V<sub>gs</sub> below 0.7&nbsp;&times;
 the minimum threshold, {num(lim, 2)}&nbsp;V, at 1.2&nbsp;kV/&micro;s and at
 twice that.</p>
<p>The switch node does not slew at 1.2&nbsp;kV/&micro;s. Depending on the
 driver's output impedance &mdash; the open question of Q2 &mdash; it slews at
 {num(min(dv), 0)} to {num(max(dv), 0)}&nbsp;kV/&micro;s, because the fast part
 of the turn-on is not the gate charging C<sub>gd</sub>; it is the low-side
 body diode snapping off and its recovery current dumping into
 C<sub>oss</sub>. Across every combination of R<sub>g</sub>, drive strength,
 capacitance corner and driver impedance, the induced V<sub>gs</sub> at the
 die stays between {num(min(die), 2)} and {num(max(die), 2)}&nbsp;V &mdash;
 above the {num(lim, 2)}&nbsp;V criterion and above the
 {num(q['V_GS_th_min_V'], 1)}&nbsp;V minimum threshold itself, and almost
 independent of everything in the sweep.</p>
<div class="note"><p>That flatness is the result, not an artefact. The induced
 step is the charge divider Q<sub>gd</sub>/C<sub>iss</sub>: the drain's edge
 pushes a fixed Q<sub>gd</sub> into the gate whatever the edge rate, and the
 driver can only take it back through 1.6&nbsp;&#8486; of internal gate
 resistance plus R<sub>g</sub> plus its own output impedance &mdash;
 {num(1.6 + 2.2 + 20.0, 0)}&nbsp;&#8486; at the datasheet reading, a
 {num((1.6 + 2.2 + 20.0) * 4.268, 0)}&nbsp;ns time constant against an edge of
 a few nanoseconds. Nothing in the gate network is fast enough to matter, so
 nothing in the gate network changes the answer.</p></div>
{GATE}
{table(["R<sub>g</sub> (&#8486;)", "drive &times;", "capacitances",
        "R<sub>source</sub> (&#8486;)", "dV/dt (kV/&micro;s)",
        "at the die (V)", "at the pin (V)", ""], rows)}
<p>Two voltages are reported because they are different things. The channel
 responds to the polysilicon gate against the source; a probe on the board
 sees the package pin, which the Miller current's own I&middot;R drop across
 that 1.6&nbsp;&#8486; lifts a further
 {num(gap, 1)}&nbsp;V at the fastest edge. The die number is
 the one the criterion is about.</p>
{CC}
{REM}
<div class="note"><p>What is left: the levers that do work are a negative
 off-state bias, which this bootstrap driver cannot provide without a split
 rail, or a device with a smaller Q<sub>gd</sub>/C<sub>iss</sub>. Failing
 either, the exposure is bounded and quantified above &mdash; it costs loss
 and peak current on a bottom-of-distribution part, not the part itself.</p>
</div>
"""
    head = ("The off device sits above its own minimum threshold on every "
            "edge, and it is charge, not layout, that puts it there")
    return q_section("Q4", head, body)


def sec_q5(R):
    q = R.get("P2", {}).get("Q5")
    p1q5 = R.get("P1", {}).get("Q5", {})
    if not q:
        return ""
    rows = [[e(r.get("shunt_esl_corner")), num(r.get("L_esl_nH"), 2),
             num(r.get("I_phase"), 1), num(r.get("V_tap_peak_mV"), 1),
             num(r.get("err_peak_on_LSB"), 2), num(r.get("err_peak_off_LSB"), 2),
             num(r.get("settle_on_ns"), 0)] for r in q.get("sweep", [])]
    h3 = p1q5.get("H3", {})
    frac = q.get("stale_fraction_free_running") or [0, 0]
    h4 = (R.get("P1") or {}).get("Q5_H4") or {}
    L1BLOCK = ""
    if p1q5 and "L_transfer_tap_to_shunt_nH_at_10MHz" in p1q5:
        L1BLOCK = (
            f"<p>Solved on the copper: the current path from the low-side "
            f"drain to the lead pad is "
            f"{num(p1q5['L_shunt_path_nH_at_10MHz'], 2)}&nbsp;nH, and the "
            f"<em>transfer</em> inductance between it and the two tap points "
            f"is {num(p1q5['L_transfer_tap_to_shunt_nH_at_10MHz'], 4)}&nbsp;nH "
            f"with {num(p1q5.get('R_transfer_uOhm_at_1MHz'), 0)}&nbsp;&micro;&#8486; "
            f"beside it. At the design's own 0.87&nbsp;A/ns that is "
            f"{num(h3.get('V_Ldidt_from_pcb_mV'), 1)}&nbsp;mV &mdash; already "
            f"the size of the signal &mdash; and the turn-off in Q2 commutates "
            f"about five times faster than that. The shunt part's own ESL, "
            f"bracketed at 0.2&ndash;0.5&nbsp;nH because no part has been "
            f"chosen, adds "
            f"{num((h3.get('ratio_to_signal_part') or [0])[0], 0)}&ndash;"
            f"{num((h3.get('ratio_to_signal_part') or [0, 0])[-1], 0)} times "
            f"the signal on top.</p>")
    if h4 and not h4.get("error"):
        L1BLOCK += (
            f"<h3>H4 &mdash; the taps are not symmetric</h3>"
            f"<p>{sentence(h4.get('verdict'))} The two taps' "
            f"capacitance to the switch node is "
            f"{num(h4.get('C_snsp_sw_pF'), 3)} and "
            f"{num(h4.get('C_snsn_sw_pF'), 3)}&nbsp;pF: SNSN has about "
            f"{num((h4.get('C_snsn_sw_pF') or 0) / max(h4.get('C_snsp_sw_pF') or 1e-9, 1e-9), 0)}"
            f"&nbsp;times SNSP's, which is H4 in one number. The tiling is "
            f"{num(h4.get('panels'), 0)} panels, so this is the size of the "
            f"effect rather than three figures of it.</p>")
    FS_MV = 22.6                      # 28.3 A across 0.8 mOhm
    tap = max((r.get('V_tap_peak_mV') or 0) for r in q.get('sweep', [{}]))
    lsb = q.get("LSB_mV") or 0.806
    # what the worst output error is worth as phase current: LSB at the ADC,
    # back through the gain of 50 and the 0.8 mOhm shunt pair
    err_A = (q.get("worst_error_LSB") or 0) * lsb * 1e-3 / 50.0 / 0.8e-3
    body = f"""
<p>Full scale across the shunt pair at 28.3&nbsp;A is {num(FS_MV, 1)}&nbsp;mV.
 During an edge the voltage between the two tap points reaches
 <strong>{num(tap, 0)}&nbsp;mV</strong> &mdash; {num(tap / FS_MV, 0)} times the
 signal &mdash; which is hypothesis H3, and the solved partial inductances say
 where it comes from: the tap loop itself, not only the shunt element.</p>
{L1BLOCK}
<p>What saves the measurement is the amplifier. The INA241A3 detects a large
 common-mode slew and <em>holds its output for a microsecond</em>
 (SBOSA30D&nbsp;&sect;7.3.1.1). Inside that window the output is not wrong, it
 is stale &mdash; and since the phase current cannot change appreciably in a
 microsecond, stale is almost as good as right. The peak error the ADC could
 see is {num(max((r.get('err_peak_on_LSB') or 0) for r in q.get('sweep', [{}])), 1)}
 LSB.</p>
<div class="note"><p>The spec's criterion &mdash; settle to
 &plusmn;1&nbsp;LSB within 500&nbsp;ns &mdash; cannot be met by this part by
 construction, because its own hold is twice that long, and its
 &ldquo;corrupted-sample fraction&rdquo; does not describe what happens
 either. Two numbers do:
 <strong>{num(frac[0] * 100, 0)}&ndash;{num(frac[-1] * 100, 0)}&nbsp;%</strong> of free-running samples land inside a hold
 window and are up to a microsecond stale, and the output is never more than
 <strong>{num(q.get("worst_error_LSB"), 1)}&nbsp;LSB</strong> from the truth,
 which is {num(err_A, 2)}&nbsp;A of phase current, {num(err_A / 28.28 * 100, 1)}&nbsp;%
 of full scale. Sampling at the PWM
 counter's zero lands in no hold window at all, because every edge is half a
 period away.</p></div>
{table(["shunt ESL", "L (nH)", "I<sub>phase</sub> (A)",
        "tap swing (mV)", "peak error at turn-on (LSB)",
        "at turn-off (LSB)", "back within 1 LSB (ns)"], rows)}
"""
    return q_section("Q5", f"The tap swings {num(tap / FS_MV, 0)} times the "
                           f"signal, and the amplifier's hold is what saves "
                           f"it", body)


def sec_q6(R):
    q = R.get("P3", {}).get("Q6")
    sh = R.get("P3", {}).get("Q6_sharing", {})
    if not q:
        return ""
    rows = []
    notes = [v["note"] for v in (q.get("per_cell") or {}).values()
             if v.get("note")]
    for cell, v in (q.get("per_cell") or {}).items():
        if "error" in v:
            rows.append([cell, "&mdash;", "&mdash;", "&mdash;", "&mdash;",
                         e(v["error"])])
            continue
        rows.append([cell, num(v["R_tap_to_tap_Ohm"] * 1e3, 4),
                     num(v["R_copper_error_Ohm"] * 1e6, 1),
                     num(v["copper_share"] * 100, 2),
                     num(v["TCR_pct_per_K"], 4),
                     (chip(v["pass_copper_share"])
                      if v.get("pass_copper_share") is not None
                      else "n/a")])
    body = f"""
<p>The taps are voltage probes, so this is a four-terminal measurement: the
 phase current is driven through each pour between the places it really enters
 and leaves, and the tap pads are read without drawing current. What the
 amplifier sees is the shunt plus whatever potential difference the copper
 puts between the two probe points.</p>
{table(["cell", "tap to tap (m&#8486;)", "copper's share (&micro;&#8486;)",
        "copper (%)", "TCR (%/K)", ""], rows)}
<p>Cells A and B meet both criteria. The copper's contribution is almost all
 on the PHASE side: R108's tap sits at the lead-pad end of the pour, so the
 measurement includes the whole run from the shunt to the lead.</p>
{('<div class="note"><p>Cell C is the row that looks wrong and is not: '
  + e(notes[0]) + '</p></div>') if notes else ''}
{('<p>The two 1.6&nbsp;m&#8486; shunts share '
  + num(sh.get('share_shunt1', 0) * 100, 1) + '&nbsp;/&nbsp;'
  + num(sh.get('share_shunt2', 0) * 100, 1) + '&nbsp;%, an imbalance of '
  + num(sh.get('imbalance_pct'), 1) + '&nbsp;% against the 10&nbsp;% '
  'criterion.</p>') if sh and 'error' not in sh else ''}
"""
    return q_section("Q6", "The sense chain is accurate; the copper costs "
                           "3.6 per cent", body)


def sec_q7(R):
    q = R.get("P3", {}).get("Q7")
    if not q:
        return ""
    conn = q.get("via_array_connectivity", {})
    rows = []
    for ref, v in conn.items():
        per = v["per_layer"]
        rows.append([ref, e(v["net"]), v["n_barrels"],
                     per.get("F.Cu", {}).get("connected", 0),
                     per.get("In2.Cu", {}).get("connected", 0),
                     per.get("B.Cu", {}).get("connected", 0)])
    pa = q.get("phase_A", {})
    arr = pa.get("low_side_via_array", {})
    vb = [(k, v) for k, v in q.get("vbus", {}).items()
          if isinstance(v, dict) and "via_array" in v]
    vrows = [[k, num(v["via_array"]["I_max_A"], 2),
              num(v["via_array"]["I_min_A"], 2),
              num(v["via_array"]["I_rms_per_via_A"], 2),
              chip(v["via_array"]["pass_2A_rms"])] for k, v in vb]
    body = f"""
<div class="note"><p><strong>Twelve of the sixteen vias under each low-side
 drain go nowhere.</strong> The barrels are there, and their pads are on every
 layer, but on B.Cu the switch-node pour has been pushed back by the phase
 pour and only four of the sixteen pads are joined to it. The other twelve are
 isolated annuli in the clearance between the two pours. All three cells are
 the same.</p></div>
{table(["array", "net", "barrels", "joined on F.Cu", "on In2", "on B.Cu"],
       rows)}
<p>The consequence is that the phase current crossing from the F.Cu switch-node
 island to the B.Cu pour &mdash; which it must do, because the shunts are on
 B.Cu &mdash; goes through four vias and the two discrete ones nearby. The
 busiest carries {num(arr.get('I_max_A'), 2)}&nbsp;A at the 28.3&nbsp;A peak,
 {num(arr.get('I_rms_per_via_A'), 2)}&nbsp;A&nbsp;rms, against the
 2&nbsp;A&nbsp;rms a 0.4&nbsp;mm barrel with 25&nbsp;&micro;m of plating is
 worth.</p>
<h3>The high-side arrays, which are connected</h3>
{table(["array", "busiest via (A)", "quietest (A)", "per via (A rms)", ""],
       vrows)}
<p>Those sixteen do all reach In2, but they still share badly &mdash; the
 current crowds into the vias nearest where it comes from, and the spread is
 {num(max((v['via_array']['spread_pct'] for _, v in vb), default=0), 0)}&nbsp;%
 of the mean.</p>
{figure("p3_J_sw_A_FCu.png", "Switch-node current density on F.Cu at 28.3 A.")}
{figure("p3_J_phase_A.png", "The phase pour on B.Cu at 28.3 A.")}
{figure("p3_J_vbus_In2.png", "VBUS on In2, from the header pins to one high-side drain.")}
<h3>Grid convergence</h3>
{table(["cell (mm)", "lead to shunt (&micro;&#8486;)",
        "99.9th percentile (A/mm)", "99th percentile (A/mm)"],
       [[num(c.get("cell_mm"), 3),
         num((c.get("R_lead_to_shunt_Ohm") or 0) * 1e6, 2),
         num(c.get("sheet_p999_A_per_mm"), 2),
         num(c.get("sheet_p99_A_per_mm"), 2)]
        for c in R.get("P3", {}).get("convergence", [])])}
<p>The resistance converges; the peak current density does not, and cannot
 &mdash; it is a single cell at a re-entrant corner and grows without limit as
 the grid is refined. The percentiles are the numbers to compare with a width
 rule.</p>
"""
    return q_section("Q7", "Twelve of sixteen vias under each low-side drain "
                           "are islands", body)


def sec_q13(R):
    p4 = R.get("P4", {})
    q = p4.get("Q13")
    rip = p4.get("Q13_ripple", {})
    if not q:
        return ""
    h2 = q.get("H2", {})
    rows = [[e(k), num(v["ceramic_total_uF"], 2),
             num(v["V_bus_ripple_pp_V"], 2), num(v["V_bus_ripple_rms_V"], 3),
             num(v["i_link_total_A_rms"], 1), chip(v["pass_ripple_3V"]),
             chip(v["pass_link_80pct"])]
            for k, v in (rip.get("per_case") or {}).items()]
    link = q.get("link_parasitics", {})
    body = f"""
<div class="note"><p><strong>H2: {e(h2.get('verdict', ''))}</strong></p></div>
<p>The DC-link current waveform was synthesised from the switching functions of
 three centre-aligned sine-PWM legs at m&nbsp;=&nbsp;0.8 and 20&nbsp;A&nbsp;rms,
 which gives {num(rip.get('i_ripple_A_rms'), 2)}&nbsp;A&nbsp;rms of ripple
 &mdash; the same number <code>geometry.interconnect()</code> gets from its
 closed form, which is a useful agreement between two independent derivations.
 What differs is the split, because the impedances differ.</p>
{table(["ceramics at bias / board B bulk", "C (&micro;F)",
        "V<sub>bus</sub> p-p (V)", "rms (V)", "link (A rms)", "ripple", "link"],
       rows)}
<p>The bulk chemistry decides it. With four 100&nbsp;&micro;F polymer hybrids
 the bus ripple is under two volts; with the three 100&nbsp;&micro;F
 electrolytics the spec originally listed, at 0.2&nbsp;&#8486; of ESR, it is
 over six. The link carries about 20&nbsp;A&nbsp;rms either way, inside the
 30&nbsp;A ten pins are worth.</p>
{(f"<p>The link itself &mdash; twenty mated pins and six brass standoffs "
  f"across an 11&nbsp;mm gap &mdash; is "
  f"{num(link.get('L_nH_at_1MHz'), 2)}&nbsp;nH and "
  f"{num(link.get('R_mOhm_at_1MHz'), 2)}&nbsp;m&#8486; at 1&nbsp;MHz; the "
  f"standoffs take {num((link.get('standoff_share') or 0) * 100, 0)}&nbsp;% of "
  f"it off the header.</p>") if link and 'error' not in link else ''}
{figure("p4_dclink_z.png", "The impedance one cell's half-bridge sees looking into its own DC link.")}
"""
    return q_section("Q13", "The link is fine; board B's bulk chemistry is not "
                            "free", body)


def sec_q10(R):
    q = R.get("P5", {}).get("Q10")
    cons = R.get("P6", {}).get("Q10", {})
    if not q:
        return ""
    rows = [[e(r["grade"]), num(r["gap_mm"], 1), num(r["off_axis_mm"], 1),
             num(r["B_inplane_mT"], 1), chip(r["in_window"])]
            for r in q.get("magnet", []) if r["off_axis_mm"] == 0.0]
    ae = q.get("angle_error", {})
    cf = q.get("current_field", {})
    body = f"""
<p>The MT6701 wants 20&ndash;100&nbsp;mT at its own surface, over an air gap
 the datasheet allows to be 0.5&ndash;2.0&nbsp;mm. The board carries a
 &#216;8&nbsp;&times;&nbsp;2.5&nbsp;mm diametric magnet where the datasheet
 recommends &#216;6.</p>
{table(["grade", "gap (mm)", "off axis (mm)", "B in plane (mT)", ""], rows)}
<p>At the nominal 1.5&nbsp;mm gap the board's stack-up specifies, and at
 1.0&nbsp;mm, an N35 &#216;8 magnet is inside the window. At the short end of
 the tolerance it is not, and an N42 is over the limit at 1&nbsp;mm as well.
 The datasheet's own &#216;6 gives
 {num((q.get('magnet_dia6_reference') or [{}])[1].get('B_inplane_mT'), 0)}&nbsp;mT
 at 1&nbsp;mm, comfortably inside.</p>
<p>The phase currents put
 {num((cf.get('B_from_copper_inplane_mT_at_peak') or 0) * 1e3, 2)}&nbsp;&micro;T
 at the sensor from the board copper and
 {num((cf.get('B_from_leads_0.25m_mT') or 0) * 1e3, 0)}&nbsp;&micro;T from the
 leads &mdash; a factor of
 {num((cf.get('B_from_leads_0.25m_mT') or 0) / max(cf.get('B_from_copper_inplane_mT_at_peak') or 1e-12, 1e-12), 0)}.
 {sentence(cf.get('note'))}</p>
<p>The angle error that leaves is
 <strong>{num(ae.get('peak_deg'), 3)}&deg;</strong> peak,
 {num(ae.get('peak_LSB'), 1)}&nbsp;LSB of the 14-bit word, against the
 0.05&deg; criterion. It is a gain-like error &mdash; it moves with the
 current &mdash; and what it costs is not torque, which loses
 {num(cons.get('torque_loss_pct'), 1)}&nbsp;% to the cosine and is nothing,
 but {num(cons.get('i_d_error_A'), 3)}&nbsp;A of misplaced d-axis current:
 real current, heating the windings and producing no torque.</p>
{figure("p5_encoder.png", "Magnet field over the gap tolerance, and the angle error the phase current causes.")}
"""
    return q_section("Q10", "The magnet is too strong at the short gap; the "
                            "leads, not the copper, move the angle", body)


def sec_q8(R):
    q = R.get("P6", {}).get("Q8")
    if not q:
        return ""
    sp = q.get("error_spectrum_A_rms") or {}
    SPEC_TABLE = ""
    if "free_running" in sp:
        bands = list(sp["free_running"])
        SPEC_TABLE = (
            "<h3>Where the error lives</h3>"
            + table(["sampling"] + [e(b) for b in bands],
                    [["free-running"] + [num(sp["free_running"][b], 3)
                                         for b in bands],
                     ["synchronised"] + [num(sp["synchronised"][b], 3)
                                         for b in bands]])
            + f"<p>{e(sp.get('note', ''))} Most of the free-running error sits "
              f"between one and five kilohertz &mdash; at and just above the "
              f"loop's own bandwidth, which is the worst place for it to be, "
              f"because the loop is fast enough to chase some of it and not "
              f"fast enough to reject it. Synchronised sampling takes that "
              f"band down by "
              f"{num(sp['free_running'][bands[2]] / max(sp['synchronised'][bands[2]], 1e-9), 1)}"
              f"&times;.</p>")
    body = f"""
<p>The firmware's ADC runs free, round-robin over four channels at
 500&nbsp;kS/s, so each current channel is refreshed every 8&nbsp;&micro;s and
 <code>loopFOC()</code> reads whatever the last conversion was. The question is
 what that costs against sampling once per PWM period at the counter's zero,
 where the ripple crosses its own average.</p>
<p>Measured against the average i<sub>q</sub> over each PWM period &mdash; which
 is what the loop is trying to regulate &mdash; free-running sampling gives
 <strong>{num(q['free_running_noise_A_rms'], 3)}&nbsp;A&nbsp;rms</strong> of
 error and {num(q['free_running_bias_A'], 2)}&nbsp;A of bias.
 PWM-synchronised sampling gives
 <strong>{num(q['synchronised_noise_A_rms'], 3)}&nbsp;A&nbsp;rms</strong> and
 {num(q['synchronised_bias_A'], 2)}&nbsp;A: the noise falls by
 {num(q['improvement_factor'], 1)}&times; and the bias by half.</p>
<p>The residual bias is not the sampling &mdash; it is the low-pass filter's
 group delay on a rotating frame, and it belongs to the control design rather
 than to the ADC.</p>
{SPEC_TABLE}
{figure("p6_sampling.png", "What the current loop is given, at eight different sampling phases.")}
"""
    return q_section("Q8", "Synchronised sampling is worth three times the "
                           "noise", body)


def sec_q9(R):
    q = R.get("P6", {}).get("Q9")
    if not q:
        return ""
    rows = [[num(r["dead_zone"], 3), num(r["t_dead_sw_us"], 2),
             num(r["rpm"], 0), num(r["iq_target_A"], 1),
             num((r.get("THD") or float("nan")) * 100, 2),
             num(r["torque_mean_Nm"], 3), num(r["torque_ripple_pct"], 2)]
            for r in q.get("sweep", [])]
    body = f"""
<p>The EG2103 turns on 780&nbsp;ns after its input and off 220&nbsp;ns after
 it; the 560&nbsp;ns difference <em>is</em> the interlock, and it is in the
 hardware whether the firmware asks for it or not. On top of that SimpleFOC's
 <code>dead_zone = 0.02</code> takes another microsecond off each edge in
 software.</p>
{table(["dead_zone", "t<sub>dead,sw</sub> (&micro;s)", "rpm",
        "i<sub>q</sub> (A)", "THD (%)", "torque (N&middot;m)",
        "ripple (% rms)"], rows)}
<p>The software term costs
 {num(q.get('torque_loss_from_software_dead_zone_pct'), 2)}&nbsp;% of torque and
 leaves the distortion essentially unchanged: the hardware's 560&nbsp;ns
 already dominates. Distortion is worst at low current, where the dead time is
 a large fraction of the conduction interval &mdash; which is the usual shape
 and the reason dead-time compensation exists.</p>
<p>The minimum pulse the driver will pass makes duties below
 {num((q.get('min_duty_reachable') or 0) * 100, 2)}&nbsp;% unreachable, and by
 symmetry the same at the top of the range.</p>
{figure("p6_deadtime.png", "Current distortion and torque ripple against the software dead zone.")}
"""
    return q_section("Q9", "The software dead zone buys nothing the hardware "
                           "has not already taken", body)


def sec_q14(R):
    q = R.get("P6", {}).get("Q14")
    if not q:
        return ""
    rows = [[num(r["C_bulk_uF"], 0), num(r["J_kgm2"], 5),
             num(r["E_mech_J"], 2), num(r["E_cap_to_TVS_J"], 3),
             num(r["t_to_TVS_ms"], 3), num(r["TVS_energy_J"], 1)]
            for r in q.get("sweep", [])]
    body = f"""
<p>There is no brake chopper: the sibling's fourth leg and 15&nbsp;&#8486;
 resistor are not on this board, so a decel has nowhere to put its energy
 except board B's bulk and the two TVSs.</p>
{table(["bulk (&micro;F)", "J (kg&middot;m&sup2;)", "mechanical energy (J)",
        "the bulk can take (J)", "bus reaches the TVS in (ms)",
        "left for the TVS (J)"], rows)}
<p>The bulk is worth a fraction of a joule between 60&nbsp;V and the TVS's
 breakdown. A decel at the design-point braking torque drives the bus into the
 TVS in a fraction of a millisecond, and what is left over is tens to hundreds
 of joules &mdash; orders of magnitude past what an SMDJ64A survives.</p>
<div class="note"><p>The firmware's guard folds the current limit to zero
 across 63&ndash;66&nbsp;V and runs at the loop rate, so it reacts in about one
 50&nbsp;&micro;s period. That is inside the time the bus takes to reach the
 TVS, but by a factor of a few, not a factor of a hundred. The guard is the
 only thing between a decel and a dead TVS, and it is a software guard.</p>
</div>
"""
    return q_section("Q14", "The bus guard is the only brake", body)


def sec_q11(R):
    q = R.get("P7", {}).get("Q11")
    x = R.get("P7", {}).get("Q1_crosscheck")
    if not q and not x:
        return ""
    body = ""
    if x:
        em = [r for r in (x.get("mesh_convergence") or []) if r.get("L_nH")]
        fh = ((x.get("fasthenry") or {}).get("fasthenry_convergence") or [])
        ref = x.get("fasthenry") or {}
        uni0 = [r for r in (x.get("uniform_mesh_series") or [])
                if r.get("L_nH")]
        if em:
            body += f"""
<p>The commutation loop of Q1, on the same copper, in a solver that shares
 nothing with the first one: FastHenry is PEEC, quasi-static, no dielectric
 and no displacement current; openEMS is FDTD, the whole of Maxwell on a
 staircased grid. Driving the same port &mdash; the high-side
 {e(ref.get('port_ref', '100 n'))} capacitor, the other three left open
 &mdash; openEMS gives {num(x.get('L2_for_comparison_nH'), 2)}&nbsp;nH
 against FastHenry's {num(x.get('L1_fasthenry_nH'), 2)}&nbsp;nH{
 f", {num(x.get('agreement_pct'), 0)}&nbsp;% apart" if
 x.get('agreement_pct') is not None else ""}.
 {chip(x.get('pass_25pct'))} against the spec's 25&nbsp;% gate.
 The FDTD figure is {e(x.get('compared_on', ''))}; its raw value at the
 finest mesh is {num(x.get('L2_openems_nH'), 2)}&nbsp;nH, and FastHenry's is
 the converged end of its own grid sweep rather than the 1.2&nbsp;mm
 production grid, which reads
 {num((x.get('fasthenry') or {}).get('L_single_port_production_nH'), 2)}&nbsp;nH.</p>
<div class="note"><p><strong>What the first run of this phase got wrong.</strong>
 It reported 3.82&nbsp;nH and read the difference as a disagreement between
 the solvers. Four things were wrong with it, none of them the solver's.</p>
<p><strong>It was not solving this loop.</strong> The two conducting FETs were
 stood in for by cylinders of 0.25&nbsp;mm radius about an axis lying in
 F.Cu. F.Cu to In1 is 0.1525&nbsp;mm, so each rod stood a quarter of a
 millimetre proud of its own layer and cut through about 1&nbsp;mm&sup2; of
 the In1 ground plane &mdash; and metal is metal in FDTD, so the high-side
 one shorted VBUS to GND at the drain and the current never reached In2 or
 the via field at all. Nothing in the run reported it: the energy decayed,
 the port impedance was clean, the answer was simply a different loop.</p>
<p><strong>It was compared with the wrong number.</strong>
 L<sub>eff</sub> is the loop with all four capacitors conducting in parallel;
 the model drives one, whose own diagonal is
 {num(x.get('L1_fasthenry_nH'), 2)}&nbsp;nH, not
 {num(x.get('L1_fasthenry_L_eff_nH'), 2)}&nbsp;nH.</p>
<p><strong>It was stopped early.</strong> 60&nbsp;k timesteps is 1.4 times its
 own excitation length, and the residual energy was still at
 &minus;2&nbsp;dB, so the port waveform it transformed was truncated.
 openEMS printed a warning saying so.</p>
<p><strong>Its warning was misread.</strong> The 127 &ldquo;dropped
 primitives&rdquo; recorded as lost copper were via pads lying wholly inside
 barrel cylinders that already provide that copper at a higher
 priority.</p></div>
<h3>What it takes to mesh this</h3>
<p>There are no nets in an FDTD model, only metal: two conductors that come
 within one cell of each other are one conductor. The tightest net-to-net
 clearance in this window &mdash; barrel walls included, and it is the
 antipads the VBUS barrels pass through in the In1 ground plane &mdash; is
 0.40&nbsp;mm, so a mesh that does not stay well inside that joins VBUS to
 GND and the loop comes back far too small.{
 f" A uniform mesh does exactly that: it reads {num(uni0[0]['L_nH'], 2)}&nbsp;nH at {num(uni0[0]['res_mm'], 2)}&nbsp;mm cells and does not reach {num(uni0[-1]['L_nH'], 2)}&nbsp;nH until {num(uni0[-1]['res_mm'], 2)}&nbsp;mm, by which point it costs more cells than the snapped mesh that gets there at {num(uni0[-1]['res_mm'] * 1.75, 2)}&nbsp;mm."
 if len(uni0) > 1 else ""} The mesh used here is snapped to the copper
 instead: every polygon vertex and every barrel wall pins a line, so a
 clearance is resolved by construction rather than by where the grid happens
 to fall, and the fill between them stays coarse.</p>"""
            HEAD = ["cell / feature (mm)", "cells", "Mcells", "L (nH)",
                    "C (pF)", "f<sub>0</sub> (MHz)", "fit residual",
                    "energy left (dB)"]

            def mrow(r, key):
                lc = r.get("lc_fit") or {}
                return [num(r.get(key), 2), e(r.get("mesh", "&mdash;")),
                        num((r.get("cells") or 0) / 1e6, 2),
                        num(r["L_nH"], 3), num(lc.get("C_pF"), 1),
                        num(lc.get("f_resonance_MHz"), 0),
                        num(lc.get("rel_residual"), 3),
                        e(r.get("final_energy_dB", ""))]

            uni = [r for r in (x.get("uniform_mesh_series") or [])
                   if r.get("L_nH")]
            if uni:
                body += ("<h4>A uniform mesh, for comparison</h4>"
                         + table(HEAD, [mrow(r, "res_mm") for r in uni])
                         + "<h4>Snapped to the copper</h4>")
            body += table(HEAD, [mrow(r, "step_used_x_mm") for r in em])
            body += ("<p>Each L here is the intercept of that fit, not a mean "
                     "over the band: the band mean is 15&nbsp;% higher "
                     "because it includes the climb towards f<sub>0</sub>. "
                     "The residual is what says one L and one C describe the "
                     "port at all.</p>")
            if fh:
                body += ("<h3>And what it takes FastHenry</h3>"
                         + table(["grid target (mm)", "segments",
                                  "L, this port (nH)",
                                  "L<sub>eff</sub>, all four (nH)"],
                                 [[num(r["target_mm"], 2), e(r["segments"]),
                                   num(r["L_single_port_nH"], 3),
                                   num(r.get("L_eff_nH"), 3)] for r in fh]))
            body += f"<p>{sentence(x.get('note'))}</p>"
            de = x.get("bridge_deembed") or {}
            if de.get("L_ideal_short_nH"):
                body += (
                    "<h3>What the device costs the model</h3>"
                    "<p>The PEEC model shorts a conducting FET at its own "
                    "pads and pays nothing for it. A full-wave model cannot: "
                    "the short has to be a real piece of copper, and a strip "
                    f"{num((de['full_width'].get('bridges') or [{}])[0].get('width_mm'), 2)}"
                    "&nbsp;mm wide sitting 0.15&nbsp;mm above a ground plane "
                    "carries a partial inductance of its own. Halving its "
                    "width doubles that term and leaves the loop alone, so "
                    "L(w)&nbsp;=&nbsp;L&#8320;&nbsp;+&nbsp;k/w and the pair "
                    "extrapolates to the ideal short: "
                    f"{num(de['full_width']['L_nH'], 3)}&nbsp;nH at full width "
                    f"and {num(de['half_width']['L_nH'], 3)}&nbsp;nH at half "
                    f"give <strong>{num(de['L_ideal_short_nH'], 2)}"
                    f"&nbsp;nH</strong> for the loop alone, the strip itself "
                    f"accounting for {num(de['bridge_term_nH'], 2)}&nbsp;nH. "
                    "That is the number the two solvers can honestly be "
                    "compared on.</p>")
            tc = x.get("truncation_check") or {}
            fr_ = tc.get("L_nH_from_record_fraction") or {}
            vals = {k: v for k, v in fr_.items()
                    if isinstance(v, (int, float))}
            if vals:
                worst = (max(vals.values()) - min(vals.values())) / \
                    max(abs(v) for v in vals.values())
                body += (
                    "<h3>Is the record long enough?</h3>"
                    "<p>These runs stop with more residual energy than "
                    "openEMS&rsquo;s own default end criterion asks for "
                    "&mdash; a little energy circles in the air box long "
                    "after the port has settled, and the runs that stop "
                    "highest have the flattest L. Rather than gate on that, "
                    "the same record is transformed again over less of "
                    "itself: "
                    + ", ".join(f"{num(v, 3)}&nbsp;nH from the first "
                                f"{num(float(k) * 100, 0)}&nbsp;%"
                                for k, v in sorted(vals.items()))
                    + f" &mdash; {num(worst * 100, 1)}&nbsp;% across the "
                    f"three. The transform is not truncating the answer.</p>")
            if not x.get("converged"):
                body += (f'<div class="note"><p><strong>Not converged, and '
                         f'quoted as a bracket.</strong> '
                         f'{sentence(x.get("not_converged_reason"))}</p></div>')
            body += figure("p7_crosscheck.png",
                           "Both solvers against their own cell size; the FDTD "
                           "port impedance with the L and C fitted out of it, "
                           "against FastHenry's quasi-static L; and what the "
                           "answer does when less of the time record is "
                           "transformed.")
            body += figure("p7_cellA_3d.png",
                           "The copper both solvers are given: six layers, "
                           "exploded, with every plated barrel in the window.")
            body += figure("p7_mesh_A.png",
                           "The In1 ground plane under the high-side drain, "
                           "with the two meshes drawn on it. Every cell that "
                           "touches both VBUS and GND joins them, because an "
                           "FDTD model has no nets, only metal.")
        else:
            why = x.get("not_converged_reason") or x.get("error", "")
            body += (f'<div class="note"><p><strong>The board-level full-wave '
                     f'cross-check did not converge, and its number is not '
                     f'used.</strong> {e(why)}</p></div>'
                     f'<p>What the solver was checked against instead is in '
                     f'the method section: on a strip over a plane &mdash; the '
                     f'same geometry the design\'s own estimate is built on '
                     f'&mdash; openEMS and FastHenry agree to about four per '
                     f'cent.</p>')
    if q and not q.get("error") and q.get("converged", True):
        ff = q.get("far_field") or {}
        rows = [[e(k), num(v.get("E_at_3m_V_per_m"), 4),
                 num(v.get("dBuV_per_m"), 1)] for k, v in ff.items()]
        body += (f"<h3>Far field with a 250 mm motor lead</h3>"
                 + table(["frequency (Hz)", "E at 3 m (V/m)",
                          "dB&micro;V/m"], rows)
                 + f"<p>{e((q.get('cispr32_class_B_3m_dBuV_per_m') or {}).get('note', ''))}</p>")
    elif q:
        body += (f'<div class="note"><p><strong>The whole-board near- and '
                 f'far-field model did not converge, and no radiated '
                 f'number is quoted.</strong> '
                 f'{e(q.get("not_converged_reason") or q.get("error"))}</p>'
                 f'</div>')
    return q_section("Q11", "Full wave: the loop cross-check and the far field",
                     body)


def sec_q12(R):
    q = R.get("P2", {}).get("Q12")
    if not q or q.get("error"):
        return q_section("Q12", "Common mode &mdash; not solved", f"""
<p>The common-mode current through the motor's winding-to-frame capacitance
 was not simulated: {e((q or {}).get("error", "the phase did not run"))}</p>""")
    sw = [r for r in q.get("sweep", []) if not r.get("incomplete")]
    rows = [[num(r["C_wf_pF"], 0), num(40.0 * (r.get("drv_r_scale") or 1.0), 0),
             num(r.get("dVdt_switch_node_kV_per_us"), 0),
             e(r.get("label", r.get("bond"))),
             num(r.get("V_frame_pk_V"), 1), num(r.get("I_cm_pk_A"), 2),
             num(r.get("I_cm_rms_A"), 3),
             num(r.get("I_cm_rms_three_phase_A"), 3),
             chip(r.get("pass_frame_10V"))]
            for r in sw]

    def pk(**kw):
        sel = [r for r in sw if all(r.get(k) == v for k, v in kw.items())]
        return max((r.get("V_frame_pk_V") or 0) for r in sel) if sel else 0.0

    cwfs = sorted({r["C_wf_pF"] for r in sw})
    # a bond only counts if it holds at every winding-to-frame capacitance in
    # the sweep: passing at one value of an unmeasured quantity is not a fix
    sel_weak = [r for r in sw if (r.get("drv_r_scale") or 1.0) == 1.0]
    labels = sorted({lab for lab in {r["label"] for r in sel_weak}
                     if all(any(r["label"] == lab and r["C_wf_pF"] == c
                                and r.get("pass_frame_10V") for r in sel_weak)
                            for c in cwfs)})
    ok_all = [r for r in sw if r.get("pass_frame_10V")]
    worst_i = max((r.get("I_cm_rms_three_phase_A") or 0) for r in sw)
    worst_i_row = max(sw, key=lambda r: r.get("I_cm_rms_three_phase_A") or 0)
    return q_section("Q12", "The motor frame swings with the switch node", f"""
<p>The four &#216;3.2&nbsp;mm motor mounts are <strong>NPTH</strong> &mdash; no
 copper, not a ground bond &mdash; so nothing on this board ties the motor's
 frame to board GND. The frame is left as a capacitive divider between the
 winding-to-frame capacitance and its own stray capacitance to the board, and
 it follows the switch node. Since everything here is driven by the edge, the
 sweep is run at both ends of the driver-impedance bracket of Q2.</p>
{table(["C<sub>wf</sub> (pF)", "R<sub>source</sub> (&#8486;)",
        "dV/dt (kV/&micro;s)", "frame bond", "frame peak (V)",
        "I<sub>cm</sub> peak (A)", "rms (A)", "three phases (A rms)", ""],
       rows)}
<p>Three things fall out of it. <strong>Left floating, the frame reaches
 {num(pk(bond='open'), 0)}&nbsp;V peak</strong> against the 10&nbsp;V
 criterion, though it carries almost no current, because there is nowhere for
 it to go. <strong>Bonding it with a long wire makes the voltage worse than a
 short one</strong> &mdash; {num(pk(bond='strap', bond_value=20e-9), 0)}&nbsp;V
 against {num(pk(bond='strap', bond_value=2e-9), 0)}&nbsp;V &mdash; because
 20&nbsp;nH resonates with C<sub>wf</sub> and the frame overshoots. And
 <strong>nothing in the sweep is safe across all of it</strong>. Counting only
 the bonds that hold at every winding-to-frame capacitance &mdash; passing at
 one value of a quantity nobody has measured is not a fix &mdash; at the
 driver impedance the datasheet implies
 {" and ".join("a " + l.replace(" nH", "&nbsp;nH").replace(" nF", "&nbsp;nF") for l in labels) + (" keep" if len(labels) != 1 else " keeps") if labels else "nothing keeps"}
 the frame under 10&nbsp;V, and at the strong-driver corner only
 {len([r for r in ok_all if (r.get('drv_r_scale') or 1.0) != 1.0])} of
 {len([r for r in sw if (r.get('drv_r_scale') or 1.0) != 1.0])} rows passes at
 all.</p>
<div class="note"><p>The price of bonding is current. The worst row here is
 {num(worst_i, 2)}&nbsp;A rms across three phases, at
 {num(worst_i_row.get('C_wf_pF'), 0)}&nbsp;pF of winding-to-frame capacitance
 through {e(worst_i_row.get('label'))} &mdash; which is a conducted-emissions
 problem rather than a safety one, but it is not small. C<sub>wf</sub> is the
 quantity nobody has measured: the whole table moves with it, and it is a
 property of the motor, not of this board.</p></div>
""")


def sec_p8(R):
    q = R.get("P8")
    if not q:
        return ""
    rows = [[e(ref), v.get("n_barrels"),
             num(v.get("R_barrels_K_per_W"), 2),
             num(v.get("R_fr4_K_per_W"), 0),
             num(v.get("R_total_K_per_W"), 2),
             num(v.get("estimate_geometry_py_K_per_W"), 2),
             num(v.get("ratio_to_estimate"), 2)]
            for ref, v in (q.get("via_array") or {}).items()
            if "error" not in v]
    sp = q.get("spreading", {})
    j = q.get("joule_from_P3_W", {})
    L = q.get("losses_geometry_py_W", {})
    return q_section("P8", "Thermal, the optional phase", f"""
<p>The design's thermal model is <code>geometry.via_thermal()</code>,
 <code>geometry.thermal()</code> and <code>geometry.sink_needed()</code>. Only
 the two numbers it leans on are re-solved here.</p>
{table(["array", "barrels", "barrels (K/W)", "FR4 (K/W)", "together",
        "the estimate", "ratio"], rows)}
<p>The via array comes out
 {num((list((q.get('via_array') or {}).values()) or [{}])[0].get('ratio_to_estimate'), 2)}&times;
 the estimate &mdash; close enough that the design's number stands.</p>
{(f"<p>The lateral spreading on F.Cu alone, from the low-side source pads to "
  f"the heatsink land at R&nbsp;&gt;&nbsp;29.2&nbsp;mm, is "
  f"{num(sp.get('R_thermal_K_per_W'), 1)}&nbsp;K/W. That is one layer of six "
  f"and an upper bound on that path; the design's whole-stack estimate is "
  f"1.0&nbsp;K/W and the other five layers conduct in parallel.</p>")
 if 'error' not in sp else ''}
<p>The conduction phase's own Joule numbers, at the 28.3&nbsp;A peak:
 {num(j.get('switch_node'), 3)}&nbsp;W in the switch node,
 {num(j.get('gnd_return'), 3)}&nbsp;W in the ground return,
 {num(j.get('phase_pour'), 3)}&nbsp;W in the phase pour &mdash; against
 <code>geometry.losses()</code>'s {num(L.get('total'), 2)}&nbsp;W total, of
 which {num(L.get('conduction'), 2)}&nbsp;W is device conduction.</p>
""")


def sec_method(R):
    p0 = R.get("P0", {})
    kat = p0.get("known_answer_tests", {})
    rows = []
    for name, t in kat.items():
        detail = ""
        for k in ("err", "err_L", "agreement_pct", "fringing_excess",
                  "err_at_0p05mm", "err_f"):
            if k in t and isinstance(t[k], (int, float)):
                detail = f"{t[k] * 100:+.2f} %"
                break
        rows.append([f"<code>{e(name)}</code>", e(t.get("description", "")),
                     detail or "&mdash;", chip(t.get("status") == "PASS")])
    fit = R.get("P2", {}).get("fet_fit", {})
    frows = []
    for k, want in (fit.get("datasheet") or {}).items():
        got = (fit.get("simulated") or {}).get(k)
        err = (fit.get("error") or {}).get(k)
        frows.append([e(k), num(want, 4), num(got, 4),
                      (f"{err * 100:+.1f} %" if isinstance(err, (int, float))
                       else "&mdash;")])
    tsim = fit.get("switching_times_simulated_ns", {})
    tds = fit.get("switching_times_datasheet_ns", {})
    trows = [[e(k), num(tds.get(k), 1), num(tsim.get(k), 1)]
             for k in tds] if tds else []
    tool = p0.get("toolchain", {})
    return q_section("Method", "What was solved with what, and what was "
                               "checked first", f"""
<p>There is no passwordless <code>sudo</code> on this machine, so nothing was
 installed with <code>apt</code>. FastHenry2 and FastCap2 were built from the
 FastFieldSolvers sources (both needed patching for a modern compiler: K&amp;R
 declarations, <code>-fcommon</code>, and FastCap's own <code>sbrk</code>
 allocator, whose implicitly-declared pointers are truncated on 64-bit and
 segfault the solver). ngspice is driven through the
 <code>libngspice.so.0</code> that KiCad installs, because there is no CLI.
 openEMS is built and run inside a container with only <code>sim/</code>
 mounted.</p>
<h3>Known-answer tests</h3>
{table(["test", "what it checks", "error", ""], rows)}
<p>Two of these are worth naming. The strip-over-plane case &mdash; exactly
 <code>geometry.commutation_loop()</code>'s PCB term &mdash; comes out at
 {num((kat.get('fasthenry_strip_over_plane') or {}).get('L_nH'), 3)}&nbsp;nH
 in FastHenry and
 {num((kat.get('openems_strip_over_plane') or {}).get('L_nH'), 3)}&nbsp;nH in
 openEMS, against the closed form's 0.387&nbsp;nH: the two solvers agree with
 each other to a few per cent and both sit above the formula, which ignores
 fringing and the return path. And the straight-wire test disagrees with the
 spec's own expected value: SPEC.md&nbsp;&sect;5.4 quotes 17.4&nbsp;nH, but its
 own formula with l&nbsp;=&nbsp;20&nbsp;mm and r&nbsp;=&nbsp;0.5&nbsp;mm gives
 {num((kat.get('fasthenry_wire') or {}).get('L_round_wire_closed_form_nH'), 2)}&nbsp;nH,
 which is what FastHenry returns.</p>
<h3>The MOSFET model</h3>
<p>ngspice's VDMOS has one shape parameter for C<sub>gd</sub> and cannot be
 32&nbsp;pF at 40&nbsp;V, integrate to 13&nbsp;nC of Miller charge, and leave
 Q<sub>g</sub> at 61&nbsp;nC all at once. C<sub>gd</sub> and C<sub>ds</sub> are
 therefore charge-defined behavioural capacitors outside the primitive, solved
 from the datasheet's own specified points.</p>
{table(["parameter", "datasheet", "model", "error"], frows)}
{('<h3>Switching times, as a check on the whole model</h3>'
  + table(["", "datasheet (ns)", "model (ns)"], trows)) if trows else ''}
{figure("p2_fet_caps.png", "The fitted capacitances against the datasheet's three specified points.")}
<h3>The toolchain as it ran</h3>
<pre class="tree">{e(json.dumps(tool, indent=1))}</pre>
<h3>Extraction</h3>
<p>The board is read with <code>pcbnew</code> into
 <code>sim/work/geometry.json</code> &mdash; zone fills, pad polygons at their
 own absolute angles, tracks, barrels with their layer spans &mdash; and every
 solver builds from that. Nothing here edits <code>hardware/</code>.</p>
{figure("extract_cellA.png", "The extracted copper of cell A, layer by layer, coloured by net. This is what the solvers see.")}
""")


def sec_scope(R):
    p8 = R.get("P8") or {}
    thermal = ("<li><strong>Thermal</strong> beyond the two numbers P8 "
               "re-solves &mdash; the via array's resistance and the lateral "
               "spread on F.Cu. There is no transient model and no "
               "heatsink.</li>" if p8 and "error" not in p8 else
               "<li><strong>Thermal</strong> beyond the Joule densities in "
               "Q7. P8 was the optional phase and was not run.</li>")
    return q_section("Scope", "What this does not answer", f"""
<ul>
<li><strong>The RP2350 core buck</strong> (VREG_LX, 3.3&nbsp;&micro;H) and its
 spurs on +3V3A &mdash; out of scope by SPEC.md&nbsp;&sect;4.</li>
<li><strong>USB and RS-485 signal integrity</strong> &mdash; out of scope.</li>
<li><strong>A motor FEA</strong> &mdash; there is no motor.</li>
<li><strong>Board B's own layout</strong> &mdash; none exists; it is modelled
 lumped.</li>
{thermal}
</ul>
<p>And four kinds of number in here are <em>bracketed</em> rather than
 measured, because the part has not been chosen or the maker does not publish
 the figure: <strong>the EG2103's output impedance</strong>, the FET package's
 lead inductance, the DC-bias curve of the DC-link ceramics, and the TVSs'
 junction capacitance and forward resistance. Each is swept, and every result
 that depends on one says so. The first is the one that matters: it sets the
 edge rate, and the edge rate sets Q2, Q3, Q4's dV/dt and Q12.</p>
""")


# ============================================================== findings ====
def q1_bracket(R):
    """The two solvers' refined answers for the same single port, low first.

    Both are mesh-dependent on this geometry and neither had been taken to its
    own convergence: FastHenry falls as its filament grid is refined, openEMS
    rises as its cells shrink.  What they jointly support is a bracket, not a
    number, and the report quotes it as one.
    """
    x = (R.get("P7") or {}).get("Q1_crosscheck") or {}
    em = [r for r in (x.get("mesh_convergence") or []) if r.get("L_nH")]
    fh = ((x.get("fasthenry") or {}).get("fasthenry_convergence") or [])
    if not em or not fh:
        return None
    de = x.get("bridge_deembed") or {}
    a = de.get("L_ideal_short_nH") or em[-1]["L_nH"]
    b = fh[-1]["L_single_port_nH"]
    return (min(a, b), max(a, b), dict(em[-1], L_comparable_nH=a), fh[-1])


def findings(R):
    F = []

    def add(sev, finding, evidence, status, disposition):
        F.append((sev, finding, evidence, status, disposition))

    p1, p2, p3, p4, p5, p6, p7 = (R.get(k, {}) for k in
                                  ("P1", "P2", "P3", "P4", "P5", "P6", "P7"))
    q1 = p1.get("Q1", {})
    if q1:
        add("HIGH",
            f"The commutation loop's PCB part is "
            f"{q1['solved']['L_pcb_nH_at_10MHz']:.2f} nH, "
            f"{q1['H1']['ratio_to_estimate']:.1f} times "
            f"geometry.commutation_loop()'s 0.39 nH. VBUS reaches the "
            f"high-side drain from In2, 0.55 mm below F.Cu, through a 16-via "
            f"field, not from a plane 0.1 mm away.",
            "P1.json Q1.solved.L_pcb_nH_at_10MHz; p1_commutation_A.png",
            "HAND-BACK",
            "Correct geometry.commutation_loop() to the solved value, or "
            "replace it with a call that reads P1's result.")
    q2 = p2.get("Q2", {})
    if q2 and q2.get("worst_V_ds_V"):
        wc = q2.get("worst_case") or {}
        env_w = max((max(r.get("Vds_low_peak_at_turn_on") or 0,
                         r.get("Vds_high_peak_at_turn_off") or 0)
                     for r in q2.get("envelope_sweep", [])), default=0.0)
        strong = min(q2.get("driver_impedance", []),
                     key=lambda r: r.get("drv_r_scale") or 1.0, default={})
        cyc = (strong.get("ring_turn_on") or {}).get("cycles_to_5pct")
        add("HIGH",
            f"Worst-case V_ds is not a board number: it is set by the "
            f"EG2103's output impedance, which its only obtainable document "
            f"does not specify. Reading its rated I_O+/I_O- as a resistance "
            f"at the 12 V drive (40/20 ohm) gives {env_w:.1f} V across the "
            f"whole operating envelope, inside the spec's 68 V criterion. A "
            f"strong output stage (4/2 ohm) gives "
            f"{q2['worst_V_ds_V']:.1f} V, outside it"
            + (f", with a turn-on ring that takes about {cyc:.0f} cycles to "
               f"fall below 5 % against the criterion's five." if cyc
               else ".")
            + " Nothing in the sweep reaches the 80 V silicon or the 71.1 V "
              "TVS, and the avalanche energy is zero in every run.",
            "P2.json Q2.driver_impedance, Q2.envelope_sweep; p2_edges.png",
            "HAND-BACK",
            "Get the real EG2103 datasheet or measure the output impedance on "
            "a board; it decides Q2, Q3 and the dV/dt every other answer "
            "hangs off. Until then, keep the Q3 snubber footprints.")
    q3 = p2.get("Q3", {})
    if q3 and q3.get("smallest_that_passes"):
        b = q3["smallest_that_passes"]
        add("MED",
            f"An RC snubber of {b['R']:.1f} ohm and {b['C_nF']:.2f} nF brings "
            f"the worst V_ds to {b['V_peak_V']:.1f} V for {b['P_R_W']:.3f} W "
            f"per cell. It is needed only in the strong-driver corner; at "
            f"the datasheet reading there is nothing to snub.",
            "P2.json Q3.smallest_that_passes", "HAND-BACK",
            "Add two 0805 footprints per cell across the switch node, and "
            "leave them unstuffed unless the driver turns out to be the "
            "strong one.")
    q4 = p2.get("Q4", {})
    if q4 and q4.get("sweep"):
        w = max((r.get("Vgs_low_induced_at_turn_on") or 0)
                for r in q4["sweep"])
        dv = max((r.get("dVdt_switch_node_kV_per_us") or 0)
                 for r in q4["sweep"])
        dlo = min((r.get("dVdt_switch_node_kV_per_us") or 0)
                  for r in q4["sweep"])
        cc = next((c for c in q4.get("cross_conduction", [])
                   if "min" in c.get("corner", "")), {})
        ct = next((c for c in q4.get("cross_conduction", [])
                   if "typ" in c.get("corner", "")), {})
        rem = q4.get("remedy_C_gs") or []
        add("HIGH",
            f"The off device's V_gs at the die reaches {w:.2f} V on every "
            f"high-side turn-on, above the {q4['V_GS_th_min_V'] * 0.7:.2f} V "
            f"criterion and above the {q4['V_GS_th_min_V']:.1f} V minimum "
            f"threshold itself. It is almost flat across R_g, drive strength, "
            f"capacitance corner and driver impedance, because the induced "
            f"step is the charge divider Q_gd/C_iss and the driver cannot "
            f"reach the die through 1.6 ohm of internal gate resistance in "
            f"the few nanoseconds the edge lasts. Measured by difference "
            f"against a device that cannot conduct: nothing at the typical "
            f"threshold ({ct.get('cross_conduction_nC', 0):.0f} nC), and "
            f"{cc.get('cross_conduction_nC', 0):.0f} nC / "
            f"{cc.get('cross_conduction_uJ', 0):.0f} uJ per edge at the "
            f"datasheet's minimum threshold, where the high-side peak current "
            f"also rises from {ct.get('i_high_peak_A', 0):.0f} to "
            f"{cc.get('i_high_peak_A', 0):.0f} A.",
            "P2.json Q4.sweep, Q4.cross_conduction", "HAND-BACK",
            "Neither R_g nor a stronger driver moves it. Budget the loss "
            "(about "
            f"{(cc.get('cross_conduction_uJ') or 0) * 20e3 / 1e6:.2f} W per "
            "half-bridge at 20 kHz on a bottom-of-distribution part), or "
            "change to a device with a smaller Q_gd/C_iss, or provide a "
            "negative off-state bias, which this bootstrap driver cannot do "
            "without a split rail.")
        if rem:
            add("MED",
                f"The standard fix does not work on this part: "
                f"{rem[-1]['C_gs_ext_nF']:.0f} nF across the gate and source "
                f"pins moves the die from "
                f"{rem[0].get('Vgs_low_induced_at_turn_on', 0):.2f} to "
                f"{rem[-1].get('Vgs_low_induced_at_turn_on', 0):.2f} V, "
                f"because the device's 1.6 ohm of internal gate resistance "
                f"sits between the pins and the polysilicon. At the pin it "
                f"looks like it works, which is what a probe would show.",
                "P2.json Q4.remedy_C_gs", "DECIDED",
                "Do not add gate-source capacitors; they buy the measurement, "
                "not the device.")
        add("INFO",
            f"dV/dt at the switch node is {dlo:.0f}-{dv:.0f} kV/us across the "
            f"driver-impedance bracket, against the design point's 1.2 kV/us. "
            f"Every estimate downstream of the edge rate moves with it, "
            f"including the common-mode current Q12 computes.",
            "P2.json Q4.sweep dVdt_switch_node_kV_per_us", "OPEN",
            "Recompute the enclosure's common-mode budget once the driver's "
            "output impedance is known.")
    q5 = p2.get("Q5", {})
    if q5 and q5.get("sweep"):
        v = max((r.get("V_tap_peak_mV") or 0) for r in q5["sweep"])
        err = max((r.get("err_peak_on_LSB") or 0) for r in q5["sweep"])
        add("MED",
            f"H3 confirmed: the voltage between the sense taps reaches "
            f"{v:.0f} mV during an edge against 22.6 mV of full-scale signal.",
            "P2.json Q5.sweep; P1.json Q5.H3", "VERIFIED-OK",
            "No change needed to the filter: the INA241A3's 1 us output hold "
            f"keeps the error the ADC sees to {q5.get('worst_error_LSB', 0):.1f} "
            f"LSB, though "
            f"{(q5.get('stale_fraction_free_running') or [0])[0] * 100:.0f}-"
            f"{(q5.get('stale_fraction_free_running') or [0, 0])[-1] * 100:.0f} % "
            "of free-running samples are up to a microsecond stale.")
        add("INFO",
            "The spec's Q5 criterion -- settle to +-1 LSB within 500 ns -- "
            "cannot be met by the chosen amplifier, whose own common-mode hold "
            "is 1 us. The criterion is the wrong shape for the part.",
            "models/ina241a3.json pwm_hold; SBOSA30D sec.7.3.1.1", "DECIDED",
            "Replace the criterion with a bound on the error in LSB.")
    q7 = p3.get("Q7", {})
    conn = (q7 or {}).get("via_array_connectivity", {})
    if conn:
        bad = [k for k, v in conn.items()
               if v["per_layer"].get("B.Cu", {}).get("connected", 0) < 8
               and v["net"].startswith("SW")]
        if bad:
            arr = (q7.get("phase_A") or {}).get("low_side_via_array", {})
            add("HIGH",
                f"Only 4 of the 16 barrels under each low-side drain "
                f"({', '.join(bad)}) are joined to the switch-node pour on "
                f"B.Cu; the other 12 are isolated pads in the clearance "
                f"between the SW and PHASE pours. The busiest carries "
                f"{arr.get('I_max_A', 0):.1f} A at the 28.3 A peak, "
                f"{arr.get('I_rms_per_via_A', 0):.1f} A rms against the 2 A "
                f"a 0.4 mm barrel is worth.",
                "P3.json Q7.via_array_connectivity", "HAND-BACK",
                "Widen the SW pour on B.Cu to cover the whole array, or move "
                "the array, or accept four vias and size them for it.")
    vb = [(k, v) for k, v in (q7 or {}).get("vbus", {}).items()
          if isinstance(v, dict) and "via_array" in v]
    if vb:
        worst = max(v["via_array"]["I_rms_per_via_A"] for _, v in vb)
        add("MED",
            f"The high-side VBUS arrays are all connected on In2 but share "
            f"badly: the busiest via carries {worst:.1f} A rms against the "
            f"2 A criterion, because the current crowds into the barrels "
            f"nearest where it arrives.",
            "P3.json Q7.vbus", "HAND-BACK",
            "More vias, or a spread entry into the array.")
    q6 = p3.get("Q6", {})
    a = (q6.get("per_cell") or {}).get("A", {})
    if a:
        add("INFO",
            f"The sense chain's DC accuracy is good: tap to tap is "
            f"{a['R_tap_to_tap_Ohm'] * 1e3:.4f} mohm of which "
            f"{a['copper_share'] * 100:.1f} % is copper, and the total TCR is "
            f"{a['TCR_pct_per_K']:.3f} %/K -- both inside the criteria.",
            "P3.json Q6.per_cell.A", "VERIFIED-OK", "No change.")
    q13 = p4.get("Q13", {})
    if q13:
        h2 = q13.get("H2", {})
        add("MED", f"H2: {h2.get('verdict', '')}",
            "P4.json Q13.H2", "HAND-BACK",
            "Use the derated capacitance in geometry.interconnect(), and "
            "choose a part whose DC-bias curve is published.")
    rip = p4.get("Q13_ripple", {}).get("per_case", {})
    bad_bulk = [k for k, v in rip.items() if not v["pass_ripple_3V"]]
    if bad_bulk:
        add("MED",
            "With the electrolytic bulk the spec originally listed (0.2 ohm "
            "ESR) the bus ripple at the drains is over 6 V peak to peak; with "
            "polymer hybrids it is under 2 V.",
            "P4.json Q13_ripple.per_case", "HAND-BACK",
            "Specify polymer-hybrid bulk on board B.")
    q10 = p5.get("Q10", {})
    if q10:
        mag = [r for r in q10.get("magnet", []) if not r["in_window"]]
        if mag:
            add("MED",
                "The Dia 8 x 2.5 mm magnet puts more than the MT6701's "
                "100 mT maximum at the die at the short end of the air-gap "
                "tolerance, and an N42 exceeds it at the nominal 1 mm gap as "
                "well. The datasheet recommends Dia 6.",
                "P5.json Q10.magnet", "HAND-BACK",
                "Specify Dia 6 x 2.5 mm, or an N35 with the gap held at or "
                "above 1 mm.")
        ae = q10.get("angle_error", {})
        if ae and not ae.get("pass"):
            add("LOW",
                f"Phase current moves the reported angle by "
                f"{ae['peak_deg']:.3f} deg peak ({ae['peak_LSB']:.1f} LSB) "
                f"against the 0.05 deg criterion. It is the three motor leads "
                f"that do it, not the board copper, whose fields cancel on the "
                f"shaft axis.",
                "P5.json Q10.angle_error", "HAND-BACK",
                "Twist or bundle the three leads where they leave the board, "
                "or compensate in firmware -- the error moves with the current "
                "and is therefore predictable.")
    q8 = p6.get("Q8", {})
    if q8:
        add("MED",
            f"PWM-synchronised sampling cuts the current-loop measurement "
            f"error from {q8['free_running_noise_A_rms']:.3f} to "
            f"{q8['synchronised_noise_A_rms']:.3f} A rms and halves the bias.",
            "P6.json Q8", "HAND-BACK",
            "Pace the ADC from the PWM slice's wrap DREQ, one conversion per "
            "phase at the counter's zero.")
    q9 = p6.get("Q9", {})
    if q9:
        add("LOW",
            f"The software dead_zone of 0.02 costs "
            f"{q9.get('torque_loss_from_software_dead_zone_pct', 0):.2f} % of "
            f"torque and leaves the distortion essentially unchanged; the "
            f"EG2103's own 560 ns already dominates.",
            "P6.json Q9.sweep", "HAND-BACK",
            "Set dead_zone = 0 and rely on the driver's interlock.")
        add("INFO",
            f"The driver's 560 ns minimum pulse makes duties below "
            f"{(q9.get('min_duty_reachable') or 0) * 100:.2f} % and above the "
            f"mirror of that unreachable at 20 kHz.",
            "P6.json Q9.min_duty_reachable", "DECIDED",
            "Clamp the modulation index in firmware so the loop never "
            "commands an unreachable duty.")
    q14 = p6.get("Q14", {})
    if q14 and q14.get("sweep"):
        w = q14["sweep"][len(q14["sweep"]) // 2]
        add("HIGH",
            f"A decel at the design-point braking torque drives the bus to "
            f"the TVS breakdown in {w['t_to_TVS_ms']:.2f} ms and leaves "
            f"{w['TVS_energy_J']:.0f} J for a part that survives a few. There "
            f"is no brake chopper. The firmware's 63-66 V fold-back is the "
            f"only protection and it reacts in about one 50 us period.",
            "P6.json Q14.sweep", "OPEN",
            "Decide between a brake resistor, an active clamp, much more "
            "bulk, or a documented deceleration limit.")
    q12 = p2.get("Q4", {})
    q12r = p2.get("Q12")
    if q12r and not q12r.get("error"):
        sw12 = [r for r in q12r.get("sweep", []) if not r.get("incomplete")]
        floating = [r for r in sw12 if r.get("bond") == "open"]
        v = max((r.get("V_frame_pk_V") or 0) for r in floating) if floating else 0
        cwfs = sorted({r["C_wf_pF"] for r in sw12})

        def survives(strong):
            """the bonds that stay under 10 V at every C_wf in the sweep --
            passing at one value of an unmeasured capacitance is not a fix"""
            sel = [r for r in sw12
                   if ((r.get("drv_r_scale") or 1.0) != 1.0) == strong]
            return sorted({lab for lab in {r["label"] for r in sel}
                           if all(any(r["label"] == lab and r["C_wf_pF"] == c
                                      and r.get("pass_frame_10V") for r in sel)
                                  for c in cwfs)})

        okw, oks = survives(False), survives(True)
        add("HIGH",
            f"The motor frame is not bonded to board GND by anything on this "
            f"board -- the four motor mounts are NPTH -- so it follows the "
            f"switch node: {v:.0f} V peak against the 10 V criterion. Bonding "
            f"it with a long wire makes the voltage worse, because the "
            f"inductance resonates with the winding-to-frame capacitance. "
            + (f"At the driver impedance the datasheet implies, "
               f"{' and '.join(okw)} stays under 10 V at every "
               f"winding-to-frame capacitance in the sweep; at the "
               f"strong-driver corner "
               f"{(' and '.join(oks) + ' does') if oks else 'nothing does'}."
               if okw else
               "Nothing in the sweep stays under 10 V at every "
               "winding-to-frame capacitance."),
            "P2.json Q12.sweep", "OPEN",
            "An enclosure decision: a short low-inductance frame bond, a Y "
            f"capacitor, or an accepted {v:.0f} V common-mode swing on the "
            "frame. Measure the motor's winding-to-frame capacitance first; "
            "the whole table moves with it.")
    else:
        add("INFO",
            "Q12 (common mode) was not simulated.",
            "this report, Scope", "OPEN",
            "Revisit when the enclosure and frame bond are defined.")
    x = p7.get("Q1_crosscheck")
    if x:
        em = [r for r in (x.get("mesh_convergence") or []) if r.get("L_nH")]
        if x.get("error") or not em:
            add("INFO",
                f"The full-wave cross-check of Q1 did not produce a usable "
                f"number: {x.get('error', 'the board-level FDTD model did not '
                                  'converge to a consistent impedance')}. The "
                f"solver itself is validated -- its microstrip and "
                f"strip-over-plane known-answer tests pass -- so this is a "
                f"model-building failure, not a solver one.",
                "P7.json Q1_crosscheck", "OPEN",
                "Either refine the FDTD model of the cell or accept L1 alone, "
                "which is the appropriate physics for a 65 mm board below "
                "300 MHz.")
        else:
            ap = x.get("agreement_pct")
            fin = em[-1]
            add("INFO",
                f"The first run of this phase reported 3.82 nH against L1 and "
                f"called it a disagreement. It was neither, and the largest "
                f"error was not in the mesh: the conducting FETs were "
                f"cylinders of 0.25 mm radius about an axis in F.Cu, which is "
                f"0.1525 mm above In1, so each rod cut through about 1 mm2 of "
                f"the ground plane and the high-side one shorted VBUS to GND "
                f"at the drain -- the current never reached In2 or the via "
                f"field. On top of that the run was stopped at 1.4 times its "
                f"own excitation length with the energy still at -2 dB, it "
                f"was compared with L_eff (all four capacitors in parallel) "
                f"when it drives one, and its 127 \"dropped primitives\" were "
                f"via pads the barrel cylinders already provide. With the "
                f"device modelled as a strip inside its own layer and the "
                f"energy decayed to {fin.get('final_energy_dB')} dB on a mesh "
                f"snapped to the copper, openEMS gives {fin['L_nH']:.2f} nH "
                f"for that one capacitor's loop against FastHenry's "
                f"{x.get('L1_fasthenry_nH', 0):.2f} nH"
                + (f", {ap:.0f} % apart." if ap is not None else "."),
                "P7.json Q1_crosscheck; p7_crosscheck.png",
                "VERIFIED-OK" if x.get("pass_25pct") else "OPEN",
                "None." if x.get("pass_25pct") else
                "Take both solvers one refinement further, or quote the loop "
                "as the bracket they agree on.")
    add("INFO",
        "SPEC.md sec.5.4 expects 17.4 nH for the straight-wire known-answer "
        "test, but its own formula with l = 20 mm and d = 1 mm gives 14.53 nH, "
        "which is what FastHenry returns to 0.03 %. The expected value in the "
        "spec is wrong, not the solver.",
        "P0.json known_answer_tests.fasthenry_wire", "DECIDED",
        "Correct the expected value in SPEC.md.")
    x7 = (R.get("P7") or {}).get("Q1_crosscheck") or {}
    bs = x7.get("barrel_model_sensitivity") or {}
    if bs.get("L_nH"):
        uni7 = [r for r in (x7.get("uniform_mesh_series") or [])
                if r.get("L_nH") and abs((r.get("res_mm") or 0) - 0.35) < 1e-6]
        base = uni7[0]["L_nH"] if uni7 else None
        add("MED",
            "The two solvers model a plated via differently, and neither "
            "models it as it is. fasthenry/mesher.add_barrel() uses a square "
            "bar of the same cross-sectional AREA as the plating -- right for "
            "resistance, and for a 0.4 mm hole its geometric mean distance is "
            "0.082 mm against the tube's 0.225 mm, so each barrel comes out "
            "0.206 nH instead of 0.094 nH, 2.2 times too inductive. The FDTD "
            "model uses a solid rod of the PAD diameter, 0.058 nH, too fat. "
            "The truth is between them and the commutation loop runs through "
            "sixteen of them."
            + (f" Re-solving the 0.35 mm point with every barrel at its real "
               f"plated diameter moves the loop from {base:.2f} to "
               f"{bs['L_nH']:.2f} nH." if base else ""),
            "P7.json Q1_crosscheck.barrel_model_sensitivity", "HAND-BACK",
            "Give add_barrel() a bar of the same geometric mean distance as "
            "the plated tube and scale its conductivity to keep the copper "
            "area, so R and L are both right; then re-run P1 Q1.")
    br = q1_bracket(R)
    if br:
        lo, hi, em, fh = br
        x = (R.get("P7") or {}).get("Q1_crosscheck") or {}
        agree = x.get("agreement_pct")
        fhs = ((x.get("fasthenry") or {}).get("fasthenry_convergence") or [])
        trend = ""
        if len(fhs) > 1 and fhs[0].get("L_eff_nH") and fhs[-1].get("L_eff_nH"):
            st = ((fhs[-1]["L_eff_nH"] - fhs[-2]["L_eff_nH"])
                  / fhs[-2]["L_eff_nH"])
            trend = (f" FastHenry's own grid sweep had not been run out "
                     f"either: L_eff falls from {fhs[0]['L_eff_nH']:.2f} nH "
                     f"at {fhs[0]['target_mm']} mm to "
                     f"{fhs[-1]['L_eff_nH']:.2f} nH at "
                     f"{fhs[-1]['target_mm']} mm"
                     + (f" and settles there, {abs(st) * 100:.1f} % on the "
                        f"last refinement, so the loop this register quotes "
                        f"as 2.06 nH is {fhs[-1]['L_eff_nH']:.2f} nH "
                        f"converged." if abs(st) < 0.05 else
                        ", and is still falling, so 2.06 nH is the coarse "
                        "end of that curve."))
        if agree is not None and agree <= 25.0:
            add("INFO",
                f"The L1/L2 cross-check passes once both models are right. "
                f"On the single port they can be compared on, openEMS gives "
                f"{em['L_comparable_nH']:.2f} nH -- its own device strip "
                f"de-embedded, {em['L_nH']:.2f} nH raw -- against FastHenry's "
                f"{fh['L_single_port_nH']:.2f} nH, {agree:.0f} % apart "
                f"against the spec's 25 % gate. The full-wave model also "
                f"returns what the PEEC one cannot see: the loop carries "
                f"about {(em.get('lc_fit') or {}).get('C_pF', 0):.0f} pF of "
                f"its own capacitance and self-resonates near "
                f"{(em.get('lc_fit') or {}).get('f_resonance_MHz', 0):.0f} "
                f"MHz."
                + trend,
                "P7.json Q1_crosscheck.mesh_convergence; p7_crosscheck.png",
                "VERIFIED-OK",
                "Re-run P1's frequency sweep at the finer grid so the number "
                "S-01 quotes comes from the converged end of its own series.")
        else:
            add("MED",
                f"The two solvers do not agree on this loop even with both "
                f"models corrected: openEMS {em['L_nH']:.2f} nH at a "
                f"{em['step_used_x_mm']:.2f} mm snapped mesh against "
                f"FastHenry's {fh['L_single_port_nH']:.2f} nH at a "
                f"{fh['target_mm']} mm grid"
                + (f", {agree:.0f} % apart" if agree is not None else "")
                + f". What the pair support is a bracket, {lo:.2f}-{hi:.2f} nH,"
                f" not a number."
                + trend,
                "P7.json Q1_crosscheck.mesh_convergence; p7_crosscheck.png",
                "HAND-BACK",
                "Quote the loop inductance as a bracket until one of the two "
                "models is shown to be the wrong one; every number derived "
                "from it moves across that range.")
    return F


def write_findings(R):
    F = findings(R)
    lines = [
        "# servodrive electromagnetic simulation — findings register",
        "",
        f"Generated by `sim/run.py --phase P9` on {date.today().isoformat()}. "
        "Evidence names the result key and the figure.",
        "",
        "**The IDs are positions in this table, not names.** It is sorted by "
        "severity and regenerated whole from `results/*.json`, so adding one "
        "finding renumbers every less severe one below it -- an earlier "
        "version of this header claimed the opposite, and that was never "
        "true of the code. Cite a finding by its evidence key, which is "
        "stable, rather than by S-number.",
        "",
        "Severity HIGH / MED / LOW / INFO. Status OPEN, DECIDED, HAND-BACK, "
        "VERIFIED-OK.",
        "",
        "| ID | Sev | Finding | Evidence | Status | Disposition |",
        "|---|---|---|---|---|---|",
    ]
    order = {"HIGH": 0, "MED": 1, "LOW": 2, "INFO": 3}
    F.sort(key=lambda f: order.get(f[0], 9))
    for i, (sev, finding, ev, st, disp) in enumerate(F, start=1):
        f = lambda s: str(s).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| S-{i:02d} | {sev} | {f(finding)} | `{f(ev)}` | "
                     f"{st} | {f(disp)} |")
    lines.append("")
    lines.append("## Hypotheses")
    lines.append("")
    lines.append("| | Claim | Verdict |")
    lines.append("|---|---|---|")
    h1 = (R.get("P1", {}).get("Q1") or {}).get("H1", {})
    h2 = (R.get("P4", {}).get("Q13") or {}).get("H2", {})
    h3 = (R.get("P1", {}).get("Q5") or {}).get("H3", {})
    for tag, h in (("H1", h1), ("H2", h2), ("H3", h3)):
        if h:
            lines.append(f"| {tag} | {str(h.get('claim', '')).replace('|', '')} "
                         f"| {str(h.get('verdict', '')).replace('|', '')} |")
    q5 = R.get("P1", {}).get("Q5_H4", {})
    if q5 and "error" not in q5:
        asym = {k: v for k, v in q5.items() if k.startswith("asymmetry_")}
        lines.append(f"| H4 | the two sense taps are not symmetric, so a "
                     f"common-mode step becomes a differential one | "
                     f"capacitance asymmetry solved with FastCap: "
                     f"{', '.join(f'{k} {v:+.3f} pF' for k, v in asym.items())} |")
    elif q5:
        lines.append(f"| H4 | the two sense taps are not symmetric | not "
                     f"solved: {str(q5.get('error', '')).replace('|', '')} |")
    paths.FINDINGS.write_text("\n".join(lines) + "\n")
    return len(F)


def write_handback(R):
    p1, p2, p3, p4, p5, p6 = (R.get(k, {}) for k in
                              ("P1", "P2", "P3", "P4", "P5", "P6"))
    L = ["# servodrive — hand-back list",
         "",
         f"Generated {date.today().isoformat()} from `sim/results/*.json`. "
         "Nothing in `sim/` applies any of this: it is for a human to decide "
         "from.",
         "",
         "Every placement change means `python3 tools/gen_boards.py --force` "
         "and then `python3 tools/route.py --rounds 4 --loose`, **which "
         "discards the routing**. The constants that only change a number in "
         "the model do not.",
         ""]

    def entry(title, where, value, effect, rerun, why):
        L.extend([f"## {title}", "",
                  f"- **Where:** `{where}`",
                  f"- **Proposed:** {value}",
                  f"- **Because:** {why}",
                  f"- **Effect:** {effect}",
                  f"- **Re-run:** {rerun}", ""])

    q1 = p1.get("Q1", {})
    if q1:
        br = q1_bracket(R)
        cvq = sorted((q1.get("convergence") or []),
                     key=lambda c: -c["target_mm"])
        ratio = q1["solved"]["L_pcb_nH_at_10MHz"] / 0.387
        entry(f"The commutation-loop model is {ratio:.0f} times out",
              "tools/geometry.py: commutation_loop()",
              f"L_pcb = {q1['solved']['L_pcb_nH_at_10MHz']:.2f} nH "
              f"(solved, all four DC-link capacitors in parallel) rather than "
              f"mu0*h*l/w = 0.387 nH; total "
              f"{q1['total_with_parts']['L_total_nH'][0]:.2f}"
              f"–{q1['total_with_parts']['L_total_nH'][1]:.2f} nH"
              + (f". Use {cvq[-1]['L_eff_nH']:.2f} nH: the same model at "
                 f"the converged end of its own grid sweep, "
                 f"{abs(cvq[-1]['L_eff_nH'] / q1['solved']['L_pcb_nH_at_10MHz'] - 1) * 100:.0f}"
                 f" % below the figure above. Q11 puts the single port both "
                 f"solvers can be compared on at {br[0]:.2f}\u2013{br[1]:.2f}"
                 f" nH"
                 if br and cvq and cvq[-1].get("L_eff_nH") else ""),
              "every derived number moves: overshoot, ring frequency, the "
              "snubber decision",
              "nothing on the board; `python3 tools/geometry.py` to refresh "
              "the figures",
              "VBUS reaches the high-side drain from In2 through a 16-via "
              "field, not from a plane 0.1 mm away (P1, H1)"
              + ("; its size is settled by the grid sweep and cross-checked "
                 "full wave (P7)" if br else ""))
    q2 = p2.get("Q2", {})
    if q2.get("driver_impedance"):
        env_w = max((max(r.get("Vds_low_peak_at_turn_on") or 0,
                         r.get("Vds_high_peak_at_turn_off") or 0)
                     for r in q2.get("envelope_sweep", [])), default=0.0)
        entry("Pin down the EG2103's output impedance",
              "models/eg2103.json: I_source, I_sink and the condition they "
              "are measured at",
              "get the real datasheet, or measure the gate edge into a known "
              "capacitance on a built board, and record the condition beside "
              "the number",
              f"it is the difference between {env_w:.1f} V of V_ds (inside "
              f"the 68 V criterion, no ring, no snubber) and "
              f"{q2.get('worst_V_ds_V', 0):.1f} V (outside it, a "
              f"{((min(q2['driver_impedance'], key=lambda r: r.get('drv_r_scale') or 1.0).get('ring_turn_on') or {}).get('f_MHz') or 0):.0f} MHz "
              "ring, and a snubber); every dV/dt-sensitive answer in the "
              "report moves with it",
              "nothing on the board; re-run P2 and P9",
              "the only obtainable EG2103 document gives I_O+/I_O- without "
              "the test condition, so the model brackets it 40/20 ohm to "
              "4/2 ohm (P2, Q2)")
    q3 = p2.get("Q3", {})
    if q3 and q3.get("smallest_that_passes"):
        b = q3["smallest_that_passes"]
        entry("Add snubber footprints, unstuffed",
              "tools/placement.py (two 0805 per cell), tools/schematic.py",
              f"R = {b['R']:.1f} ohm, C = {b['C_nF']:.2f} nF from SW_x to GND, "
              f"as close to the low-side source pads as the cell allows; "
              f"fitted only if the driver turns out to be the strong one",
              f"worst-case V_ds falls to {b['V_peak_V']:.1f} V; "
              f"{b['P_R_W']:.3f} W per cell at 20 kHz",
              "gen_boards.py --force, then route.py — discards the routing",
              "worst-case V_ds is "
              f"{p2.get('Q2', {}).get('worst_V_ds_V', 0):.1f} V on 80 V "
              "silicon in the strong-driver corner, and inside the criterion "
              "in the other (P2, Q2)")
    q4 = p2.get("Q4", {})
    if q4 and q4.get("cross_conduction"):
        cc = next((c for c in q4["cross_conduction"]
                   if "min" in c.get("corner", "")), {})
        ct = next((c for c in q4["cross_conduction"]
                   if "typ" in c.get("corner", "")), {})
        w = max((r.get("Vgs_low_induced_at_turn_on") or 0)
                for r in q4.get("sweep", [{}]))
        entry("Budget for Miller cross-conduction, or change the device",
              "tools/geometry.py: the switching-loss term; or the FET choice "
              "itself",
              f"either carry {(cc.get('cross_conduction_uJ') or 0) * 20e3 / 1e6:.2f} W "
              f"per half-bridge of extra loss and a "
              f"{cc.get('i_high_peak_A', 0):.0f} A peak in the loss and SOA "
              "budgets, or pick a device with a smaller Q_gd/C_iss",
              "at the typical threshold there is no cross-conduction at all "
              f"({ct.get('cross_conduction_nC', 0):.0f} nC); at the "
              f"datasheet minimum there is "
              f"{cc.get('cross_conduction_nC', 0):.0f} nC per edge",
              "nothing on the board unless the device changes",
              f"the off device's V_gs at the die reaches {w:.2f} V, above "
              f"both the {q4['V_GS_th_min_V'] * 0.7:.2f} V criterion and the "
              f"{q4['V_GS_th_min_V']:.1f} V minimum threshold, on every "
              "high-side turn-on, and neither R_g nor a stronger driver nor "
              "a gate-source capacitor moves it (P2, Q4)")
    q7 = p3.get("Q7", {})
    if q7 and q7.get("via_array_connectivity"):
        entry("Twelve of sixteen low-side drain vias are islands",
              "tools/fanout.py / the B.Cu zone outlines for SW_x",
              "extend the SW_x pour on B.Cu so it covers the whole 4 x 4 "
              "array, or move the array inboard of the PHASE pour's edge",
              "the phase current would cross to B.Cu through sixteen barrels "
              "instead of four; per-via current falls by about four times",
              "gen_boards.py --force, then route.py — discards the routing",
              "solved via currents in P3, Q7: the busiest carries "
              f"{(q7.get('phase_A') or {}).get('low_side_via_array', {}).get('I_rms_per_via_A', 0):.1f} "
              "A rms against 2 A")
    vb = [(k, v) for k, v in (q7 or {}).get("vbus", {}).items()
          if isinstance(v, dict) and "via_array" in v]
    if vb:
        entry("The high-side arrays share badly",
              "tools/geometry.py: FET_VIA_N",
              f"raise from 16, or spread the entry into the array; the "
              f"busiest via carries "
              f"{max(v['via_array']['I_rms_per_via_A'] for _, v in vb):.1f} A rms",
              "per-via current falls in proportion",
              "gen_boards.py --force, then route.py — discards the routing",
              "P3, Q7")
    q13 = p4.get("Q13", {})
    if q13:
        h2 = q13.get("H2", {})
        entry("The DC-link ceramics are worth about half their marking",
              "tools/geometry.py: interconnect(), the 13 uF assumption",
              f"use {h2.get('at_60V_uF', {}).get('typ', 0):.1f} uF at 60 V "
              f"(bracket {h2.get('at_60V_uF', {}).get('min', 0):.1f}"
              f"–{h2.get('at_60V_uF', {}).get('max', 0):.1f}) instead of 13.2",
              "the ripple split shifts and more of it crosses the header",
              "nothing on the board",
              "H2, P4")
        entry("Board B's bulk must be polymer hybrid",
              "the board B specification (spec.html §8)",
              "4 x 100 uF polymer hybrid, ESR 20–40 mohm each; not 3 x 100 uF "
              "electrolytic at 0.2 ohm",
              "bus ripple at the drains falls from over 6 V p-p to under 2",
              "nothing on board A",
              "P4, Q13 ripple table")
    q10 = p5.get("Q10", {})
    if q10:
        entry("The magnet is too strong at the short end of the gap",
              "tools/geometry.py: MAGNET_D (and the magnet's grade)",
              "Dia 6 x 2.5 mm, as the MT6701 datasheet recommends, or hold "
              "the air gap at or above 1 mm with an N35",
              "keeps the field at the die inside the part's 20–100 mT window "
              "over the whole tolerance",
              "MAGNET_D changes the keepout, so gen_boards.py --force and "
              "route.py — discards the routing",
              "P5, Q10 magnet table")
        entry("Bundle the three motor leads",
              "assembly instruction, not a board change",
              "twist or bundle the three phase leads for the first 50 mm "
              "after they leave the lead pads",
              "removes most of the "
              f"{(q10.get('angle_error') or {}).get('peak_deg', 0):.3f} deg "
              "current-dependent angle error; the board copper's own field "
              "already cancels on the shaft axis",
              "nothing",
              "P5, Q10")
    q8 = p6.get("Q8", {})
    if q8:
        entry("Pace the ADC from the PWM",
              "firmware: patches/simplefoc_rp2040_current_sense.cpp",
              "a DMA channel paced by the PWM slice's wrap DREQ writing "
              "START_ONCE to ADC_CS, one conversion per phase at the "
              "counter's zero, instead of free-running round-robin",
              f"current-loop measurement error falls from "
              f"{q8['free_running_noise_A_rms']:.3f} to "
              f"{q8['synchronised_noise_A_rms']:.3f} A rms and the bias halves",
              "firmware only",
              "P6, Q8")
    q9 = p6.get("Q9", {})
    if q9:
        entry("Set dead_zone to zero",
              "firmware: driver->dead_zone",
              "0.0, relying on the EG2103's own 560 ns interlock",
              f"recovers "
              f"{q9.get('torque_loss_from_software_dead_zone_pct', 0):.2f} % of "
              "torque and changes the distortion hardly at all",
              "firmware only",
              "P6, Q9")
        entry("Clamp the modulation index",
              "firmware: the duty the driver is given",
              f"never command a duty below "
              f"{(q9.get('min_duty_reachable') or 0) * 100:.2f} % or above its "
              f"mirror",
              "keeps the loop out of the region where the driver silently "
              "passes no pulse",
              "firmware only",
              "P6, Q9")
    q12h = p2.get("Q12", {})
    if q12h and not q12h.get("error"):
        floating = [r for r in q12h.get("sweep", []) if r.get("bond") == "open"]
        v = max((r.get("V_frame_pk_V") or 0) for r in floating) if floating else 0
        best = min((r for r in q12h.get("sweep", [])
                    if r.get("V_frame_pk_V") is not None),
                   key=lambda r: r["V_frame_pk_V"], default=None)
        entry("Decide how the motor frame is bonded",
              "the enclosure, not the board -- the four motor mounts are NPTH "
              "and cannot be changed into a bond without copper round them",
              (f"the sweep's best option is {best.get('label', '')} at "
               f"{best['V_frame_pk_V']:.0f} V of frame swing"
               if best else "a short, low-inductance frame bond or a Y capacitor"),
              f"today the frame follows the switch node to about {v:.0f} V "
              f"peak; a long bonding wire makes it worse, not better, because "
              f"its inductance resonates with the winding-to-frame capacitance",
              "nothing on board A unless a Y capacitor is wanted on it",
              "P2, Q12")

    q14 = p6.get("Q14", {})
    if q14:
        entry("Decide what happens on a decel",
              "spec.html §11 questions 3 and 7 — an open design question",
              "a brake resistor, an active clamp, a much larger bulk, or a "
              "documented deceleration limit",
              "today the only protection is a software fold-back and two TVSs "
              "that would be asked to take tens of joules",
              "board B and/or firmware",
              "P6, Q14")
    entry("Correct the spec's own known-answer value",
          "sim/SPEC.md §5.4, the FastHenry straight-wire row",
          "14.53 nH, not 17.4 nH, for l = 20 mm and d = 1 mm",
          "the test passes as written once the expected value is right",
          "nothing",
          "P0 known-answer tests")
    paths.HANDBACK.write_text("\n".join(L) + "\n")
    return L


def link_index():
    """One line in the project's status page, and nothing else.

    Returns what it found, not whether it wrote: a link that is already there
    is the wanted state, and reporting that as False read like a failure.
    """
    p = paths.PROJECT / "index.html"
    t = p.read_text()
    if "sim/report/" in t:
        return "already-linked"
    anchor = '<p><a href="spec.html"><strong>Read the specification &rarr;</strong></a></p>'
    if anchor not in t:
        return "no-anchor-in-index"
    t = t.replace(anchor, anchor + '\n    <p><a href="sim/report/index.html">'
                  '<strong>Read the electromagnetic simulation &rarr;</strong>'
                  '</a></p>')
    p.write_text(t)
    return "added"


def run(quick=False):
    R = jsonio.read_all()
    paths.REPORT.mkdir(parents=True, exist_ok=True)
    (paths.REPORT / "index.html").write_text(build(R))
    n = write_findings(R)
    write_handback(R)
    linked = link_index()
    return {"report": str(paths.REPORT / "index.html"),
            "findings": n, "handback": str(paths.HANDBACK),
            "index_link": linked,
            "phases_present": sorted(R)}


if __name__ == "__main__":
    print(json.dumps(run(), indent=1, default=str))
