#!/usr/bin/env python3
"""schlayout_s.py -- board S's sheets, laid out by hand.

One function per sheet. Each places the sheet's parts where a person would
put them -- signal left to right, supplies up, ground down, a part's
support parts beside the pin they serve -- and wires what belongs together;
schdraw.Sheet.finish() puts ground and supply symbols, labels and no-connect
flags on whatever is left. Positions are grid units of 2.54 mm.

    python3 tools/schlayout_s.py            # check every sheet against the netlist
"""
import sys

import schdraw as D
import schematic as SCH
import schematic_s as S


# ------------------------------------------------------------ power stage ---
def phase(s, X, ox, oy, ntc=False):
    """One half-bridge cell: EG2103, its FET pair, shunt and amplifier."""
    n = "ABC".index(X) + 1
    P = lambda x, y: (ox + x, oy + y)
    amp = f"U{3 + n}"

    s.block(f"Phase {X}", *P(-18, -14), *P(58, 15))

    # the driver, its supply decoupling and bootstrap
    u = s.place(f"U{n}", *P(0, 0), fields=(-1, 5))
    s.place(f"C{n}06", *P(-3, -5.5), fields="left")
    s.place(f"D{n}01", *P(2.5, -7), rot=180, fields="above")
    s.place(f"C{n}05", *P(6.5, -7), rot=90, fields="above")
    s.wire(u["1"], P(0, -7))
    s.wire(P(-3, -7), P(1, -7))
    s.power(s.g(*P(-3, -7)), "+12V")
    s.wire(u["8"], P(4, -3), P(4, -7), P(5, -7))
    s.label(s[f"C{n}05"]["2"], stub=1)

    # inputs: HIN and LIN from the CPU, each held low by a pull-down
    s.wire(u["2"], P(-12, 0))
    s.label(s.g(*P(-12, 0)), f"HIN_{X}", stub=0, d=(-1, 0), shape="input")
    s.place(f"R{n}11", *P(-10, 1.5))
    s.wire(u["3"], P(-5, 1), P(-5, 6), P(-12, 6))
    s.label(s.g(*P(-12, 6)), f"LIN_{X}", stub=0, d=(-1, 0), shape="input")
    s.place(f"R{n}12", *P(-8, 7.5))

    # gates: series resistor, then the pull-down to the FET's own source
    s.place(f"R{n}01", *P(9, -2), rot=90, fields="above")
    s.place(f"R{n}02", *P(9, 7), rot=90, fields="below")
    hi = s.place(f"Q{2 * n - 1}", *P(17, -2))
    lo = s.place(f"Q{2 * n}", *P(17, 7))
    s.wire(u["7"], s[f"R{n}01"]["1"])
    s.wire(s[f"R{n}01"]["2"], hi["4"])
    s.place(f"R{n}09", *P(13, -0.5))
    s.wire(s[f"R{n}09"]["2"], P(13, 2))
    s.wire(u["5"], P(6, 3), P(6, 7), s[f"R{n}02"]["1"])
    s.wire(s[f"R{n}02"]["2"], lo["4"])
    s.place(f"R{n}10", *P(13, 8.5))

    # the switch node: driver's VS, both FETs, the snubber, the shunts
    s.wire(hi["1"], lo["5"])
    s.wire(u["6"], P(18, 2))
    s.wire(P(18, 2), P(37, 2))
    s.label(s.g(*P(15, 2)), f"SW_{X}", stub=0, d=(1, 0))
    s.place(f"R{n}13", *P(28, 3.5), rot=180)
    s.place(f"C{n}09", *P(28, 7.5), rot=180)
    s.wire(s[f"R{n}13"]["1"], s[f"C{n}09"]["2"])

    # DC link: VBUS rail over the high side, four ceramics to ground
    s.wire(hi["5"], P(18, -10), P(42, -10))
    s.power(s.g(*P(18, -10)), "VBUS")
    for k, x in zip((1, 2, 3, 4), (21, 28, 35, 42)):
        s.place(f"C{n}0{k}", *P(x, -8.5))

    # shunt pair, SW at the top and PHASE below; Kelvin taps to the amplifier
    s.place(f"R{n}05", *P(32, 3.5))
    s.place(f"R{n}06", *P(36, 3.5))
    s.place(f"R{n}07", *P(38.5, 2), rot=270, fields="above")
    s.place(f"R{n}08", *P(38.5, 5), rot=90, fields="below")
    s.wire(s[f"R{n}05"]["2"], s[f"R{n}08"]["1"])
    s.place(f"C{n}08", *P(41, 3.5))
    a = s.place(amp, *P(48, 3))
    s.wire(s[f"R{n}07"]["1"], a["8"])
    s.wire(s[f"R{n}08"]["2"], P(44, 5), P(44, 4), a["1"])
    # REF2 and GND to ground, REF1 to the supply: the output sits at mid-rail
    s.wire(a["2"], P(47, 7), P(48, 7), a["3"])
    s.power(s.g(*P(47.5, 7)), "GND")
    s.wire(a["7"], P(49, 8), P(51, 8))
    s.power(s.g(*P(51, 8)), "+3V3")
    if a.comp.nets.get("5"):
        s.label(a["5"], stub=1, shape="output")

    # the phase output: TVS to ground, out to the motor lead
    s.wire(s[f"R{n}06"]["2"], P(36, 10), P(54, 10))
    s.label(s.g(*P(54, 10)), f"PHASE_{X}", stub=0, d=(1, 0))
    s.place(f"D{n}02", *P(41, 11.5), rot=270, fields="right")

    if ntc:
        s.place("R901", *P(51, -8.5))
        s.label(s["R901"]["2"], stub=1, d=(0, 1), text_dir=(1, 0))


