#!/usr/bin/env python3
"""schematic.py — board A's netlist, and the KiCad sheets it emits.

The circuit is DATA here, not drawing: every component is a reference, a
symbol, a value, a footprint and a pin-to-net map. The sheets are generated
from it, and the references and footprints are cross-checked against
tools/placement.py, so a part cannot exist on the board and not in the netlist
or the other way round.

    python3 tools/schematic.py          # the netlist report and the checks

What it emits is a machine-drawn schematic: parts on a grid, grouped by
function, with a stub and a label on every pin. It is not a hand-drawn
document and does not pretend to be. What it is, is a netlist that agrees with
the placement -- which is what routing needs and what capture by hand would
have spent a day producing and a week keeping in sync.

Layout convention, verified against a KiCad-authored schematic:

    schematic_point = (X + rx, Y - ry)

for a symbol at (X, Y, A) and a library point rotated counter-clockwise by A.
Library y is up, schematic y is down, and the flip means library angles are
screen angles.
"""
import os
from math import cos, sin, radians
from pathlib import Path

import sexp
import placement as PL

ROOT = Path(__file__).resolve().parent.parent
KSYM = Path("/usr/share/kicad/symbols")
PROJ_SYM = ROOT / "hardware/parts/servodrive.kicad_sym"

# ------------------------------------------------------------- libraries ----
_LIBS, _SYMS, _PINS = {}, {}, {}

def _lib(nick):
    if nick not in _LIBS:
        path = PROJ_SYM if nick == "servodrive" else KSYM / f"{nick}.kicad_sym"
        _LIBS[nick] = sexp.parse(path.read_text())
    return _LIBS[nick]

def symbol(lib_id):
    """The symbol definition, with any `extends` resolved into it.

    A schematic carries its own copy of every symbol it uses, and a derived
    symbol whose base is not also present will not load. Flattening is simpler
    than shipping both and hoping KiCad resolves the bare base name.
    """
    if lib_id in _SYMS:
        return _SYMS[lib_id]
    nick, name = lib_id.split(":", 1)
    root = _lib(nick)
    src = next((s for s in sexp.findall(root, "symbol")
                if sexp.unq(s[1]) == name), None)
    if src is None:
        raise KeyError(f"no symbol {lib_id}")
    ext = sexp.find(src, "extends")
    if ext:
        base = next(s for s in sexp.findall(root, "symbol")
                    if sexp.unq(s[1]) == sexp.unq(ext[1]))
        out = ["symbol", sexp.q(lib_id)]
        for c in base[2:]:                      # graphics, pins, base flags
            if isinstance(c, list) and c[0] in ("property", "extends"):
                continue
            out.append(_rename_units(c, sexp.unq(base[1]), name))
        for c in src[2:]:                       # the derived part's own fields
            if isinstance(c, list) and c[0] == "property":
                out.append(c)
        for c in base[2:]:                      # anything the derived one lacks
            if isinstance(c, list) and c[0] == "property":
                nm = sexp.unq(c[1])
                if not any(sexp.unq(d[1]) == nm
                           for d in sexp.findall(out, "property")):
                    out.append(c)
    else:
        out = ["symbol", sexp.q(lib_id)] + [
            _rename_units(c, name, name) for c in src[2:]]
    _SYMS[lib_id] = out
    return out

def _rename_units(node, old, new):
    """Sub-symbols are named "<parent>_<unit>_<style>", and the parent there is
    the UNQUALIFIED name even when the symbol itself is stored as "Lib:Name" --
    a schematic's lib_symbols holds "Device:R" whose units are "R_0_1". Get it
    wrong and the sheet fails to load, which in a hierarchy is silent: the
    parent still opens, the netlist just comes out empty."""
    if isinstance(node, list) and node and node[0] == "symbol":
        nm = sexp.unq(node[1])
        if nm.startswith(old + "_"):
            node = ["symbol", sexp.q(new + nm[len(old):])] + node[2:]
    return node

def pins(lib_id):
    """{number: (x, y, rot, name, etype)} in library coordinates."""
    if lib_id in _PINS:
        return _PINS[lib_id]
    out = {}
    for p in sexp.walk(symbol(lib_id), "pin"):
        at = sexp.find(p, "at")
        nb, nm = sexp.find(p, "number"), sexp.find(p, "name")
        if not (at and nb):
            continue
        out[sexp.unq(nb[1])] = (float(at[1]), float(at[2]), float(at[3]),
                                sexp.unq(nm[1]) if nm else "", p[1])
    _PINS[lib_id] = out
    return out

def extent(lib_id):
    """(w, h) of the symbol body plus its pins, in library units."""
    xs, ys = [], []
    for head in ("rectangle", "polyline", "circle", "arc", "text"):
        for g in sexp.walk(symbol(lib_id), head):
            for k in ("start", "end", "mid", "center", "xy", "at"):
                for c in sexp.walk(g, k):
                    try:
                        xs.append(float(c[1])); ys.append(float(c[2]))
                    except (ValueError, IndexError):
                        pass
    for x, y, rot, _, _ in pins(lib_id).values():
        xs.append(x); ys.append(y)
    if not xs:
        return (5.08, 5.08)
    return (max(xs) - min(xs), max(ys) - min(ys))

