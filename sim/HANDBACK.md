# servodrive — hand-back list

Generated 2026-09-12 from `sim/results/*.json`. Nothing in `sim/` applies any of this: it is for a human to decide from.

Every placement change means `python3 tools/gen_boards.py --force` and then `python3 tools/route.py --rounds 4 --loose`, **which discards the routing**. The constants that only change a number in the model do not.

## The commutation-loop model is 5 times out

- **Where:** `tools/geometry.py: commutation_loop()`
- **Proposed:** L_pcb = 2.06 nH (solved, all four DC-link capacitors in parallel) rather than mu0*h*l/w = 0.387 nH; total 3.35–3.19 nH. Use 1.98 nH: the same model at the converged end of its own grid sweep, 4 % below the figure above. Q11 puts the single port both solvers can be compared on at 2.68–2.91 nH
- **Because:** VBUS reaches the high-side drain from In2 through a 16-via field, not from a plane 0.1 mm away (P1, H1); its size is settled by the grid sweep and cross-checked full wave (P7)
- **Effect:** every derived number moves: overshoot, ring frequency, the snubber decision
- **Re-run:** nothing on the board; `python3 tools/geometry.py` to refresh the figures

## Pin down the EG2103's output impedance

- **Where:** `models/eg2103.json: I_source, I_sink and the condition they are measured at`
- **Proposed:** get the real datasheet, or measure the gate edge into a known capacitance on a built board, and record the condition beside the number
- **Because:** the only obtainable EG2103 document gives I_O+/I_O- without the test condition, so the model brackets it 40/20 ohm to 4/2 ohm (P2, Q2)
- **Effect:** it is the difference between 62.6 V of V_ds (inside the 68 V criterion, no ring, no snubber) and 73.9 V (outside it, a 145 MHz ring, and a snubber); every dV/dt-sensitive answer in the report moves with it
- **Re-run:** nothing on the board; re-run P2 and P9

## Add snubber footprints, unstuffed

- **Where:** `tools/placement.py (two 0805 per cell), tools/schematic.py`
- **Proposed:** R = 2.2 ohm, C = 0.47 nF from SW_x to GND, as close to the low-side source pads as the cell allows; fitted only if the driver turns out to be the strong one
- **Because:** worst-case V_ds is 73.9 V on 80 V silicon in the strong-driver corner, and inside the criterion in the other (P2, Q2)
- **Effect:** worst-case V_ds falls to 62.7 V; 0.034 W per cell at 20 kHz
- **Re-run:** gen_boards.py --force, then route.py — discards the routing

## Budget for Miller cross-conduction, or change the device

- **Where:** `tools/geometry.py: the switching-loss term; or the FET choice itself`
- **Proposed:** either carry 0.86 W per half-bridge of extra loss and a 74 A peak in the loss and SOA budgets, or pick a device with a smaller Q_gd/C_iss
- **Because:** the off device's V_gs at the die reaches 3.62 V, above both the 1.54 V criterion and the 2.2 V minimum threshold, on every high-side turn-on, and neither R_g nor a stronger driver nor a gate-source capacitor moves it (P2, Q4)
- **Effect:** at the typical threshold there is no cross-conduction at all (-23 nC); at the datasheet minimum there is 715 nC per edge
- **Re-run:** nothing on the board unless the device changes

## Twelve of sixteen low-side drain vias are islands

- **Where:** `tools/fanout.py / the B.Cu zone outlines for SW_x`
- **Proposed:** extend the SW_x pour on B.Cu so it covers the whole 4 x 4 array, or move the array inboard of the PHASE pour's edge
- **Because:** solved via currents in P3, Q7: the busiest carries 5.2 A rms against 2 A
- **Effect:** the phase current would cross to B.Cu through sixteen barrels instead of four; per-via current falls by about four times
- **Re-run:** gen_boards.py --force, then route.py — discards the routing

## The high-side arrays share badly

- **Where:** `tools/geometry.py: FET_VIA_N`
- **Proposed:** raise from 16, or spread the entry into the array; the busiest via carries 2.4 A rms
- **Because:** P3, Q7
- **Effect:** per-via current falls in proportion
- **Re-run:** gen_boards.py --force, then route.py — discards the routing