def gpio(comps, net):
    """The RP2350's GPIO number for a net, read off the netlist."""
    import re
    u7 = next(c for c in comps if c.ref == "U7")
    name = next(SCH.pins(u7.lib_id)[n][3] for n, v in u7.nets.items() if v == net)
    return int(re.match(r"GPIO(\d+)", name).group(1))


def notes(*paras, width=74):
    """Paragraphs as one KiCad text: wrapped, a blank line between."""
    import textwrap
    return "\\n\\n".join("\\n".join(textwrap.wrap(p, width)) for p in paras)


POWER_NOTES = notes(
    "Each phase is an EG2103 half-bridge driver on a pair of BSC030N08NS5: "
    "bootstrap diode and capacitor (D101, C105 on phase A), 2R2 gate resistors, "
    "10k gate-source pull-downs.",
    "HIN and LIN are held low (R111, R112): with both low the EG2103 turns the "
    "low side on, so a CPU in reset brakes the motor.",
    "Inline shunt: two 2m0 in parallel between the switch node and the motor "
    "lead, Kelvin-sensed through 10R, 10R and 1n into an INA241A3. REF1 on +3V3 "
    "and REF2 on GND put its output at mid-rail, so it reads current either way.",
    "Phase C: the shunts are 0R links and U6, R307, R308, C308 are not fitted. "
    "The RP2350A has no fourth ADC channel; phase C current is -(Ia + Ib).",
    "R113 + C109 (and B, C): RC snubber across each low side, footprints only.",
    "R901: NTC beside the FETs, read on ADC3 as FET_TEMP.")


def power_stage(comps, glob):
    s = D.Sheet("01_power_stage", comps, glob)
    for i, X in enumerate("ABC"):
        phase(s, X, 18, 14 + 31 * i, ntc=(X == "A"))
    s.text("Notes", 80, 0, size=2.0, bold=True)
    s.text(POWER_NOTES, 80, 2)
    return s


# ---------------------------------------------------------------- control ---
CONTROL_NOTES = notes(
    "RP2350A as Raspberry Pi's minimal design (RP-006440): the core supply is "
    "the chip's own switching regulator, VREG_LX through L701 to +1V1, which "
    "VREG_FB senses and DVDD takes. VREG_AVDD is +3V3 through R706 and C718.",
    "One 100n at each IOVDD pin (C701-C706); C714 4u7 at VREG_VIN.",
    "ADC_AVDD is +3V3 filtered by R703 and C716 (+3V3A).",
    "BOOTSEL: SW1 on sheet 05 pulls QSPI_SS low through R701 at reset.",
    "{enc}",
    "GPIO19-24 go to the expansion header (sheet 05).",
    width=62)