# ---------------------------------------------------------------- netlist ---
class Comp:
    __slots__ = ("ref", "lib_id", "value", "fp", "nets", "sheet", "group", "dnp")

    def __init__(self, ref, lib_id, value, fp, nets, sheet, group, dnp=False):
        self.ref, self.lib_id, self.value, self.fp = ref, lib_id, value, fp
        self.nets, self.sheet, self.group, self.dnp = nets, sheet, group, dnp

R  = "Device:R"
C  = "Device:C"
L  = "Device:L"
D  = "Device:D"
DZ = "Device:D_Zener"
NTC = "Device:Thermistor_NTC"

FP_R = {"0402": "Resistor_SMD:R_0402_1005Metric",
        "0603": "Resistor_SMD:R_0603_1608Metric",
        "2010": "Resistor_SMD:R_2010_5025Metric"}
FP_C = {"0402": "Capacitor_SMD:C_0402_1005Metric",
        "0402s": "servodrive:C_0402_1005Metric_small_pads",   # the reference's, see placement
        "0603": "Capacitor_SMD:C_0603_1608Metric",
        "0805": "Capacitor_SMD:C_0805_2012Metric",
        "1206": "Capacitor_SMD:C_1206_3216Metric",
        "1210": "Capacitor_SMD:C_1210_3225Metric"}

# Nets that appear on more than one sheet get a global label; the rest stay
# local to the sheet that owns them.
GLOBAL = {
    "VBUS", "GND", "+12V", "+5V", "+3V3", "+3V3A", "+1V1",
    "PHASE_A", "PHASE_B", "PHASE_C",
    "HIN_A", "LIN_A", "HIN_B", "LIN_B", "HIN_C", "LIN_C",
    "ISENSE_A", "ISENSE_B", "VBUS_SENSE", "FET_TEMP",
    "TP23", "TP24", "TP25",
    "ENC_SCK", "ENC_CS", "ENC_DO", "ENC_OUT",
    "RS485_DI", "RS485_RO", "RS485_DE", "RS485_TERM_EN",
    "PD_SDA", "PD_SCL", "PD_INT_n", "GATE_EN", "FAULT_n",
    "LED_R", "LED_G", "LED_B", "USB_DP", "USB_DM", "USB_VBUS_DET",
    "SWCLK", "SWDIO", "RUN", "BOOTSEL",
}
PWR = {"GND", "+12V", "+5V", "+3V3", "VBUS", "+1V1", "+3V3A"}

