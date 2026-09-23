# servodrive — electromagnetic simulation specification

**For:** the agent that will build and run the simulation (Opus).
**Written:** 2026-09-11, from board A as routed on 2026-09-09 (130 parts, 84 nets,
413 connections, DRC and ERC clean) and from the sibling project
`~/pcb/rp2350-motor-controller` (firmware, tuner, findings register).
**Owner:** Sequoia Hope Alexander. Licence of everything produced: CERN-OHL-P, like the board.

---

## 0. Read this first

### 0.1 What this is

A plan for a comprehensive electromagnetic simulation of the servodrive motor board
(board A) and of its power link to board B, with the drive's firmware and its motor
modelled well enough that every electromagnetic result lands on a decision about the
board, the firmware, or the enclosure. It is not a research project. Every phase ends in a
number, a figure, a pass/fail against a stated criterion, and a hand-back.

The board has been designed on closed-form estimates that live in `tools/geometry.py`
(loop inductance, ripple split, loss budget, sense full scale). The simulation's job is to
replace each of those with a solved number, say by how much the estimate was wrong, and
say what changes if it matters. Appendix A lists the estimates and their arithmetic.

### 0.2 What "done" means

1. `sim/` exists in this project and `python3 sim/run.py` reproduces everything —
   results JSON, figures, report — from the board file and the constants, on this
   machine, with no hand steps.
2. Every question in §4 has an answer in `sim/results/<phase>.json` with a figure, the
   method, the validation test that passed for the solver used, the convergence
   evidence, and a disposition in `sim/FINDINGS.md` (register format in §8.3).
3. A static report `sim/report/index.html`, linked from the project's `index.html`,
   viewed through `proj up servodrive` and `proj url servodrive`.
4. A hand-back list (§8.4): exact constants to move in `tools/geometry.py`,
   `tools/placement.py`, `tools/fanout.py`, plus firmware recommendations, each tied to a
   result.

### 0.3 Ground rules — not negotiable

- **No claude.ai Artifacts.** Reports are static files in the project, served the
  registered way. Never start an ad-hoc HTTP server; never hardcode a port. The project is
  already registered with `proj` (`~/scripts/launcher.toml`, entry `servodrive`). The port
  comes from `--port`, then `$PORT`, then `proj port`, then fail loudly.
- **Never edit `hardware/` by hand and never run `python3 tools/gen_boards.py --force`**
  — it throws away the routing. The board is generated: any change the simulation wants is
  proposed as an edit to a constant in `tools/*.py` (hand-back list), never as an edit to
  the `.kicad_pcb`. `hardware/` is not under git. Copy the board into `sim/work/` before
  anything that writes it (zone refills, pcbnew scripts that save).
- **The RP2350 regulator cluster and the 1V1 ring follow Raspberry Pi's RP-006440
  reference** and are not up for change.
- Never `pkill -f` a pattern that appears anywhere in your own command line (it has killed
  the shell four times on this project).
- **Cite every component value.** Datasheet numbers go in `sim/models/<part>.json` with the
  source URL and the date read. If a value cannot be found, say so and bracket it; never
  silently pick a typical.
- **Every solver gets a known-answer test before it is believed** (§5.4). Convergence in
  mesh, grid, or time step is reported with numbers, not assumed.
- **Report faithfully.** A failed toolchain build, a solver that will not converge, a
  criterion that is not met: all of these are findings, written down as such.
- The motor is deliberately undefined in the design (spec §11, question 1). Do not pick
  one. Model it as a parameter set and sweep it (§3).

---

## 1. The design under simulation

### 1.1 Source of truth