def encoder_note(comps):
    do, sck, cs, out = (gpio(comps, n) for n in ("ENC_DO", "ENC_SCK", "ENC_CS", "ENC_OUT"))
    spi = do == 4 and sck == 6
    return (f"The encoder: ENC_DO on GPIO{do}, ENC_SCK on GPIO{sck}, ENC_CS on GPIO{cs}, "
            f"ENC_OUT on GPIO{out}" + (" -- SPI0's RX and SCK, so SPI0 reads it." if spi
                                      else ". SPI0 would want the data on GPIO4 and the "
                                           "clock on GPIO6."))


def control(comps, glob):
    s = D.Sheet("02_control", comps, glob)
    s.block("RP2350A", 32, 20, 104, 73)
    u = s.place("U7", 75, 50, fields=(-12, -20.5))

    # supplies along the top: DVDD on +1V1, the IO supplies on +3V3, the
    # ADC's own filtered +3V3A
    for n in ("39", "23", "6"):
        s.wire(u[n], (u[n].x / D.U, 28))
    s.wire((66, 28), (68, 28))
    s.power(s.g(67, 28), "+1V1")
    for n in ("54", "45", "38", "30", "20", "11", "1", "53"):
        s.wire(u[n], (u[n].x / D.U, 28))
    s.wire((71, 28), (80, 28))
    s.power(s.g(75.5, 28), "+3V3")
    s.wire(u["44"], (81.5, 24), (87, 24))
    s.place("C716", 84, 25.5, fields="right")
    s.place("R703", 88.5, 24, rot=270, fields="above")
    s.power(s["R703"]["1"], "+3V3", stub=1)
    s.flag(s.g(82.5, 24), "+3V3A", stub=1)

    # the core regulator: AVDD through its RC, VIN on +3V3, LX through
    # L701 to +1V1, which FB senses
    s.wire(u["46"], (58, 34.5), (58, 26), (48, 26))
    s.place("R706", 46.5, 26, rot=90, fields="above")
    s.power(s["R706"]["1"], "+3V3", stub=1)
    s.place("C718", 52, 27.5, fields="right")
    s.flag(s.g(55, 26), "VREG_AVDD", stub=1)
    s.wire(u["49"], (56, 35.5), (56, 31))
    s.power(s.g(56, 31), "+3V3")
    s.place("L701", 50, 37.5, rot=90, fields="below")
    s.wire(u["48"], s["L701"]["2"])
    s.wire(u["50"], (44, 36.5), (44, 37.5))
    s.wire(s["L701"]["1"], (32, 37.5))
    for ref, x in (("C715", 42), ("C719", 37.5), ("C707", 33)):
        s.place(ref, x, 39, fields="right")
    s.power(s.g(32, 37.5), "+1V1")
    s.flag(s.g(40, 37.5), "+1V1", stub=1)
    s.power(u["47"], "GND", stub=1)

    # QSPI: labels here and at the flash
    for n in ("60", "57", "59", "58", "55", "56"):
        s.label(u[n], stub=2)

    # crystal: XIN straight in, XOUT through R702
    s.wire(u["21"], (50, 51), (50, 55))
    y = s.place("Y1", 53, 55, fields=(-1.6, -2.2, "left"))
    s.wire((50, 55), y["1"])
    s.place("C709", 50, 56.5, fields="left")
    s.place("R702", 59, 53, rot=270, fields="below")
    s.wire(u["22"], s["R702"]["1"])
    s.wire(s["R702"]["2"], (56, 53), (56, 55), y["3"])
    s.place("C710", 56, 56.5, fields="right")

    # reset: RC on RUN, which also leaves for the header and a test pad
    s.wire(u["26"], (61, 58), (61, 60), (37, 60))
    s.label(s.g(37, 60), "RUN", stub=0, d=(-1, 0))
    s.place("R704", 43, 58.5, rot=180, fields="right")
    s.place("C717", 40, 61.5, fields="left")
    for n in ("24", "25"):
        s.label(u[n], stub=2)

    # USB: 27R in series with each data line, at the chip
    s.wire(u["52"], (89, 33), (89, 29))
    s.wire(u["51"], (90, 34), (90, 32))
    s.place("R707", 93, 29, rot=270, fields="above")
    s.place("R708", 93, 32, rot=270, fields="below")
    s.wire((89, 29), s["R707"]["2"])
    s.wire((90, 32), s["R708"]["2"])
    s.wire(s["R707"]["1"], (98, 29))
    s.label(s.g(98, 29), "USB_DP", stub=0, d=(1, 0))
    s.wire(s["R708"]["1"], (98, 32))
    s.label(s.g(98, 32), "USB_DM", stub=0, d=(1, 0))
    for p in u.pins.values():
        if p.d == (1, 0) and p.net and p.num not in ("52", "51"):
            s.label(p, stub=2)

    # the flash, labelled to match
    s.block("QSPI flash", 3, 32, 28, 53)
    f = s.place("U8", 19, 44, fields=(4.5, 3, "left"))
    for n in ("6", "5", "2", "3", "7"):
        s.label(f[n], stub=2)
    s.wire(f["1"], (13, 41))
    s.label(s.g(13, 41), "QSPI_SS", stub=0, d=(-1, 0))
    s.place("R701", 14, 39.5, rot=180, fields="right")
    s.label(s["R701"]["2"], stub=1, d=(0, -1), text_dir=(-1, 0))
    s.wire(f["8"], (19, 37), (23, 37))
    s.power(s.g(19, 37), "+3V3")
    s.place("C711", 23, 38.5, fields="right")

    # the 3V3 LDO, from +5V
    s.block("3V3 LDO", 3, 57, 28, 70)
    r = s.place("U9", 16, 63)
    s.wire(r["1"], (9, 62))
    s.wire(r["3"], (11, 63), (11, 62))
    s.power(s.g(9, 62), "+5V")
    s.place("C713", 7, 63.5, fields="left")
    s.wire((7, 62), (9, 62))
    s.wire(r["5"], (24, 62))
    s.place("C712", 22, 63.5, fields="right")
    s.power(s.g(24, 62), "+3V3")

    # IOVDD decoupling, one 100n at each supply pin, and the regulator's
    # input capacitor
    s.block("Decoupling", 60, 9, 101, 18)
    s.wire((63, 12), (95, 12))
    s.power(s.g(63, 12), "+3V3")
    for ref, x in zip(("C701", "C702", "C703", "C704", "C705", "C706", "C714"),
                      (65, 70, 75, 80, 85, 90, 95)):
        s.place(ref, x, 13.5, fields="right")

    s.text("Notes", 108, 36, size=2.0, bold=True)
    s.text(CONTROL_NOTES.replace("{enc}", encoder_note(comps)), 108, 38)
    return s