def phase_cell(i):
    """One of the three identical half-bridge cells."""
    n, X = i + 1, "ABC"[i]
    sh, out = "01_power_stage", []
    def add(ref, lib_id, value, fp, nets, group, dnp=False):
        out.append(Comp(ref, lib_id, value, fp, nets, sh, f"phase {X}: {group}", dnp))

    gate = "gate drive"
    add(f"U{n}", "servodrive:EG2103", "EG2103",
        "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
        {"1": "+12V", "2": f"HIN_{X}", "3": f"LIN_{X}", "4": "GND",
         "5": f"LO_{X}", "6": f"SW_{X}", "7": f"HO_{X}", "8": f"VB_{X}"}, gate)
    # Device:D numbers its pins 1 = cathode, 2 = anode, and so does the
    # footprint. A bootstrap diode charges the cap FROM the rail: anode on
    # +12V, cathode on VB. The first netlist had it the other way round.
    add(f"D{n}01", D, "100V fast", "Diode_SMD:D_SOD-323",
        {"1": f"VB_{X}", "2": "+12V"}, gate)
    add(f"C{n}05", C, "1u/25V", FP_C["0603"],
        {"1": f"VB_{X}", "2": f"SW_{X}"}, gate)
    add(f"C{n}06", C, "100n", FP_C["0603"], {"1": "+12V", "2": "GND"}, gate)
    add(f"R{n}01", R, "2R2", FP_R["0402"], {"1": f"HO_{X}", "2": f"GH_{X}"}, gate)
    add(f"R{n}02", R, "2R2", FP_R["0402"], {"1": f"LO_{X}", "2": f"GL_{X}"}, gate)
    add(f"R{n}09", R, "10k", FP_R["0603"], {"1": f"GH_{X}", "2": f"SW_{X}"}, gate)
    add(f"R{n}10", R, "10k", FP_R["0603"], {"1": f"GL_{X}", "2": "GND"}, gate)
    # The two the datasheets made compulsory: EG2103's internal 200 k pull-up on
    # LIN against RP2350's reset-state pull-down parks the input at ~1.2 V.
    # Both go to GND: HIN = LIN = 0 turns the low side on, so a CPU in reset
    # brakes the motor (decided 2026-09-22; LIN went to +3V3 for a coast).
    add(f"R{n}11", R, "4k7", FP_R["0402"], {"1": f"HIN_{X}", "2": "GND"}, gate)
    add(f"R{n}12", R, "4k7", FP_R["0402"], {"1": f"LIN_{X}", "2": "GND"}, gate)

    br = "half bridge"
    add(f"Q{2*n-1}", "Transistor_FET:BSC030N08NS5", "BSC030N08NS5",
        "servodrive:TDSON-8-1_ThermalVias_HS",
        {"1": f"SW_{X}", "2": f"SW_{X}", "3": f"SW_{X}",
         "4": f"GH_{X}", "5": "VBUS"}, br)
    add(f"Q{2*n}", "Transistor_FET:BSC030N08NS5", "BSC030N08NS5",
        "servodrive:TDSON-8-1_ThermalVias",
        {"1": "GND", "2": "GND", "3": "GND",
         "4": f"GL_{X}", "5": f"SW_{X}"}, br)
    for k, fp in ((1, "1206"), (2, "1206"), (3, "0603"), (4, "0603")):
        add(f"C{n}0{k}", C, "2.2u/100V" if fp == "1206" else "100n/100V",
            FP_C[fp], {"1": "VBUS", "2": "GND"}, br)
    # RC snubber across the low side, footprints only: it is fitted if the
    # EG2103's output stage proves to be the strong one (sim P2, Q3).
    add(f"R{n}13", R, "2R2", FP_R["0603"], {"1": f"SNUB_{X}", "2": f"SW_{X}"},
        "snubber", dnp=True)
    add(f"C{n}09", C, "470p/100V C0G", FP_C["0603"], {"1": "GND", "2": f"SNUB_{X}"},
        "snubber", dnp=True)

    sn = "phase output and sense"
    val = "1m6" if i < 2 else "0R"
    add(f"R{n}05", R, val, FP_R["2010"], {"1": f"SW_{X}", "2": f"PHASE_{X}"}, sn)
    add(f"R{n}06", R, val, FP_R["2010"], {"1": f"SW_{X}", "2": f"PHASE_{X}"}, sn)
    add(f"D{n}02", DZ, "TPSMF4L64A", "Diode_SMD:D_SOD-123F",
        {"1": f"PHASE_{X}", "2": "GND"}, sn)
    dnp = (i == 2)
    add(f"U{3+n}", "servodrive:INA241A3", "INA241A3",
        "Package_TO_SOT_SMD:TSOT-23-8",
        {"1": f"SNSN_{X}", "2": "GND", "3": "GND", "4": "GND",
         # Phase C's amplifier is a footprint and nothing else: there is no
         # fourth ADC channel for it, so its output has nowhere to go.
         "5": "" if dnp else f"ISENSE_{X}",
         "6": "+3V3", "7": "+3V3", "8": f"SNSP_{X}"}, sn, dnp)
    # Pin 2 is the outer pad on a radial 0603, and the outer pad is the one
    # over the switch-node pour: it takes a via, the inner one takes a track.
    add(f"R{n}07", R, "10R", FP_R["0603"],
        {"1": f"SNSP_{X}", "2": f"SW_{X}"}, sn, dnp)
    add(f"R{n}08", R, "10R", FP_R["0603"],
        {"1": f"PHASE_{X}", "2": f"SNSN_{X}"}, sn, dnp)
    add(f"C{n}08", C, "1n", FP_C["0603"],
        {"1": f"SNSP_{X}", "2": f"SNSN_{X}"}, sn, dnp)
    if i == 0:
        add("R901", NTC, "10k NTC", FP_R["0603"],
            {"1": "+3V3", "2": "FET_TEMP"}, "FET temperature")
    return out

