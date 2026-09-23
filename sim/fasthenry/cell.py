#!/usr/bin/env python3
"""The cell-A FastHenry models: commutation loop, gate loops, sense taps.

Every model here is built from `sim/work/geometry.json`, i.e. from the board as
routed, not from a sketch of it.  What each model asserts about the circuit --
which pads are shorted to stand for a conducting FET, where the ports are -- is
stated in the docstring of the builder, because that is the modelling choice a
reader has to be able to disagree with.
"""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from extract import copper                                   # noqa: E402
from fasthenry import runner, mesher                         # noqa: E402

CU = copper.CU_LAYERS

# The cell-A parts, from SPEC.md sec.1.5.  Cells B and C are the same rotated.
CELL = {
    "A": dict(hi="Q1", lo="Q2", bulk=("C101", "C102"), hf=("C103", "C104"),
              drv="U1", rg=("R101", "R102"), boot="C105", vcc="C106",
              shunts=("R105", "R106"), taps=("R107", "R108"), filt="C108",
              amp="U4", bleed=("R109", "R110"), sw="SW_A", phase="PHASE_A",
              lead="J1"),
    "B": dict(hi="Q3", lo="Q4", bulk=("C201", "C202"), hf=("C203", "C204"),
              drv="U2", rg=("R201", "R202"), boot="C205", vcc="C206",
              shunts=("R205", "R206"), taps=("R207", "R208"), filt="C208",
              amp="U5", bleed=("R209", "R210"), sw="SW_B", phase="PHASE_B",
              lead="J2"),
    "C": dict(hi="Q5", lo="Q6", bulk=("C301", "C302"), hf=("C303", "C304"),
              drv="U3", rg=("R301", "R302"), boot="C305", vcc="C306",
              shunts=("R305", "R306"), taps=("R307", "R308"), filt="C308",
              amp="U6", bleed=("R309", "R310"), sw="SW_C", phase="PHASE_C",
              lead="J3"),
}


def _pad_pts(ref, padnum, layer=None):
    """Every pad entry of ref/padnum, as (x, y)."""
    out = []
    for p in copper.pads_of(ref):
        if p["pad"] != str(padnum):
            continue
        if layer and layer not in p["shapes"]:
            continue
        out.append((p["x"], p["y"]))
    return out


def power_window(cell="A", margin=2.0):
    """A box that holds the whole commutation loop: both FETs, the four
    DC-link capacitors and the switch-node island, with room for the return
    current to spread."""
    c = CELL[cell]
    pts = []
    for ref in (c["hi"], c["lo"], *c["bulk"], *c["hf"]):
        for p in copper.pads_of(ref):
            pts.append((p["x"], p["y"]))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)