# ---------------------------------------------------------------- encoder ---
def encoder(comps, glob):
    s = D.Sheet("03_encoder", comps, glob)
    s.block("Encoder", 3, 5, 40, 24)
    u = s.place("U10", 20, 15, fields=(-1, -4.5, "left"))
    s.wire(u["1"], (11, 13))
    s.wire(u["2"], (14, 14), (14, 13))
    s.place("C901", 11, 14.5, fields="left")
    s.power(s.g(11, 13), "+3V3")
    for n in ("3", "6", "7", "8"):
        s.label(u[n], stub=2, shape="output" if n in ("3", "6") else "input")
    s.text(notes(
        "MT6701 on the motor-facing side, centred on the shaft over a diametric "
        "magnet. MODE tied to VDD. The CPU reads it over SSI (sheet 02).",
        width=60), 3, 27)
    return s


# ------------------------------------------------------------------ power ---
def buck(s, ox, oy, u_ref, cin, cboot, ind, cout, rfb, rrt, en=None):
    """An LMR38010 as its datasheet draws it: VIN and its capacitors on the
    left, the bootstrap cap and inductor on the right, the feedback divider
    under the output rail. Returns the output rail's right-hand end."""
    P = lambda x, y: (ox + x, oy + y)
    u = s.place(u_ref, *P(0, 0), fields=(-4, -5, "left"))
    # input: VIN up to the bus rail, ceramics hanging off it
    left = -30 if en else -18
    s.wire(u["3"], P(-6, -2), P(-6, -8), P(left, -8))
    s.power(s.g(*P(left, -8)), "VBUS")
    for ref, x in zip(cin, (left + 2, left + 9)):
        s.place(ref, *P(x, -6.5), fields="right")
    if en is None:                       # EN tied to VIN: on with the bus
        s.wire(u["2"], P(-5, -1), P(-5, -2))
    else:                                # EN divider, and the FET that stops it
        r_top, r_bot, q, r_g = en
        s.place(r_top, *P(-11, -6.5), fields="right")
        s.wire(s[r_top]["2"], P(-11, -1))
        s.wire(u["2"], P(-17, -1))
        s.place(r_bot, *P(-11, 0.5), fields="right")
        fet = s.place(q, *P(-18, 1), fields=(-2.5, 3.2, "right"))
        s.place(r_g, *P(-24, 2.5), fields="left")
        s.wire(fet["1"], P(-28, 1))
        s.label(s.g(*P(-28, 1)), "GATE_OFF", stub=0, d=(-1, 0), shape="input")
    # RT to ground
    s.wire(u["4"], P(-6, 1), P(-6, 2))
    s.place(rrt, *P(-6, 3.5), fields="left")
    # GND and the exposed pad together
    s.wire(u["1"], P(-0.5, 5), P(0.5, 5), u["9"])
    s.power(s.g(*P(0, 5)), "GND")
    # bootstrap across BOOT and SW, then the inductor to the output rail
    s.wire(u["7"], P(5, -2), P(5, -5), P(8, -5))
    s.place(cboot, *P(8, -3.5), fields="right")
    s.wire(s[cboot]["2"], P(8, -1))
    s.place(ind, *P(14.5, -1), rot=90, fields="above")
    s.wire(u["8"], s[ind]["1"])
    # feedback divider from the output rail
    s.place(rfb[0], *P(19, 0.5), fields="right")
    s.place(rfb[1], *P(19, 4.5), fields="right")
    s.wire(s[rfb[0]]["2"], s[rfb[1]]["1"])
    s.wire(u["5"], P(6, 1), P(6, 2.5), P(19, 2.5))
    end = 24
    for ref in cout:
        s.place(ref, *P(end, 0.5), fields="right")
        end += 5.5
    s.wire(s[ind]["2"], P(end - 2, -1))
    return (ox + end - 2, oy - 1)