| What | Where |
|---|---|
| Board A layout, routed, zones filled (138 filled polygons in the file) | `hardware/motor_board/servodrive_A.kicad_pcb` — KiCad 9, 2.9 MB |
| Board A schematic (generated; four sheets) | `hardware/motor_board/*.kicad_sch` |
| Every dimension, the design point, the analytic model | `tools/geometry.py` — `python3 tools/geometry.py` prints the model (it also regenerates `img/*.svg`) |
| Every part position, polar, and the floorplan checks | `tools/placement.py` — `board_a()` returns `Part` objects with `.ref .value .fp .layer .x .y .r .ang .rot .note .dnp .block` |
| The netlist as intended | `tools/schematic.py` — `nets()` |
| A copper rasteriser at 0.05 mm (PIL + distance transform) | `tools/finish.py` — reuse it |
| Layer SVGs in register and `layers.json` | `img/layers/a/`, made by `tools/plot_layers.py` |
| KiCad Python | `import pcbnew` works on the system `python3` (3.14.4, pcbnew 9.0.8); `kicad-cli` 9.0.8 |
| The specification, draft 0.5 | `spec.html`; the README carries the routing story |
| Sibling firmware | `~/pcb/rp2350-motor-controller/firmware/src/main.cpp`, `tune.py`, `tuned_gains.txt`, `patches/simplefoc_rp2040_current_sense.{h,cpp}`, `CLAUDE.md`, `platformio.ini` |
| Sibling reviews | `~/pcb/rp2350-motor-controller/review/{findings,power,gan,schematic}.html` |
| MT6701 datasheet | `~/pcb/rp2350-motor-controller/hardware/encoder/docs/2109011830_Magn-Tek-MT6701CT-STD_C2856764.pdf` |
| EG3113 datasheet (the sibling's driver, for comparison) | `~/pcb/ebike_controller/rp2040-motor-controller/docs/gate_drive/` |

Board origin: KiCad (148, 105) mm is the shaft axis and the MT6701 sensing centre; aux
and grid origins are set there. Angles in the tools are measured from +x,
counter-clockwise, top view (outward face). B.Cu is the motor-facing face.

### 1.2 Geometry

| Item | Value |
|---|---|
| Board | Ø65.0 mm, 1.6 mm, 6 layers, copper pullback 1.0 mm (R_USABLE 31.5) |
| Motor mount | 4 × Ø3.2 **NPTH** at (±12.5, 0), (0, ±9.5). No copper: not a ground bond |
| Perimeter bosses | 6 × M2.5 at R 29.5, angles 0/68/136/204/256/308°; Ø4.8 copper pads on every layer, mask open both sides. Standoff, heat path and chassis-ground bond (the aluminium ring clamps here) |
| Sections | Phase cells A, B, C of 68°, centred at **34°, 102°, 170°**; utility wedges of 52° centred at 230° (power link), 282° (CPU), 334° (signal link) |
| Heatsink land | Bare F.Cu, R 29.2–31.5, one ~56° arc per phase cell plus the boss pads. Nothing but GND may be under it |
| Lead pads | `PhasePad_Arc` on B.Cu, ≈ R 28.5–31.3, ±7° about the cell axis; 14 AWG leads |
| Centre | Nothing but the encoder inside R 16; MT6701 (SOIC-8) at (0, 0) on B.Cu; Ø12 magnet keepout on the motor side |
| Magnet | Ø8 × 2.5 mm diametric (geometry.py; the MT6701 datasheet recommends Ø6 × 2.5), air gap 1.5 mm (datasheet 0.5–2.0, typ 1.0) |
| Stack | Motor rear face → board A 10.75 mm (5.0 shaft + 2.5 magnet + 1.5 gap + 1.75 body); board A → board B 11.0 mm on the six bosses |

### 1.3 Stackup, from the board file

| Layer | Thickness | Content |
|---|---|---|
| F.Cu | 70 µm (2 oz) | FET drains and switch-node islands, DC-link pads, gate drive, GND pour |
| prepreg | **0.100 mm**, FR4 εr 4.5, tanδ 0.02 | the number the 0.39 nH estimate rests on |
| In1.Cu | 35 µm | **GND, solid** |
| core | 0.367 mm | |
| In2.Cu | 35 µm | **VBUS sector** under the phase cells; **+3V3** on the centre disc and CPU wedge; GND elsewhere |
| core | 0.367 mm | |
| In3.Cu | 35 µm | signals + GND fill (the only signal layer that crosses the board) |
| core | 0.367 mm | |
| In4.Cu | 35 µm | **GND, solid** |
| prepreg | 0.100 mm | |
| B.Cu | 70 µm (2 oz) | switch-node pours (to R 20), phase-output pours and lead pads, shunts, sense amplifiers, encoder |

Mask 10 µm per side. Via plating 25 µm. JLCPCB 6-layer, via-in-pad filled and capped
(POFV) — treat the fill as non-conductive; the barrel conducts.

Design rules: 0.15 mm track and clearance; **0.4 mm clearance on the 60 V nets** (class
Phase: `VBUS`, `SW_*`, `PHASE_*`); vias 0.5/0.20 signal, 0.6/0.30 power, 0.8/0.40 phase;
hole-to-hole 0.5 mm; FET thermal vias 16 per drain pad, 0.4 mm drill, 0.8 mm pad, 0.95 mm
pitch.

### 1.4 Nets that matter

Power: `VBUS`, `GND`, `+12V` (gate rail, from board B), `+5V`, `+3V3` (plane on In2),
`+3V3A`, `+1V1`.

Per phase X ∈ {A, B, C}: `HIN_X`, `LIN_X` (CPU → driver); `HO_X`, `LO_X` (driver → gate
resistor); `GH_X`, `GL_X` (gate resistor → gate, with the 10 k bleed); `VB_X` (bootstrap);
`SW_X` (switch node: F.Cu island between the FETs, B.Cu pour reaching in to R 20 with
half-width 10 mm); `PHASE_X` (B.Cu pour R 25.9–28.9 across ±11.5 mm plus the lead pad);
`SNSP_X`, `SNSN_X` (sense taps); `ISENSE_A`, `ISENSE_B` (amplifier out → ADC0, ADC1).

Sense and CPU: `VBUS_SENSE` (ADC2), `FET_TEMP` (ADC3), `ENC_SCK/CS/DO/OUT` (SSI, on the
same board — no line drivers), `USB_DP/DM`, `RS485_*`, `SWCLK/SWDIO`, `RUN`.

**Where the 20 A goes.** Confirmed with pcbnew on 2026-09-11: `VBUS` has exactly one
zone, on In2, and no tracks. The high-side drain pads reach it only through their 16-via
arrays (footprint pads with drills, not PCB vias) and the DC-link capacitor pads through
via-in-pad (13 PCB vias on VBUS in total). `GND` from the low-side source pads goes down
to In1 by via-in-pad. So the commutation loop is **not** "F.Cu over In1 at 0.1 mm"
throughout: its VBUS leg runs on In2, 0.5 mm below F.Cu, with In1 in between, and enters
F.Cu through a 4 × 4 via field whose antipads perforate the In1 return plane. That is
hypothesis H1 in §4.

### 1.5 Phase cell A, as placed

From `tools/placement.py` on 2026-09-11. Radius in mm from the shaft axis, angle in
degrees from +x, rotation as placed. Cells B and C are the same, rotated by +68° and
+136°. Bolts at 0° and 68°; cell axis 34°.

| Ref | Value | Layer | R | Angle | Rot | Role |
|---|---|---|---|---|---|---|
| C101 | 2.2 µF / 100 V, 1206 | F.Cu | 24.80 | 5.26 | 0 | DC-link bulk ceramic, behind the 100 n |
| C103 | 100 n / 100 V, 0603 | F.Cu | 24.80 | 10.30 | 0 | DC-link HF, at the high-side drain |
| Q1 | BSC030N08NS5 | F.Cu | 26.10 | 21.60 | −90 | **high side** (drain = VBUS via 16 vias to In2; source = SW) |
| Q2 | BSC030N08NS5 | F.Cu | 26.10 | 46.40 | −90 | **low side** (drain = SW; source = GND via-in-pad to In1) |
| C104 | 100 n / 100 V, 0603 | F.Cu | 24.80 | 57.70 | 0 | DC-link HF, at the low-side source |
| C102 | 2.2 µF / 100 V, 1206 | F.Cu | 24.80 | 62.74 | 0 | DC-link bulk ceramic |
| U1 | EG2103, SOIC-8 | F.Cu | 19.90 | 34.00 | 90 | gate driver, on the cell axis |
| R101 | 2.2 Ω, 0402 | F.Cu | 19.90 | 48.19 | 90 | gate, high side (damping, not edge shaping) |
| R102 | 2.2 Ω, 0402 | F.Cu | 19.90 | 54.41 | 90 | gate, low side |
| C105 | 1 µF / 25 V, 0603 | F.Cu | 19.90 | 60.06 | 0 | bootstrap; SW pad over the pour, via |
| R111 | 4.7 k, 0402 | F.Cu | 19.90 | 19.81 | 90 | HIN pull-down to GND |
| R112 | 4.7 k, 0402 | F.Cu | 19.90 | 13.59 | 90 | LIN pull-up to +3V3 (coast at reset) |
| C106 | 100 n, 0603 | F.Cu | 19.90 | 7.94 | 0 | VCC decoupling at the driver |
| D101 | "100 V fast", SOD-323 | B.Cu | 19.55 | 64.77 | 0 | bootstrap diode (no part number yet) |
| R109 | 10 k, 0603 | B.Cu | 20.85 | 49.58 | 0 | G–S bleed, high side, via through both pads |
| R110 | 10 k, 0603 | B.Cu | 23.90 | 55.80 | 0 | G–S bleed, low side |
| R105 | 1.6 mΩ, 2010 | B.Cu | 24.85 | 35.50 | −1.5 | inline shunt, SW pad over the SW pour, PHASE pad in the PHASE pour |
| R106 | 1.6 mΩ, 2010 | B.Cu | 24.85 | 27.66 | 6.3 | inline shunt, parallel with R105 (0.8 mΩ) |
| R107 | 10 Ω, 0603 | B.Cu | 19.30 | 20.67 | 0 | sense tap, SW side (SNSP) |
| R108 | 10 Ω, 0603 | B.Cu | 25.00 | 51.53 | 180 | sense tap, PHASE side (SNSN) |
| C108 | 1 n, 0603 | B.Cu | 19.30 | 25.90 | 0 | differential filter SNSP–SNSN |
| U4 | INA241A3, TSOT-23-8 | B.Cu | 19.30 | 34.00 | 180 | 50 V/V, 1.65 V reference |
| D102 | TPSMF4L64A, SOD-123FL | B.Cu | 29.80 | 46.50 | 90 | phase clamp to GND, beside the lead pad |
| R901 | 10 k NTC, 0603 | B.Cu | 18.50 | 15.11 | 180 | FET temperature (cell A only) → ADC3 |
| J1 | PhasePad_Arc | B.Cu | ≈28.5–31.3 | 34 ± 7 | | lead pad |

The cell's topology: the two 2.2 µF sit at the two ends of the cell (5° and 63°), the two
100 n beside the high-side drain and the low-side source (10° and 58°), the FETs at 21.6°
and 46.4° with the switch-node island between them. The high-frequency loop therefore spans
roughly 48° of arc at R 24.8–26.1 (≈ 21 mm), VBUS-fed from In2, GND-returned on In1.

Netlist of the cell (from `tools/schematic.py`): Q1 pins 1–3 = SW, 4 = GH, 5 = VBUS;
Q2 pins 1–3 = GND, 4 = GL, 5 = SW; C101–C104 VBUS–GND; R105/R106 SW–PHASE (cell C:
0 Ω); D102 PHASE–GND; U4 pin 1 SNSN, 2–4 GND, 5 ISENSE, 6–7 +3V3, 8 SNSP; R107 SNSP–SW;
R108 PHASE–SNSN; C108 SNSP–SNSN; R109 GH–SW, R110 GL–GND; R101 HO–GH, R102 LO–GL; C106 +12V–GND; R111 HIN–GND;
R112 LIN–+3V3.

### 1.6 What is *not* on the board

- **No RC snubbers** on the switch nodes (the sibling has them; this board decides after
  measurement — this simulation is that measurement).
- **No PNP turn-off booster and no gate clamp diodes.** Spec §3 says their footprints were
  kept; the captured power-stage sheet has none. Treat the spec as stale on that point and
  say so in the findings.
- **No RC between the INA241 output and the ADC pin** and no anti-alias filter anywhere in
  the current-sense chain.
- **No fourth half-bridge** → no brake chopper. Regen has only board B's bulk and the TVS.
- **Board B is an outline** and five empty sheets: bulk, bus TVS, both LMR38010 bucks, the
  LTC4359 ideal diode, USB-C, RS-485. Model it lumped (§3.4).
- No Y capacitors; no frame bond except the six M2.5 bosses.

---

## 2. Operating conditions and the firmware model

### 2.1 Design point (`tools/geometry.py`)

| Quantity | Value |
|---|---|
| Bus | 60 V nominal; 80 V silicon; phase TVS TPSMF4L64A (64 V standoff, breakdown 71–79 V); board B bus TVS SMDJ64A |
| Phase current | **20 A RMS continuous per phase**; 28.3 A peak; mean rectified 18.0 A |
| Switching | 20 kHz, three half-bridges, centre-aligned PWM |
| Sense | 0.8 mΩ × 50 V/V = 40 mV/A about 1.65 V; ±36.2 A full scale (INA241 worst-case swing V_S − 0.2 V). Precision mode: one shunt desoldered, 1.6 mΩ, 80 mV/A, ±18.1 A |
| Board A loss | 10.56 W: conduction 5.69, switching 3.84, shunts 0.64, gate 0.14, logic 0.26. At 10 A RMS: 4.12 W |
| Edge rates (driver-limited) | t_on = Q_gd / 0.3 A = 65 ns; t_off = Q_gd / 0.6 A = 32.5 ns; di/dt = 28.3 A / 32.5 ns = 0.87 A/ns; dV/dt ≈ 60 V / 50 ns ≈ 1.2 kV/µs |
| Thermal path | via array 7.6 K/W per FET; spreading 1.0 K/W; ring joint 0.4 K/W; ring to ambient must be ≤ 6.8 K/W (≥ 98 cm²) for T_j ≤ 125 °C |
| Power sources | XT30 60 V battery, or USB-PD 20 V into VMOT through the LTC4359; pre-contract 5 V cannot raise the 12 V gate rail |

Operating envelope to sweep: V_bus ∈ {20, 48, 60} V; I_phase ∈ {0, 5, 10, 20} A RMS with
28.3 A peak edges; T_j ∈ {25, 125} °C; edge speed nominal and 2× faster (a stronger driver
is the spec's named escape).

### 2.2 The firmware, as inherited from the sibling

All read from `~/pcb/rp2350-motor-controller/firmware` on 2026-09-11. SimpleFOC 2.3.5
(`askuric/Simple FOC@^2.3.5`), arduino-pico (earlephilhower) core, `build_type = debug`,
`lib_ignore = FreeRTOS`, `-DSIMPLEFOC_PWM_LOWSIDE_ACTIVE_HIGH=false` (active-low LIN —
same convention on EG2103).

| Item | Sibling value | Source |
|---|---|---|
| Driver | `BLDCDriver6PWM`, `pwm_frequency = 20000`, `dead_zone = 0.02` (2 % of the period = **1.0 µs per edge, in software**, applied in `swDti()`), `voltage_power_supply` read from the VMOT ADC (30 V initial) | `main.cpp:718-720`, SimpleFOC `rp2040_mcu.cpp:218-242` |
| PWM mode | `pwm_set_phase_correct(slice, true)` — centre-aligned; wrap and clkdiv from the frequency | `rp2040_mcu.cpp:76-78` |
| Modulation | `foc_modulation` never set → **SinePWM** (not SVPWM), `modulation_centered = 1` (phases centred on V_supply/2) | `main.cpp` (absent), SimpleFOC defaults |
| Torque control | `TorqueControlType::foc_current`; motion `MotionControlType::velocity` | `main.cpp:593-594` |
| Current loop, tuned 2026-05-15 | P = 0.36, I = 1.8, output_ramp 0, LPF Tf = 5 ms (both Iq and Id) | `tuned_gains.txt` |
| Current loop, code defaults (MT6701 build) | P = 0.6, I = 0.3, Tf = 20 ms | `main.cpp:595-600` |
| Velocity loop, tuned | P = 0.708, I = 3.185, D = 0, output_ramp 200, Tf = 10 ms | `tuned_gains.txt` |
| Limits (bench) | `voltage_limit` 4 V, `current_limit` 4 A (clamped below the sense saturation, `CURRENT_LIMIT_MAX_A = 0.98 × 1.65 V / (R_shunt × 20)`), `velocity_limit` 20 rad/s, `voltage_sensor_align` 1.0 V | `main.cpp:587-592` |
| Loop rate | `loopFOC()` + `move()` ≈ **20 kHz** when nothing prints; 407 Hz when a CSV row prints per iteration | `CLAUDE.md` |
| Current sense (sibling) | `InlineCurrentSense(SHUNT, 20, …)`, INA240A1 20 V/V, 20 mΩ (±4.1 A) or 10 mΩ; gains and phase mapping found by `driverAlign()` and stored in LittleFS | `main.cpp:2111` |
| ADC engine | RP2350 ADC free-running at `clkdiv 0` (≈ **500 ksps**), **round-robin over the enabled channels** (Ia, Ib, Ic, VMOT = 4 → each channel refreshed every ≈ 8 µs), 12-bit, 3.3 V, DMA ring buffer, **no synchronisation to the PWM**, no averaging; `loopFOC()` reads whatever the last conversion was | `patches/simplefoc_rp2040_current_sense.cpp` |
| Bus guard | fold `current_limit` to zero across 63 → 66 V, restore below; runs every `loop()` at ≈ FOC rate | `main.cpp:104-105, 449-490` |
| Brake chopper | 4th leg, 15 Ω / 100 W, on 64 → 66 V, 95 % max duty, 300 J budget — **not available on servodrive** | `main.cpp:106-113` |
| VMOT sense | 100 k / 5.1 k divider (servodrive: 2 × 56 k / 4.7 k → 2.42 V at 60 V) | `main.cpp:72-74` |
| Reset state | sibling: 10 k pull-downs on both driver inputs → **low sides on, motor shorted** at every reset; servodrive: 4.7 k up on LIN → **coast** | spec §9 |
| Alignment | `voltage_sensor_align` reduced 2.0 → 1.0 V because 2.0 V saturated a ±4.1 A sense chain | `main.cpp:588` |
| Encoder | MT6701, 14-bit SSI over SPI0 (sibling: through SIT3088 transceivers, response shifted 2 bytes/1 bit; servodrive: on the same board, no shift) | `CLAUDE.md` |

**Servodrive deltas to apply in the model.** GPIO 8/9, 10/11, 12/13 are PWM slices 4, 5,
6 with both channels at the same duty (EG2103 inverts LIN internally; `B_INV` clear).
ADC0/1 = I_a/I_b, ADC2 = V_bus, ADC3 = FET NTC; I_c = −(I_a + I_b). INA241A3 at 50 V/V on
0.8 mΩ → 40 mV/A. No fourth leg. EG2103 provides interlock and a 560 ns internal dead time
(turn-on delay 780 ns vs turn-off 220 ns, so the effective dead time is that asymmetry;
input pulses shorter than ≈ 560 ns produce no output → duty 0–1.1 % and 98.9–100 %
unreachable at 20 kHz). The software `dead_zone` can therefore go to ≈ 0, and whether it
should is Q9.

### 2.3 The sampling model the simulation must reproduce

- Conversion: 96 ADC clocks at 48 MHz = 2 µs per sample; free-running; N channels in
  round-robin; a channel's value is 0–(2N) µs stale when `loopFOC()` reads it.
- No anti-alias filter: the INA241 (bandwidth ≈ 1 MHz class — take the A3 grade's number
  from the datasheet) drives the ADC pin directly. Every switching transient that has not
  settled when a conversion lands on it is a corrupted sample. With six switch-node edges
  per 50 µs period and a sampling aperture t_ap per conversion (the SAR's track phase, a
  few ADC clocks — take it from the RP2350 datasheet, not the full 96-cycle conversion),
  the fraction of corrupted samples is ≈ 6 × (t_settle + t_ap) / 50 µs unless sampling is
  synchronised. The system model (§5, level 4) must carry this statistically, with the
  transient waveform from level 3.
- Quantisation 12 bit over 3.3 V: 0.806 mV = 20 mA per LSB at 40 mV/A (10 mA in precision
  mode). RP2350 ADC DNL/INL: take from the RP2350 datasheet; report but do not model
  beyond ±1 LSB unless it shows up.
- A PWM-synchronised alternative (RP2350: a DMA channel paced by the PWM slice's wrap DREQ
  writing `START_ONCE` to ADC_CS, one conversion per phase at the counter's zero, when all
  low sides are on and the current equals its average) is the firmware recommendation the
  simulation should either justify or dismiss with numbers (Q8).

---

## 3. Motor model

The motor is a parameter set, not a part. Two named sets, plus sweeps.

### 3.1 Set "bench" — what the sibling actually ran

Derived, with the derivation shown so it can be redone:

| Parameter | Value | How derived |
|---|---|---|
| Pole pairs | **11** | `#define POLE_PAIRS 11` in `main.cpp:117` (both configs) |
| Phase resistance R (line-to-neutral, as SimpleFOC sees it) | **0.25–0.5 Ω** | `voltage_sensor_align` 2.0 V saturated the ±4.1 A chain and 1.0 V did not (`main.cpp:588-590`, `CLAUDE.md`), so I(2 V) ≳ 4.1 A and I(1 V) ≲ 4.1 A |
| R, second bracket | 0.01–0.15 Ω | tuner: K_i = ω_c·R with K_i = 1.8 and ω_c ∈ {200, 100, 50, 25, 12.5} rad/s (`tune.py:354-470` halves the bandwidth on overshoot) — inconsistent with the row above; the tuner's DC-gain estimate is unreliable with the 5 ms LPF and serial-throttled loop in the path. Use the row above; note the disagreement |
| Phase inductance L | **unknown; sweep 0.1–2 mH** | the tuner's L = τ·(R + P) includes the 5 ms filter time constant and is not a motor property |
| Back-EMF constant | unknown; sweep so that rated speed at `velocity_limit` 20 rad/s uses ≤ 4 V | `voltage_limit` 4 V, `velocity_limit` 20 rad/s |
| Supply on the bench | 15–30 V (`DEMO_MIN_VMOT` 15 V; `voltage_power_supply` 30 V initial) | `main.cpp:219, 720` |
| Current | ≤ 4 A (`current_limit`), shunts 20 mΩ | `main.cpp:591` |

This set exercises the control model at the sibling's gains and confirms the system model
against the sibling's measured loop behaviour (≈ 20 kHz loop, `tuned_gains.txt`). It does
not stress the power stage.

### 3.2 Set "design point" — what the servodrive is built for

20 A RMS at 60 V with SinePWM (phase amplitude ≤ V_bus/2 = 30 V → 21.2 V RMS per phase,
≈ 1.27 kW at unity power factor). A plausible 1 kW-class BLDC for that operating point,
stated as ranges to sweep, **not** as a chosen motor:

| Parameter | Range | Rationale |
|---|---|---|
| Pole pairs | 7, 11, 14, 21 | 11 from the sibling; the others bracket common outrunners |
| R (line-to-neutral) | 20–60 mΩ | I²R at 20 A = 8–24 W per phase, 2–6 % of output |
| L (line-to-neutral) | 20–150 µH | L/R ≈ 0.5–3 ms |
| Back-EMF | such that 21 V RMS phase is reached at rated speed; sweep rated speed 1 000–4 000 rpm | sets the electrical frequency 120–1 500 Hz → current-loop and sampling ratios |
| Winding-to-frame capacitance | 100 pF – 2 nF | common-mode path (Q12) |
| Lead length, board → motor | 0.05–0.5 m | radiated and CM (Q11, Q12) |
| Rotor stray field at the shaft end | 0–5 mT, dipole-like, rotating with the rotor | encoder (Q10) |
| Inertia J, damping | sweep 1e-4 – 1e-2 kg·m², small viscous term | velocity loop only |

### 3.3 Electrical model of the motor for levels 3 and 4

Three-phase wye, per-phase R and L (constant, no saturation — say so), sinusoidal
back-EMF with the electrical angle from the mechanical model, neutral floating.
Level 3 (SPICE) uses it as the load of one cell with the other two phases as ideal
sources at the commanded duty; level 4 (system) uses the dq model with the same numbers.

### 3.4 Board B, lumped

| Element | Model | Basis |
|---|---|---|
| Bulk | 4 × 100 µF / 100 V polymer-hybrid: C 100 µF each, ESR 20–40 mΩ each, ESL 3–5 nH each; sweep 3 × 100 µF electrolytic (ESR 0.2 Ω, the spec's original) as the bad case | spec §8 |
| Bus TVS | SMDJ64A: capacitance from datasheet, clamp 71–79 V | spec §7 |
| Power link | 2 × (2×5) 2.54 mm headers, 10 VBUS + 10 GND pins; mated pin length from the chosen header (assume 6 mm mated, 0.64 mm square pins) → extract L and R per pin pair with the same solver as the board; plus six M2.5 brass standoffs (11 mm) in parallel with the ground pins — model each as a wire with a contact resistance 1–10 mΩ | spec §8 |
| Bucks | LMR38010 ×2: constant-power loads 1.2 W (12 V × 0.1 A) and 2.5 W (5 V × 0.5 A) with input ceramics 4.7 µF each; not switched in the model unless a bus-ripple result asks for it | spec §7 |
| Source | XT30: 60 V behind 50 mΩ + 1 µH (cable); or USB-PD 20 V behind the LTC4359 | spec §7 |

---

## 4. Questions the simulation must answer

Each question names the current estimate, the method (level, §5.1), the output, the
pass/fail criterion, and the decision it feeds. Hypotheses H1–H4 are things this spec
suspects are wrong in the design's own arithmetic; test them explicitly and report the
verdict either way.

**H1 — the commutation loop is bigger than 0.39 nH.** `geometry.commutation_loop()` models
a 15.4 × 5 mm strip 0.1 mm above In1. But VBUS reaches the high-side drain from In2, 0.5 mm
below F.Cu, through 16 vias whose antipads perforate In1; the cell's HF loop spans ≈ 21 mm
of arc. Expect the PCB part to be 0.8–1.5 nH rather than 0.39, total ≈ 2.5–3 nH, overshoot
≈ 2–3 V rather than 1.7. Still fine if so — but the number in the spec is wrong.

**H2 — the DC-link ceramics are not 13 µF at 60 V.** Six 2.2 µF / 100 V 1206 X7R at
60 V bias typically retain 40–60 %. The interconnect ripple split (spec §8) assumed 13 µF
and got 23.2 A RMS on a 30 A link; at 6 µF the share crossing the header rises. Also the
100 n / 100 V 0603s at 60 V.

**H3 — the shunt's ESL dominates the sense signal during edges.** Full-scale signal at
28.3 A is 22.6 mV across 0.8 mΩ. A 2010 shunt pair has ESL of order 0.2–0.5 nH; at
0.87 A/ns that is 170–430 mV of L·di/dt — 8–20× the signal — before the 10 Ω + 10 Ω / 1 nF
filter (f_c ≈ 8 MHz) and the INA241's bandwidth. Plus mutual inductance from the
commutation loop into the tap loop (the taps are 5 mm apart in radius, on opposite sides
of the cell).

**H4 — the two sense taps are not symmetric.** R107 (SNSP, at R 19.3, 20.7°) and R108
(SNSN, at R 25.0, 51.5°) follow different paths to the amplifier at R 19.3, 34°: different
lengths, different capacitance to the SW pour and to GND. The switch node slews 60 V in
≈ 50 ns; any asymmetry converts common-mode into differential.

| # | Question | Estimate today | Method | Output | Pass if | Decision it feeds |
|---|---|---|---|---|---|---|
| **Q1** | Commutation-loop inductance and resistance per cell, 1 kHz–300 MHz, including packages, cap ESL and the via fields | 2.0 nH total (0.39 PCB + 1.0 pkg + 0.6 ESL) | L1 quasi-static (FastHenry2) on the extracted copper; L2 (openEMS) cross-check on cell A | L(f), R(f), current-density plots on In1/In2 under the loop; H1 verdict | reported; L2 within 25 % of L1 | snubber (Q3), cap placement (hand-back to `placement.py` R_DCLINK / cap order) |
| **Q2** | V_ds overshoot and ringing at turn-off, 28.3 A, 60 V, both FETs; V_ds peak vs Rg ∈ {2.2, 4.7, 10, 22 Ω}, T_j 25/125 °C | 1.7 V → 61.7 V peak; ring f ≈ 1/(2π√(L·C_oss)) ≈ 100 MHz | L3 SPICE, cell with Q1 parasitics, VDMOS fitted to BSC030N08NS5, EG2103 behavioural | waveforms, V_peak table, ring frequency and Q | **V_ds,peak ≤ 68 V** worst case (TVS never conducts: breakdown min 71 V); ringing decays to < 5 % in ≤ 5 cycles | snubber yes/no and values; Rg value |
| **Q3** | RC snubber need and value | none fitted | L3 sweep R ∈ {2.2–22 Ω}, C ∈ {0.47–4.7 nF} with power in R | V_peak, ring Q, snubber loss at 20 kHz | if Q2 fails: smallest snubber that passes with P_R ≤ 0.25 W | add snubber footprints (hand-back: 2 parts per cell, 0805) or not |
| **Q4** | Gate-loop parasitics and Miller-induced turn-on of the off FET during the complementary edge | not estimated; Rg 2.2 Ω, 10 k bleed, driver 0.3/0.6 A | L1 for the gate loops (HO/LO → Rg → gate pad → source → driver GND, incl. the bleed via through both pads); L3 with C_gd(V) | L_gate, induced V_gs peak vs dV/dt, V_gs ringing | induced V_gs,peak ≤ 0.7 × V_gs(th),min at 1.2 kV/µs and at 2× that; V_gs overshoot ≤ 20 % | Rg; whether the (unfitted) PNP booster or a Miller clamp needs footprints; hand-back to `fanout.py` gate arcs if L_gate is the problem |
| **Q5** | Sense-chain transient error: L·di/dt across the shunt pair, mutual coupling into the SNSP/SNSN loop, tap asymmetry (H3, H4), through R107/R108/C108, the INA241 (bandwidth, slew, CMRR vs frequency, PWM-rejection figure) to the ADC pin | not estimated | L1 (partial and mutual L of shunts and taps; C of taps to SW and GND via FastCap2), L3 (INA241 behavioural), L2 cross-check of the tap-loop mutual inductance | ISENSE waveform after each edge, settling time to ±1 LSB, sample-corruption fraction at 125 ksps unsynchronised | **settles to ±1 LSB (0.8 mV) within 500 ns** of the switch-node edge, both edges, both current polarities; corrupted-sample fraction ≤ 6 % | tap placement (hand-back), filter values (R107/R108/C108), an ISENSE RC, PWM-synchronised sampling (Q8) |
| **Q6** | DC and low-frequency sense accuracy: effective resistance between the tap points including pour copper, its temperature coefficient (copper 0.39 %/K vs the shunt's), current-sharing between R105 and R106, and the same for cell C's 0 Ω links | 0.8 mΩ, 50 ppm/K assumed | L5 conduction solve on the raster (6 layers + vias) at 28 A | R_tap-to-tap, share of copper in it, TCR, per-shunt current | copper share ≤ 5 % of 0.8 mΩ; TCR of the total ≤ 0.05 %/K; R105/R106 share within 10 % | tap positions; whether to Kelvin the taps onto the shunt pads (hand-back to `placement.py` S_TAP_PH / `fanout.py` sense arcs) |
| **Q7** | Current density and I²R in the 20 A copper at 20 A RMS: switch-node islands, phase pours (the band is 95 A/mm² by the spec's own arithmetic), lead pads, the 16-via arrays (sharing), the shunt pads | IPC-2152 wants ≈ 6 mm of 2 oz; band is 3.0 mm | L5 | J maps per layer, hot-spot list, per-via current, resistance lead pad → FET | max via current ≤ 2 A RMS (0.4 mm drill, 25 µm plating); local Joule density reported; no single hot spot > 3× the band's mean | pour widths (hand-back), via counts (`FET_VIA_N`), whether the phase band must widen |
| **Q8** | Sampling: with the transient from Q5 and the 20 kHz ripple, what does the free-running 125 ksps/channel ADC deliver to the current loop, vs PWM-synchronised sampling at the counter zero | none | L4 system model, Monte-Carlo over sample phase | current-loop noise floor (A RMS), bias, spectral content; same for synchronised sampling | synchronised sampling shown to cut corrupted samples to 0 and noise by a stated factor | firmware recommendation (DMA-paced ADC trigger from the PWM wrap) |
| **Q9** | Dead time: software `dead_zone` 0.02 (1 µs) plus EG2103's 560 ns; voltage distortion (V_bus × t_dead / T = 1.9 V with, 0.67 V without the software term), 6th-harmonic torque ripple, min-pulse clipping at the duty extremes; loop behaviour with `dead_zone` = 0 | spec: "can go to roughly zero" | L4 with the SimpleFOC control law, both motor sets | current THD, torque ripple, Iq error vs speed and current; stability margins | recommendation with numbers | firmware: `dead_zone` value and dead-time compensation |
| **Q10** | Field at the MT6701: (a) the magnet's B at the IC over air gap 1.0–2.0 mm and ±0.3 mm off-axis; (b) stray field from the three phase currents in the pours, lead pads and leads (Biot–Savart from the level-5 current distribution) at 28.3 A; (c) rotor leakage, parametric 0–5 mT; → angle error vs rotor angle and current → torque ripple through the loop | none | L6 (magpylib); L4 for the ripple consequence | B_mag(gap), B_stray vectors, angle error (deg) vs electrical angle, LSB equivalent (14-bit = 0.022°/LSB) | B_mag within 20–100 mT over the gap tolerance; **current-induced angle error ≤ 0.05° (≈ 2 LSB) at 28.3 A**, or reported with its torque-ripple cost | lead routing rule (where the three leads leave), magnet grade, whether the adjacent-cell layout (0–204°) needs a compensating term in firmware |
| **Q11** | Near and far field of the switching: H-field map 3 mm above both faces at 20 kHz harmonics and at the ring frequency; coupling into the encoder SSI lines, USB, RS-485, the CPU wedge; radiated E at 3 m (motor lead as a 0.5 m antenna) | none | L2 openEMS (whole board, ports on victims; NF2FF) | field maps, S-parameters SW → victims, dBµV/m vs frequency against CISPR 32 class B limits as a reference | informational; any victim receiving > 100 mV pk from an edge is a finding | shielding/enclosure notes; encoder trace routing (`fanout.py`) |
| **Q12** | Common-mode current through the motor's winding-to-frame capacitance from dV/dt, its return path (frame → ? → board GND: the M3 mounts are NPTH; only the ring/bosses bond), bus-side CM at the XT30 | none | L3 with the parametric C_wf and a lumped frame path | I_cm peak and RMS vs C_wf and lead length; voltage between motor frame and board GND | reported; frame-to-GND excursion ≤ 10 V pk | Y-cap / frame-bond recommendation for the enclosure |
| **Q13** | Bus ripple and the interconnect at the design point: ripple-current split between board A ceramics (H2-derated) and board B bulk through the header + standoffs; resonance of header L with the ceramics; V_bus ripple at the FET drains; header pin current | 12.4 A RMS ripple, 75 % crosses, 23.2 A RMS on 30 A; ripple ≈ 3 V | L1 (header, standoffs) + L3 (whole DC link, three cells switching with SinePWM at m = 0.8) | I_link RMS, V_bus,pp at the drains, impedance plot of the DC link seen from a cell | I_link ≤ 80 % of rating; V_bus,pp ≤ 3 V; no DC-link resonance with Q > 3 below 1 MHz | header pin count; ceramic count and value (hand-back to `placement.py`/`schematic.py`); board B bulk spec |
| **Q14** | Regen and the coast/brake question: a 20 A decel event pumping the bus (0.5·C·(71² − 60²) = 0.22 J at 300 µF); time to TVS conduction; TVS energy; the bus-guard fold-back timing at ≈ 20 kHz | none for this board | L4 with the bus model | V_bus(t), TVS energy per event, what the 63–66 V guard can and cannot catch | reported | brake resistor / active clamp / bulk size (spec §11 questions 7 and 3) |

Out of scope, and say so in the report: the RP2350 core buck (VREG_LX, 3.3 µH) and its
spurs on `+3V3A`; USB and RS-485 signal integrity; a motor FEA (no motor); board B's own
layout (none exists); thermal beyond the coupling in §6 P8.

---

## 5. Simulation architecture and tools

### 5.1 Levels

| Level | Physics | Tool | Used for |
|---|---|---|---|
| L0 | closed form | `tools/geometry.py` | the baseline every result is compared with |
| L1 | magneto-/electro-quasi-static PEEC | **FastHenry2** (partial and mutual L, R(f) with skin and proximity), **FastCap2** (C) | Q1, Q4, Q5, Q13 — the workhorse. Valid here: the board is 65 mm, λ/15 at 300 MHz in FR4 |
| L2 | full-wave FDTD | **openEMS** (+ CSXCAD, Python interface) | Q1 cross-check, Q5 cross-check, Q11 (near field, S-parameters, NF2FF) |
| L3 | circuit, transient and AC | **ngspice 45** (apt), driven from Python by writing netlists and reading raw files (`libngspice.so` is already installed for KiCad; the CLI is not) | Q2, Q3, Q4, Q5, Q12, Q13 |
| L4 | system: inverter + motor + SimpleFOC control law + sampling | **Python/numpy/scipy** (own code, `sim/loop/`) | Q8, Q9, Q10 (consequence), Q14 |
| L5 | DC/LF conduction on multilayer copper with vias | own finite-difference solver on the 0.05 mm raster (`tools/finish.py` already rasterises), sparse solve with scipy + **pyamg**; or gmsh + **scikit-fem** if the raster is too coarse at the shunt pads | Q6, Q7, and the current sources for Q10 |
| L6 | magnetostatics of magnets and currents | **magpylib** | Q10 |
| L7 | thermal (optional, P8) | scikit-fem, or Elmer in Docker if 3-D detail is needed | via array and spreading resistances, T_j at 20 A |

### 5.2 What is on this machine (checked 2026-09-11)

Present: Python 3.14.4 with numpy, scipy, matplotlib, sympy, pcbnew (9.0.8); kicad-cli
9.0.8; `libngspice.so.0` (KiCad's); pdftotext; wine; docker; `uv`, `pip`, `apt-get`,
`sudo`; 24 cores, 89 GB RAM; an RTX 3060 whose driver currently mismatches its library
(`nvidia-smi` fails) — do not plan on the GPU.

Absent: ngspice CLI, openEMS/CSXCAD, FastHenry/FastCap, FEMM, gmsh, Elmer, ParaView,
shapely, meshio, scikit-rf, PySpice, magpylib.

### 5.3 Installation plan (P0)

```sh
sudo apt-get install -y ngspice gmsh paraview            # ngspice 45.2, gmsh 4.14, paraview 6
pip install --user magpylib shapely meshio scikit-fem pyamg h5py cython
# FastHenry2 / FastCap2 (FastFieldSolvers' maintained forks; C, build with make):
#   git clone https://github.com/ediloren/FastHenry2  && make -C FastHenry2/src
#   git clone https://github.com/ediloren/FastCap2    && make -C FastCap2/src
# openEMS: the documented source build, into ~/opt/openEMS with the Python interface:
#   sudo apt-get install build-essential cmake git libhdf5-dev libvtk9-dev libboost-all-dev \
#        libcgal-dev libtinyxml-dev qtbase5-dev libvtk9-qt-dev
#   git clone --recursive https://github.com/thliebig/openEMS-Project.git
#   cd openEMS-Project && ./update_openEMS.sh ~/opt/openEMS --python
```

Budget one working session for the toolchain. If openEMS will not build, record it and
proceed with L1 + L3 for everything except Q11, which then waits; do not let the toolchain
eat the project. Docker is available if a prebuilt openEMS image is preferred; if used,
mount only `sim/`.

### 5.4 Known-answer tests — each solver, before use

| Solver | Test | Expected |
|---|---|---|
| FastHenry2 | straight round wire, l = 20 mm, d = 1 mm, partial self-inductance | 2·10⁻⁷·l·(ln(2l/r) − 0.75) ≈ 17.4 nH; low-frequency R = ρl/A |
| FastHenry2 | strip 15.4 × 5 mm, 70 µm, 0.1 mm over a wide plane | ≈ µ0·h·l/w = 0.39 nH plus fringing (expect 0.45–0.6 nH); this is exactly `geometry.commutation_loop()`'s PCB term |
| FastHenry2 | two-wire loop | closed form (Grover) within 3 % |
| FastCap2 | parallel plates 10 × 10 mm at 0.1 mm, εr 4.5 | 39.8 pF + fringing |
| openEMS | the same strip over plane as an S-parameter port → L from Im(Z)/ω below 100 MHz | matches FastHenry2 within 10 % |
| openEMS | microstrip line impedance, 0.2 mm wide on 0.1 mm εr 4.5 | closed form (Wheeler) within 5 % |
| ngspice | RLC step: L 2 nH, C 1 nF, R 0.5 Ω | analytic ring frequency and decay |
| ngspice VDMOS fit | I_d–V_ds and C_oss(V), C_rss(V), Q_g(V_gs) vs the BSC030N08NS5 datasheet curves | every point within 20 % over 0–60 V |
| L5 conduction | rectangular strip; annular sector with radial flow (R = ρ·ln(r₂/r₁)/(t·θ)) and tangential flow; a via barrel | analytic within 2 % at the chosen cell size; grid refinement 0.1 → 0.05 → 0.025 mm shows convergence |
| magpylib | dipole far field of the Ø8 × 2.5 magnet; B of a straight wire at 25 mm (µ0·I/2πr = 0.226 mT at 28.3 A) | analytic |
| L4 control | current loop on an RL plant with the sibling's tuned gains reproduces the tuner's step metrics (`tune.py analyze_step`) and the SimpleFOC PID/LPF discretisation exactly | rise/overshoot match to the same metric definitions |

### 5.5 Geometry pipeline (P0)

`sim/extract/` reads the board with `pcbnew` and writes `sim/work/geometry.json`:
polygons per net per layer (zone fills, pads with `GetEffectivePolygon`, tracks as
polygons), vias (position, drill, diameter, layer span, net), the stackup, and a
per-cell crop (cell A: angles −5°…73°, R 15…32.5). From that: (a) FastHenry `.inp`
segment meshes for the loop and gate nets (pours as `G` plane elements with holes for
antipads; tracks as segments; vias as vertical segments), (b) CSXCAD polygons for openEMS,
(c) the layer rasters for L5 (reuse `tools/finish.py`'s rasteriser at 0.05 mm, extend it to
keep net identity per cell), (d) current-path polylines for magpylib.

Cells B and C are rotated copies of A. Simulate A fully; confirm B and C only where the
surrounding copper differs: the signal-link wedge (308–360°) adjoins cell A at 0°, and the
power-link wedge (204–256°), where VBUS and GND enter the board, adjoins cell C at 204°.

---

## 6. Phases and deliverables

Run them in this order. Each phase ends with `sim/results/P<n>.json`, figures in
`sim/report/img/`, entries in `sim/FINDINGS.md`, and one paragraph in the report. Do not
start the next phase while a known-answer test of the current one fails.

| Phase | Work | Deliverable | Gate |
|---|---|---|---|
| **P0 setup** | install (§5.3); `sim/extract/`; known-answer tests (§5.4); `sim/run.py` skeleton; report scaffold linked from `index.html` | tests pass; `geometry.json`; cell-A crop drawn and checked by eye against `img/layers/a/` | all §5.4 tests within tolerance |
| **P1 parasitics** | FastHenry2/FastCap2 on cell A: commutation loop (Q1, H1), gate loops (Q4), shunt + tap loops and mutual terms (Q5, H3/H4), tap capacitances; the header and standoffs (Q13) | L(f), R(f), C tables in `sim/models/parasitics_A.json`; current-density plots on In1/In2; H1 verdict | convergence in segment count reported |
| **P2 cell circuit** | ngspice cell model: VDMOS fit, EG2103 behavioural, bootstrap, caps with ESL/ESR and DC-bias curves (H2), TVS, shunt, taps, INA241 behavioural, ADC pin; Q2, Q3, Q4, Q5 sweeps | waveform sets, V_peak tables, snubber verdict, ISENSE settling | Q2 criterion met or a snubber that meets it |
| **P3 conduction** | L5 on all six layers at 28.3 A per phase pair (A→B, B→C, C→A at the peak instants); Q6, Q7 | J maps, via currents, tap-to-tap R and TCR, hot-spot list | grid convergence |
| **P4 DC link** | Q13 with three cells switching (SinePWM m = 0.8 at 20 A), board B lumped, H2 | I_link, V_bus ripple, impedance plot | criterion in Q13 |
| **P5 encoder field** | magpylib: magnet over gap and misalignment; phase currents from P3 current paths and parametric leads; rotor leakage; Q10 | B at the sensor vs rotor angle and current; angle-error map | Q10 criterion |
| **P6 system** | L4: inverter + motor sets + SimpleFOC law (SinePWM centred, PID with ramp/limit, LPF, `foc_current`, velocity loop) + sampling model (from P2 transients) + dead time; Q8, Q9, Q10 consequence, Q14 | noise floors, distortion, torque-ripple spectra, `dead_zone` recommendation, sampling recommendation, regen timeline | reproduces the sibling's tuned step response with set "bench" |
| **P7 full wave** | openEMS: cell A loop cross-check (Q1), tap mutual cross-check (Q5), whole-board near field and victims (Q11), NF2FF with a lead | field maps, S-parameters, dBµV/m plot | L2 vs L1 agreement stated |
| **P8 thermal (optional)** | Joule sources from P3 into a board thermal model with the ring boundary; verify 7.6 K/W via array and 1.0 K/W spreading | T maps; T_j vs ring K/W | only if P0–P7 are done |
| **P9 report & hand-back** | report page, findings register complete, hand-back list with exact constants and firmware items; a final `python3 sim/run.py` from clean | everything in §0.2 | reproduces |

---

## 7. Component models and material data

Everything in `sim/models/*.json` with source and date. Where a part has no number yet
(the bootstrap diode "100 V fast", the 2.2 µF/100 V 1206, the 100 n/100 V 0603, the
1.6 mΩ 2010, the 2×5 header), pick a representative in-stock JLCPCB/LCSC part, name it, and
flag that the BOM has not chosen one.

| Part | Needed | Notes |
|---|---|---|
| BSC030N08NS5 (Infineon, PG-TDSON-8) | R_ds(on) 3.0 mΩ max at 10 V, ×1.58 at 125 °C; Q_g 76 nC, Q_gd 19.5 nC, C_iss 5600 pF max; C_oss(V), C_rss(V) curves; V_gs(th) min/max; Q_rr 188 nC; body-diode V_f; package L ≈ 0.5 nH source, R_thJC 0.9 K/W | Infineon publishes a PSpice model — try converting; otherwise fit ngspice's VDMOS (`Cgdmax/Cgdmin/a`, `Rds`, `Kp`, `Vto`) to the curves and show the fit |
| EG2103 (EG Micro, SOP-8) | source 0.3 A, sink 0.6 A; t_on 780 ns, t_off 220 ns; dead time 560 ns (460–660); V_IH 2.5, V_IL 1.0; UVLO on ≤ 9.7 V, off ≥ 7.0 V; VB(on) ≤ 9.6 V; LIN internal 200 k pull-up, HIN 200 k pull-down; 600 V level shift dV/dt rating | behavioural: current-limited push-pull with delays and interlock |
| INA241A3 (TI, TSOT-23-8, 50 V/V) | bandwidth at 50 V/V, slew rate, output swing to rails (0.2 V worst), CMRR vs frequency, the "enhanced PWM rejection" CM-step response, input bias current, input R/C | behavioural: gain block with the measured BW and CM-step response; the CM step here is 60 V in 50 ns |
| TPSMF4L64A (Littelfuse, SOD-123FL) | junction capacitance vs voltage, clamp curve | on `PHASE_X` — adds C at the phase node and the lead |
| SMDJ64A | as above | board B |
| 1.6 mΩ 2010 (metal foil/alloy) | ESL, TCR (≤ 50–100 ppm/K typical), 1 % | H3 |
| 2.2 µF / 100 V 1206 X7R; 100 n / 100 V 0603 | C vs DC bias curve, ESR, ESL | H2 |
| 1 µF / 25 V 0603 (bootstrap), 100 n (VCC) | ESR | |
| MT6701 | B_pk at the IC 200–1000 Gauss (20–100 mT); air gap 0.5–2.0 mm; off-axis ≤ 0.3 mm; recommended Ø6 × 2.5 mm magnet; NdFeB TC −0.12 %/°C; 14-bit → 0.022°/LSB; transition noise 0.01° RMS (ABZ); 55 000 rpm max | Q10 |
| Magnet | Ø8 × 2.5 mm diametric, assume N35 (B_r 1.17–1.22 T) and N42 (1.28–1.32 T) | Q10 |
| RP2350 ADC | 12 bit, 500 ksps, 96-cycle conversion, input model, DNL/INL from the datasheet | Q8 |
| Copper | σ = 5.8·10⁷ S/m annealed (use 4.7·10⁷ for plated barrels and ED foil if the fab's data supports it; state which), α = 0.00393 /K | L1, L5 |
| FR4 | εr 4.5, tanδ 0.02 (board file) | L2 |
| Solder mask | εr ≈ 3.5, 10 µm | L2, minor |
| Air gap board A ↔ board B | 11 mm | L2 |

---

## 8. Reporting

### 8.1 Directory layout

```
sim/
  SPEC.md              this document
  FINDINGS.md          the register (§8.3)
  run.py               reproduces everything; `--phase P2` runs one
  extract/             board → geometry.json, FastHenry .inp, CSXCAD, rasters, polylines
  models/              per-part JSON with sources; parasitics_A.json (from P1)
  fasthenry/ fastcap/  inputs and raw outputs
  spice/               netlist templates, ngspice runs, raw parsers
  field/               openEMS scripts
  conduction/          the L5 solver
  loop/                the L4 system model (SimpleFOC law reimplemented, sampling, motor sets)
  encoder/             magpylib scripts
  thermal/             P8, if done
  work/                board copies, meshes, scratch — regenerable, not precious
  results/             P0.json … P9.json — every number the report quotes
  report/              index.html + img/ — static, no server code
```

`results/*.json` is the interface: the report reads only from there, so a re-run can never
show yesterday's number beside today's figure. Keep the JSON flat and named
(`{"Q1": {"L_pcb_nH": …, "L_total_nH": …, "method": "fasthenry", "converged": true, …}}`).

### 8.2 The report page

Static HTML in the project's existing style (see `index.html`, `spec.html`, `style.css`;
light and dark). Sections in the order of §4, one per question: the estimate, the solved
number, the figure, the verdict, the hand-back. A summary table at the top. Link it from
`index.html`'s status page (a one-line addition; nothing else in `index.html` changes).
View it with `proj up servodrive` then `proj url servodrive`. Run `proj doctor` if
anything about serving changes (it should not).

### 8.3 Findings register format

Stable IDs `S-01`, `S-02`, … never renumbered, one row each in `sim/FINDINGS.md`:

```
| ID | Sev | Finding | Evidence | Status | Disposition |
```

Severity HIGH / MED / LOW / INFO as in the sibling's `review/findings.html`. Evidence names
the result key and figure. Status: OPEN, DECIDED, HAND-BACK, VERIFIED-OK. Hypotheses H1–H4
each get a row whatever the verdict. Toolchain failures and unmet criteria are rows too.

### 8.4 The hand-back list

`sim/HANDBACK.md`. Each entry: the result that motivates it, the file and constant
(`tools/placement.py: R_DCLINK`, `tools/fanout.py: …`, `tools/geometry.py: …`), the
proposed value, the expected effect, and what would have to be re-run (`gen_boards.py
--force` then `route.py --rounds 4 --loose` for placement changes — say that this discards
the routing). Firmware items name the SimpleFOC parameter or the RP2350 mechanism.
Enclosure items go to the same list. This file is for a human to decide from; nothing in
`sim/` applies any of it.

---

## 9. Things that will bite

- **Zone fills.** The file carries fills (138 polygons). If a pcbnew script refills, it
  must do so on a copy in `sim/work/`; fills depend on the project's design-rule file
  (`servodrive_A.kicad_pro`) being beside the board.
- **Pad orientation.** Every pad on this board carries its own absolute angle; take
  geometry from `GetEffectivePolygon()`, never from pad size + footprint angle.
- **Flipped footprints.** `PAD::GetLayer()` answers F.Cu for a pad on a flipped part; use
  the layer set. The shunts, amplifier, encoder and bleeds are on B.Cu.
- **Via-in-pad.** 104 stitched vias sit in pads; the FET drain arrays are 16 vias each. A
  via barrel spans all six layers; its antipad on foreign planes is what perforates In1
  under the high-side drain (H1).
- **Net names.** `SW_X` is the switch node, `PHASE_X` is after the shunt. The Phase class
  (0.4 mm clearance) is `VBUS`, `SW_*`, `PHASE_*` and deliberately not `GND`.
- **The design point is RMS.** 20 A RMS per phase, 28.3 A peak. The sense chain is sized
  to the peak; do not mix them (the spec did, twice).
- **Units in the tools are mm and degrees**, KiCad internal units are nanometres, FastHenry
  is whatever the `.units` line says — write it every time.
- **The sibling's firmware labels A/B/C swapped vs its schematic.** Irrelevant here (the
  servodrive netlist is self-consistent), but do not copy pin tables from it.
- **A protected via can come back moved by 25 µm** and a track can end 0.05 mm inside its
  pad — geometry from the file is what was built; do not "clean" it.
- **`python3 tools/geometry.py` rewrites `img/*.svg`.** Harmless but noisy in a diff;
  import it as a module instead (`sys.path.insert(0, 'tools'); import geometry as G`).

---

## Appendix A — the analytic baseline (`tools/geometry.py`, 2026-09-11)

Reproduce by importing the module and calling the functions; do not paste the numbers into
the report — read them from the module at run time so they cannot drift.

| Function | Result |
|---|---|
| `losses()` at 20 A RMS, 20 kHz, 60 V | conduction 5.69 W (3·I²·3.0 mΩ·1.58); switching 3.84 W (e_on = ½·60·18.0·65 ns = 35.1 µJ, e_off 17.6 µJ, e_rr = 188 nC·60 V = 11.3 µJ, ×3×20 kHz); shunts 0.64 W; gate 0.14 W (q_gate = 76 n + 5.6 n·(12−10) = 87.2 nC); logic 0.26 W; **10.56 W** |
| `commutation_loop()` | L_pcb = µ0·0.1 mm·15.4 mm / 5 mm = **0.387 nH**; + 2 × 0.5 nH package + 0.6 nH ESL = **1.99 nH**; di/dt 0.87 A/ns; overshoot **1.73 V**; peak 61.7 V |
| `interconnect(10)` | ripple 12.37 A RMS (m = 0.8, cos φ = 1); bus 21.2 A DC; ceramic 0.612 Ω at 20 kHz vs bulk 0.2 Ω → 75.4 % crosses; link **23.2 A RMS** vs 30 A |
| `bootstrap()` | 1 µF: 0.10 V droop, 1.5 V margin over VB(on) 9.6 V |
| `via_thermal()` | 16 × 0.4 mm: 7.6 K/W (FR4 alone 354 K/W) |
| `sink_needed()` | ring ≤ 6.8 K/W → ≥ 98 cm² at 15 W/m²K |
| Sense | 0.8 mΩ × 50 = 40 mV/A; (1.65 − 0.20)/0.040 = **±36.2 A** vs 28.3 A peak |

## Appendix B — sibling firmware constants, verbatim

```
// main.cpp
#define POLE_PAIRS 11
#define SHUNT_20MOHM 0.020f   // ±4A, 0.4 V/A     (MT6701 build)
#define SHUNT_10MOHM 0.010f   // ±8A, 0.2 V/A     (halls build)
#define CURRENT_AMP_GAIN 20.0f
#define CURRENT_SENSE_SAT_A (CURRENT_SENSE_REF_V / (SHUNT_RESISTOR * CURRENT_AMP_GAIN))  // 1.65 V ref
#define CURRENT_LIMIT_MAX_A (CURRENT_SENSE_SAT_A * 0.98f)
#define VMOT_DIVIDER_RATIO (105.1f / 5.1f)
#define VBUS_GUARD_V 63.0f
#define VBUS_GUARD_FULL_V 66.0f
#define BRAKE_ON_V 64.0f
#define BRAKE_FULL_V 66.0f
#define BRAKE_MAX_DUTY 0.95f
#define BRAKE_PWM_HZ 20000
#define BRAKE_R_OHMS 15.0f
#define BRAKE_R_WATTS 100.0f
#define BRAKE_R_JOULES 300.0f
#define DEMO_MIN_VMOT 15.0f
driver->pwm_frequency = 20000;
driver->dead_zone = 0.02;
driver->voltage_power_supply = 30.0f;   // then readVMOT()
motor->voltage_limit = 4.0;             // MT6701 build
motor->voltage_sensor_align = 1.0;
motor->current_limit = 4.0;
motor->velocity_limit = 20.0;
motor->controller = MotionControlType::velocity;
motor->torque_controller = TorqueControlType::foc_current;
motor->PID_current_q.P = 0.6;  motor->PID_current_q.I = 0.3;  motor->LPF_current_q.Tf = 0.02;  // defaults
motor->PID_velocity.P = 0.3;   motor->PID_velocity.I = 0.1;   motor->PID_velocity.output_ramp = 200; motor->LPF_velocity.Tf = 0.01;

// tuned_gains.txt (2026-05-15)
motor.PID_current_q.P = 0.36;  motor.PID_current_q.I = 1.8;  motor.LPF_current_q.Tf = 0.005;
motor.PID_current_d.P = 0.36;  motor.PID_current_d.I = 1.8;  motor.LPF_current_d.Tf = 0.005;
motor.PID_velocity.P = 0.70779; motor.PID_velocity.I = 3.18506; motor.PID_velocity.output_ramp = 200; motor.LPF_velocity.Tf = 0.01;

// tune.py current-loop tuner: P-only step at P = 1.0, target 0.3 A;
// R = P·(1−G)/G from the DC gain, L = τ63·(R+P); then Kp = ω·L, Ki = ω·R with ω = 200 rad/s,
// halved on overshoot > 20 % or oscillation (up to 5 tries).

// patches/simplefoc_rp2040_current_sense.cpp: adc_set_round_robin(enabled), clkdiv 0,
// FIFO → DMA ring of channelCount × 2 bytes, samples read as latest value; no PWM sync.
```

## Appendix C — reading the board with pcbnew

```python
import pcbnew
b = pcbnew.LoadBoard('sim/work/servodrive_A.kicad_pcb')       # a copy, never the original
origin = pcbnew.VECTOR2I_MM(148, 105)                          # shaft axis
for z in b.Zones():
    for layer in z.GetLayerSet().Seq():
        polys = z.GetFilledPolysList(layer)                    # SHAPE_POLY_SET, already filled
for fp in b.GetFootprints():
    for pad in fp.Pads():
        layers = pad.GetLayerSet().Seq()                       # not pad.GetLayer()
        poly = pad.GetEffectivePolygon()                       # rotated, per-pad angle applied
        drill = pad.GetDrillSizeX()
for t in b.GetTracks():                                        # PCB_TRACK, PCB_VIA, PCB_ARC
    ...
```

Convert with `pcbnew.ToMM()`; subtract the origin so the sim's frame is the tools' frame.
`kicad-cli pcb export gerbers` and `… drill` give an alternative path if a gerber-based
openEMS flow (antmicro's `gerber2ems`) turns out easier for P7.