def commutation_model(cell="A", target=0.7, nhinc=3, margin=2.5,
                      include_b_cu=False, min_gap=0.15):
    """The hard commutation loop of one phase cell.

    Conductors meshed: VBUS on F.Cu (the drain-pad island and the capacitor
    pads), VBUS on In2 (the only place VBUS is a plane), the switch-node island
    on F.Cu, and GND on F.Cu, In1, In3 and In4.  Every plated barrel of those
    nets in the window is a vertical bar joined to the mesh only on the layers
    where its net has copper -- so the antipads that perforate In1 under the
    high-side drain are in the model, not assumed away.

    Both FETs are shorted at their pads: the high side's source pads to the
    nearest node of its drain-pad island, the low side's drain to its source
    pads.  What comes out is therefore the PCB part of the loop; the packages
    and the capacitors' own ESL are added afterwards, with their brackets.

    Ports, one per DC-link capacitor, between its VBUS pad and its GND pad:
    Z_kk / j.omega is the loop inductance that capacitor sees.
    """
    c = CELL[cell]
    win = power_window(cell, margin)
    g = copper.load()

    nets = ("VBUS", "GND", c["sw"])
    bar = copper.barrels(nets, win)

    req_x = sorted({b["x"] for b in bar})
    req_y = sorted({b["y"] for b in bar})
    for ref in (c["hi"], c["lo"], *c["bulk"], *c["hf"]):
        for p in copper.pads_of(ref):
            req_x.append(p["x"])
            req_y.append(p["y"])

    m = mesher.Mesh(f"servodrive cell {cell} commutation loop", win,
                    target=target, required_x=req_x, required_y=req_y,
                    nhinc=nhinc, min_gap=min_gap)

    layer_tag = {}
    m.add_conductor("vbF", "VBUS", "F.Cu")
    layer_tag["F.Cu"] = "vbF"
    m.add_conductor("vb2", "VBUS", "In2.Cu")
    m.add_conductor("swF", c["sw"], "F.Cu")
    for tag, layer in (("gF", "F.Cu"), ("g1", "In1.Cu"),
                       ("g3", "In3.Cu"), ("g4", "In4.Cu")):
        m.add_conductor(tag, "GND", layer)
    if include_b_cu:
        m.add_conductor("swB", c["sw"], "B.Cu")

    vbus_tags = {"F.Cu": "vbF", "In2.Cu": "vb2"}
    gnd_tags = {"F.Cu": "gF", "In1.Cu": "g1", "In3.Cu": "g3", "In4.Cu": "g4"}
    sw_tags = {"F.Cu": "swF"}
    if include_b_cu:
        sw_tags["B.Cu"] = "swB"

    for b in bar:
        tags = {"VBUS": vbus_tags, "GND": gnd_tags}.get(b["net"], sw_tags)
        m.add_barrel(b, tags)

    # --- short the two FETs at their pads -------------------------------
    hi, lo = c["hi"], c["lo"]
    hi_drain = _pad_pts(hi, 5, "F.Cu")          # the big F.Cu tab
    hi_src = [p for n in (1, 2, 3) for p in _pad_pts(hi, n, "F.Cu")]
    lo_drain = _pad_pts(lo, 5, "F.Cu")
    lo_src = [p for n in (1, 2, 3) for p in _pad_pts(lo, n, "F.Cu")]

    # every pad is an equipotential piece of copper, not a mesh point
    shorts = []
    hi_d_nodes = m.pad_equipotential("vbF", hi, 5)
    lo_d_nodes = m.pad_equipotential("swF", lo, 5)
    hi_s_nodes, lo_s_nodes = [], []
    for n in (1, 2, 3):
        hi_s_nodes += m.pad_equipotential("swF", hi, n)
        lo_s_nodes += m.pad_equipotential("gF", lo, n)

    # high side: VBUS tab -> SW island; low side: SW tab -> GND on F.Cu.
    # These are the two conducting channels; what comes out is the PCB part of
    # the loop, the packages being added afterwards with their bracket.
    if hi_d_nodes and hi_s_nodes:
        m.model.equiv(hi_d_nodes[0], *sorted(set(hi_s_nodes)))
        shorts.append((hi_d_nodes[0], hi_s_nodes[0]))
    if lo_d_nodes and lo_s_nodes:
        m.model.equiv(lo_d_nodes[0], *sorted(set(lo_s_nodes)))
        shorts.append((lo_d_nodes[0], lo_s_nodes[0]))

    # --- ports at the four DC-link capacitors ----------------------------
    ports = []
    for ref in (*c["hf"], *c["bulk"]):
        vn = m.pad_equipotential("vbF", ref, 1)
        gn = m.pad_equipotential("gF", ref, 2)
        if not vn or not gn:
            vp = _pad_pts(ref, 1, "F.Cu")
            gp = _pad_pts(ref, 2, "F.Cu")
            vn = vn or [m.terminal(f"{ref}_v", "vbF", *vp[0], max_dist=1.5)]
            gn = gn or [m.terminal(f"{ref}_g", "gF", *gp[0], max_dist=1.5)]
        m.model.port(f"P{ref}", vn[0], gn[0])
        ports.append(ref)

    m.pruned = m.model.prune_to_ports()
    return m, ports, win, shorts