POWER_SUPPLY_NOTES = notes(
    "Bus: 48 V operating on 80 V FETs. D1001 (SMDJ54A, 54 V standoff) clamps "
    "it; C1001 and C1002 are the bulk. VBUS_SENSE = VBUS x 4k7 / 116k7 "
    "(0.1 %), on ADC2.",
    "12 V gate rail: LMR38010, starting at 9.9 V on the R1003/R1004 divider. "
    "GATE_OFF (GPIO14) high turns Q1001 on and stops the rail, taking the "
    "gate drivers' supply away. R1005 holds it low through reset, so the rail "
    "comes up and the drivers brake the motor.",
    "5 V logic rail: LMR38010, diode-ORed with USB (D1002, sheet 05) through "
    "D1003. The 3V3 LDO on sheet 02 runs from +5V.",
    width=60)


def power(comps, glob):
    s = D.Sheet("04_power", comps, glob)
    # bus input: XT30, TVS, bulk, the bus divider
    s.block("Bus input", 3, 4, 62, 27)
    j = s.place("J4", 8, 12, rot=180, fields=(-1, -3, "left"))
    s.wire(j["2"], (54, 11))
    s.power(s.g(54, 11), "VBUS")
    s.flag(s.g(13, 11), "VBUS", stub=1)
    s.wire(j["1"], (12, 12), (12, 17))
    s.power(s.g(12, 17), "GND")
    s.wire((12, 15), (14, 15))
    s.flag(s.g(14, 15), "GND", stub=0)
    s.place("D1001", 18, 12.5, rot=270, fields="right")
    for ref, x in (("C1001", 24), ("C1002", 34)):
        s.place(ref, x, 12.5, fields="right")
    s.place("R801", 45, 12.5, fields="right")
    s.place("R806", 45, 16.5, fields="right")
    s.wire(s["R801"]["2"], s["R806"]["1"])
    s.place("R802", 45, 21.5, fields="right")
    s.wire(s["R806"]["2"], s["R802"]["1"])
    s.place("C801", 51, 21.5, fields="right")
    s.wire((45, 19), (51, 19), (51, 20))
    s.wire((51, 19), (56, 19))
    s.label(s.g(56, 19), "VBUS_SENSE", stub=0, d=(1, 0), shape="output")

    s.block("12 V gate rail", 3, 31, 73, 53)
    x, y = buck(s, 36, 43, "U11", ("C1003", "C1004"), "C1005", "L1001", ("C603", "C1009"),
                ("R1001", "R1002"), "R1008", en=("R1003", "R1004", "Q1001", "R1005"))
    s.wire((x, y), (x + 3, y))
    s.power(s.g(x + 3, y), "+12V")
    s.flag(s.g(x + 1.5, y), "+12V", stub=1)

    s.block("5 V logic rail", 77, 31, 145, 53)
    x, y = buck(s, 98, 43, "U12", ("C1006", "C1007"), "C1008", "L1002",
                ("C604", "C1010", "C1011"), ("R1006", "R1007"), "R1009")
    d = s.place("D1003", x + 2.5, y, rot=180, fields="below")
    s.wire((x, y), d["2"])
    s.wire(d["1"], (x + 7, y))
    s.power(s.g(x + 7, y), "+5V")
    s.flag(s.g(x + 5.5, y), "+5V", stub=1)

    # mounting: the heatsink lands and bosses are ground; the motor leads
    s.block("Mounting and motor leads", 3, 57, 95, 70)
    for ref, y in (("J1", 61), ("J2", 63.5), ("J3", 66)):
        s.place(ref, 6, y, fields=(-1.5, 0, "right"))
    for i, ref in enumerate(("TL1", "TL2", "TL3", "H10", "H11", "H12", "H13", "H14", "H15")):
        s.place(ref, 22 + 8 * i, 62, fields=(1.2, 0, "left"))

    s.text("Notes", 70, 4, size=2.0, bold=True)
    s.text(POWER_SUPPLY_NOTES, 70, 6)
    return s


