#!/usr/bin/env python3
"""schematic_s.py — board S's netlist: board A plus the minimum of board B.

Board S is a VARIANT of board A (decided 2026-09-22), and its netlist is built
the same way the placement is: board A's own data, with the link headers taken
away and board B's minimum put in their place. The three phase cells, the
RP2350 and its support, and the encoder come from tools/schematic.py
unchanged except where the variant says otherwise:

  - the per-phase clamps drop to the 54 V grade, to sit under a 48 V bus and
    its 54 V bus TVS
  - the GPIO map: 0/1 are UART0 for RS-485 port IN and 2/3 a PIO UART for
    port OUT (the CPU relays between them); 14 is GATE_OFF; 19-24 go to the
    expansion header; USB_VBUS_DET moves to 25
  - J7, J8, J9 and TP1-TP3 go; C603 and C604 become the two bucks' output
    caps; TP4 moves to the centre with three new test pads beside it

    python3 tools/schematic_s.py        # the netlist report and the checks

The sheets are emitted by gen_boards.py through schematic.sheet_text(), the
same machine-drawn, label-per-pin style as board A's.
"""
import schematic as SCH
from schematic import Comp, R, C, L, DZ, FP_R, FP_C

SCHOTTKY = "Device:D_Schottky"

# The RP2350's GPIO on board S, where it differs from board A (spec sec.6).
# 20..23 are both I2C0 SDA/SCL (20/21) and SPI0 RX/CSn/SCK/TX, which covers
# a FUSB302 or a W5500 on a stacked board; 19 and 24 are its interrupt and
# reset. The header's nets carry the GPIO number, not one of the functions.
GP_S = {0: "RS485_IN_TX", 1: "RS485_IN_RX", 2: "RS485_OUT_TX", 3: "RS485_OUT_RX",
        # The 12 V buck's EN sits on a divider from VMOT -- up to 8 V at a
        # 60 V bus, which no GPIO may see -- so a 2N7002 does the pulling, and
        # the GPIO's sense is inverted: HIGH kills the gate rail. Named for
        # what high does. A reset leaves the pin pulled down, the rail on and
        # the low sides braking, which is what spec sec.3 asks of a reset.
        14: "GATE_OFF",
        19: "EXP_GP19", 20: "EXP_GP20", 21: "EXP_GP21", 22: "EXP_GP22",
        23: "EXP_GP23", 24: "EXP_GP24", 25: "USB_VBUS_DET"}
GPIO_PINS = [2, 3, 4, 5, 7, 8, 9, 10, 12, 13, 14, 15, 16, 17, 18, 19,
             27, 28, 29, 31, 32, 33, 34, 35, 36, 37]

# Expansion header, 2 x 10 at 1.27 mm, across the shaft axis on the outward
# face. The odd row carries VMOT at the end toward C1001 and the even row the
# ground beside each of those pins, so a PD board feeding the bus through
# here passes the encoder as a dipole, not a loop.
EXP_PINS = {1: "VBUS", 2: "GND", 3: "VBUS", 4: "GND", 5: "VBUS", 6: "GND",
            7: "VBUS", 8: "GND", 9: "+5V", 10: "GND", 11: "+3V3", 12: "RUN",
            13: "EXP_GP19", 14: "EXP_GP24", 15: "EXP_GP20", 16: "EXP_GP21",
            17: "EXP_GP22", 18: "EXP_GP23", 19: "SWCLK", 20: "SWDIO"}

def phase_cells():
    out = []
    for i in range(3):
        for c in SCH.phase_cell(i):
            if c.lib_id == DZ:
                c.value = "TPSMF4L54A"      # the 54 V grade, under a 48 V bus
            out.append(c)
    return out

def control():
    """02_control, with board S's GPIO map. Everything else is board A's."""
    out = SCH.control()
    u7 = next(c for c in out if c.ref == "U7")
    for g, net in GP_S.items():
        u7.nets[str(GPIO_PINS[g])] = net
    return out