def gate_model(cell="A", side="high", target=1.0, nhinc=3, margin=2.0):
    """One gate loop: driver output -> gate resistor -> gate pad -> through the
    device -> source -> back to the driver's own reference pin.

    The gate nets are thin tracks, so they are meshed as tracks -- one
    FastHenry segment per PCB track -- rather than rasterised: a grid coarse
    enough to carry a ground plane misses a 0.15 mm trace entirely and the net
    comes back as a string of islands.  The return path is a plane and is
    rasterised as one.

    The gate resistor and the device's gate-source are shorted, so the port
    impedance is the loop's own R(f) and L(f); the 2.2 Ohm and C_iss are put
    back in P2.
    """
    c = CELL[cell]
    fet = c["hi"] if side == "high" else c["lo"]
    rg = c["rg"][0] if side == "high" else c["rg"][1]
    drv_out = 7 if side == "high" else 5          # EG2103 HO / LO
    drv_ref = 6 if side == "high" else 4          # VS / COM
    gate_net = f"GH_{cell}" if side == "high" else f"GL_{cell}"
    out_net = f"HO_{cell}" if side == "high" else f"LO_{cell}"
    ret_net = c["sw"] if side == "high" else "GND"

    pts = []
    for ref in (c["drv"], rg, fet):
        for p in copper.pads_of(ref):
            pts.append((p["x"], p["y"]))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    win = (min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)

    nets = (out_net, gate_net, ret_net, "GND")
    bar = copper.barrels(nets, win)
    req_x = sorted({b["x"] for b in bar} | {p[0] for p in pts})
    req_y = sorted({b["y"] for b in bar} | {p[1] for p in pts})

    m = mesher.Mesh(f"servodrive cell {cell} {side}-side gate loop", win,
                    target=target, required_x=req_x, required_y=req_y,
                    nhinc=nhinc, min_gap=0.25)
    layers = ("F.Cu", "In3.Cu", "B.Cu")
    m.add_tracks("gate_out", out_net, layers)
    m.add_tracks("gate_g", gate_net, layers)
    tags = {out_net: "gate_out", gate_net: "gate_g"}

    # the return: a pour, rasterised
    ret_tags = {}
    if ret_net == "GND":
        for layer, suf in (("F.Cu", "F"), ("In1.Cu", "1"), ("In3.Cu", "3"),
                           ("In4.Cu", "4"), ("B.Cu", "B")):
            u = copper.net_union("GND", layer, win)
            if u is not None and u.area > 1.0:
                m.add_conductor("n" + suf, "GND", layer)
                ret_tags[layer] = "n" + suf
    else:
        for layer, suf in (("F.Cu", "F"), ("B.Cu", "B")):
            u = copper.net_union(ret_net, layer, win)
            if u is not None and u.area > 1.0:
                m.add_conductor("r" + suf, ret_net, layer)
                ret_tags[layer] = "r" + suf
    tags[ret_net] = ret_tags

    for b in bar:
        t = tags.get(b["net"])
        if isinstance(t, str):
            t = {l: t for l in layers}
        m.add_barrel(b, t or {})

    def ret_pad(ref, num):
        got = []
        for layer, tag in ret_tags.items():
            got += m.pad_equipotential(tag, ref, num, layer=layer)
        return got

    rg_a = m.pad_node("gate_out", rg, 1)
    rg_b = m.pad_node("gate_g", rg, 2)
    fet_g = m.pad_node("gate_g", fet, 4)
    fet_s = ret_pad(fet, 1) + ret_pad(fet, 2) + ret_pad(fet, 3)
    drv_o = m.pad_node("gate_out", c["drv"], drv_out)
    drv_r = ret_pad(c["drv"], drv_ref)

    if rg_a and rg_b:
        m.model.equiv(rg_a, rg_b)                 # the gate resistor, shorted
    if fet_g and fet_s:
        m.model.equiv(fet_g, fet_s[0])            # the device's gate-source
    if not (drv_o and drv_r):
        raise RuntimeError(f"gate model {cell}/{side}: no driver pad nodes "
                           f"(out={drv_o}, ref={bool(drv_r)})")
    m.model.port("Pgate", drv_o, drv_r[0])
    m.pruned = m.model.prune_to_ports()
    return m, win


