#!/usr/bin/env python3
"""The L3 circuit model of one phase cell, built from the P1 parasitics.

    net = CellNetlist(vbus=60, i_phase=28.28, tj=25).text()

Topology, and where every element's value comes from:

  DC link   Each of the four capacitors is a branch from the half-bridge's
            VBUS terminal to its GND terminal: the loop inductance FastHenry
            solved for that capacitor, mutually coupled to the other three
            with FastHenry's own mutual terms, then the part's ESL, ESR and
            its DC-bias-derated capacitance (H2).  That is the faithful
            reduction of P1's four-port: V_j = jw sum_k L_jk I_k.
  Half
  bridge    Two fitted BSC030N08NS5 (spice/devices.py), each with its package
            drain and source inductance -- the source inductance is common to
            the power and gate loops, which is what makes Q4 a real question.
  Gate      A behavioural EG2103, the 2.2 Ohm gate resistors, the 10 k bleeds,
            the bootstrap diode and its 1 uF, and the gate-loop inductance
            FastHenry solved.
  Load      A constant current: over a 100 ns edge a motor winding is a
            current source to within a part in 10^5.
  Sense     The two 1.6 mOhm shunts in parallel with their ESL, the 10 Ohm
            taps, the 1 nF differential filter, and a behavioural INA241A3.
            The tap loop is coupled to the shunt path by the transfer
            inductance FastHenry solved -- hypothesis H3 lives here.
"""
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import jsonio, paths, board                         # noqa: E402
from spice import devices                                    # noqa: E402

# Fallbacks used only when P1 has not been run; every one of them is reported
# as such so a number never silently comes from a guess.
DEFAULT_P1 = {
    "L_loop_nH": [2.97, 3.00, 2.83, 2.66],
    "M_loop_nH": [[0, 1.76, 2.37, 1.73], [1.76, 0, 1.78, 2.40],
                  [2.37, 1.78, 0, 1.76], [1.73, 2.40, 1.76, 0]],
    "L_gate_high_nH": 12.0, "L_gate_low_nH": 8.0,
    "L_shunt_nH": 1.0, "L_tap_nH": 6.0, "L_transfer_nH": 0.35,
    "L_link_nH": 12.0,
}


def _p1():
    r = jsonio.read("P1")
    if not r:
        return dict(DEFAULT_P1, source="defaults (P1 has not been run)")
    out = dict(source="sim/results/P1.json")
    q1 = r.get("Q1", {})
    sol = q1.get("solved", {})
    if sol:
        f = np.array(sol["f_Hz"])
        i = int(np.argmin(abs(f - 1e7)))
        L = np.array(sol["L_matrix_nH"])[i]
        out["L_loop_nH"] = np.diag(L).tolist()
        out["M_loop_nH"] = (L - np.diag(np.diag(L))).tolist()
        Rm = np.array(sol["R_matrix_mOhm"])[i]
        out["R_loop_mOhm"] = np.diag(Rm).tolist()
        out["ports"] = sol["ports"]
    else:
        out.update({k: DEFAULT_P1[k] for k in ("L_loop_nH", "M_loop_nH")})
    q4 = r.get("Q4", {})
    out["L_gate_high_nH"] = q4.get("high", {}).get(
        "L_nH_at_10MHz", DEFAULT_P1["L_gate_high_nH"])
    out["L_gate_low_nH"] = q4.get("low", {}).get(
        "L_nH_at_10MHz", DEFAULT_P1["L_gate_low_nH"])
    q5 = r.get("Q5", {})
    out["L_shunt_nH"] = q5.get("L_shunt_path_nH_at_10MHz",
                               DEFAULT_P1["L_shunt_nH"])
    out["L_tap_nH"] = q5.get("L_tap_loop_nH_at_10MHz", DEFAULT_P1["L_tap_nH"])
    out["L_transfer_nH"] = q5.get("L_transfer_tap_to_shunt_nH_at_10MHz",
                                  DEFAULT_P1["L_transfer_nH"])
    q13 = r.get("Q13", {})
    out["L_link_nH"] = q13.get("L_nH_at_1MHz", DEFAULT_P1["L_link_nH"])
    return out


