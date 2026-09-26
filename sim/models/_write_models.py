#!/usr/bin/env python3
"""Write sim/models/*.json -- every component value with its source and the
date it was read (SPEC.md sec.0.3: cite every component value; where a value
cannot be found, say so and bracket it, never silently pick a typical).

A value carries:
    value / min / typ / max     the number(s)
    unit, source, read          provenance
    status: "datasheet" | "bracketed" | "derived" | "assumed"
Anything not "datasheet" is a thing the report must say out loud.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
READ = "2026-09-11"


def D(value=None, unit="", source="", read=READ, status="datasheet", note="",
      **kw):
    d = {"unit": unit, "source": source, "read": read, "status": status}
    if value is not None:
        d["value"] = value
    d.update(kw)
    if note:
        d["note"] = note
    return d


MODELS = {}

# ---------------------------------------------------------------- MOSFET ----
DS_FET = ("Infineon BSC030N08NS5 final data sheet rev 2.3, 2019-10-31, "
          "local copy sim/models/datasheets/bsc030n08ns5.pdf")
MODELS["bsc030n08ns5"] = {
    "part": "BSC030N08NS5", "maker": "Infineon",
    "package": D("PG-TDSON-8 (SuperSO8)", source=DS_FET),
    "V_DS": D(80, "V", DS_FET),
    "V_BR_DSS": D(unit="V", source=DS_FET, min=80),
    "I_D_25C": D(100, "A", DS_FET),
    "R_ds_on_10V": D(unit="Ohm", source=DS_FET, typ=2.6e-3, max=3.0e-3,
                     note="V_GS = 10 V, I_D = 50 A"),
    "R_ds_on_6V": D(unit="Ohm", source=DS_FET, typ=3.4e-3, max=4.5e-3),
    "R_ds_on_125C_factor": D(1.58, "-", "tools/geometry.py FET_RDSON_K, "
                             "read off datasheet diagram 12 by the board author",
                             status="derived"),
    "V_GS_th": D(unit="V", source=DS_FET, min=2.2, typ=3.0, max=3.8,
                 note="V_DS = V_GS, I_D = 95 uA"),
    "gfs": D(unit="S", source=DS_FET, min=55, typ=110),
    "R_G_internal": D(unit="Ohm", source=DS_FET, typ=1.6, max=2.4),
    "C_iss_40V": D(unit="F", source=DS_FET, typ=4300e-12, max=5600e-12,
                   note="V_GS = 0, V_DS = 40 V, 1 MHz"),
    "C_oss_40V": D(unit="F", source=DS_FET, typ=700e-12, max=910e-12),
    "C_rss_40V": D(unit="F", source=DS_FET, typ=32e-12, max=56e-12),
    "Q_g": D(unit="C", source=DS_FET, typ=61e-9, max=76e-9,
             note="V_DD = 40 V, I_D = 50 A, 0..10 V"),
    "Q_gs": D(20e-9, "C", DS_FET),
    "Q_gd": D(unit="C", source=DS_FET, typ=13e-9, max=19.5e-9),
    "Q_g_th": D(12e-9, "C", DS_FET),
    "Q_sw": D(21e-9, "C", DS_FET),
    "V_plateau": D(4.6, "V", DS_FET),
    "Q_oss": D(unit="C", source=DS_FET, typ=73e-9, max=97e-9),
    "Q_rr": D(unit="C", source=DS_FET, typ=94e-9, max=188e-9,
              note="V_R = 40 V, I_F = 50 A, di/dt = 100 A/us -- the design "
                   "point's di/dt is 870 A/us, where Q_rr is larger"),
    "t_rr": D(unit="s", source=DS_FET, typ=54e-9, max=108e-9),
    "V_SD": D(unit="V", source=DS_FET, typ=0.9, max=1.1),
    "R_th_JC": D(unit="K/W", source=DS_FET, typ=0.5, max=0.9),
    "R_th_JA_6cm2": D(50, "K/W", DS_FET),
    "t_d_on": D(20e-9, "s", DS_FET, note="R_G,ext = 3 Ohm, 40 V, 50 A"),
    "t_r": D(12e-9, "s", DS_FET),
    "t_d_off": D(43e-9, "s", DS_FET),
    "t_f": D(13e-9, "s", DS_FET),
    "L_source_package": D(unit="H", source="not in the datasheet", status="bracketed",
                          min=0.3e-9, typ=0.5e-9, max=1.0e-9,
                          note="SuperSO8 source-lead inductance is not specified. "
                               "tools/geometry.py assumes 0.5 nH per device; the "
                               "bracket is the usual range for this package."),
    "L_drain_package": D(unit="H", source="not in the datasheet", status="bracketed",
                         min=0.1e-9, typ=0.2e-9, max=0.4e-9),
}

# ------------------------------------------------------------ gate driver ---
DS_DRV = ("EG Micro EG2103 product page egmicro.com/products/detail/?name=EG2103, "
          "read 2026-09-11 (the LCSC datasheet PDF would not download)")
MODELS["eg2103"] = {
    "part": "EG2103", "maker": "EG Micro",
    "package": D("SOP-8", source=DS_DRV),
    "V_offset": D(600, "V", DS_DRV),
    "I_source": D(0.30, "A", DS_DRV),
    "I_sink": D(0.60, "A", DS_DRV),
    "t_on": D(780e-9, "s", DS_DRV, note="input to output, turn-on"),
    "t_off": D(220e-9, "s", DS_DRV, note="turn-off; the 560 ns asymmetry is the "
                                         "effective internal dead time"),
    "t_dead": D(unit="s", source=DS_DRV, typ=560e-9,
                min=460e-9, max=660e-9, status="bracketed",
                note="560 ns typ is from the maker's table; the 460..660 bracket "
                     "is tools/geometry.py's, whose datasheet source could not be "
                     "re-read on 2026-09-11."),
    "UVLO_VCC_on": D(8.7, "V", DS_DRV),
    "UVLO_VCC_off": D(8.0, "V", DS_DRV),
    "UVLO_VB_on": D(8.6, "V", DS_DRV),
    "UVLO_VB_off": D(8.0, "V", DS_DRV),
    "R_HIN_pulldown": D(200e3, "Ohm", DS_DRV),
    "R_LIN_pullup": D(200e3, "Ohm", DS_DRV),
    "V_IH": D(unit="V", source="tools/geometry.py DRV_VIH", status="bracketed",
              typ=2.5, note="not on the maker's page; carried from the board model"),
    "V_IL": D(unit="V", source="tools/geometry.py DRV_VIL", status="bracketed",
              typ=1.0),
    "disagreement": ("tools/geometry.py uses UVLO_ON 9.7 V and VB(on) 9.6 V, "
                     "about 1 V above the maker's 8.7 / 8.6.  The board's numbers "
                     "are the conservative ones, so geometry.bootstrap()'s margin "
                     "is pessimistic rather than optimistic."),
}

# ------------------------------------------------------- current amplifier --
DS_INA = ("TI INA241A/INA241B SBOSA30D, March 2022 rev December 2024, "
          "local copy sim/models/datasheets/ina241a.pdf")
MODELS["ina241a3"] = {
    "part": "INA241A3", "maker": "Texas Instruments",
    "package": D("TSOT-23-8", source=DS_INA),
    "gain": D(50.0, "V/V", DS_INA),
    "gain_error": D(unit="%", source=DS_INA, typ=0.002, max=0.01),
    "gain_error_drift": D(unit="ppm/K", source=DS_INA, typ=0.05, max=1.0),
    "V_os": D(unit="V", source=DS_INA, typ=3e-6, max=10e-6),
    "dV_os_dT": D(unit="V/K", source=DS_INA, typ=20e-9, max=100e-9),
    "V_CM_range": D(unit="V", source=DS_INA, min=-5.0, max=110.0),
    "CMRR_dc": D(unit="dB", source=DS_INA, min=150, typ=166),
    "CMRR_50kHz": D(105, "dB", DS_INA),
    "bandwidth": D(1.1e6, "Hz", DS_INA, note="-3 dB, all gains"),
    "slew_rate": D(8e6, "V/s", DS_INA, note="rising"),
    "settling_1pct": D(1e-6, "s", DS_INA, note="V_CM step to 48 V, 0.5..4.5 V out"),
    "settling_0p5pct": D(1.5e-6, "s", DS_INA),
    "settling_5pct": D(0.5e-6, "s", DS_INA),
    "pwm_hold": D(1e-6, "s", DS_INA,
                  note="Enhanced PWM rejection (sec.7.3.1.1): on a large "
                       "common-mode dV/dt the part HOLDS its output for 1 us. "
                       "Valid up to 125 kHz PWM or CM edges >= 3 us apart."),
    "pwm_reject_max_f": D(125e3, "Hz", DS_INA),
    "pwm_reject_min_edge_sep": D(3e-6, "s", DS_INA),
    "I_bias": D(unit="A", source=DS_INA, min=25e-6, typ=35e-6, max=45e-6),
    "swing_to_VS": D(unit="V", source=DS_INA, typ=0.07, max=0.20,
                     note="R_L = 10 k, -40..125 C"),
    "swing_to_GND": D(unit="V", source=DS_INA, typ=0.008, max=0.020),
    "noise_density": D(39e-9, "V/sqrt(Hz)", DS_INA, note="A3 grade, input referred"),
    "C_load_max": D(1e-9, "F", DS_INA),
    "I_q": D(unit="A", source=DS_INA, typ=2.5e-3, max=3.2e-3),
    "R_in_diff": D(unit="Ohm", source="not specified as a number in SBOSA30D",
                   status="bracketed", min=1e3, typ=5e3,
                   note="TI gives the input bias current (35 uA) rather than an "
                        "input resistance."),
}

# --------------------------------------------------------------- encoder ----
DS_ENC = ("MagnTek MT6701 rev 1.5, 2021-03, local copy "
          "~/pcb/rp2350-motor-controller/hardware/encoder/docs/"
          "2109011830_Magn-Tek-MT6701CT-STD_C2856764.pdf")
MODELS["mt6701"] = {
    "part": "MT6701CT-STD", "maker": "MagnTek",
    "package": D("SOP-8", source=DS_ENC),
    "B_pk_at_IC": D(unit="T", source=DS_ENC, min=20e-3, max=100e-3,
                    note="200..1000 Gauss measured at the IC surface"),
    "air_gap": D(unit="m", source=DS_ENC, min=0.5e-3, typ=1.0e-3, max=2.0e-3),
    "off_axis_max": D(0.3e-3, "m", DS_ENC),
    "magnet_recommended": D({"diameter": 6.0e-3, "thickness": 2.5e-3}, "m", DS_ENC,
                            note="the board carries Dia 8 x 2.5 (geometry.py)"),
    "rotation_speed_max": D(55000, "rpm", DS_ENC),
    "INL": D(unit="deg", source=DS_ENC, typ=1.0, max=1.5,
             note="max is over temperature, max air gap and worst-case off-axis"),
    "DNL": D(0.02, "deg", DS_ENC, note="ABZ mode"),
    "transition_noise": D(0.01, "deg_rms", DS_ENC, note="ABZ mode, 25 C"),
    "hysteresis": D(0.088, "deg", DS_ENC),
    "propagation_delay": D(5e-6, "s", DS_ENC, note="constant speed"),
    "TC_magnet_NdFeB": D(-0.12, "%/K", DS_ENC),
    "TC_magnet_SmCo": D(-0.035, "%/K", DS_ENC),
    "ssi_bits": D(14, "bit", DS_ENC),
    "lsb_deg": D(360.0 / 2 ** 14, "deg", "derived from the 14-bit SSI word",
                 status="derived"),
}

# ------------------------------------------------------------------- ADC ----
DS_RP = ("Raspberry Pi RP2350 datasheet, local copy "
         "~/pcb/RP2350/rp2350-datasheet.pdf, section 12.4 and Table 1437")
MODELS["rp2350_adc"] = {
    "part": "RP2350 SAR ADC", "maker": "Raspberry Pi",
    "resolution": D(12, "bit", DS_RP),
    "ENOB": D(unit="bit", source=DS_RP, min=9.0, typ=9.5),
    "clk_adc": D(48e6, "Hz", DS_RP, note="must be 48 MHz"),
    "conversion_cycles": D(96, "clk_adc", DS_RP),
    "conversion_time": D(2e-6, "s", DS_RP, note="96 / 48 MHz -> 500 kS/s"),
    "V_ref": D(3.3, "V", "board: ADC_AVDD from +3V3A", status="derived"),
    "R_in": D(unit="Ohm", source=DS_RP, min=100e3),
    "INL": D(unit="LSB", source=DS_RP + ' -- section 12.4.5 reads "Details to '
             'follow"; Raspberry Pi does not publish INL or DNL for the RP2350',
             status="bracketed", min=-2.0, max=2.0,
             note="bracketed at +-2 LSB, the RP2040's behaviour after the "
                  "RP2040-E11 fix; NOT a datasheet number."),
    "DNL": D(unit="LSB", source=DS_RP + " -- not published", status="bracketed",
             min=-1.0, max=1.0),
    "t_aperture": D(unit="s", source=DS_RP + " -- the split of the 96-cycle "
                    "conversion into track and convert phases is not published",
                    status="bracketed", min=1 / 48e6, max=8 / 48e6,
                    note="1..8 clk_adc cycles = 20.8..167 ns.  The sampling "
                         "model sweeps this bracket rather than picking one."),
    "round_robin": D(True, "-", DS_RP, note="CS.RROBIN interleaves channels; each "
                                            "enabled channel is refreshed every "
                                            "N x 2 us"),
}

# ------------------------------------------------------------------ TVS -----
MODELS["tpsmf4l64a"] = {
    "part": "TPSMF4L64A", "maker": "Littelfuse", "role": "PHASE_x clamp to GND",
    "V_RWM": D(64.0, "V", "Littelfuse TPSMF4L series; values carried from the "
                          "board's BOM choice, the PDF was not fetched on "
                          "2026-09-11", status="bracketed"),
    "V_BR": D(unit="V", source="same", status="bracketed", min=71.1, max=78.6),
    "V_C": D(unit="V", source="same", status="bracketed", typ=103.0,
             note="clamping voltage at I_PP"),
    "C_j": D(unit="F", source="not read", status="bracketed",
             min=150e-12, typ=300e-12, max=600e-12,
             note="SOD-123FL TVS junction capacitance at 0 V bias, swept; this "
                  "capacitance sits on PHASE_x and on the motor lead."),
    "package": D("SOD-123FL", source="board footprint"),
}
MODELS["smdj64a"] = {
    "part": "SMDJ64A", "maker": "Littelfuse", "role": "board B bus TVS",
    "V_RWM": D(64.0, "V", "Littelfuse SMDJ series", status="bracketed"),
    "V_BR": D(unit="V", source="same", status="bracketed", min=71.1, max=78.6),
    "C_j": D(unit="F", source="not read", status="bracketed",
             min=1e-9, typ=2e-9, max=4e-9,
             note="SMDJ (DO-214AB) junction capacitance is of order nF and is "
                  "part of the DC-link impedance."),
}

# --------------------------------------------------------------- shunts -----
MODELS["shunt_1m6_2010"] = {
    "part": "1.6 mOhm 2010 metal-alloy shunt", "maker": "not chosen in the BOM",
    "note": ("SPEC.md sec.7: the BOM has not picked a part.  The representative "
             "in-stock choice is a 2010 1.6 mOhm 1 % 1 W alloy shunt "
             "(Uniroyal/Walsin class); the values below are the class's."),
    "R": D(1.6e-3, "Ohm", "board value (tools/geometry.py SHUNT_EACH)"),
    "tolerance": D(1.0, "%", "class", status="bracketed"),
    "TCR": D(unit="ppm/K", source="class", status="bracketed", min=50, max=100),
    "L_esl": D(unit="H", source="not specified for 2010 alloy shunts",
               status="bracketed", min=0.2e-9, typ=0.35e-9, max=0.5e-9,
               note="SPEC.md H3's bracket.  P1 replaces this with a solved "
                    "partial inductance of the actual pad geometry."),
    "P_rated": D(1.0, "W", "class", status="bracketed"),
}

# ---------------------------------------------------------- DC-link caps ----
_CAP_NOTE = ("The BOM has not chosen a part (SPEC.md sec.7).  Representative: "
             "the Murata GRM31CR72A225 class for the 1206 and GRM188R72A104 for "
             "the 0603.  No manufacturer DC-bias curve could be retrieved on "
             "2026-09-11, so the retention at bias is a swept bracket, not a "
             "datasheet curve -- this is hypothesis H2 and the report says so.")
MODELS["cap_2u2_100v_1206"] = {
    "part": "2.2 uF 100 V X7R 1206", "maker": "not chosen in the BOM",
    "note": _CAP_NOTE,
    "C_nominal": D(2.2e-6, "F", "board value"),
    "V_rated": D(100.0, "V", "board value"),
    "retention_at_60V": D(unit="-", source="bracketed, see note", status="bracketed",
                          min=0.40, typ=0.50, max=0.70),
    "retention_at_20V": D(unit="-", source="bracketed", status="bracketed",
                          min=0.70, typ=0.80, max=0.90),
    "ESR_20kHz": D(unit="Ohm", source="class", status="bracketed",
                   min=3e-3, typ=6e-3, max=15e-3),
    "ESL": D(unit="H", source="class", status="bracketed",
             min=0.7e-9, typ=1.0e-9, max=1.5e-9,
             note="1206 two-terminal, the part's own ESL; P1 solves the mounting "
                  "loop separately."),
}
MODELS["cap_100n_100v_0603"] = {
    "part": "100 nF 100 V X7R 0603", "maker": "not chosen in the BOM",
    "note": _CAP_NOTE,
    "C_nominal": D(100e-9, "F", "board value"),
    "V_rated": D(100.0, "V", "board value"),
    "retention_at_60V": D(unit="-", source="bracketed", status="bracketed",
                          min=0.45, typ=0.60, max=0.80),
    "ESR_20kHz": D(unit="Ohm", source="class", status="bracketed",
                   min=20e-3, typ=50e-3, max=150e-3),
    "ESL": D(unit="H", source="class", status="bracketed",
             min=0.4e-9, typ=0.6e-9, max=0.9e-9),
}
MODELS["cap_1u_25v_0603"] = {
    "part": "1 uF 25 V X7R 0603 (bootstrap)", "maker": "not chosen in the BOM",
    "C_nominal": D(1e-6, "F", "board value"),
    "V_rated": D(25.0, "V", "board value"),
    "retention_at_12V": D(unit="-", source="bracketed", status="bracketed",
                          min=0.45, typ=0.60, max=0.75),
    "ESR": D(unit="Ohm", source="class", status="bracketed",
             min=10e-3, typ=30e-3, max=80e-3),
    "ESL": D(0.6e-9, "H", "class", status="bracketed"),
}

# ------------------------------------------------------------- materials ----
MODELS["materials"] = {
    "copper": {
        "sigma": D(5.8e7, "S/m", "SPEC.md sec.7, annealed copper at 20 C"),
        "sigma_plated_barrel": D(4.7e7, "S/m", "SPEC.md sec.7, plated barrel"),
        "alpha": D(0.00393, "1/K", "SPEC.md sec.7"),
        "k_thermal": D(385.0, "W/m.K", "tools/geometry.py CU_K"),
        "rho_20C": D(1 / 5.8e7, "Ohm.m", "derived", status="derived"),
    },
    "fr4": {
        "epsilon_r": D(4.5, "-", "the board file's stackup block"),
        "loss_tangent": D(0.02, "-", "the board file's stackup block"),
        "k_thermal": D(0.3, "W/m.K", "typical FR4; not in the board file",
                       status="bracketed"),
    },
    "solder_mask": {
        "epsilon_r": D(3.5, "-", "SPEC.md sec.7", status="bracketed"),
        "thickness": D(10e-6, "m", "the board file's stackup block"),
    },
    "magnet_N35": {
        "B_r": D(unit="T", source="NdFeB N35 grade", status="bracketed",
                 min=1.17, max=1.22),
        "TC": D(-0.12, "%/K", "MT6701 datasheet TCmag1"),
    },
    "magnet_N42": {
        "B_r": D(unit="T", source="NdFeB N42 grade", status="bracketed",
                 min=1.28, max=1.32),
    },
}

# ----------------------------------------------------------- interconnect ---
MODELS["header_2x5"] = {
    "part": "2 x 5 2.54 mm pin header pair", "maker": "not chosen in the BOM",
    "pins_vbus": D(10, "-", "SPEC.md sec.3.4 / spec sec.8"),
    "pins_gnd": D(10, "-", "SPEC.md sec.3.4"),
    "pin_square": D(0.64e-3, "m", "SPEC.md sec.3.4, standard 2.54 mm header"),
    "mated_length": D(6.0e-3, "m", "SPEC.md sec.3.4 assumption", status="assumed"),
    "pitch": D(2.54e-3, "m", "standard"),
    "current_per_pin": D(3.0, "A", "tools/geometry.py interconnect() default",
                         status="assumed"),
    "standoffs": {
        "n": D(6, "-", "the six M2.5 perimeter bosses"),
        "length": D(11.0e-3, "m", "tools/geometry.py GAP_AB"),
        "material": D("brass", source="SPEC.md sec.3.4"),
        "contact_R": D(unit="Ohm", source="SPEC.md sec.3.4 bracket",
                       status="bracketed", min=1e-3, max=10e-3),
    },
}

# ----------------------------------------------------------------- diodes ---
MODELS["d101_bootstrap"] = {
    "part": '"100 V fast" SOD-323 bootstrap diode',
    "maker": "NOT CHOSEN -- the schematic carries no part number",
    "note": ("SPEC.md sec.7 flags this.  Representative: a 100 V 1 A fast "
             "recovery part in SOD-323 (RS1M/ES1J class).  Values are the "
             "class's, not a chosen part's."),
    "V_R": D(100.0, "V", "board requirement", status="assumed"),
    "V_f": D(unit="V", source="class", status="bracketed", typ=0.9, max=1.2),
    "t_rr": D(unit="s", source="class", status="bracketed", typ=35e-9, max=75e-9),
    "C_j": D(unit="F", source="class", status="bracketed", typ=15e-12),
}

# ------------------------------------------------ board S's chosen parts ----
# Board S (hardware/single_board) carries parts board A's run did not know:
# the chosen 54 V clamps, the bulk cans on the board itself, and the 2 mOhm
# shunts sourcing forced.  Read 2026-09-24 from the LCSC/JLCPCB listings of the
# C-numbers in hardware/parts/lcsc.csv and the datasheets they link (local
# copies in sim/work/datasheets/, not committed: vendor PDFs).
READ_S = "2026-09-24"
_LCSC = "https://wmsc.lcsc.com/wmsc/upload/file/pdf/v2/lcsc/"
DS_TPS54 = ("Littelfuse TPSMF4L54A, LCSC C1973452 listing: 54 V V_RWM, "
            "66.3 V V_BR max, 87.1 V V_C at 4.6 A, 400 W; V_BR min = "
            "V_BR max / 1.105 per the series' 10 % window")
DS_SMDJ54 = ("SMDJ54A (Shandong Jingdao), LCSC C438170 listing: 54 V V_RWM, "
             "66.3 V V_BR max, 87.1 V V_C at 34.4 A, 3 kW")
MODELS["tpsmf4l54a"] = {
    "part": "TPSMF4L54A", "maker": "Littelfuse", "role": "PHASE_x clamp to GND (board S)",
    "V_RWM": D(54.0, "V", DS_TPS54, read=READ_S),
    "V_BR": D(unit="V", source=DS_TPS54, read=READ_S, min=60.0, max=66.3),
    "V_C": D(unit="V", source=DS_TPS54, read=READ_S, typ=87.1,
             note="clamping voltage at I_PP = 4.6 A"),
    "C_j": D(unit="F", source="not in the listing", status="bracketed",
             read=READ_S, min=150e-12, typ=300e-12, max=600e-12,
             note="as the 64 V grade's bracket; a lower-voltage grade of the "
                  "same die area has somewhat more"),
    "package": D("SOD-123FL", source="board footprint", read=READ_S),
}
MODELS["smdj54a"] = {
    "part": "SMDJ54A", "maker": "Shandong Jingdao (LCSC C438170)",
    "role": "bus TVS, on board S under the XT30",
    "V_RWM": D(54.0, "V", DS_SMDJ54, read=READ_S),
    "V_BR": D(unit="V", source=DS_SMDJ54, read=READ_S, min=60.0, max=66.3),
    "V_C": D(unit="V", source=DS_SMDJ54, read=READ_S, typ=87.1,
             note="at I_PP = 34.4 A (10/1000 us)"),
    "P_PP": D(3000.0, "W", DS_SMDJ54, read=READ_S, note="10/1000 us"),
    "C_j": D(unit="F", source="not in the listing", status="bracketed",
             read=READ_S, min=1e-9, typ=2e-9, max=4e-9),
}
DS_WSLP = (_LCSC + "2304140030_Vishay-Intertech-WSLP20102L000FEA_C413487.pdf "
           "(Vishay Dale WSLP, rev. of the LCSC copy)")
MODELS["shunt_2m0_2010"] = {
    "part": "WSLP20102L000FEA, 2 mOhm 2010 metal strip", "maker": "Vishay Dale",
    "note": ("Board S's shunt (LCSC C413487): no 1.6 mOhm 2010 is stocked, "
             "so each sensed phase has 2 x 2 mOhm = 1.0 mOhm."),
    "R": D(2.0e-3, "Ohm", DS_WSLP, read=READ_S),
    "tolerance": D(1.0, "%", DS_WSLP, read=READ_S),
    "TCR": D(unit="ppm/K", source=DS_WSLP, read=READ_S, min=-275, max=275,
             note="component TCR including the copper terminals, 1-2.9 mOhm; "
                  "the element alloy alone is < 20 ppm/K"),
    "L_esl": D(unit="H", source=DS_WSLP + ": 'very low inductance 0.5 nH to "
               "5 nH' across the family", status="bracketed", read=READ_S,
               min=0.5e-9, typ=0.8e-9, max=1.5e-9,
               note="the family's floor for the smallest parts; a 2010 is "
                    "among the smallest the series makes"),
    "P_rated": D(2.0, "W", DS_WSLP, read=READ_S),
}
DS_CAN = (_LCSC + "2109021730_NJCON-1011001013R00_C2887236.pdf -- Nanjing "
          "Winner PH series, part 1011001013R00, 100 V 100 uF D10 x 12")
MODELS["cap_100u_100v_polymer"] = {
    "part": "1011001013R00, 100 uF 100 V conductive-polymer aluminium, D10 x 12",
    "maker": "Nanjing Winner (NJCON), LCSC C2887236",
    "C_nominal": D(100e-6, "F", DS_CAN, read=READ_S, note="+-20 % at 120 Hz"),
    "V_rated": D(100.0, "V", DS_CAN, read=READ_S),
    "ESR": D(unit="Ohm", source=DS_CAN, read=READ_S, status="bracketed",
             min=0.020, typ=0.030, max=0.035,
             note="the datasheet's 35 mOhm is a maximum at 100 kHz, 20 C; "
                  "20-40 kHz, where the ripple is, sits a little above it on "
                  "a polymer part's curve, so max is the number to design with"),
    "ripple_rated_A_rms": D(2.5, "A", DS_CAN, read=READ_S,
                            note="at 105 C and 100 kHz"),
    "ripple_freq_factor_20kHz": D(unit="-", source="not in the datasheet (no "
                                  "frequency-multiplier table)",
                                  status="bracketed", read=READ_S,
                                  min=0.6, typ=0.75, max=0.9,
                                  note="polymer parts' usual 10-50 kHz "
                                       "multipliers against the 100 kHz rating"),
    "ESL": D(unit="H", source="not in the datasheet", status="bracketed",
             read=READ_S, min=4e-9, typ=6e-9, max=9e-9,
             note="a D10 radial on 5 mm lead spacing, leads trimmed flush"),
    "leakage_A": D(unit="A", source=DS_CAN, read=READ_S, max=1000e-6),
}


def main():
    for name, data in MODELS.items():
        p = HERE / f"{name}.json"
        p.write_text(json.dumps(data, indent=1))
        print("wrote", p.name)


if __name__ == "__main__":
    main()