def control():
    """02_control: the RP2350A and everything it cannot run without."""
    sh, out = "02_control", []
    def add(ref, lib_id, value, fp, nets, group, dnp=False):
        out.append(Comp(ref, lib_id, value, fp, nets, sh, group, dnp))

    gp = {  # spec sec.6
        0: "RS485_DI", 1: "RS485_RO", 2: "RS485_DE", 3: "RS485_TERM_EN",
        4: "ENC_SCK", 5: "ENC_CS", 6: "ENC_DO", 7: "ENC_OUT",
        8: "HIN_A", 9: "LIN_A", 10: "HIN_B", 11: "LIN_B",
        12: "HIN_C", 13: "LIN_C", 14: "GATE_EN", 15: "FAULT_n",
        16: "LED_R", 17: "LED_G", 18: "LED_B",
        # I2C0 is SDA on GPIO20 and SCL on GPIO21. PD had SDA on 19 and SCL
        # on 20, which is I2C1's SCL and I2C0's SDA -- no hardware I2C at all.
        # The three names were rotated on the routed copper, so J9's pins 9,
        # 10 and 12 moved with them (SIG_PINS) and nothing was re-routed.
        # 20..23 are also SPI0 RX/CSn/SCK/TX, for whatever else wants them.
        19: "PD_INT_n", 20: "PD_SDA", 21: "PD_SCL",
        22: "USB_VBUS_DET", 23: "TP23", 24: "TP24", 25: "TP25",
    }
    cpu = {}
    for pin, name in (("1", "+3V3"), ("11", "+3V3"), ("20", "+3V3"),
                      ("30", "+3V3"), ("38", "+3V3"), ("45", "+3V3"),
                      ("6", "+1V1"), ("23", "+1V1"), ("39", "+1V1"),
                      ("61", "GND"), ("21", "XIN"), ("22", "XOUT"),
                      ("24", "SWCLK"), ("25", "SWDIO"), ("26", "RUN"),
                      ("40", "ISENSE_A"), ("41", "ISENSE_B"),
                      ("42", "VBUS_SENSE"), ("43", "FET_TEMP"),
                      # 44 ADC_AVDD, 45 IOVDD, 46 VREG_AVDD, 47 VREG_PGND,
                      # 48 VREG_LX, 49 VREG_VIN, 50 VREG_FB, 53 USB_OTP_VDD.
                      # VREG_AVDD and the USB pins are treated as the
                      # reference design treats them: an RC into VREG_AVDD,
                      # 27 R in series with each USB line.
                      ("44", "+3V3A"), ("46", "VREG_AVDD"), ("47", "GND"),
                      ("48", "VREG_LX"), ("49", "+3V3"), ("50", "+1V1"),
                      ("51", "USB_DM_C"), ("52", "USB_DP_C"),
                      ("53", "+3V3"), ("54", "+3V3"),
                      ("55", "QSPI_SD3"), ("56", "QSPI_SCLK"),
                      ("57", "QSPI_SD0"), ("58", "QSPI_SD2"),
                      ("59", "QSPI_SD1"), ("60", "QSPI_SS")):
        cpu[pin] = name
    # GPIO0..29 land on pins 2..5, 7..10, 12..19, 27..29, 31..37
    order = [2, 3, 4, 5, 7, 8, 9, 10, 12, 13, 14, 15, 16, 17, 18, 19,
             27, 28, 29, 31, 32, 33, 34, 35, 36, 37]
    for g, pin in zip(range(26), order):
        cpu[str(pin)] = gp[g]
    add("U7", "servodrive:RP2350_60QFN", "RP2350A",
        "servodrive:RP2350-QFN-60-1EP_7x7_P0.4mm_EP3.4x3.4mm_ThermalVias",
        cpu, "CPU")

    # The core regulator, as Raspberry Pi's RP2350A minimal design has it:
    # 3.3 uH from VREG_LX, 4.7 u on the output beside it and again at DVDD
    # pin 23, 100 n at DVDD pin 39, 4.7 u on VREG_VIN at pin 49, and
    # VREG_AVDD fed through 33 R with its own 4.7 u. The inductor pad order
    # is the reference's: pin 1 on the output.
    add("L701", L, "3u3", "servodrive:L_pol_2016",
        {"1": "+1V1", "2": "VREG_LX"}, "core rail")
    add("C715", C, "4u7", FP_C["0402s"], {"1": "+1V1", "2": "GND"}, "core rail")
    add("C719", C, "4u7", FP_C["0402"], {"1": "+1V1", "2": "GND"}, "core rail")
    add("C707", C, "100n", FP_C["0402"], {"1": "+1V1", "2": "GND"}, "core rail")
    add("C714", C, "4u7", FP_C["0402s"], {"1": "+3V3", "2": "GND"}, "core rail")
    add("R706", R, "33R", FP_R["0402"], {"1": "+3V3", "2": "VREG_AVDD"}, "core rail")
    add("C718", C, "4u7", FP_C["0402"], {"1": "VREG_AVDD", "2": "GND"}, "core rail")
    for k in range(1, 7):
        add(f"C70{k}", C, "100n", FP_C["0402"],
            {"1": "+3V3", "2": "GND"}, "decoupling")
    add("R703", R, "10R", FP_R["0402"], {"1": "+3V3", "2": "+3V3A"}, "ADC supply")
    add("C716", C, "1u", FP_C["0402"], {"1": "+3V3A", "2": "GND"}, "ADC supply")
    add("R707", R, "27R", FP_R["0402"], {"1": "USB_DP", "2": "USB_DP_C"}, "USB")
    add("R708", R, "27R", FP_R["0402"], {"1": "USB_DM", "2": "USB_DM_C"}, "USB")

    add("Y1", "Device:Crystal_GND24", "12MHz",
        "Crystal:Crystal_SMD_2520-4Pin_2.5x2.0mm",
        {"1": "XIN", "2": "GND", "3": "XTAL2", "4": "GND"}, "clock")
    add("R702", R, "1k", FP_R["0402"], {"1": "XOUT", "2": "XTAL2"}, "clock")
    add("C709", C, "27p", FP_C["0402"], {"1": "XIN", "2": "GND"}, "clock")
    add("C710", C, "27p", FP_C["0402"], {"1": "XTAL2", "2": "GND"}, "clock")

    add("U8", "servodrive:W25Q128JV", "W25Q128JVUXIQ",
        "Package_SON:Winbond_USON-8-1EP_3x2mm_P0.5mm_EP0.2x1.6mm",
        {"1": "QSPI_SS", "2": "QSPI_SD1", "3": "QSPI_SD2", "4": "GND",
         "5": "QSPI_SD0", "6": "QSPI_SCLK", "7": "QSPI_SD3", "8": "+3V3"},
        "QSPI flash")
    add("C711", C, "100n", FP_C["0402"], {"1": "+3V3", "2": "GND"}, "QSPI flash")
    add("R701", R, "1k", FP_R["0402"], {"1": "QSPI_SS", "2": "BOOTSEL"},
        "QSPI flash")

    add("U9", "Regulator_Linear:SPX3819M5-L-3-3", "SPX3819M5-3.3",
        "Package_TO_SOT_SMD:SOT-23-5",
        {"1": "+5V", "2": "GND", "3": "+5V", "4": "", "5": "+3V3"}, "3V3 LDO")
    add("C713", C, "1u", FP_C["0603"], {"1": "+5V", "2": "GND"}, "3V3 LDO")
    add("C712", C, "1u", FP_C["0603"], {"1": "+3V3", "2": "GND"}, "3V3 LDO")
    add("R704", R, "10k", FP_R["0402"], {"1": "RUN", "2": "+3V3"}, "reset")
    add("C717", C, "100n", FP_C["0402"], {"1": "RUN", "2": "GND"}, "reset")
    return out