def power():
    """04_power: the bus from its XT30 to the rails."""
    sh, out = "04_power", []
    def add(ref, lib_id, value, fp, nets, group, dnp=False):
        out.append(Comp(ref, lib_id, value, fp, nets, sh, group, dnp))

    # The bus input (2026-09-24, was two arc pads for a pigtail): the
    # rp2350-motor-controller's J9, symbol, footprint and pinout -- AMASS
    # XT30PW-M, pin 1 GND, pin 2 VMOT -- so one pack lead fits both boards.
    add("J4", "Connector_Generic:Conn_01x02", "XT30",
        "servodrive:AMASS_XT30PW-M_1x02_P2.50mm_Horizontal", {"1": "GND", "2": "VBUS"},
        "bus: input")

    add("D1001", DZ, "SMDJ54A", "Diode_SMD:D_SMC", {"1": "VBUS", "2": "GND"},
        "bus: TVS and bulk")
    # Two solid-polymer cans, C2887236, in the centre of the outward face.
    for ref in ("C1001", "C1002"):
        add(ref, "Device:C_Polarized", "100u/100V polymer",
            "Capacitor_THT:CP_Radial_D10.0mm_P5.00mm", {"1": "VBUS", "2": "GND"},
            "bus: TVS and bulk")
    # The bus divider, board A's: 24:1, two resistors in the high leg for the
    # working voltage.
    add("R801", R, "56k 0.1%", FP_R["0603"], {"1": "VBUS", "2": "VDIV_MID"}, "bus voltage")
    add("R806", R, "56k 0.1%", FP_R["0603"], {"1": "VDIV_MID", "2": "VBUS_SENSE"},
        "bus voltage")
    add("R802", R, "4k7 0.1%", FP_R["0603"], {"1": "VBUS_SENSE", "2": "GND"}, "bus voltage")
    add("C801", C, "100n", FP_C["0603"], {"1": "VBUS_SENSE", "2": "GND"}, "bus voltage")

    # Two LMR38010 off the bus rather than a cascade: a fault on the gate rail
    # does not take the CPU down with it (spec sec.7). Pins per SNVSC73B:
    # 1 GND, 2 EN, 3 VIN, 4 RT/SYNC, 5 FB, 6 PG, 7 BOOT, 8 SW, 9 exposed pad.
    # The passives are TI's design points for 400 kHz from a 48 V bus
    # (SNVSC73B table 9-1, sec. 9.2.2): the internal compensation is tuned
    # for that L and COUT. Input: 100 n at the pins and at least 4.7 uF of
    # ceramic rated for the bus. 12 V: 68 uH, 22 uF nominal / 15 uF minimum,
    # here a 1206 beside the inductor and a 1210 X7R, about 20 uF left at
    # 12 V. 5 V: 33 uH, 3 x 22 uF nominal / 2 minimum. Both inductors are
    # Sunlord SWPA4030S: 0.72 A and 1.1 A saturation against peaks of about
    # 0.3 A (12 V at 0.1 A) and 0.7 A (5 V at 0.5 A). Where each part sits,
    # and why some are in the outward centre: placement_s.MOD_LMR38010.
    def buck(tag, u, cin, cbulk, boot, l, couts, fbt, fbb, rt, vout, en, lval, fbb_v, grp):
        add(u, "servodrive:LMR38010", "LMR38010SDDAR", "Package_SO:TI_SO-PowerPAD-8",
            {"1": "GND", "2": en, "3": "VBUS", "4": f"RT_{tag}", "5": f"FB_{tag}",
             "6": "", "7": f"BOOT_{tag}", "8": f"SW_{tag}", "9": "GND"}, grp)
        add(cin, C, "100n/100V", FP_C["0805"], {"1": "VBUS", "2": "GND"}, grp)
        add(cbulk, C, "4u7/100V", FP_C["1206"], {"1": "VBUS", "2": "GND"}, grp)
        add(boot, C, "100n", FP_C["0402"], {"1": f"BOOT_{tag}", "2": f"SW_{tag}"}, grp)
        add(l, L, lval, "Inductor_SMD:L_Sunlord_SWPA4030S", {"1": f"SW_{tag}", "2": vout}, grp)
        for cout, size in couts:
            add(cout, C, "22u/25V", FP_C[size], {"1": vout, "2": "GND"}, grp)
        # VREF 1.000 V: the bottom leg is 100k / (Vout - 1)
        add(fbt, R, "100k", FP_R["0402"], {"1": vout, "2": f"FB_{tag}"}, grp)
        add(fbb, R, fbb_v, FP_R["0402"], {"1": f"FB_{tag}", "2": "GND"}, grp)
        # RT/SYNC cannot float or be grounded: 64.9k is 400 kHz
        add(rt, R, "64k9", FP_R["0402"], {"1": f"RT_{tag}", "2": "GND"}, grp)

    g12 = "12 V gate rail"
    buck("12V", "U11", "C1003", "C1004", "C1005", "L1001", (("C603", "1206"), ("C1009", "1210")),
         "R1001", "R1002", "R1008", "+12V", "EN_12V", "68u", "9k09", g12)
    # EN from a divider on the bus: starts at 9.9 V typ (1.25 V rising;
    # 11.1 V at the 1.4 V maximum) and stops at 8.7 V, inside the 12 V floor.
    # GATE_OFF high turns the 2N7002 on and pulls EN under its 0.95 V.
    add("R1003", R, "470k", FP_R["0402"], {"1": "VBUS", "2": "EN_12V"}, g12)
    add("R1004", R, "68k", FP_R["0402"], {"1": "EN_12V", "2": "GND"}, g12)
    add("Q1001", "Transistor_FET:2N7002", "2N7002", "Package_TO_SOT_SMD:SOT-23",
        {"1": "GATE_OFF", "2": "GND", "3": "EN_12V"}, g12)
    # 4k7 against RP2350-E9, like the drivers' input pulls: the pin cannot
    # latch at 2.2 V and half-turn the FET on through a reset.
    add("R1005", R, "4k7", FP_R["0402"], {"1": "GATE_OFF", "2": "GND"}, g12)

    g5 = "5 V logic rail"
    # EN tied to VIN: the logic rail is on whenever the bus is.
    buck("5V", "U12", "C1006", "C1007", "C1008", "L1002",
         (("C604", "0805"), ("C1010", "0805"), ("C1011", "0805")), "R1006", "R1007", "R1009",
         "+5V_BUCK", "VBUS", "33u", "24k9", g5)
    # The buck and USB both feed +5V, each through a Schottky, so neither can
    # back-feed the other (USB_VBUS's is on 05_io).
    add("D1003", SCHOTTKY, "B5819WS", "Diode_SMD:D_SOD-323",
        {"1": "+5V", "2": "+5V_BUCK"}, g5)
    return out