def sense_model(cell="A", target=0.5, nhinc=3, margin=1.5):
    """The shunt pair and the two sense taps.

    The pours -- SW and PHASE on B.Cu -- are rasterised; the SNSP and SNSN
    tracks are thin and are meshed as tracks.  Two ports:

      Pshunt  the current path, SW pour to PHASE pour across the shunt pads
      Ptap    the voltage the amplifier sees, between its two input pins

    Z21 of that pair is the sense voltage per amp of phase current, including
    the L.di/dt the tap loop picks up -- which is what hypothesis H3 is about.
    """
    c = CELL[cell]
    # the window has to hold the whole current path -- the low-side drain's
    # via array, the shunts, and the lead pad -- as well as the tap network,
    # because the port that carries the current runs from one end to the other
    refs = [*c["shunts"], *c["taps"], c["filt"], c["amp"], c["lo"], c["lead"]]
    pts = []
    for ref in refs:
        for p in copper.pads_of(ref):
            pts.append((p["x"], p["y"]))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    win = (min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)

    sp_net, sn_net = f"SNSP_{cell}", f"SNSN_{cell}"
    nets = ("GND", c["sw"], c["phase"], sp_net, sn_net)
    bar = copper.barrels(nets, win)
    req_x = sorted({b["x"] for b in bar} | {p[0] for p in pts})
    req_y = sorted({b["y"] for b in bar} | {p[1] for p in pts})

    m = mesher.Mesh(f"servodrive cell {cell} sense chain", win, target=target,
                    required_x=req_x, required_y=req_y, nhinc=nhinc,
                    min_gap=0.2)
    m.add_conductor("swB", c["sw"], "B.Cu")
    m.add_conductor("phB", c["phase"], "B.Cu")
    m.add_conductor("g4", "GND", "In4.Cu")
    m.add_conductor("gB", "GND", "B.Cu")
    layers = ("F.Cu", "In3.Cu", "B.Cu")
    m.add_tracks("spT", sp_net, layers)
    m.add_tracks("snT", sn_net, layers)
    tags = {c["sw"]: {"B.Cu": "swB"}, c["phase"]: {"B.Cu": "phB"},
            "GND": {"In4.Cu": "g4", "B.Cu": "gB"},
            sp_net: {l: "spT" for l in layers},
            sn_net: {l: "snT" for l in layers}}
    for b in bar:
        m.add_barrel(b, tags.get(b["net"], {}))

    sh1, sh2 = c["shunts"]
    t1, t2 = c["taps"]
    amp = c["amp"]

    sw_a = m.pad_equipotential("swB", sh1, 1) + m.pad_equipotential("swB", sh2, 1)
    ph_a = m.pad_equipotential("phB", sh1, 2) + m.pad_equipotential("phB", sh2, 2)
    if len(set(sw_a)) > 1:
        m.model.equiv(*sorted(set(sw_a)))
    if len(set(ph_a)) > 1:
        m.model.equiv(*sorted(set(ph_a)))

    # the tap resistors, shorted: their 10 Ohm is a lumped element in L3
    ta_sw = m.pad_equipotential("swB", t1, 2)
    ta_sp = m.pad_node("spT", t1, 1)
    tb_ph = m.pad_equipotential("phB", t2, 1)
    tb_sn = m.pad_node("snT", t2, 2)
    if ta_sw and ta_sp:
        m.model.equiv(ta_sw[0], ta_sp)
    if tb_ph and tb_sn:
        m.model.equiv(tb_ph[0], tb_sn)

    # The tap port is taken at the two tap resistors' pour-side pads, not at
    # the amplifier's pins: those two points are where the copper is probed,
    # and Z21 between them and the current port is exactly the sense voltage
    # per amp.  The tracks onward to the amplifier carry no current (the
    # amplifier's inputs are high impedance) so they add no L.di/dt of their
    # own; what they do add is capacitance, and that is the FastCap question
    # in tap_asymmetry().
    amp_p = ta_sw[0] if ta_sw else None
    amp_n = tb_ph[0] if tb_ph else None

    # the shunts themselves are shorted: their 0.8 mOhm is a lumped element in
    # L3, and what is wanted here is the copper the tap network sees across it
    if sw_a and ph_a:
        m.model.equiv(sw_a[0], ph_a[0])

    ports = []
    src = m.pad_equipotential("swB", c["lo"], 5)
    sink = []
    for p in copper.pads_of(c["lead"]):
        if "B.Cu" in p["shapes"]:
            sink += m.pad_equipotential("phB", p["ref"], p["pad"],
                                        layer="B.Cu")
    if src and sink:
        if len(set(sink)) > 1:
            m.model.equiv(*sorted(set(sink)))
        m.model.port("Pshunt", src[0], sink[0])
        ports.append("Pshunt")
    if amp_p and amp_n:
        m.model.port("Ptap", amp_p, amp_n)
        ports.append("Ptap")
    m.pruned = m.model.prune_to_ports()
    return m, win, ports