def encoder():
    """03_encoder: the MT6701, straight across from the sister project."""
    sh = "03_encoder"
    return [
        Comp("U10", "servodrive:MT6701", "MT6701",
             "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
             {"1": "+3V3", "2": "+3V3", "3": "ENC_OUT", "4": "GND",
              "5": "", "6": "ENC_DO", "7": "ENC_SCK", "8": "ENC_CS"},
             sh, "encoder"),
        Comp("C901", C, "100n", FP_C["0402"], {"1": "+3V3", "2": "GND"},
             sh, "encoder"),
    ]

# Signal link pinout. Grounds are interleaved so every fast pair has a return
# beside it; USB sits at position 3 with a ground either side.
SIG_PINS = [
    "GND", "+12V", "+5V", "GND", "USB_DP", "USB_DM", "GND", "USB_VBUS_DET",
    "PD_INT_n", "PD_SDA", "GND", "PD_SCL", "RS485_DI", "RS485_RO",
    "GND", "RS485_DE", "RS485_TERM_EN", "GATE_EN", "GND", "SWCLK",
    "SWDIO", "RUN", "GND", "BOOTSEL", "LED_R", "LED_G", "LED_B", "GND",
]

def interconnect():
    """04_interconnect: two power links, one signal link, and the ADC front end."""
    sh, out = "04_interconnect", []
    def add(ref, lib_id, value, fp, nets, group, dnp=False):
        out.append(Comp(ref, lib_id, value, fp, nets, sh, group, dnp))
    for ref in ("J7", "J8"):
        add(ref, "Connector_Generic:Conn_02x05_Odd_Even", "PWR_LINK",
            "Connector_PinHeader_2.54mm:PinHeader_2x05_P2.54mm_Vertical",
            {str(k): ("VBUS" if k % 2 else "GND") for k in range(1, 11)},
            "power link")
    # The rails' bulk, once, where they arrive. Each driver keeps its own 100 n;
    # three 1 uF caps on one 0.1 A rail was two too many, and they were costing
    # the phase cells degrees they could not spare.
    add("C603", C, "4u7/25V", FP_C["0603"], {"1": "+12V", "2": "GND"}, "power link")
    add("C604", C, "4u7", FP_C["0603"], {"1": "+5V", "2": "GND"}, "power link")
    add("J9", "Connector_Generic:Conn_02x14_Odd_Even", "SIG_LINK",
        "Connector_PinHeader_1.27mm:PinHeader_2x14_P1.27mm_Vertical_SMD",
        {str(k + 1): SIG_PINS[k] for k in range(28)}, "signal link")

    # Bus divider: 24:1, and two resistors in the high leg because a 0603 is
    # rated 50 V working and the bus is 60.
    add("R801", R, "56k 0.1%", FP_R["0603"], {"1": "VBUS", "2": "VDIV_MID"},
        "bus voltage")
    add("R806", R, "56k 0.1%", FP_R["0603"], {"1": "VDIV_MID", "2": "VBUS_SENSE"},
        "bus voltage")
    add("R802", R, "4k7 0.1%", FP_R["0603"], {"1": "VBUS_SENSE", "2": "GND"},
        "bus voltage")
    add("C801", C, "100n", FP_C["0603"], {"1": "VBUS_SENSE", "2": "GND"},
        "bus voltage")
    add("R803", R, "10k 0.1%", FP_R["0603"], {"1": "FET_TEMP", "2": "GND"},
        "FET temperature")
    add("C802", C, "100n", FP_C["0603"], {"1": "FET_TEMP", "2": "GND"},
        "FET temperature")
    # Not fitting the overcurrent comparator leaves GPIO15 floating, which is
    # the one thing an input must not be. The pull-up is the "not fitted" state.
    add("R705", R, "10k", FP_R["0402"], {"1": "FAULT_n", "2": "+3V3"},
        "overcurrent, if it is ever fitted")
    for ref, net in (("TP1", "TP23"), ("TP2", "TP24"),
                     ("TP3", "TP25"), ("TP4", "FET_TEMP")):
        add(ref, "Connector:TestPoint", net.replace("TP", "GPIO") if net != "FET_TEMP" else "ADC3",
            "TestPoint:TestPoint_Pad_D1.5mm", {"1": net}, "test points")
    return out