def io():
    """05_io: USB-C, the RS-485 relay, the expansion header, LED, BOOTSEL,
    test pads, and board A's ADC3 front end and FAULT_n pull-up."""
    sh, out = "05_io", []
    def add(ref, lib_id, value, fp, nets, group, dnp=False):
        out.append(Comp(ref, lib_id, value, fp, nets, sh, group, dnp))

    gu = "USB-C: data and 5 V logic, no PD"
    usb = {"S1": "GND"}
    for p in ("A1", "A12", "B1", "B12"):
        usb[p] = "GND"
    for p in ("A4", "A9", "B4", "B9"):
        usb[p] = "USB_VBUS"
    usb.update({"A5": "CC1", "B5": "CC2", "A6": "USB_DP", "B6": "USB_DP",
                "A7": "USB_DM", "B7": "USB_DM", "A8": "", "B8": ""})
    add("J12", "Connector:USB_C_Receptacle_USB2.0_16P", "USB-C",
        "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12", usb, gu)
    # Pin 5 on USB_VBUS is safe here: the port is a 5 V sink with no PD
    # (spec F-39 is about a 20 V contract on this pin).
    add("U13", "Power_Protection:USBLC6-2SC6", "USBLC6-2SC6", "Package_TO_SOT_SMD:SOT-23-6",
        {"1": "USB_DP", "6": "USB_DP", "3": "USB_DM", "4": "USB_DM",
         "2": "GND", "5": "USB_VBUS"}, gu)
    add("R1101", R, "5k1", FP_R["0402"], {"1": "CC1", "2": "GND"}, gu)
    add("R1102", R, "5k1", FP_R["0402"], {"1": "CC2", "2": "GND"}, gu)
    add("D1002", SCHOTTKY, "B5819WS", "Diode_SMD:D_SOD-323",
        {"1": "+5V", "2": "USB_VBUS"}, gu)
    # 5 V -> 3.0 V on GPIO25
    add("R1103", R, "10k", FP_R["0402"], {"1": "USB_VBUS", "2": "USB_VBUS_DET"}, gu)
    add("R1104", R, "15k", FP_R["0402"], {"1": "USB_VBUS_DET", "2": "GND"}, gu)

    # The RS-485 relay (decided 2026-09-22): two ports, each a point-to-point
    # full-duplex link, the CPU passing traffic between them. Pairs are named
    # from each port's own side, as spec sec.7's pinout is, so a straight
    # cable from one drive's OUT to the next one's IN is correct:
    #   port IN   DOWN pair  master -> this drive      U14 receives
    #             UP pair    this drive -> master      U15 drives
    #   port OUT  DOWN pair  this drive -> next drive  U17 drives
    #             UP pair    next drive -> this drive  U18 receives
    # Every driver is alone on its pair, so DE is tied on and the receiver
    # half is off (RE high); every receiver has its 120 R always -- there is
    # no mid-bus position for a jumper to serve.
    # SIT3088ETK: the MAX3485 symbol, pin for pin, as the sister project has
    # it, derived into the project library (gen_boards.DRAWN_SYMBOLS).
    gr = "RS-485 relay"
    SIT, DFN = "servodrive:SIT3088", "parts_motor:DFN-8_L3.0-W3.0-P0.65-BL-EP"
    def rx(ref, ro, a, b, cap):
        add(ref, SIT, "SIT3088ETK", DFN,
            {"1": ro, "2": "GND", "3": "GND", "4": "GND", "5": "GND",
             "6": a, "7": b, "8": "+3V3"}, gr)
        add(cap, C, "100n", FP_C["0402"], {"1": "+3V3", "2": "GND"}, gr)
    def tx(ref, di, a, b, cap):
        add(ref, SIT, "SIT3088ETK", DFN,
            {"1": "", "2": "+3V3", "3": "+3V3", "4": di, "5": "GND",
             "6": a, "7": b, "8": "+3V3"}, gr)
        add(cap, C, "100n", FP_C["0402"], {"1": "+3V3", "2": "GND"}, gr)
    IDP, IDN, IUP, IUN = "RS485_IN_DN_P", "RS485_IN_DN_N", "RS485_IN_UP_P", "RS485_IN_UP_N"
    ODP, ODN, OUP, OUN = "RS485_OUT_DN_P", "RS485_OUT_DN_N", "RS485_OUT_UP_P", "RS485_OUT_UP_N"
    rx("U14", "RS485_IN_RX", IDP, IDN, "C1102")
    tx("U15", "RS485_IN_TX", IUP, IUN, "C1103")
    tx("U17", "RS485_OUT_TX", ODP, ODN, "C1104")
    rx("U18", "RS485_OUT_RX", OUP, OUN, "C1105")
    add("R1105", R, "120R", FP_R["0603"], {"1": IDP, "2": IDN}, gr)
    add("R1106", R, "120R", FP_R["0603"], {"1": OUP, "2": OUN}, gr)
    for ref, a, b in (("D1101", IDP, IDN), ("D1102", IUP, IUN),
                      ("D1104", ODP, ODN), ("D1105", OUP, OUN)):
        add(ref, "Diode:SM712_SOT23", "SM712", "Package_TO_SOT_SMD:SOT-23",
            {"1": a, "2": b, "3": "GND"}, gr)
    SH6 = "Connector_JST:JST_SH_SM06B-SRSS-TB_1x06-1MP_P1.00mm_Horizontal"
    for ref, val, dp, dn, up, un in (("J14", "RS485 IN", IDP, IDN, IUP, IUN),
                                     ("J15", "RS485 OUT", ODP, ODN, OUP, OUN)):
        add(ref, "Connector_Generic_MountingPin:Conn_01x06_MountingPin", val, SH6,
            {"1": "GND", "2": dp, "3": dn, "4": up, "5": un, "6": "GND", "MP": "GND"}, gr)

    ge = "expansion header: PD or Ethernet board"
    add("J13", "Connector_Generic:Conn_02x10_Odd_Even", "EXPANSION",
        "Connector_PinHeader_1.27mm:PinHeader_2x10_P1.27mm_Vertical_SMD",
        {str(k): v for k, v in EXP_PINS.items()}, ge)

    gl = "status LED, BOOTSEL, test pads"
    # Common anode on +3V3, each cathode through 1k to its GPIO: low is on.
    # Check the pinout against the part bought: 1210 RGB parts differ.
    add("D1103", "Device:LED_RGBA", "RGB", "LED_SMD:LED_RGB_1210",
        {"1": "LED_R_K", "2": "LED_G_K", "3": "LED_B_K", "4": "+3V3"}, gl)
    for ref, net in (("R1107", "LED_R"), ("R1108", "LED_G"), ("R1109", "LED_B")):
        add(ref, R, "1k", FP_R["0402"], {"1": net, "2": net + "_K"}, gl)
    add("SW1", "Switch:SW_Push", "BOOTSEL", "Button_Switch_SMD:SW_SPST_B3U-1000P",
        {"1": "BOOTSEL", "2": "GND"}, gl)
    for ref, net, val in (("TP4", "FET_TEMP", "ADC3"), ("TP5", "SWCLK", "SWCLK"),
                          ("TP6", "SWDIO", "SWDIO"), ("TP7", "RUN", "RUN")):
        add(ref, "Connector:TestPoint", val, "TestPoint:TestPoint_Pad_D1.0mm", {"1": net}, gl)

    ga = "ADC3 front end, FAULT_n"
    add("R803", R, "10k 0.1%", FP_R["0603"], {"1": "FET_TEMP", "2": "GND"}, ga)
    add("C802", C, "100n", FP_C["0603"], {"1": "FET_TEMP", "2": "GND"}, ga)
    add("R705", R, "10k", FP_R["0402"], {"1": "FAULT_n", "2": "+3V3"}, ga)
    return out