# --------------------------------------------------------------------- io ---
def port(s, ox, oy, upper, lower, conn):
    """One RS-485 port: two transceivers, each on one pair of the connector.
    upper/lower: (transceiver, its capacitor, ESD, termination or None)."""
    P = lambda x, y: (ox + x, oy + y)
    J = s.place(conn, *P(24, 10), fields=(-1, -4.5, "left"))
    for (ref, cap, esd, term), y, (pa, pb), (xa, xb) in (
            (upper, 0, ("2", "3"), (16, 14)), (lower, 20, ("4", "5"), (14, 16))):
        t = s.place(ref, *P(0, y), fields=(-4, -7.5, "right"))
        rx = bool(t.comp.nets.get("1"))
        # the CPU side
        if rx:
            s.label(t["1"], stub=2, shape="output")
            s.wire(t["2"], P(-5, y - 1))
            s.wire(t["3"], P(-5, y))
            s.wire(t["4"], P(-5, y + 2), P(-5, y - 1))
            s.wire(P(-5, y + 2), P(-5, y + 3))
            s.power(s.g(*P(-5, y + 3)), "GND")
        else:
            s.wire(t["2"], P(-5, y - 1))
            s.wire(t["3"], P(-5, y), P(-5, y - 3))
            s.power(s.g(*P(-5, y - 3)), "+3V3")
            s.label(t["4"], stub=2, shape="input")
        # supply and its capacitor
        s.wire(t["8"], P(0, y - 9), P(3, y - 9))
        s.power(s.g(*P(0, y - 9)), "+3V3")
        s.place(cap, *P(3, y - 7.5), fields="right")
        # the pair: B spread down so the ESD and termination sit between
        s.wire(t["6"], P(xa, y - 3))
        s.wire(t["7"], P(5, y - 1), P(5, y + 4), P(xb, y + 4))
        s.place(esd, *P(10, y + 0.5), rot=270, fields=(1.2, 0, "left"))
        if term:
            s.place(term, *P(12, y - 1.5), fields=(0.8, -0.8, "left"))
            s.wire(s[term]["2"], P(12, y + 4))
        s.join(P(xa, y - 3), J[pa], first="v")
        s.join(P(xb, y + 4), J[pb], first="v")
    s.power(J["1"], "GND", stub=1, d=(-1, 0))
    s.power(J["6"], "GND", stub=1, d=(-1, 0))
    return J