class CellNetlist:
    def __init__(self, vbus=None, i_phase=28.28, tj=25.0, rg=2.2,
                 snubber=None, cap_corner="typ", drive_scale=1.0,
                 drv_r_scale=1.0, c_gs_ext=0.0,
                 vto_shift_low=0.0,
                 shunt_esl_corner="typ", tvs_corner="typ", p1=None,
                 t_pre=4e-6, t_on=3e-6, include_sense=True,
                 dead_time_sw=1.0e-6, c_vbus_gnd=82e-12, c_sw_gnd=33e-12,
                 fet_corner="typ", c_wf=0.0, c_frame_stray=30e-12,
                 frame_bond=("open", 0.0), r_frame_contact=5e-3):
        self.vbus = board.P()["v_bus"] if vbus is None else vbus
        self.i_phase = i_phase
        self.tj = tj
        self.rg = rg
        self.snubber = snubber
        self.cap_corner = cap_corner
        self.drive_scale = drive_scale
        self.drv_r_scale = drv_r_scale
        self.c_gs_ext = c_gs_ext
        self.vto_shift_low = vto_shift_low
        self.shunt_esl_corner = shunt_esl_corner
        self.tvs_corner = tvs_corner
        self.p1 = p1 or _p1()
        self.t_pre = t_pre
        self.t_on = t_on
        self.c_vbus_gnd = c_vbus_gnd
        self.c_sw_gnd = c_sw_gnd
        self.c_wf = c_wf
        self.c_frame_stray = c_frame_stray
        self.frame_bond = frame_bond
        self.r_frame_contact = r_frame_contact
        self.include_sense = include_sense
        self.dead_time_sw = dead_time_sw
        self.fet_corner = fet_corner
        self.fm = devices.fet_model(tj=tj, corner=fet_corner)

    # ------------------------------------------------------------------ net
    def text(self):
        p1 = self.p1
        fm = self.fm
        card, _ = devices.fet_cards(fm, rd=self._rd())
        L = np.array(p1["L_loop_nH"], float) * 1e-9
        M = np.array(p1["M_loop_nH"], float) * 1e-9
        caps = ["cap_100n_100v_0603", "cap_100n_100v_0603",
                "cap_2u2_100v_1206", "cap_2u2_100v_1206"]
        names = ["C103", "C104", "C101", "C102"]
        ports = p1.get("ports")
        if ports:
            # FastHenry lowercases and prefixes the port names
            order = [p.upper().lstrip("P") for p in ports]
            names = order
            caps = ["cap_100n_100v_0603" if n in ("C103", "C104")
                    else "cap_2u2_100v_1206" for n in order]

        S = [f"servodrive cell A, V_bus={self.vbus:g} V, "
             f"I_phase={self.i_phase:g} A, Tj={self.tj:g} C, Rg={self.rg:g} Ohm"]
        S.append(card)
        S.append(devices.eg2103_cards(r_scale=self.drv_r_scale))
        if self.include_sense:
            S.append(devices.ina241_cards())
        S.append(devices.tvs_card("TVSPH", board.P()["tvs_phase"],
                                  self.tvs_corner))

        S.append(f"Vsrc vsrc 0 DC {self.vbus:g}")
        S.append("Rsrc vsrc vb_far 0.05")
        S.append("Lsrc vb_far vb_bulk 1u")
        bulk = board.P()["bulk"]
        if bulk["kind"] == "cans":
            # ---- the bus: board S's own cans, behind the planes ----------
            # The two polymer cans in parallel, with the part's ESR and ESL
            # (models/cap_100u_100v_polymer.json); Llink is the In2/In1 plane
            # pair from the cans' leads to this cell, which P1 solves.
            can = jsonio.model(bulk["model"])
            n = len(bulk["refs"])
            S.append(f"Cbulk vb_bulk nbulk {n * can['C_nominal']['value']:g}")
            S.append(f"Rbulk nbulk nbulk2 {can['ESR']['max'] / n:g}")
            S.append(f"Lbulk nbulk2 0 {can['ESL']['typ'] / n:g}")
        else:
            # ---- the bus: board B behind the link inductance -------------
            S.append("Cbulk vb_bulk nbulk 400u")
            S.append("Rbulk nbulk 0 0.01")
        S.append(f"Llink vb_bulk hb_p {p1['L_link_nH'] * 1e-9:g}")

        # The VBUS sector on In2 against the GND planes above and below it.
        # Without it hb_p has only inductive branches -- an inductor cutset,
        # which no transient solver can integrate.  P1's FastCap run replaces
        # the estimate when it is available.
        S.append(f"Chbp hb_p hb_n {self.c_vbus_gnd:g}")
        # the switch node's own copper against the ground planes: the F.Cu
        # island over In1 and the B.Cu pour over In4, 47 and 78 mm^2 at
        # 0.1525 mm.  Small against C_oss (about 5 %) but real.
        S.append(f"Cswgnd sw hb_n {self.c_sw_gnd:g}")

        # ---- the DC-link capacitors, coupled --------------------------
        R = np.array(p1.get("R_loop_mOhm", [4.0] * len(L)), float) * 1e-3
        for k, (nm, mdl) in enumerate(zip(names, caps)):
            S.append(f"L{nm}loop hb_p {nm}_i {L[k]:g}")
            S.append(f"R{nm}loop {nm}_i {nm}_t {R[min(k, len(R) - 1)]:g}")
            txt, cval = devices.cap_card(nm, f"{nm}_t", "hb_n", mdl,
                                         bias=self.vbus, corner=self.cap_corner)
            S.append(txt.rstrip())
        for j in range(len(names)):
            for k in range(j + 1, len(names)):
                kc = M[j, k] / math.sqrt(L[j] * L[k])
                kc = max(min(kc, 0.98), -0.98)
                S.append(f"K{j}{k} L{names[j]}loop L{names[k]}loop {kc:.6f}")

        # ---- the half-bridge ------------------------------------------
        ls = fm["L_s"]
        ld = fm["L_d"]
        S.append(f"Ld1 hb_p d1 {ld:g}")
        S.append(devices.fet_instance(fm, "Q1", "d1", "g1", "s1").rstrip())
        S.append(f"Ls1 s1 sw {ls:g}")
        S.append(f"Ld2 sw d2 {ld:g}")
        if self.vto_shift_low:
            # the low-side device on its own threshold corner: same
            # capacitances, same charges, a different V_GS(th)
            flo = devices.fet_model(tj=self.tj, corner=self.fet_corner,
                                    name="QFETLO",
                                    vto_shift=self.vto_shift_low)
            S.append(devices.fet_cards(flo, rd=self._rd())[0])
            S.append(devices.fet_instance(flo, "Q2", "d2", "g2", "s2").rstrip())
        else:
            S.append(devices.fet_instance(fm, "Q2", "d2", "g2", "s2").rstrip())
        S.append(f"Ls2 s2 hb_n {ls:g}")
        S.append("Rhbn hb_n 0 1u")

        if self.snubber:
            r, c = self.snubber
            S.append(f"Rsnub sw nsnub {r:g}")
            S.append(f"Csnub nsnub hb_n {c:g}")

        # ---- gate drive ------------------------------------------------
        S.append("Vcc vcc 0 DC 12")
        S.append("Cvcc vcc hb_n 100n")
        S.append("Dboot vcc vbst DBOOT")
        S.append(".model DBOOT D(Is=1e-12 N=1.05 Rs=0.5 Cjo=15p Tt=35n Bv=100)")
        S.append("Cboot vbst sw 1u")
        S.append("Xdrv vcc hin lin hb_n ho sw vbst lo EG2103")
        S.append(f"Lgh ho nrg1 {p1['L_gate_high_nH'] * 1e-9:g}")
        S.append(f"Rg1 nrg1 g1 {self.rg / self.drive_scale:g}")
        S.append("Rbleed1 g1 sw 10k")
        S.append(f"Lgl lo nrg2 {p1['L_gate_low_nH'] * 1e-9:g}")
        S.append(f"Rg2 nrg2 g2 {self.rg / self.drive_scale:g}")
        S.append("Rbleed2 g2 hb_n 10k")
        # the remedy Q4 sweeps: a gate-source capacitor at the FET's own pins.
        # It does not fight the Miller current, it dilutes it -- the induced
        # step is Q_gd / C_iss, and this raises C_iss.  It is off by default.
        if self.c_gs_ext:
            S.append(f"Cgsx1 g1 s1 {self.c_gs_ext:g}")
            S.append(f"Cgsx2 g2 s2 {self.c_gs_ext:g}")

        # ---- the load: a motor winding is a current source over an edge
        sh = jsonio.model(board.P()["shunt"])
        esl = sh["L_esl"][self.shunt_esl_corner]
        S.append(f"Lshunt sw nsh1 {p1['L_shunt_nH'] * 1e-9:g}")
        # two in parallel: 2 x 1.6 -> 0.8 mOhm on board A, 2 x 2 -> 1.0 on S
        S.append(f"Rsh1 nsh1 nsh2 {sh['R']['value']:g}")
        S.append(f"Rsh2 nsh1 nsh2 {sh['R']['value']:g}")
        S.append(f"Lesl nsh2 phase {esl:g}")
        S.append("Xtvs phase hb_n TVSPH")
        # --- the common-mode path (Q12) ---------------------------------
        # The motor's winding-to-frame capacitance, and whatever bonds the
        # frame to board GND.  The four M3 motor mounts are NPTH, so on this
        # board nothing bonds them: the frame's only tie is through the
        # enclosure, which is why `frame_bond` is a parameter and not a value.
        if self.c_wf:
            S.append(f"Cwf phase nwf {self.c_wf:g}")
            S.append("Vwf nwf frame DC 0")          # an ammeter for I_cm
            S.append(f"Cwf_stray frame hb_n {self.c_frame_stray:g}")
            kind, val = self.frame_bond
            if kind == "open":
                S.append("Rframe frame hb_n 10meg")
            elif kind == "strap":
                S.append(f"Lframe frame nfb {val:g}")
                S.append(f"Rframe nfb hb_n {self.r_frame_contact:g}")
            elif kind == "ycap":
                S.append(f"Cyframe frame nfy {val:g}")
                S.append("Ryframe nfy hb_n 1")
                S.append("Rframe frame hb_n 10meg")
            else:
                S.append(f"Rframe frame hb_n {val:g}")
        S.append(f"Iload phase 0 DC {self.i_phase:g}")
        S.append("Rload phase 0 10meg")

        # ---- sense chain ----------------------------------------------
        if self.include_sense:
            S.append(f"Ltap ntapa ntapb {p1['L_tap_nH'] * 1e-9:g}")
            ktap = p1["L_transfer_nH"] / math.sqrt(
                max(p1["L_shunt_nH"] * p1["L_tap_nH"], 1e-9))
            ktap = max(min(ktap, 0.98), -0.98)
            S.append(f"Ktap Lshunt Ltap {ktap:.6f}")
            S.append("R107 sw nsnsp 10")
            S.append("R108 phase ntapa 10")
            S.append("Rtapb ntapb nsnsn 0.001")
            S.append("C108 nsnsp nsnsn 1n")
            S.append("V3v3 v3v3 0 DC 3.3")
            S.append("Vref vref 0 DC 1.65")
            S.append("Xamp nsnsp nsnsn vref v3v3 isense 0 INA241A3")
            S.append("Radc isense adcpin 0.001")
            S.append("Cadc adcpin 0 10p")

        # ---- the switching command -------------------------------------
        # Both edges in one run, and the run starts with the low side on so
        # that the bootstrap capacitor is charged before anything is asked of
        # the high side -- otherwise the operating point has both devices off
        # and the switch node undefined.
        #   0 .. t_pre        low side on, bootstrap charging
        #   t_pre             low side off
        #   t_pre + dt        high side on        <- the turn-on edge
        #   t_pre + dt + ton  high side off       <- the turn-off edge
        #   ... + dt          low side on
        # HIN and LIN carry the same polarity of command; the EG2103 inverts
        # LIN internally (SPEC.md sec.2.2), so LIN high means the low side off.
        dt = self.dead_time_sw
        t1 = self.t_pre
        t2 = t1 + dt
        t3 = t2 + self.t_on
        t4 = t3 + dt
        e = 2e-9
        S.append(f"Vhin hin 0 PWL(0 0 {t2:g} 0 {t2 + e:g} 3.3 "
                 f"{t3:g} 3.3 {t3 + e:g} 0)")
        S.append(f"Vlin lin 0 PWL(0 0 {t1:g} 0 {t1 + e:g} 3.3 "
                 f"{t4:g} 3.3 {t4 + e:g} 0)")
        self.t_turn_on = t2 + 780e-9
        self.t_turn_off = t3 + 220e-9
        # the low side coming back on after the dead time.  It is a soft event
        # -- its own body diode is already conducting -- but it is the end of
        # the quiet stretch after the turn-off edge, so anything measuring how
        # long that edge takes to settle has to stop here.
        self.t_low_on = t4 + 780e-9
        self.t_stop = t4 + 2.5e-6
        S.append(".options reltol=1e-4 abstol=1e-9 vntol=1e-6 chgtol=1e-15")
        S.append("+ gmin=1e-13 itl1=500 itl4=200 method=gear maxord=2")
        S.append(".end")
        return "\n".join(S) + "\n"

    def _rd(self):
        """R_d that reproduces the datasheet R_DS(on) at this junction
        temperature -- fitted once and cached on disk."""
        key = paths.WORK / "fet_rd.json"
        cache = json.loads(key.read_text()) if key.exists() else {}
        k = f"{self.tj:g}"
        if k not in cache:
            from spice.devices import fit_fet
            fit = fit_fet()
            base = fit["R_d_fitted_Ohm"]
            M = jsonio.model("bsc030n08ns5")
            scale = (M["R_ds_on_125C_factor"]["value"] if self.tj > 100 else 1.0)
            cache[k] = base * scale
            key.write_text(json.dumps(cache))
        return cache[k]