def mechanical():
    """Board A's heatsink lands, bosses and phase lead pads. (The bus's two arc
    pads went for the XT30 on 2026-09-24; it is a placed part, in power().)"""
    out = []
    for c in SCH.mechanical():
        c.sheet = "04_power"
        out.append(c)
    return out

GEN_PLACED = {c.ref for c in mechanical()}

def board_s():
    return phase_cells() + control() + SCH.encoder() + power() + io() + mechanical()

SHEETS = [
    ("01_power_stage", "Three half-bridges, gate drive, DC link, inline sense"),
    ("02_control", "RP2350A, crystal, QSPI flash, 3V3 LDO, ADC supply"),
    ("03_encoder", "MT6701 on the shaft axis, SSI to SPI0"),
    ("04_power", "XT30 bus input, TVS, bulk, bus divider, the 12 V and 5 V bucks"),
    ("05_io", "USB-C, RS-485 relay, expansion header, LED, BOOTSEL, test pads"),
]

# Rails that arrive on passive pins, declared by a flag.
SHEET_FLAGS = {"04_power": ("VBUS", "GND", "+12V", "+5V"),
               "02_control": ("+1V1", "+3V3A", "VREG_AVDD")}

def global_nets(comps=None):
    """Nets on more than one sheet: those get a global label."""
    where = {}
    for c in (comps or board_s()):
        for net in c.nets.values():
            if net:
                where.setdefault(net, set()).add(c.sheet)
    return {n for n, s in where.items() if len(s) > 1} | set(SCH.PWR)