def io(comps, glob):
    s = D.Sheet("05_io", comps, glob)

    # USB-C: data through the ESD array, CC pull-downs, VBUS into +5V
    s.block("USB-C: data and 5 V logic, no PD", 1, 5, 60, 34)
    j = s.place("J12", 10, 20, fields=(-3, -12, "left"))
    s.wire(j["A7"], (17, 19)); s.wire(j["B7"], (17, 20)); s.wire((17, 19), (17, 20))
    s.wire(j["A6"], (18, 21)); s.wire(j["B6"], (18, 22)); s.wire((18, 21), (18, 22))
    e = s.place("U13", 26, 21, fields=(-3, 5, "left"))
    s.wire((18, 21), e["1"])
    s.wire((17, 19), (20, 19), (20, 22), e["3"])
    s.label(s.g(18, 22), "USB_DP", stub=1, d=(0, 1), text_dir=(1, 0))
    s.label(s.g(17, 19), "USB_DM", stub=1, d=(0, -1), text_dir=(1, 0))
    s.wire(e["6"], (33, 21))
    s.label(s.g(33, 21), "USB_DP", stub=0, d=(1, 0))
    s.wire(e["4"], (33, 22))
    s.label(s.g(33, 22), "USB_DM", stub=0, d=(1, 0))
    s.label(e["5"], "USB_VBUS", stub=1, text_dir=(1, 0))
    s.wire(j["A4"], (52, 14))
    s.label(s.g(22, 14), "USB_VBUS", stub=0, d=(1, 0))
    s.wire(j["A5"], (45, 16))
    s.wire(j["B5"], (42, 17))
    s.place("R1101", 45, 17.5, fields="right")
    s.place("R1102", 42, 18.5, fields="left")
    s.place("R1103", 49, 15.5, fields="right")
    s.place("R1104", 49, 19.5, fields="right")
    s.wire(s["R1103"]["2"], s["R1104"]["1"])
    s.wire((49, 17.5), (53, 17.5))
    s.label(s.g(53, 17.5), "USB_VBUS_DET", stub=0, d=(1, 0), shape="output")
    s.place("D1002", 55.5, 14, rot=180, fields="above")
    s.wire((52, 14), s["D1002"]["2"])
    s.power(s["D1002"]["1"], "+5V", stub=1)

    # the RS-485 relay: port IN and port OUT
    s.block("RS-485 relay: port IN", 1, 39, 48, 80)
    port(s, 16, 50, ("U14", "C1102", "D1101", "R1105"), ("U15", "C1103", "D1102", None), "J14")
    s.block("RS-485 relay: port OUT", 52, 39, 99, 80)
    port(s, 67, 50, ("U17", "C1104", "D1104", None), ("U18", "C1105", "D1105", "R1106"), "J15")

    # expansion header
    s.block("Expansion header: PD or Ethernet board", 64, 5, 100, 34)
    h = s.place("J13", 80, 20, fields=(-1, -7, "left"))
    # VBUS down the odd row and ground down the even row, each gathered on
    # one wire; a supply pin gets its symbol on a stub long enough to clear
    # its neighbour's; everything else is a label
    stubs = {(-1, 0): iter((3, 5)), (1, 0): iter((3, 5))}
    vb, gnd = [], []
    for n in sorted(h.pins, key=int):
        p = h[n]
        if p.net == "VBUS":
            s.wire(p, p.out(1)); vb.append(p.out(1))
        elif p.net == "GND":
            s.wire(p, p.out(1)); gnd.append(p.out(1))
        elif p.net in D.SUPPLY:
            k = next(stubs[p.d])
            s.wire(p, p.out(k))
            s.power(p.out(k), p.net)
        else:
            s.label(p, stub=2)
    top = min(vb, key=lambda q: q.y)
    s.wire(top, top.go(0, -3), max(vb, key=lambda q: q.y))
    s.power(top.go(0, -3), "VBUS")
    top = min(gnd, key=lambda q: q.y)
    s.wire(max(gnd, key=lambda q: q.y), top.go(0, -2), top.go(3, -2))
    s.power(top.go(3, -2), "GND")

    # LED, BOOTSEL, test pads, the ADC3 divider and FAULT_n's pull-up
    s.block("Status LED, BOOTSEL, test pads", 103, 5, 146, 34)
    led = s.place("D1103", 131, 14, fields=(-1, -4.5, "left"))
    for ref, pin, y in (("R1107", "1", 11), ("R1108", "2", 14), ("R1109", "3", 17)):
        r = s.place(ref, 121.5, y, rot=90, fields="above")
        s.join(r["2"], led[pin], first="h")
        s.label(r["1"], stub=2)
    s.power(led["4"], "+3V3", stub=1)
    sw = s.place("SW1", 115, 25, fields="above")
    s.label(sw["1"], stub=2)
    s.power(sw["2"], "GND", stub=1)
    for i, ref in enumerate(("TP4", "TP5", "TP6", "TP7")):
        t = s.place(ref, 124 + 5 * i, 24, fields=(0.5, -1.5, "left"))
        s.label(t["1"], stub=1, d=(0, 1), text_dir=(1, 0))

    s.block("ADC3 divider, FAULT_n pull-up", 103, 39, 146, 58)
    s.place("R803", 112, 46.5, fields="right")
    s.place("C802", 119, 46.5, fields="right")
    s.wire((112, 45), (119, 45))
    s.wire((112, 45), (108, 45))
    s.label(s.g(108, 45), "FET_TEMP", stub=0, d=(-1, 0))
    s.place("R705", 132, 46.5, rot=180, fields="right")
    s.label(s["R705"]["1"], stub=1, d=(0, 1), text_dir=(-1, 0))
    s.text(notes(
        "RS-485 is a CPU relay: each port is two point-to-point pairs, one in "
        "and one out, so a drive passes the chain on in software. Drivers are "
        "always enabled; each receiver has its 120R termination (R1105, R1106).",
        "R803 with the NTC (R901, sheet 01) makes FET_TEMP.",
        width=48), 104, 60)
    return s