def mechanical():
    """Copper that is geometry rather than a part: the heatsink lands, the six
    plated bosses and the three motor lead pads.

    They are in the netlist because the router needs to know what they are --
    a lead pad with no net is a lead pad nothing connects to -- but they are
    positioned by gen_boards.py rather than by placement.py, because an arc has
    no sensible rectangular courtyard to place by.
    """
    out = []
    for i, X in enumerate("ABC"):
        out.append(Comp(f"TL{i+1}", "Mechanical:MountingHole_Pad", "heatsink land",
                        "servodrive:ThermalLand_Phase", {"1": "GND"},
                        "04_interconnect", "heatsink and mounting"))
        out.append(Comp(f"J{i+1}", "Connector:Conn_01x01_Pin", f"PHASE_{X}",
                        "servodrive:PhasePad_Arc", {"1": f"PHASE_{X}"},
                        "04_interconnect", "motor leads"))
    for i in range(6):
        out.append(Comp(f"H{10+i}", "Mechanical:MountingHole_Pad", "M2.5 boss",
                        "servodrive:ThermalBoss_M2.5", {"1": "GND"},
                        "04_interconnect", "heatsink and mounting"))
    return out

GEN_PLACED = {c.ref for c in mechanical()}

def board_a():
    out = []
    for i in range(3):
        out += phase_cell(i)
    return out + control() + encoder() + interconnect() + mechanical()

SHEETS = [
    ("01_power_stage", "Three half-bridges, gate drive, DC link, inline sense"),
    ("02_control", "RP2350A, crystal, QSPI flash, 3V3 LDO, ADC supply"),
    ("03_encoder", "MT6701 on the shaft axis, SSI to SPI0"),
    ("04_interconnect", "Power and signal links to board B, ADC front end"),
]

# --------------------------------------------------------------- checking ---
def nets(comps=None):
    """{net: [(ref, pin), ...]} over the whole board."""
    out = {}
    for c in (comps or board_a()):
        for pin, net in c.nets.items():
            if net:
                out.setdefault(net, []).append((c.ref, pin))
    return out

def check(comps=None):
    comps = comps or board_a()
    bad = []
    placed = {p.ref: p for p in PL.board_a()}
    here = {c.ref: c for c in comps if c.ref not in GEN_PLACED}
    for ref in sorted(set(placed) - set(here)):
        bad.append(f"{ref} is placed on the board but has no netlist entry")
    for ref in sorted(set(here) - set(placed)):
        bad.append(f"{ref} is in the netlist but is not placed")
    for ref in sorted(set(here) & set(placed)):
        if here[ref].fp != placed[ref].fp:
            bad.append(f"{ref}: netlist says {here[ref].fp}, "
                       f"board has {placed[ref].fp}")
        if here[ref].dnp != placed[ref].dnp:
            bad.append(f"{ref}: DNP disagrees between netlist and board")
    for c in comps:
        want = set(pins(c.lib_id))
        got = set(c.nets)
        for p in sorted(want - got):
            bad.append(f"{c.ref} ({c.lib_id}) pin {p} "
                       f"({pins(c.lib_id)[p][3]}) has no net")
        for p in sorted(got - want):
            bad.append(f"{c.ref} ({c.lib_id}) has a net on pin {p}, "
                       f"which the symbol does not have")
    for net, conns in nets(comps).items():
        if len(conns) < 2 and not net.endswith("_NC"):
            bad.append(f"net {net} has one connection: {conns}")
    return bad

def report():
    comps = board_a()
    n = nets(comps)
    print(f"board A netlist: {len(comps)} components, {len(n)} nets, "
          f"{sum(len(v) for v in n.values())} pin connections")
    for sh, _ in SHEETS:
        cs = [c for c in comps if c.sheet == sh]
        print(f"  {sh:16s} {len(cs):3d} components")
    big = sorted(n.items(), key=lambda kv: -len(kv[1]))[:6]
    print("  busiest nets: " + ", ".join(f"{k} ({len(v)})" for k, v in big))
    bad = check(comps)
    if bad:
        print(f"\n{len(bad)} PROBLEM(S):")
        for b in bad:
            print("  !", b)
    else:
        print("\nclean: every placed part is in the netlist, every symbol pin "
              "has a net, every net has at least two connections.")
    return bad

if __name__ == "__main__":
    import sys
    sys.exit(1 if report() else 0)