# --------------------------------------------------------------- checking ---
def nets(comps=None):
    return SCH.nets(comps or board_s())

def check(comps=None):
    """schematic.check()'s rules, against board S's placement."""
    import placement_s as PS
    comps = comps or board_s()
    bad = []
    placed = {p.ref: p for p in PS.parts()}
    here = {c.ref: c for c in comps if c.ref not in GEN_PLACED}
    for ref in sorted(set(placed) - set(here)):
        bad.append(f"{ref} is placed on the board but has no netlist entry")
    for ref in sorted(set(here) - set(placed)):
        bad.append(f"{ref} is in the netlist but is not placed")
    for ref in sorted(set(here) & set(placed)):
        if here[ref].fp != placed[ref].fp:
            bad.append(f"{ref}: netlist says {here[ref].fp}, board has {placed[ref].fp}")
        if here[ref].dnp != placed[ref].dnp:
            bad.append(f"{ref}: DNP disagrees between netlist and board")
    refs = [c.ref for c in comps]
    for r in sorted({r for r in refs if refs.count(r) > 1}):
        bad.append(f"{r} appears twice in the netlist")
    for c in comps:
        want, got = set(SCH.pins(c.lib_id)), set(c.nets)
        for p in sorted(want - got):
            bad.append(f"{c.ref} ({c.lib_id}) pin {p} ({SCH.pins(c.lib_id)[p][3]}) has no net")
        for p in sorted(got - want):
            bad.append(f"{c.ref} ({c.lib_id}) has a net on pin {p}, which the symbol does not have")
    for net, conns in nets(comps).items():
        if len(conns) < 2 and not net.endswith("_NC"):
            bad.append(f"net {net} has one connection: {conns}")
    return bad

def report():
    comps = board_s()
    n = nets(comps)
    print(f"board S netlist: {len(comps)} components, {len(n)} nets, "
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