SHEETS = {"01_power_stage": power_stage, "02_control": control,
          "03_encoder": encoder, "04_power": power, "05_io": io}


def draw(name, comps, glob):
    s = SHEETS[name](comps, glob)
    auto = s.finish()
    return s, auto


def sheet_text(name, comps, project, file_uuid, inst_uuid, title, flags=(), glob=None,
               root_uuid=None):
    """gen_boards' entry point: a drawn sheet, checked, as KiCad text."""
    glob = glob if glob is not None else S.global_nets()
    if name not in SHEETS:
        return SCH.sheet_text(name, comps, project, file_uuid, inst_uuid, title,
                              flags=flags, glob=glob)
    s, _ = draw(name, comps, glob)
    bad, _ = s.check()
    if bad:
        raise RuntimeError(f"{name}: the drawing disagrees with the netlist:\n  "
                           + "\n  ".join(bad))
    no = [n for n, _ in S.SHEETS].index(name) + 2
    return s.emit(project, file_uuid, inst_uuid, title, sheet_no=no, root_uuid=root_uuid)


if __name__ == "__main__":
    glob = S.global_nets()
    comps = S.board_s()
    fail = False
    for name in SHEETS:
        s, auto = draw(name, [c for c in comps if c.sheet == name], glob)
        bad, lint = s.check()
        print(f"{name}: {len(s.parts)} parts, {len(s.split())} wires, "
              f"{len(s.labels)} labels, {len(s.powers)} power symbols")
        for b in bad:
            print("  !", b)
        for l in lint:
            print("  ~", l)
        for p, why in auto:
            print(f"  label left by finish(): {p!r} {why}")
        fail |= bool(bad)
    sys.exit(1 if fail else 0)