# ----------------------------------------------------------------- emitter --
# A KiCad schematic is drawn with wires; this one is drawn with labels. Every
# pin gets a 2.54 mm stub and the net's name, which is what makes a generated
# sheet legible at all -- routing wires between 115 parts automatically
# produces something worse than a table.

MM     = 1.27                  # the grid everything snaps to
STUB   = 2.54                  # pin to label
MARGIN = 15.0                  # page edge
ROW_GAP = 14.0                 # between bands of parts
LABEL_ROOM = 22.0              # room for the net name beside the stub

def cell(lib_id):
    """How much sheet a part needs: its pins, its stubs, and its labels."""
    w, h = extent(lib_id)
    return (_snap(w + 2 * STUB + LABEL_ROOM), _snap(h + 2 * STUB + 9.0))
PAGES = {"A4": (297, 210), "A3": (420, 297), "A2": (594, 420), "A1": (841, 594)}

def _snap(v, g=MM):
    return round(round(v / g) * g, 4)

def pin_at(comp_xy, ang, px, py):
    """Library point -> schematic point, for a symbol at comp_xy rotated ang."""
    X, Y = comp_xy
    a = radians(ang)
    return (_snap(X + px * cos(a) - py * sin(a)),
            _snap(Y - (px * sin(a) + py * cos(a))))

def _uid(*parts):
    import uuid
    NS = uuid.UUID("6f2a1c44-0000-4000-8000-000000000000")
    return str(uuid.uuid5(NS, "/".join(str(p) for p in parts)))

def _prop(name, value, x, y, hide=False, just=None):
    j = f' (justify {just})' if just else ''
    h = ' (hide yes)' if hide else ''
    return (f'\t\t(property "{name}" "{value}"\n'
            f'\t\t\t(at {x:.4f} {y:.4f} 0)\n'
            f'\t\t\t(effects (font (size 1.27 1.27)){j}{h}))')

def _label(net, x, y, ang, tag, glob=None):
    kind = "global_label" if net in (GLOBAL if glob is None else glob) else "label"
    shape = ' (shape bidirectional)' if kind == "global_label" else ''
    just = "right" if 90 < ang % 360 < 270 else "left"
    extra = ""
    if kind == "global_label":
        extra = (f'\n\t\t(property "Intersheetrefs" "${{INTERSHEET_REFS}}"\n'
                 f'\t\t\t(at {x:.4f} {y:.4f} 0)\n'
                 f'\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))')
    return (f'\t({kind} "{net}"{shape}\n'
            f'\t\t(at {x:.4f} {y:.4f} {ang % 360:.0f})\n'
            f'\t\t(effects (font (size 1.27 1.27)) (justify {just}))\n'
            f'\t\t(uuid "{_uid(tag)}"){extra})')

def _wire(x1, y1, x2, y2, tag):
    return (f'\t(wire (pts (xy {x1:.4f} {y1:.4f}) (xy {x2:.4f} {y2:.4f}))\n'
            f'\t\t(stroke (width 0) (type default)) (uuid "{_uid(tag)}"))')

def _text(s, x, y, tag, size=2.0, bold=True):
    b = " (bold yes)" if bold else ""
    return (f'\t(text "{s}"\n\t\t(exclude_from_sim no)\n'
            f'\t\t(at {x:.4f} {y:.4f} 0)\n'
            f'\t\t(effects (font (size {size} {size}){b}) (justify left bottom))\n'
            f'\t\t(uuid "{_uid(tag)}"))')

def _symbol_instance(c, x, y, project, path, tag, unit=1):
    body = [f'\t(symbol\n\t\t(lib_id "{c.lib_id}")\n'
            f'\t\t(at {x:.4f} {y:.4f} 0)\n\t\t(unit {unit})\n'
            f'\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n'
            f'\t\t(dnp {"yes" if c.dnp else "no"})\n'
            f'\t\t(uuid "{_uid(tag, c.ref)}")']
    w, h = extent(c.lib_id)
    body.append(_prop("Reference", c.ref, x - w / 2, y - h / 2 - 2.54, just="left"))
    body.append(_prop("Value", c.value, x - w / 2, y + h / 2 + 2.54, just="left"))
    body.append(_prop("Footprint", c.fp, x, y, hide=True))
    body.append(_prop("Datasheet", "~", x, y, hide=True))
    for num in sorted(pins(c.lib_id), key=lambda s: (len(s), s)):
        body.append(f'\t\t(pin "{num}" (uuid "{_uid(tag, c.ref, "p", num)}"))')
    body.append(f'\t\t(instances\n\t\t\t(project "{project}"\n'
                f'\t\t\t\t(path "{path}"\n'
                f'\t\t\t\t\t(reference "{c.ref}") (unit {unit})\n'
                f'\t\t\t\t)\n\t\t\t)\n\t\t)')
    return "\n".join(body) + "\n\t)"