## The DC-link ceramics are worth about half their marking

- **Where:** `tools/geometry.py: interconnect(), the 13 uF assumption`
- **Proposed:** use 7.0 uF at 60 V (bracket 5.5–9.7) instead of 13.2
- **Because:** H2, P4
- **Effect:** the ripple split shifts and more of it crosses the header
- **Re-run:** nothing on the board

## Board B's bulk must be polymer hybrid

- **Where:** `the board B specification (spec.html §8)`
- **Proposed:** 4 x 100 uF polymer hybrid, ESR 20–40 mohm each; not 3 x 100 uF electrolytic at 0.2 ohm
- **Because:** P4, Q13 ripple table
- **Effect:** bus ripple at the drains falls from over 6 V p-p to under 2
- **Re-run:** nothing on board A

## The magnet is too strong at the short end of the gap

- **Where:** `tools/geometry.py: MAGNET_D (and the magnet's grade)`
- **Proposed:** Dia 6 x 2.5 mm, as the MT6701 datasheet recommends, or hold the air gap at or above 1 mm with an N35
- **Because:** P5, Q10 magnet table
- **Effect:** keeps the field at the die inside the part's 20–100 mT window over the whole tolerance
- **Re-run:** MAGNET_D changes the keepout, so gen_boards.py --force and route.py — discards the routing

## Bundle the three motor leads

- **Where:** `assembly instruction, not a board change`
- **Proposed:** twist or bundle the three phase leads for the first 50 mm after they leave the lead pads
- **Because:** P5, Q10
- **Effect:** removes most of the 0.090 deg current-dependent angle error; the board copper's own field already cancels on the shaft axis
- **Re-run:** nothing

## Pace the ADC from the PWM

- **Where:** `firmware: patches/simplefoc_rp2040_current_sense.cpp`
- **Proposed:** a DMA channel paced by the PWM slice's wrap DREQ writing START_ONCE to ADC_CS, one conversion per phase at the counter's zero, instead of free-running round-robin
- **Because:** P6, Q8
- **Effect:** current-loop measurement error falls from 0.638 to 0.210 A rms and the bias halves
- **Re-run:** firmware only

## Set dead_zone to zero

- **Where:** `firmware: driver->dead_zone`
- **Proposed:** 0.0, relying on the EG2103's own 560 ns interlock
- **Because:** P6, Q9
- **Effect:** recovers 0.76 % of torque and changes the distortion hardly at all
- **Re-run:** firmware only

## Clamp the modulation index

- **Where:** `firmware: the duty the driver is given`
- **Proposed:** never command a duty below 2.40 % or above its mirror
- **Because:** P6, Q9
- **Effect:** keeps the loop out of the region where the driver silently passes no pulse
- **Re-run:** firmware only

## Decide how the motor frame is bonded

- **Where:** `the enclosure, not the board -- the four motor mounts are NPTH and cannot be changed into a bond without copper round them`
- **Proposed:** the sweep's best option is ycap 4.7 nF at 2 V of frame swing
- **Because:** P2, Q12
- **Effect:** today the frame follows the switch node to about 71 V peak; a long bonding wire makes it worse, not better, because its inductance resonates with the winding-to-frame capacitance
- **Re-run:** nothing on board A unless a Y capacitor is wanted on it

## Decide what happens on a decel

- **Where:** `spec.html §11 questions 3 and 7 — an open design question`
- **Proposed:** a brake resistor, an active clamp, a much larger bulk, or a documented deceleration limit
- **Because:** P6, Q14
- **Effect:** today the only protection is a software fold-back and two TVSs that would be asked to take tens of joules
- **Re-run:** board B and/or firmware

## Correct the spec's own known-answer value

- **Where:** `sim/SPEC.md §5.4, the FastHenry straight-wire row`
- **Proposed:** 14.53 nH, not 17.4 nH, for l = 20 mm and d = 1 mm
- **Because:** P0 known-answer tests
- **Effect:** the test passes as written once the expected value is right
- **Re-run:** nothing

