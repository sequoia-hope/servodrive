#!/usr/bin/env python3
"""P5S -- the field at the MT6701 on board S, the single-board variant.

Board S (tools/placement_s.py) moves the two bulk cans into the middle of the
outward face, 14.5-15.3 mm off the shaft axis. P5 asked what the phase
currents do to the encoder; this asks the same of the cans' ripple current,
which on board A lived on board B, 13 mm and a board away.

What is added at the sensor, on top of P5's own terms:

  - each can's ripple loop, as the current actually runs: out of one lead,
    through the planes to the three half-bridges, back along the other plane
    to the other lead, and across the inside of the can between its two
    leads. The two leads are vertical, which is the worst orientation a
    current can have for this sensor: a vertical current makes a purely
    in-plane field, and in-plane is what the MT6701 measures. The internal
    connection height inside the can is not known, so it is swept.
  - P5's phase-copper and motor-lead terms, re-evaluated against the Dia 6
    magnet the board now specifies (P5 was run with Dia 8).

The ripple is at the switching frequency, not the electrical one, so it is
not a slow error the rotor sweeps through: it is a 20 kHz vector of fixed
direction whose sign and size follow the DC-link current. The bound taken
here is its peak, at whichever sign and rotor angle is worst.

Both arrangements are evaluated -- cans in the centre, and the same two cans
back in the power wedge -- so the cost of the centre is a number.

Since 2026-09-24 the board takes its bus on an XT30 set back from the edge
(tools/placement_s.py, bus="xt30"), which moves C1001 to 135 deg and brings
the whole DC bus current to R 14.5: in along the connector's two contacts,
a few mm above the outward face, down its pins, and out through the planes
to the bridges. That is a third arrangement, "xt30", with one more term: the
bus current's loop, a slow offset that follows the load (and reverses in
regen), added to the phase copper's static field. The height of the contacts
inside the housing is not known either, so it is swept too.
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
from encoder.field import (sensor_point, magnet_field,       # noqa: E402
                           current_paths, biot_savart, lead_field)

paths.import_tools()
MU0 = 4e-7 * math.pi
I_PEAK = 28.284                   # A, the design point's phase peak
CRITERION_DEG = 0.05              # SPEC.md Q10


def ripple():
    """The DC-link ripple the bulk carries at the design point, and its peak.

    The RMS is geometry.interconnect()'s three-phase VSI expression (m 0.8,
    unity power factor). Where it goes: at 20 kHz the board's ceramics are
    ~7 uF at bias (P4) -- 1.1 ohm -- and two polymer cans are ~0.05 ohm, so
    essentially all of it is the cans'. The instantaneous peak is the larger
    of (I_pk - I_avg) during an active vector and I_avg during a zero vector,
    with I_avg = 3/4 m I_pk.
    """
    import geometry as G
    ic = G.interconnect(10)
    m = 0.8
    i_avg = 0.75 * m * I_PEAK
    z_cer = 1 / (2 * math.pi * G.FSW * 7e-6)
    z_bulk = abs(complex(0.015, -1 / (2 * math.pi * G.FSW * 200e-6)))
    share = z_cer / (z_cer + z_bulk)
    return {"i_ripple_rms_total": ic["i_ripple"],
            "share_to_bulk": share,
            "i_bulk_rms": ic["i_ripple"] * share,
            "i_bulk_peak": max(I_PEAK - i_avg, i_avg) * share,
            "i_avg": i_avg,
            "note": "all of it split equally between the two cans"}


R_RING = 20.0          # mm: where the ripple runs round the VBUS annulus on In2
CPU_GAP = (256.0, 308.0)   # the CPU wedge: +3V3 on In2, no VBUS to run through


def _arc(a0, a1):
    """Angles from a0 to a1 the way round that does not cross the CPU wedge,
    which has no VBUS copper on In2."""
    def swept(a, b, sign):
        n = int(abs(((b - a) * sign) % 360) / 2) + 1
        return [a + sign * ((b - a) * sign % 360) * k / n for k in range(n + 1)]
    for sign in (+1, -1):
        pts = swept(a0, a1, sign)
        if not any(CPU_GAP[0] < (x % 360) < CPU_GAP[1] for x in pts[1:-1]):
            return pts
    return swept(a0, a1, +1)


def _plane_path(x, y, cx, cy):
    """From a can's pin to a bridge through the VBUS annulus: out to R_RING,
    round it, and in or out to the bridge. In a plane the current spreads,
    but at 20 kHz it follows the shortest path round the copper there is;
    this is that path as a line."""
    a0 = math.degrees(math.atan2(y, x)) % 360
    a1 = math.degrees(math.atan2(cy, cx)) % 360
    pts = [(x, y)]
    for a in _arc(a0, a1):
        pts.append((R_RING * math.cos(math.radians(a)), R_RING * math.sin(math.radians(a))))
    pts.append((cx, cy))
    return pts


def can_loops(part, z_vbus, z_gnd, h_int, cell_pts):
    """Closed current loops for one can carrying 1 A: + lead up into the can,
    across inside it, - lead down, and through the planes to each bridge --
    out on VBUS (In2) round the annulus, back on GND (In1) directly beneath
    it, which is where a 20 kHz return runs. One loop per bridge, each with
    1/len(cell_pts) A. Coordinates in m, z positive toward the motor (the
    sim's convention), so the can is at z < 0."""
    import magpylib as magpy
    holes = part.tht()
    (x1, y1, _), (x2, y2, _) = holes[0], holes[1]
    top = -h_int
    out = []
    for cx, cy in cell_pts:
        go = _plane_path(x1, y1, cx, cy)
        back = _plane_path(x2, y2, cx, cy)[::-1]
        v = ([(x1, y1, top)] + [(a, b, z_vbus) for a, b in go]
             + [(a, b, z_gnd) for a, b in back] + [(x2, y2, top), (x1, y1, top)])
        out.append(magpy.current.Polyline(
            current=1.0 / len(cell_pts),
            vertices=[(a * 1e-3, b * 1e-3, c * 1e-3) for a, b, c in v]))
    return out


def xt30_loops(j4, z_vbus, z_gnd, h_c, cell_pts, far=150.0):
    """Closed loops for 1 A of DC bus current through the XT30: in along the
    VMOT contact from `far` mm out, at h_c above the outward face, down the
    pin to In2, through the VBUS plane to the bridges, back on In1 to the
    ground pin, up it and out along the ground contact. Like can_loops, the
    planes carry it as the line _plane_path draws; at DC it spreads wider,
    which only moves it further from the sensor."""
    import magpylib as magpy
    holes = j4.tht()
    (gx, gy, _), (vx, vy, _) = holes[2], holes[3]         # pin 1 GND, pin 2 VMOT
    a = math.radians(j4.ang + 90)                         # the mouth's way out
    ux, uy = math.cos(a), math.sin(a)
    out = []
    for cx, cy in cell_pts:
        go = _plane_path(vx, vy, cx, cy)
        back = _plane_path(gx, gy, cx, cy)[::-1]
        v = ([(vx + ux * far, vy + uy * far, -h_c), (vx, vy, -h_c)]
             + [(p, q, z_vbus) for p, q in go] + [(p, q, z_gnd) for p, q in back]
             + [(gx, gy, -h_c), (gx + ux * far, gy + uy * far, -h_c),
                (vx + ux * far, vy + uy * far, -h_c)])
        out.append(magpy.current.Polyline(
            current=1.0 / len(cell_pts),
            vertices=[(p * 1e-3, q * 1e-3, r * 1e-3) for p, q, r in v]))
    return out


def unit_field_xt30(j4, h_c, split="all"):
    """B at the sensor per amp of bus current through the XT30."""
    import geometry as G
    import placement as PL
    st = copper.stackup()
    z_vbus, z_gnd = st["In2.Cu"]["z"], st["In1.Cu"]["z"]
    p = sensor_point()
    cells = [PL.polar_xy(a, PL.R_DCLINK) for a in G.PHASE_ANG]
    pts = {"all": cells, "A": [cells[0]], "B": [cells[1]], "C": [cells[2]]}[split]
    B = np.zeros(3)
    for s in xt30_loops(j4, z_vbus, z_gnd, h_c, pts):
        B += np.asarray(s.getB(p), float)
    return B


def unit_field(cans, h_int, split="all"):
    """B at the sensor per amp in each can, all cans in phase."""
    import geometry as G
    import placement as PL
    st = copper.stackup()
    z_vbus, z_gnd = st["In2.Cu"]["z"], st["In1.Cu"]["z"]
    p = sensor_point()
    # each bridge's DC link, where the ripple current turns round
    cells = [PL.polar_xy(a, PL.R_DCLINK) for a in G.PHASE_ANG]
    splits = {"all": [cells], "A": [[cells[0]]], "B": [[cells[1]]], "C": [[cells[2]]]}
    worst = None
    for pts in splits[split]:
        B = np.zeros(3)
        for c in cans:
            for s in can_loops(c, z_vbus, z_gnd, h_int, pts):
                B += np.asarray(s.getB(p), float)
        worst = B if worst is None or np.hypot(*B[:2]) > np.hypot(*worst[:2]) else worst
    return worst


def angle_error(B_mag_of, B_static_of, B_rip, n=73):
    """Worst angle error over a rotor revolution, the ripple at either sign."""
    worst, sweep = 0.0, []
    for th in np.linspace(0, 360, n, endpoint=False):
        Bm = B_mag_of(th)
        Bs = B_static_of(th)
        e = 0.0
        for s in (+1, -1, 0):
            tot = Bm + Bs + s * B_rip
            d = math.degrees(math.atan2(tot[1], tot[0]) - math.atan2(Bm[1], Bm[0]))
            d = (d + 180) % 360 - 180
            e = max(e, abs(d))
        sweep.append((float(th), e))
        worst = max(worst, e)
    return worst, sweep


def q10s(quick=False):
    t0 = time.time()
    import placement_s as PS
    p = sensor_point()
    rip = ripple()
    i_can_pk = rip["i_bulk_peak"] / 2
    i_can_rms = rip["i_bulk_rms"] / 2
    out = {"question": "Q10 on board S",
           "criterion": f"current-induced angle error <= {CRITERION_DEG} deg (SPEC.md Q10)",
           "sensor_point_mm": (p * 1e3).tolist(),
           "ripple": rip, "i_can_peak_A": i_can_pk, "i_can_rms_A": i_can_rms}

    arrangements = {
        "centre": dict(power="two", tvs="SMC", ports=2, port="SH6", cans="centre",
                       relay=True, exp="2x10", exp_first=True, enc_vias="moved"),
        "wedge": dict(power="two", tvs="SMB", ports=2, port="SH6", cans="wedge",
                      relay=True, exp="2x10", exp_first=True, enc_vias="moved"),
        "xt30": dict(PS.LAYOUT),
    }
    geo, j4 = {}, None
    for name, opt in arrangements.items():
        S = PS.board_s_open(**opt)
        cans = [q for q in S.new if q.ref in ("C1001", "C1002")]
        geo[name] = cans
        if name == "xt30":
            j4 = next(q for q in S.new if q.ref == "J4")
        out.setdefault("cans", {})[name] = [
            {"ref": c.ref, "centre_mm": [round(v, 2) for v in PS._centre(c)],
             "r_mm": round(math.hypot(*PS._centre(c)), 2),
             "pins_mm": [[round(h[0], 2), round(h[1], 2)] for h in c.tht()]} for c in cans]

    # the can loops, per amp, over the unknown internal height and the split
    h_list = (1.0, 2.0, 4.0) if not quick else (2.0,)
    units = {}
    for name, cans in geo.items():
        for h in h_list:
            for split in ("all", "A", "B", "C"):
                B = unit_field(cans, h, split)
                units[(name, h, split)] = B
    out["B_per_amp_uT"] = {f"{n}/h{h}/{s}": (v * 1e6).tolist() for (n, h, s), v in units.items()}

    # the XT30's bus loop, per amp, over the contacts' height and the split;
    # the current is the DC link's average at the design point, either sign
    i_bus = rip["i_avg"]
    hc_list = (2.0, 3.5, 5.0) if not quick else (3.5,)
    bus_units = {(h, s): unit_field_xt30(j4, h, s) for h in hc_list for s in ("all", "A", "B", "C")}
    out["xt30"] = {"pins_mm": [[round(h[0], 2), round(h[1], 2)] for h in j4.tht()[2:]],
                   "i_bus_A": i_bus,
                   "B_per_amp_uT": {f"h{h}/{s}": (v * 1e6).tolist() for (h, s), v in bus_units.items()},
                   "B_peak_uT": float(max(np.hypot(*v[:2]) for v in bus_units.values()) * i_bus * 1e6)}

    # P5's terms at the design point, for the static part of the sum
    B_cu = {}
    try:
        for cell in ("A", "B", "C"):
            pts, mom = current_paths(cell, cellsize=0.15 if quick else 0.1, current=1.0)
            B_cu[cell] = biot_savart(pts, mom, p)
    except Exception as e:
        B_cu = {k: np.zeros(3) for k in "ABC"}
        out["copper_error"] = f"{e.__class__.__name__}: {e}"

    def static(th):
        a = math.radians(th)
        ia = I_PEAK * math.cos(a)
        ib = I_PEAK * math.cos(a - 2 * math.pi / 3)
        ic = I_PEAK * math.cos(a + 2 * math.pi / 3)
        return (B_cu["A"] * ia + B_cu["B"] * ib + B_cu["C"] * ic
                + lead_field(p, ia, ib, ic, length=0.25))

    # the magnet the board now specifies: Dia 6 x 2.5, over the gap tolerance
    rows = []
    n = 73 if not quick else 25
    for grade, Br in (("N35", 1.195), ("N42", 1.30)):
        for gap in (1.0, 1.5, 2.0):
            Bm_of = (lambda th, g=gap, b=Br:
                     magnet_field(gap_mm=g, Br=b, D=6e-3, H=2.5e-3, angle_deg=th))
            B_ip = float(np.hypot(*Bm_of(0.0)[:2]))
            zero = lambda th: np.zeros(3)
            e_phase, _ = angle_error(Bm_of, static, np.zeros(3), n)
            row = {"grade": grade, "gap_mm": gap, "B_magnet_inplane_mT": B_ip * 1e3,
                   "in_window": bool(20e-3 <= B_ip <= 100e-3),
                   "err_phase_only_deg": e_phase}
            for name in geo:
                worst_cap, worst_all, which = 0.0, 0.0, None
                for h in h_list:
                    for split in ("all", "A", "B", "C"):
                        Bu = units[(name, h, split)]
                        B_rip = Bu * i_can_pk
                        e_cap, _ = angle_error(Bm_of, zero, B_rip, n)
                        e_all, _ = angle_error(Bm_of, static, B_rip, n)
                        if e_all > worst_all:
                            worst_all, which = e_all, (h, split)
                        worst_cap = max(worst_cap, e_cap)
                row[f"{name}_caps_only_deg"] = worst_cap
                row[f"{name}_combined_deg"] = worst_all
                row[f"{name}_worst_case"] = {"h_int_mm": which[0], "split": which[1]}
            # the XT30's bus current on its own, and with everything else
            wc = row["xt30_worst_case"]
            B_rip = units[("xt30", wc["h_int_mm"], wc["split"])] * i_can_pk
            e_bus, e_tot, which = 0.0, 0.0, None
            for (h, s), Bu in bus_units.items():
                for sign in (+1, -1):
                    Bb = sign * Bu * i_bus
                    e1, _ = angle_error(Bm_of, lambda th, b=Bb: b, np.zeros(3), n)
                    e2, _ = angle_error(Bm_of, lambda th, b=Bb: static(th) + b, B_rip, n)
                    e_bus = max(e_bus, e1)
                    if e2 > e_tot:
                        e_tot, which = e2, {"h_contacts_mm": h, "split": s, "sign": sign}
            row["xt30_bus_only_deg"] = e_bus
            row["xt30_all_deg"] = e_tot
            row["xt30_all_worst_case"] = which
            rows.append(row)
    out["table"] = rows

    # the headline: the weakest field the build can see, the worst the cans do
    weakest = min(rows, key=lambda r: r["B_magnet_inplane_mT"])
    nominal = next(r for r in rows if r["grade"] == "N35" and r["gap_mm"] == 1.5)
    out["headline"] = {
        "nominal_N35_gap1.5": {k: nominal[k] for k in nominal if k.endswith("_deg")
                               or k == "B_magnet_inplane_mT"},
        "weakest": {k: weakest[k] for k in weakest if k.endswith("_deg")
                    or k in ("B_magnet_inplane_mT", "grade", "gap_mm")},
        "centre_caps_pass_everywhere": bool(all(r["centre_caps_only_deg"] <= CRITERION_DEG
                                                for r in rows)),
        "centre_caps_B_peak_uT": float(max(np.hypot(*units[(k[0], k[1], k[2])][:2])
                                           for k in units if k[0] == "centre") * i_can_pk * 1e6),
        "wedge_caps_B_peak_uT": float(max(np.hypot(*units[(k[0], k[1], k[2])][:2])
                                          for k in units if k[0] == "wedge") * i_can_pk * 1e6),
        "xt30_caps_B_peak_uT": float(max(np.hypot(*units[(k[0], k[1], k[2])][:2])
                                         for k in units if k[0] == "xt30") * i_can_pk * 1e6),
        "xt30_bus_B_peak_uT": out["xt30"]["B_peak_uT"],
        "xt30_all_pass_everywhere": bool(all(r["xt30_all_deg"] <= CRITERION_DEG for r in rows)),
    }
    _plot(out, geo, units, h_list, i_can_pk, static)
    out["figure"] = "p5s_board_s.png"
    out["seconds"] = round(time.time() - t0, 1)
    return out


NAME = {"centre": "cans in the centre", "wedge": "cans in the wedge", "xt30": "cans, XT30 layout (C1001 at 135 deg)"}


def _plot(out, geo, units, h_list, i_can_pk, static):
    fig, ax = plt.subplots(1, 2, figsize=(11, 4), dpi=150)
    rows = out["table"]
    for name, col in (("centre", "C3"), ("wedge", "C0"), ("xt30", "C2")):
        for grade, ls in (("N35", "-"), ("N42", "--")):
            rr = [r for r in rows if r["grade"] == grade]
            ax[0].plot([r["gap_mm"] for r in rr], [r[f"{name}_caps_only_deg"] for r in rr],
                       color=col, ls=ls, marker="o", ms=3, lw=1.2,
                       label=f"{NAME[name]}, {grade}")
    rr = [r for r in rows if r["grade"] == "N35"]
    ax[0].plot([r["gap_mm"] for r in rr], [r["xt30_bus_only_deg"] for r in rr],
               color="C2", ls="-.", marker="^", ms=3, lw=1,
               label=f"XT30 bus current alone ({out['xt30']['i_bus_A']:.0f} A), N35")
    ax[0].plot([r["gap_mm"] for r in rr], [r["err_phase_only_deg"] for r in rr],
               color="0.4", ls=":", marker="s", ms=3, lw=1, label="phase leads + copper, N35")
    ax[0].axhline(CRITERION_DEG, color="r", ls="--", lw=.8, label="0.05 deg criterion")
    ax[0].set_xlabel("air gap (mm), Dia 6 x 2.5 magnet")
    ax[0].set_ylabel("worst angle error (deg)")
    ax[0].set_title(f"bulk-cap ripple at its peak ({i_can_pk:.1f} A per can)", fontsize=9)
    ax[0].legend(fontsize=6); ax[0].grid(alpha=.3)
    # the error over a revolution at the weakest field, worst case
    import encoder.field as EF
    w = min(rows, key=lambda r: r["B_magnet_inplane_mT"])
    Bm_of = lambda th: EF.magnet_field(gap_mm=w["gap_mm"], Br=1.195 if w["grade"] == "N35"
                                       else 1.30, D=6e-3, H=2.5e-3, angle_deg=th)
    for name, col in (("centre", "C3"), ("wedge", "C0"), ("xt30", "C2")):
        wc = w[f"{name}_worst_case"]
        B_rip = units[(name, wc["h_int_mm"], wc["split"])] * i_can_pk
        _, sw = angle_error(Bm_of, static, B_rip, 73)
        ax[1].plot([s[0] for s in sw], [s[1] for s in sw], color=col, lw=1.2,
                   label=f"{NAME[name]} + leads")
    _, sw = angle_error(Bm_of, static, np.zeros(3), 73)
    ax[1].plot([s[0] for s in sw], [s[1] for s in sw], color="0.4", ls=":", lw=1,
               label="leads + copper only")
    ax[1].axhline(CRITERION_DEG, color="r", ls="--", lw=.8)
    ax[1].set_xlabel("rotor angle (deg)"); ax[1].set_ylabel("|angle error| (deg)")
    ax[1].set_title(f"weakest field: {w['grade']}, gap {w['gap_mm']} mm "
                    f"({w['B_magnet_inplane_mT']:.0f} mT), 28.3 A", fontsize=9)
    ax[1].legend(fontsize=7); ax[1].grid(alpha=.3)
    fig.tight_layout()
    fig.savefig(paths.FIGS / "p5s_board_s.png")
    plt.close(fig)


def run(quick=False):
    return {"Q10S": q10s(quick)}


if __name__ == "__main__":
    print(json.dumps(run(quick=True), indent=1, default=str)[:4000])