def sheet_text(name, comps, project, file_uuid, inst_uuid, title, page=None,
               flags=(), glob=None):
    """One hierarchical sheet: its symbols, their stubs and their labels.

    Two different uuids, and mixing them up produces a file that opens, passes
    ERC and exports an empty netlist. `file_uuid` identifies this .kicad_sch;
    `inst_uuid` is the uuid of the (sheet ...) element in the ROOT sheet, and
    the instance path is "/" + that, with no root uuid in front of it.

    Cells are sized from each symbol's own pin extent plus the stub, so no
    part's stub can reach its neighbour's. That is not a tidiness question: two
    stub ends on the same coordinate silently weld two nets together, and the
    first attempt at this shorted +12V to VB_A that way.
    """
    if page is None:
        # Grow the page until the parts fit rather than guessing: the power
        # stage is 73 components and wanted 453 mm on a 420 mm A2.
        for page in ("A4", "A3", "A2", "A1"):
            try:
                return sheet_text(name, comps, project, file_uuid, inst_uuid,
                                  title, page, flags, glob)
            except RuntimeError as e:
                if "needs" not in str(e):
                    raise
        raise RuntimeError(f"{name}: does not fit on A1")
    W, H = PAGES[page]
    tag = f"{project}/{name}"
    out, marks = [], {}

    def stub(net, p, theta, key):
        q = (_snap(p[0] + STUB * cos(radians(theta))),
             _snap(p[1] - STUB * sin(radians(theta))))
        prev = marks.get(q)
        if prev is not None and prev != net:
            raise RuntimeError(f"{name}: {net} and {prev} both land on {q}")
        marks[q] = net
        out.append(_wire(p[0], p[1], q[0], q[1], key + ("w",)))
        out.append(_label(net, q[0], q[1], theta, key + ("l",), glob))

    def emit(c, cx, cy):
        out.append(_symbol_instance(c, cx, cy, project, f"/{inst_uuid}", tag))
        for num, (px, py, rot, _, _) in pins(c.lib_id).items():
            net = c.nets.get(num, "")
            p = pin_at((cx, cy), 0, px, py)
            if not net:
                out.append(f'\t(no_connect (at {p[0]:.4f} {p[1]:.4f}) '
                           f'(uuid "{_uid(tag, c.ref, "nc", num)}"))')
                continue
            stub(net, p, (rot + 180) % 360, (tag, c.ref, num))

    x, y, row_h, last = MARGIN, MARGIN + 12.0, 0.0, None
    for c in comps:
        cw, ch = cell(c.lib_id)
        if c.group != last or x + cw > W - MARGIN:
            if last is not None:
                x, y = MARGIN, y + row_h + ROW_GAP
                row_h = 0.0
            if c.group != last:
                out.append(_text(c.group, _snap(x), _snap(y - 4.0),
                                 (tag, "grp", c.group)))
                last = c.group
        emit(c, _snap(x + cw / 2), _snap(y + ch / 2))
        x += cw
        row_h = max(row_h, ch)

    # PWR_FLAG declares who drives a rail that arrives on a passive pin.
    if flags:
        x, y, row_h = MARGIN, y + row_h + ROW_GAP, 0.0
        out.append(_text("power sources", x, _snap(y - 4.0), (tag, "grp", "pwr")))
        for i, net in enumerate(flags):
            cw, ch = cell("power:PWR_FLAG")
            emit(Comp(f"#FLG{i}{name[1]}", "power:PWR_FLAG", "PWR_FLAG", "",
                      {"1": net}, name, "power sources"),
                 _snap(x + cw / 2), _snap(y + ch / 2))
            x += cw
            row_h = max(row_h, ch)

    if y + row_h > H - MARGIN:
        raise RuntimeError(f"{name}: needs {y + row_h:.0f} mm on a {page} page")

    used = {c.lib_id for c in comps} | ({"power:PWR_FLAG"} if flags else set())
    libs = ["\t(lib_symbols"]
    for lib_id in sorted(used):
        libs.append("\t\t" + sexp.dump(symbol(lib_id), indent=2))
    libs.append("\t)")

    return (f'(kicad_sch\n\t(version 20250114)\n\t(generator "eeschema")\n'
            f'\t(generator_version "9.0")\n\t(uuid "{file_uuid}")\n'
            f'\t(paper "{page}")\n'
            f'\t(title_block (title "{title}") (date "2026-09-07") (rev "A")\n'
            f'\t\t(company "Sequoia Hope Alexander")\n'
            f'\t\t(comment 1 "Generated by tools/schematic.py from its netlist")\n'
            f'\t\t(comment 2 "Drawn with labels, not wires. See spec sec.11.")\n'
            f'\t\t(comment 3 "CERN-OHL-P"))\n'
            + "\n".join(libs) + "\n"
            + "\n".join(out) + "\n"
            f'\t(embedded_fonts no)\n)\n')

# Rails that reach board A on a passive connector pin, so nothing on this board
# declares them a source until a flag says so.
SHEET_FLAGS = {"04_interconnect": ("VBUS", "GND", "+12V", "+5V"),
               "02_control": ("+1V1", "+3V3A", "VREG_AVDD")}
