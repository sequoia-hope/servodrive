# servodrive

A round, motor-mounted servodrive: two Ø65 mm boards stacked on the back of a
brushless motor, with a magnetic rotary encoder on the shaft axis.

**Status: board A captured, placed and routed. 136 parts, 87 nets,
425 pin connections, every one of them made. DRC- and ERC-clean, and
KiCad's own extracted netlist matches the intended one connection for
connection. Re-routed 2026-09-22 with four of the simulation's changes
([below](#the-2026-09-22-changes)); board B is still an outline.
Board S, the single-board variant, captured, placed and routed 2026-09-23
from the same generators, and re-routed the same day with TI's buck parts
([below](#board-s-one-board)).**

- **[spec.html](spec.html)** — the specification
- **[index.html](index.html)** — status, decisions, open questions; board A in the viewer (PCB and 3D tabs)
- **[single.html](single.html)** — board S, the single-board variant: captured, placed and routed, with the same viewer

The pages are styled after Altium 365's workspace: a dark frame with the
project tree on the left, built by `shell.js` from each page's own headings.

Online at <https://sequoia-hope.github.io/servodrive/> (GitHub Pages, from
the root of `main`: the pages are plain files with relative links, and
`.nojekyll` keeps them that way). Served locally: `proj up servodrive`, then
`proj url servodrive` for the address.

## What it is

| | |
|---|---|
| Form | Two Ø65 mm round boards. The motor board bolts to the encoder's 4 × M3 pattern; the power/IO board to six perimeter M2.5. |
| Sections | Three 60° phase cells **adjacent**, 0–180°, then three 60° wedges. The CPU sits in the middle wedge, as far from the switching as a round board allows. |
| Encoder | MT6701 on the shaft axis, SSI to the CPU on the same board. No cable, no line drivers, no encoder port. |
| Bus | 60 V nominal, **12 V minimum**, 80 V silicon, TVS breakdown 71–79 V; XT30 |
| Phase current | **20 A RMS per phase, in bursts** — normal running is well below it, and no fins are planned (§4) |
| CPU | RP2350A, QFN-60 |
| Sensing | Two inline 0.8 mΩ shunts + **INA241A3** at 50 V/V = 40 mV/A, third phase reconstructed; bus voltage; FET temperature |
| Gate drive | EG2103 on a 12 V rail (LCSC C480654, 2,225 in stock, MOQ 5, $0.19) — interlock + 560 ns dead time |
| I/O | USB-C with full PD — a 20 V contract feeds VMOT; full-duplex RS-485 on two chained ports |

## The design point is 20 A RMS, and that word matters

Draft 0.2 used "20 A" for two different quantities: the thermal budget quoted
20 A **RMS per phase**, and the sense chain quoted ±20.6 A **instantaneous**.
Those differ by √2, so the sense chain clipped at 14.6 A RMS — it could not have
closed a current loop at the design point. One number now, and everything is
sized against it:

| | |
|---|---|
| Design point | 20 A RMS per phase — a burst figure since 2026-09-22, not a continuous rating |
| Peak the chain must see | 28.3 A |
| Sense full scale | ±36.2 A, worst-case output swing |
| Board A dissipation | 10.6 W |
| Heatsink it would need to be continuous | ≤ 6.8 K/W, ≥ 98 cm² of external surface — not built |

## Current sensing

Full scale is set by the shunt. Keep this table, the schematic and the firmware
`SHUNT_RESISTOR` define pointed at each other — the sister project's F-14 is
exactly this drifting apart.

| Shunt (per sensed phase) | Gain | V/A | Measurable range |
|---|---|---|---|
| 0.8 mΩ (2 × 1.6 mΩ 2010) | 50 V/V | 0.040 | ±36.2 A |
| 1.6 mΩ (one desoldered) | 50 V/V | 0.080 | ±18.1 A |

Phase C is reconstructed as `Ic = -(Ia + Ib)`. Its two shunt footprints are
fitted with 0 Ω links so all three phases have the same series resistance.

Full scale is against the datasheet's **worst-case** output swing, V<sub>S</sub> − 0.2 V,
not the 0.07 V typical — that is what makes ±36.2 A rather than ±40 A, and it is
the number the design point has to clear. It does, by 28%.

**This took two goes.** Draft 0.2's 1.6 mΩ gave 80 mV/A to match the sister
project's firmware scaling, and ±20.6 A of full scale against a 28.3 A peak: it
would have clipped on every cycle above 14.6 A RMS. The first correction, 1.0 mΩ,
used the typical swing figure and had 2.5% of margin, which is not margin. 0.8 mΩ
is the one that survives the datasheet. The consolation is that the precision
mode — desolder one of each pair — lands on exactly 80 mV/A, so the sister
project's firmware constants come back after all.

**And the part number changed.** The 50 V/V grade of INA241 is **A3**, not A2.
INA240 numbers its gains A1 = 20, A2 = 50; INA241 numbers them A1 = 10, A2 = 20,
A3 = 50. Draft 0.2 carried the INA240 mapping across and specified a 20 V/V part
for a 50 V/V job.

## Relationship to rp2350-motor-controller

The sister project at `~/pcb/rp2350-motor-controller` supplies the MT6701
encoder circuit and its mount pattern (`hardware/encoder`), the power-stage part
choices, and a findings register that this board inherits the conclusions of
without inheriting the bugs. §9 of the spec lists which finding produced which
decision here.

Three things worth handing back:

- **F-17 is right, and draft 0.2 of this spec was wrong to doubt it — but not
  for the reason their register gives.** Their E-stop does brake. It brakes
  because `R1`, `R2`, `R21` and `R22` are 10 k pull-downs on both EG3113 inputs,
  and EG3113's LIN is active-low: a pulled-down LIN turns the low-side FET on.
  The driver on its own would coast. The consequence is worth knowing: their
  bridge **hard-shorts the motor at every reset and every power-up**, before
  firmware runs, with no current limit. On a spinning motor that is a braking
  current set only by winding resistance.
- **NCP5183DR2G** is a candidate answer to **F-40** (EG3113 unbuyable, currently
  blocking their orders): SOIC-8, 600 V, 4.3 A source/sink, matched 120 ns
  propagation delays, 50 V/ns dV/dt immunity, AEC-Q100, $0.92 at MOQ 1 with 2,883
  in stock. It gives up EG3113's interlock and dead time, which their own GaN
  evaluation argues they do not lean on. Also worth knowing: **EG2131 and FD2103S,
  the two obvious like-for-like swaps, are both out of stock.**
- **RP2350 erratum E9 reaches the gate driver.** A Bank 0 GPIO can latch around
  2.1–2.2 V, and at reset the pin is an input with a pull-down. Against EG Micro's
  internal 200 k pull-up on LIN that lands the driver input between V<sub>IL</sub>
  and V<sub>IH</sub>. Their pull-downs happen to defeat it; anyone copying the
  circuit without them should size an external pull deliberately.

## The channel interior to the screws

A phase cell is short of **angle**, not of area, and angle costs radius: a
millimetre of arc at R 20 is 2.9°, the same millimetre at R 29.5 is 1.9°. The
cell was 70° wide because small parts sat at small radius — a 1 µF 0805 beside
the gate driver was eating 30° on its own, while both MOSFETs together needed
only 18°.

The six boss pads sit on R 26.9–32.2, and **between them that band is empty**:
the heatsink land owns it on the outward face, and on the motor-facing face
nothing but the lead pads wants it. In the utility wedges that is still true and
the channel is still used. In a **phase** cell it turned out not to be — see
below — and only the clamp diode is left there, beside its own lead pad.

| | 70° cells | everything in the channel | **now** |
|---|---|---|---|
| Phase cell | 70° | 60° | **68°** |
| Utility wedge | 50° | 60° | **52°** |
| Bolt spacing | 70/70/70/50/50/50° | 60°, even | **68/68/68/52/52/52°** |
| In the channel, per cell | — | 6 parts | **1** — the clamp diode |
| Heatsink land | 286 mm² | 250 mm² | **279 mm²** |

**Routing took most of that back, and it was right to.** The middle column is
what the channel bought: 60° cells and an even bolt circle. What it cost only
showed up when the board was routed — a part in the channel can only be
connected if its nets are GND or the phase output, because the phase-output
pour owns B.Cu from R 25.9 to 28.9 beneath it, the heatsink land owns F.Cu from
R 29.2 above it, and the six boss pads cut the outer ring into six arcs. What
is left is a via corridor about 0.7 mm wide, twice per cell. Neither pour can
give the room back: the phase band is already 95 A/mm² at 3.0 mm and cannot
grow inward past R 25.6 without meeting the switch-node band.

So five of the six went back into the cell — the DC-link 100 n pair into the
DC-link row *ahead* of the bulk, where a high-frequency capacitor belongs; the
bootstrap diode beside the capacitor it charges; both input pulls onto the
driver's logic side with the VCC 100 n. Only the clamp diode stays, and its
nets are exactly GND and the phase output.

The cells had to pay for them: 67° is the floor, below which the DC-link row
runs into the perimeter boss, and 68° is what is set. That costs the even bolt
circle — the thing the 60° arrangement was pleasing for. It buys back
**29 mm² of heatsink land**, because the land's half-span is measured from the
cell edge.

Three consolidations paid for it: the per-driver 1 µF VCC bulk became one 4.7 µF
at the power link (three of them on one 0.1 A rail was two too many), the lead
pad narrowed from ±8.5° to ±7° (23 mm², still ample for a 14 AWG lead), and
`Row` learned to dodge parts in *other* rows — two rows that overlap radially
and sit at different angles can collide, and a packer that only looks at its own
row walks straight into them.

## The layout, second pass

The first routed board made 69 of 170 connections, and the diagnosis at
the time blamed the driver ring. Measuring the result said otherwise:
nearly every unrouted connection ended at the RP2350. In3, the only signal
layer that crosses the board, was almost empty, because nothing could get
out of the QFN to reach it. Three things were true of that board and are
not of this one.

**The QFN had no room to fan out.** A 0.4 mm pitch, 0.2 mm-wide pads, and a
via that is 0.46 mm across with 0.15 mm around it: a via per pin does not
fit in place, and it does not fit in two rows either. With 0.15 mm stubs it
takes four staggered rows, 2.9 mm deep, on every side — and the chip sat
0.3 mm from the parts either side of it, 0.8 mm from the row beyond it,
with its decoupling on the back directly under the pins where the vias had
to land. The CPU wedge is rebuilt around a clear band: the chip at R 21.15,
nothing within 2.9 mm of its pads on the front except the regulator
cluster (below), the six IOVDD 100 n on the back in two columns beside it,
the crystal in a row at R 29.9, and the flash turned so both its pad rows
face somewhere a via can go, with its own 100 n and the BOOTSEL resistor on
the back directly beneath its body. The signal header moved in to R 25.15,
where all twenty of its signal pads can take an escape via on the side away
from the edge.

**The FET gate pads faced the wrong way.** The gate is at one end of the
TDSON-8's source-pad row, and with the row pointing outward the gate sat at
R 27.9, walled in by the switch-node pour on three sides and the heatsink
land on the fourth. Both FETs are turned 180°, which swaps their sides, and
the gate pads are at R 24.4 facing the driver row across an empty 1.5 mm.
The gate resistors and input pulls are 0402s lying tangentially, so HO and
LO reach a pad at their own radius and the far pad points at the gate; the
bootstrap cap is a 0603 with its SW pad over the switch-node pour, so it
takes a via.

**The sense row on the back is arranged for arcs, not for a router.** The
amplifier is at the cell's centre with its SNSP pin, the switch-node-side
tap and the filter cap all clockwise of it within 0.4 mm of R 18.4, so
that SNSP is one arc between the amplifier's pad rows with a spur down to
each pad, and SNSN is a second arc beside it to the filter cap, a via at
the amplifier and a via in the tap at the shunt, and one run on In3
between them. The gate bleeds moved from that row to directly beneath the
gate resistors: the high side's radial under its resistor, the low side's
further out under the gate's own radial track, each with one via through
both pads. The NTC moved inside the row where its pad can take a via. The
flash is turned so that each pad row's radial order is the chip's QSPI pin
order along its side and the In3 runs between them do not cross.

**Every 20 A pad takes a via, and the router never sees those nets.** The
shunts moved from either side of the cell to side by side under the
switch-node island, between the two FETs' via clusters — nowhere else on
the back is clear of the DC-link caps' vias coming through from the other
face — with the SW pads over the switch-node pour and the PHASE pads in the
phase-output pour, the lead pad directly beyond. The sense taps sit beside
them, the clamp in the channel beside the lead pad where the pour reaches
out to it. The switch-node pour on B.Cu comes in to R 20 so the driver's VS
pin, the bootstrap cap, the bleed and the tap all stitch. +3V3 is a plane on
In2 that is now the whole centre disc as well as the CPU wedge, so each
cell's sense amplifier taps it with a stub. With all of that
stitched, `tools/deadcheck.py` proves it and `route.py` takes VBUS, the
switch nodes, the phase outputs and +3V3 away from the router: pours become
keepouts, pads lose their net and get a halo the width of their clearance
class. It cost the cells 0.9 mm of FET spacing, a 1206 DC-link bulk cap
instead of a 1210 (the 1210 ran into the perimeter boss), and a SOD-323
bootstrap diode.

### The RP2350, from the reference design

The chip's regulator and decoupling are Raspberry Pi's, copied from the
RP2350A minimal design (RP-006440) in the chip's own frame to the 0.05 mm:
the 3.3 µH 2016 inductor 3.8 mm from VREG_LX with the VIN, DVDD and AVDD
4.7 µF stacked beside it, the two by the inductor on the reference's own
small-pad 0402 footprint because the switch node's neck passes between
their pads; 33 Ω into VREG_AVDD; 27 Ω in series with each USB line; five
small pours around the regulator pins; a ring of 1V1 copper under the chip
joining the four DVDD pins, which the fan-out tool reaches with a stub from
each; and the reference's own two F.Cu tracks, VREG_AVDD out to its RC and
DVDD pin 50 out to the regulator's output pour (the second is only laid if
the fan-out beside it leaves the room, and here it does not, so the ring
and the output pour meet through a via each and a run on In3 that the
fan-out tool lays itself, dodging the via field). Two of the pours turned
out not to fill under the 0.15 mm clearance this board runs — between two
0.4 mm-pitch pads a pour is 0.3 mm wide and breaks at their corners — so
VREG_LX gets a 0.15 mm track as well, threaded between the VIN capacitor's
pads the way the reference's pour is.

Three things capture found while doing it, all in the netlist rather than
the layout:

- **The bootstrap diode was backwards.** `Device:D` numbers its pins
  1 = cathode, 2 = anode, and so does the footprint; the netlist had the
  cathode on +12V. A bootstrap diode charges the cap *from* the rail.
- **VREG_AVDD was tied straight to +3V3.** The reference feeds it through
  33 Ω with its own 4.7 µF.
- **USB had no series resistors.** 27 Ω each, as the reference.

The crystal's load caps stay at 27 p where the reference uses 15 p: the
reference's crystal is a different part with a different CL, and the value
is the crystal's to decide.

## Layout

```
tools/sexp.py        a reader/writer for KiCad's s-expressions
tools/geometry.py    every dimension and every derived number; regenerates img/*.svg
tools/placement.py   the board-A floorplan, and the checks that keep it legal
tools/placement_s.py board S, the single-board variant: its floorplan (layout()),
                     drawn to img/board_s_*.svg and single.html's tables
tools/schematic.py   the board-A netlist, and the sheets it emits
tools/schematic_s.py board S's netlist, built from schematic.py's own functions
tools/gen_boards.py  emits the KiCad projects from the three above; --board s
                     emits board S alone
tools/stitch.py      puts a via in every pad that sits over its own plane
tools/fanout.py      the copper the router could not make: the RP2350's escape
                     vias, the gate and sense arcs, the escapes, the plane taps
tools/deadcheck.py   proves every pad on a net route.py demotes already
                     touches copper of its own net
tools/padpos.py      pad centres of a placed Part, polar, without a board
tools/finish.py      a maze router over the board's free space, for whatever
                     freerouting gives back still in pieces
tools/route.py       hands the rest to freerouting, headless, and brings it back
tools/plot_layers.py one SVG per copper layer, in register, for the copper
                     viewer on index.html; gen_boards.py and route.py run it
tools/export_3d.py   the board as a GLB for the 3D viewer: kicad-cli's export,
                     merged and quantized 30 MB -> 7, stand-ins for the three
                     models this machine lacks; gen_boards.py and route.py run it
hardware/
  parts/3dmodels/    two STEP models the footprints name and KiCad's library
                     lacks, for export_3d.py (sources in its README)
  parts/             two symbols copied in, three DERIVED (EG2103, INA241A3,
                     W25Q128JV), two copied from the RP2350A reference design
                     (the 2016 inductor, a small-pad 0402) and five GENERATED
                     footprints (the FET with 16 thermal vias for the low side
                     and 20 for the high, heatsink boss, heatsink land, phase pad)
  motor_board/       servodrive_A — 6 layer, 2 oz outer, 136 parts, 87 nets
  power_board/       servodrive_B — 4 layer, 2 oz outer, outline only
  single_board/      servodrive_S — board S, 6 layer, board A plus board B's minimum
img/                 generated drawings — do not edit by hand
img/layers/a/        board A layer by layer, plus layers.json: what the copper
                     viewer on index.html stacks (img/layers/s/: board S, on single.html)
copper.js            that viewer, the PCB tab — layer panel, pan and zoom, mirror, grid
img/3d/              a.glb and s.glb, each with a .json of what its caption says
                     and a .parts.json of what the part pane says (role, place,
                     pins and nets; `export_3d.py --parts-only` rewrites it alone)
board3d.js           the 3D tab on index.html and single.html — views, layer
                     toggles, hover a part to name it, click it for the part
                     pane on the right, find by reference
shell.js             the frame every page shares, after Altium 365: top bar, the
                     PROJECT tree read off each page's headings, the sheet bar,
                     the viewer's PCB / 3D tabs
style.css            the one stylesheet, A365's palette (sim/report uses it too)
vendor/              three.js r160 (MIT), the loader, controls and environment it
                     uses; fonts/: Inter, latin and greek (OFL)
spec.html            the specification
index.html           project status
```

The three tools are checked against each other before anything is written:
`gen_boards.py` will not emit a board if the floorplan overlaps itself, or if
any part is on the board and not in the netlist, or in the netlist and not on
the board, or if their footprints disagree, or if a symbol pin has no net.

Run `python3 tools/geometry.py` after changing any dimension, then
`python3 tools/gen_boards.py --force` to re-emit the boards. `gen_boards.py`
refuses to run at all if `placement.py`'s checks fail, so a board that overlaps
itself cannot be written.

`--force` rewrites the boards from scratch, which now means **throwing away the
routing**. Without it `gen_boards.py` keeps the existing `.kicad_pcb` and only
re-renders. `--force` also runs the stitcher and the fan-out, so the board it
writes is already half made; after it, re-run `python3 tools/route.py --rounds 4
--loose` for the rest. The moment you move a part or a track in KiCad by hand,
stop using `--force` at all.

## Hardware

Both projects are KiCad 9, DRC- and ERC-clean. Board origin is the shaft axis on
both, so the stack aligns.

| | Board A | Board B |
|---|---|---|
| Stackup | 6 layer, 2 oz outer / 1 oz inner | 4 layer, 2 oz outer |
| Diameter | Ø65 mm | Ø65 mm |
| Motor mount | 4 × M3 at (±12.5, 0), (0, ±9.5) | — |
| Stack mount | 6 × M2.5 at R = 29.5 | 6 × M2.5 at R = 29.5 |
| Placed | 136 parts, 78 front / 58 back, 10 DNP | — |
| Captured | 136 components, 87 nets, 425 connections | — |
| Routed | 1361 track segments, 278 vias, all 425 connections, **DRC clean** | — |

The F.Cu → In1 prepreg is **0.1 mm**, and that is a load-bearing number, not a
stackup detail: it is what puts the ground plane under the commutation loop and
holds loop inductance at 0.39 nH of PCB, 2.0 nH all in, 1.7 V of overshoot on
80 V silicon.

Design rules: 0.15 mm track and clearance everywhere except the 60 V nets, which
get 0.4 mm; vias 0.5/0.20 signal, 0.6/0.30 power, 0.8/0.40 phase; **0.5 mm
hole-to-hole**. That last one is deliberate — JLCPCB fills and copper-caps
via-in-pad for free on 6-layer boards, which is what lets the pad centres be
holes at all, but it wants 0.5 mm between them, and the sister project's F-48 is
the cost of discovering that after 253 via pairs were already too close.

Two of those numbers moved during routing, and both were wrong before:

- **0.15 mm, not 0.20, on the power and analog classes.** A 0.65 mm-pitch
  VSSOP-8 has 0.15 mm between adjacent pins, so a 0.20 mm class made every
  INA241 a clearance violation that no layout could fix. 0.15 is also JLCPCB's
  floor for 2 oz outer copper, so nothing is given up.
- **0.20 mm drills on the 0.5 mm vias, not 0.25.** The annular ring is
  (diameter − drill) / 2, so 0.25 left 0.125 mm against this project's own
  0.13 mm minimum. The default via was illegal under the default rules, and
  nothing noticed until there were vias on the board to check.

Verify after any change:

```sh
python3 tools/placement.py       # floorplan: overlaps, bounds, cross-layer
python3 tools/schematic.py       # netlist, and that it matches the placement
python3 tools/deadcheck.py hardware/motor_board/servodrive_A.kicad_pcb
python3 tools/route.py --rounds 4 --loose   # route headless, import, finish, DRC
cd hardware
kicad-cli pcb drc motor_board/servodrive_A.kicad_pcb
kicad-cli sch erc motor_board/servodrive_A.kicad_sch
kicad-cli sch export netlist --format kicadsexpr -o /tmp/n.net \
    motor_board/servodrive_A.kicad_sch     # then diff it against schematic.nets()
```

That last one is the check worth keeping: KiCad's own extracted netlist against
the one this repo intended, net by net and connection by connection. It is how
the sub-symbol naming bug below was caught, and it is the only thing that proves
the labels actually connect what they look like they connect.

## Routing

Most of board A is not routed and never will be. GND is two solid inner
planes, VBUS is a sector of a third, +3V3 is the rest of that third — the
CPU wedge and the whole centre disc — and each switch node and phase output
is a pour on F.Cu and B.Cu, because 20 A does not go down a track and
IPC-2152 wants about 6 mm of 2 oz copper for it. What is left is the
signals, and those go to freerouting — less the ones it turned out to do
badly, which are made first, as copper, and taken away from it.

Five tools do the rest, and freerouting is the fourth of them rather than
the whole story. They all run from `gen_boards.py` and `route.py`, so the
board is still reproducible from `geometry.py`:

- `tools/stitch.py` puts a via in every pad that sits over a plane or pour
  of its own net. 104 of them.
- `tools/fanout.py` makes the connections the router could not. It escapes
  the RP2350: a 0.15 mm stub from the centre of every one of its sixty
  pads and a via at the end of it, in planned staggered rows, the stub
  threaded through the rows in front of it. It joins the four DVDD pins to
  the 1V1 ring under the chip. It wires each FET's gate to its gate
  resistor — radially in from the gate, an arc along the band between the
  driver row and the FETs, radially in to the resistor's pad — and puts a
  via through that pad into the gate bleed's pad directly beneath it, so
  the whole gate net is copper. It wires SNSP and half of SNSN as arcs
  between the sense amplifier's two pad rows on the back, and joins SNSN's
  other half on In3. It gives the flash, the signal header and the
  amplifiers' remaining pads a stub and a via each. And it taps every pad
  on a plane net that is not over its plane — +3V3 mostly, and the few
  ground pads a via would not fit in — with a stub to a via that is.
- `tools/deadcheck.py` then proves that every pad on VBUS, the three switch
  nodes, the three phase outputs and +3V3 already touches copper of its own
  net.
- `route.py` hands what is left to freerouting: it takes the demoted nets
  and every net already finished away from the router entirely — their
  pours become keepouts, their In2 planes go, their pads lose the net and
  get a keepout halo the width of their clearance class.
- `tools/finish.py` is a maze router, and it goes last, on what the router
  gives back still in pieces — deterministically, at 45 degrees, every
  segment checked against the same clearance test the fan-out uses, and
  allowed to move the router's own wires to do it.

Every track and via the tools make is **locked**. That is not a courtesy
to the KiCad user: it is how `route.py` tells the tools' copper from the
router's on the way out and on the way back, below.

### The maze

The last few connections are not hard because the board is full. They are
hard because they are *last*: the lanes out of the QFN's via field are
spent on whichever nets reached them first, and the ones left over cannot
be routed by anything that will not move what is already there.
`tools/finish.py` is that: a maze router that knows the whole board, and
rips up when it has to.

It rasterises. One bitmap per routable layer —
F.Cu, In3 and B.Cu; In1 and In4 are ground and In2 is the power planes — at
0.05 mm, every foreign shape drawn as it is and then grown by the clearance
it wants, 0.15 mm or 0.4 for the 60 V nets, by a distance transform. Then
A\* from one piece of the net to the other, eight-connected so that every
step is 45 degrees, with a via as a move between layers wherever a via
would fit. The path that comes back is a chain of cells, which is not what
anyone wants on a board, so it is folded back into as few segments as the
free space allows: from each point, the farthest point it can be joined to
by one straight run and one diagonal.

Two things it has to know that KiCad's clearance rules do not cover, and
both were found by DRC after the first run:

- **The board edge.** The clearance test is between copper and copper; the
  outline is neither. The maze knew the edge and the simplifier did not, so
  it cut a corner 0.4 mm outside the board — twice, on two layers. The
  simplified path answers to the grid as well as to the clearance test now.
  (And the edge radius comes from the outline circle rather than from the
  board's bounding box, which is bigger by half the outline's 0.1 mm
  stroke.)
- **A bare drill.** An M3 mounting hole is NPTH: it has no copper, so no
  copper clearance rule mentions it, and `min_hole_clearance` is the only
  thing keeping a track off it. Seven tracks ran within 0.16 mm of the four
  mounting holes before the grid was told about drills.

The grid is a rasterisation, so it is conservative by 0.03 mm — a little
over half a cell, which is what a drawn boundary is worth. What it proposes
is still checked exactly, segment by segment; what the exact test rejects
is punched out of the grid and the route planned again. In practice one
step in seven hundred comes back, short by a micron, and the detour costs
nothing.

When a net cannot be made at all, the maze rips up: every unlocked track
near the gap — the router's copper, never the tools' — comes out by whole
nets rather than by the segments that happen to fall in the box, the
connection is made, and then the nets that were displaced are made again,
longest first, each of them allowed to rip up in turn. If any of them
cannot be, the whole attempt is undone and the box grows.

**Undoing it is the part that was wrong.** The first version recorded which
items were on the board and which had been taken off, and undid an attempt
by deleting everything that had appeared since and putting the recorded
ones back. That is correct until an attempt rips up inside an attempt: the
inner one puts its copper back as *new* items, the outer one then deletes
them as things that were not there before, and they are in nobody's
record. The log said four connections made; KiCad said forty-one
unconnected. The undo copies the whole board now, and there is a guard
around the whole pass: KiCad's own count of what is unconnected, before
and after, and if the maze has not improved it the pass is thrown away and
the board stands as the router left it.

**Order matters more than either router.** Given the whole board first,
with only the fan-out's copper in the way, the maze makes 49 of the 61
nets and leaves 12 — and freerouting, handed a board full of locked
copper, then closes one. Given what freerouting leaves, six connections,
it makes four. Seeding it with those six by name and running it first came
back with nine. It does not matter which nets get the lanes out of the via
field; it matters how many lanes there are.

### Why the fan-out is a tool and not a pass

A 0.4 mm-pitch QFN cannot take a via per pin in place. The pads are 0.2 mm
wide with 0.2 mm between them, a via is 0.46 mm across and wants 0.15 mm
around it, and every via row that goes in front of the chip takes lane
width from the stubs that still have to pass it. The arithmetic: with
0.15 mm stubs, a row of vias every fourth pin leaves 0.84 mm between two of
them, and the three stubs that pass through need 0.75. That is the whole
margin, 0.09 mm, and it is why the fan-out is four rows deep.

It is also why the stubs are threaded rather than bent. A stub passing a
neighbour's via steps 0.06 mm aside for the length of that via and comes
back; held aside across a whole row-to-row interval, two neighbours passing
vias on opposite sides of themselves met in the middle, 0.03 mm too close.
The search is a small dynamic programme over (waypoint, lateral offset)
that strays from the pad's axis as little as it can, and it finds 51 of the
52 vias in about five seconds.

### The via in the pad

For every SMD pad whose net has a filled zone on some other layer directly
beneath it, the stitcher puts a via in the pad. That is via-in-pad, which
JLCPCB fills and plates over for free on six layers — which is the only
reason a pad centre is allowed to be a hole. It places 104 and
leaves 146 connections to the fan-out and the router.

It is worth doing by hand because the router does it badly. Given the board
whole, freerouting queued all 112 ground pads as isolated, threw the vias
away and ran 0.2 mm traces across the top and bottom layers instead — 92 of
the 177 connections it could not finish on the first board were this.

Two rules matter and the first version of the stitcher got the second wrong:

- the via's pad has to fit inside the SMD pad, and its hole has to keep
  `min_hole_clearance` from foreign copper;
- **the via's pad has to clear foreign copper on every layer, not just the
  pad's.** A through via's annulus exists on all six. Fitting inside an F.Cu
  pad says nothing about what the same copper does on B.Cu, and checking
  only the drill put 33 clearance violations on the board.

### What the round trip got to decide

Everything below was found by measuring the result, not by reading about it.

- **Freerouting does not treat a Specctra `plane` as connecting anything.** So
  ground is taken off the table before export: the planes go, the net goes, and
  In1/In4 are declared `power` layers, which is Specctra's way of saying *not
  for routing*. The pads stay, as obstacles with no net, which is exactly what
  they are to a router that is not routing them.
- **A `plane` is not an obstacle either.** `ignore_conduction` defaults to
  true, so given the 20 A pours as planes the router lays a foreign track
  straight through them and KiCad cuts the pour around it. Every pour is a
  keepout by the time the router sees it, and every pad on those nets is
  stitched to its pour first.
- **A protected via is protected on its pad's layer, by the pad, and nowhere
  else.** The ground vias are removed from the DSN and each leaves a
  keepout on the four signal layers, or the router runs a trace under the
  pad on In3 and the via lands on it when the stitch is restored.
- **KiCad's SES import takes a via's diameter from the Specctra padstack and
  its drill from the netclass.** Those are not the same thing. A stitching via
  in a pad too small for the 0.8/0.40 the Phase class wants is 0.46/0.20, and
  it came back 0.46/**0.40** — a 0.03 mm annular ring, fifteen of them. The
  diameter is the honest half of the pair, so `route.py` rebuilds every drill
  from it after import.

  The first attempt at this was to stop offering the 0.46 padstack in the DSN,
  and it made things worse in a way worth recording: freerouting then wrote a
  session that *used* that padstack for the pre-placed vias without *defining*
  it, and KiCad's importer rejected the whole file with no message at all.
- **Specctra has no hole-to-hole rule.** `via_via` and `via_pin` copper
  clearance stand in for it: 0.3 mm between two 0.5/0.20 vias puts their holes
  0.5 mm apart, which is the rule this board is built to. And a class with a
  rule of its own does not inherit them from the structure rule — measured
  as two vias on Analog-class nets 0.48 mm hole to hole — so every class
  rule carries both.
- **The router routes right up to the boundary it is given.** The outline is
  pre-inset by `min_copper_edge_clearance` before export; the board is a circle
  about the shaft axis, so the inset is exact.
- **`PAD::GetLayer()` answers F.Cu for a pad on a flipped footprint.** Only the
  layer set knows. Believing it put fifteen vias between a B.Cu pad and a B.Cu
  pour, which DRC reports as "connected on only one layer", and is.
- **KiCad's DRC report names items by uuid**, and a library footprint
  embedded verbatim carries the library's pad uuids, so six FETs shared
  them and the unconnected list named the wrong FET's gate for three runs.
  Every embedded item gets a uuid of its own now.

### What freerouting turned out to do

All of it measured, most of it by reading the router's log and some of it
by reading its bytecode, after four runs in a row stopped at the same
thirty connections.

- **A wire touches a pad only if it ends on the pad's centre, exactly.**
  `Trace.get_normal_contacts` compares the wire's end with
  `DrillItem.get_center()` using `equals`. Freerouting computes that centre
  itself, from the placement rotation, in doubles rounded to its 0.1 µm
  grid; KiCad writes wire coordinates to the whole micron. So a stub that
  started 0.05 mm inside its pad was a separate island to freerouting, and
  so was one that started on the pad's centre: it then queued 119
  connections between a pad and its own stub, could insert none of them,
  and spent every pass's ripup budget on them. `route.py` now rewrites
  every wire end within 30 µm of a pin or a via of its net to the centre
  freerouting will compute. The count of incompletes it starts from fell
  from 196 to KiCad's own.
- **In 45-degree mode it cannot leave a pad that is not on the grid.** Its
  search tree wraps a tilted pad in the pad's bounding octagon. On a round
  board every pad is tilted; for the FETs' 0.85 × 0.5 mm gate pads the
  octagon plus clearance runs into the halos beside them ("no accessible
  expansion doors", every gate, every pass), and for the flash's 0.5 mm
  pitch and the amplifiers' 0.65 it overlaps the neighbours. Free-angle
  mode is exact and routes them, and a pass of it took twenty-five minutes
  where a 45-degree pass takes five seconds — so those pads are not left
  to it.
- **A protected wire is a bad target.** The maze search reaches the stub
  before the via at its end, and a junction into a protected trace cannot
  be inserted ("the new connection could not be inserted", 791 times in
  one run); a three-segment protected polyline joined twice sends
  normalization round a loop for ever (32,000 "max normalization depth"
  lines in forty seconds, in a board of four pads; the gate arcs did the
  same to the real board, pass 1 never finishing). So the router is not
  shown the stubs at all: a pin that has its stub and its via leaves its
  net's pin list, the stub becomes a keepout, and the net's end is the via —
  a circle, on the grid at any angle, nothing to tee into. A net with no
  pins left and two vias routes fine. The nets the fan-out finishes
  outright — the gates, bleed and all, SNSP, SNSN, the 1V1 link — are
  demoted like the 60 V ones, by KiCad's own DRC just before the export.
  Passes went from thirty-five seconds to five.
- **Its last pass can undo what its earlier passes did.** An attempt that
  rips up neighbours and then fails does not put them back, the pass-end
  tally does not count them, and the session comes back with three or
  seven nets that have their vias and no wire. So `route.py` runs
  rounds: each exports the board as it stands and routes what is left.
- **A locked track exports as `(type fix)`, an unlocked one as
  `(type route)`**, and that is the only handle there is on *my copper
  versus the router's* across the round trip. A strict round protects
  both. The rounds after the first are *loose*: only the tools' copper is
  protected, the router's own earlier wires stay `(type route)` and are
  its to move, and only nets whose copper is all the tools' are demoted.
  It does not rescue the last few nets — told it may move its earlier
  wires, freerouting reports "board state has not changed since pass #1"
  and stops, because every alternative it can see costs more than what it
  has — but it is what makes the rounds safe to repeat. And since KiCad's
  session import keeps a locked item and the session carries its own copy
  of some of the protected vias, moved by up to 25 µm — the difference
  between the fan-out's 0.1505 mm clearance and a violation — the copy
  goes and the original stays, and the router's tracks end inside its pad
  regardless.
- **A class naming a net that no longer exists is fatal, silently.** Take a
  net out of the network but leave its name in a `(class ...)` list and
  freerouting reads nothing, reports one unrouted net and writes an empty
  session.
- **`-inc`, the ignore-net-classes flag, does nothing in 2.2.4 headless.**
  Measured: identical counts with and without it.

### Where it got to

**1361 track segments, 278 vias, every one of the 425 pin
connections made.** DRC finds nothing, ERC finds nothing, KiCad's own
exported netlist matches the intended one net for net, no foreign copper
anywhere under the heatsink land, every annular ring legal, and no
right-angle corners. 212 of the vias are the tools' —
112 stitched into pads, the rest the fan-out's — and freerouting
made the other 66. That is the 2026-09-22 board; the one before it was
1351 segments and 283 vias, and both finished in freerouting's first round.

| | 60°, parts in the channel | 68°, first pass | 68°, 2026-09-11 | **68°, this board** |
|---|---|---|---|---|
| Connections beyond a via in the pad | 170 | 170 | 146 | 143 |
| Left open | 74 | 101 | 0 | **0** |
| DRC | clean | clean | clean | clean |
| Right-angle corners | 2 | 0 | 0 | **0** |

The first routed board blamed the driver ring for the 101 it could not
make. It was wrong: the ring got busier, but nearly every one of the 101
ended at the RP2350, and the layout section above says what was done about
that.

### The 2026-09-22 changes

Four of the simulation's hand-back items, taken together so that the routing
was thrown away once:

- **Every low-side barrel reaches B.Cu.** The switch-node pour on the back
  stopped at R 25.2 and the low side's 4 × 4 array runs out to R 28, so only
  the inner row touched it — the other twelve sat in the clearance between it
  and the phase-output pour, joined to F.Cu and nothing else. The pour now has
  a tab over the array, cut in the FET's own frame (`gen_boards.via_tab`): to
  the centres of the end columns tangentially, so the fill reaches every pad
  and no further, and out past the outer row radially, where clearance holds
  it off the motor-lead pad.
- **The high side has twenty barrels** (`TDSON-8-1_ThermalVias_HS`): a fifth
  row at 0.905 mm, the array moved 0.15 mm along the pad away from the source
  pins, and the via pad trimmed from 0.80 to 0.78 mm so five rows fit the
  4.41 mm pad. It is the only way to add barrels. A fifth *column* lands
  inside the shunts' courtyards on the back under either FET, and a fifth row
  under the low side comes 0.14 mm from the lead pad.
- **An RC snubber per cell**, 2.2 Ω + 470 pF C0G, DNP: `R{n}13` and `C{n}09`
  on the back under the high side, tangential, the capacitor's ground pad on
  a via, the resistor's other pad on the switch-node pour, their middle node
  (`SNUB_x`) a small pour of its own in the Phase class. 0603, not the
  simulation's 0805: an 0805 pair's corner comes within 0.25 mm of the DC-link
  capacitor's VBUS via, and radial they take the room the fifth row needs.
- **A Ø6 magnet.** Nothing on the board reads `MAGNET_D`, so the hand-back's
  "the keepout moves" was wrong; only the drawings changed.

And one of the decisions made the same day reached the board: **both input
pulls of every driver go to GND**, so a reset brakes. That took the LIN
pull-up's +3V3 via away from the thermistor beside it, whose plane tap had
been sharing it; the fan-out's plane taps gained a last resort for
two-terminal parts (`fanout.dogleg`) — a first leg at any of the eight
45-degree headings, then straight to the nearest via that lands on the
plane — and only for the pads the ordinary taps cannot make, so every other
tap is laid exactly as before.

What it bought, from the simulation's conduction solver (P3) re-run on the
routed board in a scratch copy — `sim/results/` itself still describes the
board before:

| | before | after |
|---|---|---|
| Low-side barrels joined on B.Cu, per cell | 4 of 16 | **16 of 16** |
| Busiest low-side barrel, 20 A RMS | 5.16 A rms | **2.82 A rms** |
| Busiest high-side barrel, 20 A RMS | 2.42 A rms (16) | **2.10 A rms** (20) |
| Switch-node copper, cell A | 0.74 W | 0.61 W |
| Switch-node current density, p99.9, B.Cu | 259 A/mm² | 199 A/mm² |

Better, and still over the 2 A a 0.4 mm barrel is allowed at a continuous
20 A: the current crowds into the barrels nearest the shunts, so four times
the barrels bought less than twice the margin. By the same guideline the
low-side arrays carry about 14 A RMS continuously and the high side 19 A;
above that is burst territory, which is how the drive is now meant to be used.

### The last three, and why they were arithmetic

For a long time the board stopped at two or three open connections, and
they moved around. Freerouting left six; the maze made four of them.
Running the maze first instead, on a board with only the fan-out's copper
in the way, made 49 of the 61 nets and left 12 — and freerouting, handed a
board full of locked copper, then closed one. Seeding the maze with the six
by name and running it first came back with nine. Three routers, three
different sets of survivors, the same order of magnitude: that is the shape
of a capacity limit, not of a search that needs more time.

The capacity was the lanes out of the QFN's via field, and it was
countable. The escape vias sat in four rows 0.71 mm apart. A 0.15 mm track
passing *radially* between two rows needs 0.23 mm to clear a via's pad,
0.15 mm of clearance and half its own width, on each side: **0.91 mm
between row centres, and there was 0.71.** So there was no radial lane
anywhere in the field, and all sixty escaped signals had to leave through
the tangential gaps within a row — 1.14 mm between two vias, which holds
two tracks. The last few connections were the ones that ran out of them.

Two lines of the fan-out closed the board:

- **The outermost row moved out by 0.20 mm** (`LANE` in `tools/fanout.py`),
  which makes that one gap 0.91 and buys a radial lane the whole way round
  the chip for 0.2 mm of extra depth. A lane between *every* pair of rows
  would want 0.95 mm pitch, 3.5 mm of fan-out against today's 2.8, and
  another 0.6 mm of clear band on every side of the chip — the wedge
  rebuild again, one notch further. It was not needed.
- **The pins the planned rows could not place get a second try** from the
  generic escape, which is not fussy about which row a via lands in. That
  was one pin, LED_R — and a QFN pin without a via is a pin no router can
  ever leave, because the pads are 0.4 mm apart and a track with its
  clearance needs 0.45. One pin, one connection, and it was the last one.

With both, freerouting finished the board in its **first round** and the
maze found nothing left to do.

### 45 degrees, and no stair steps

The imported tracks are measured, not assumed: `route.py` buckets every
segment by direction and counts corners where a horizontal segment meets a
vertical one. Any that survive are chamfered — both new endpoints lie on
the old track and the diagonal between them lies inside the corner it
replaces, so a chamfer only ever removes copper and can never create a
clearance violation.

What is on the board: **345 segments at 45°, 361 orthogonal,
645 at neither**, and the last are the tools' — pad exits along a
rotated pad's own axis, the gate arcs, the sense arcs, and a fan-out that
is radial to a chip at 270° on a board where nothing is on the grid.
**No right-angle corners at all**, and 9 tees — a branch leaving a
run at 90°, which is a junction and not a stair step.

Four things are repaired after the round trip, and all four are
deterministic passes rather than judgement:

- **degenerate segments.** Specctra leaves sub-micron stubs — a corner point
  emitted twice, half a micron apart — and each turns a clean 45° bend
  into a right angle that cannot be chamfered because there is nothing to
  cut.
- **hole-to-hole.** Specctra cannot express the rule, so a few drills come
  back 20–40 µm too close. Narrowing one via's drill changes no geometry
  at all; where that is not enough the via is nudged, checked against
  everything around it.
- **unused vias.** An escape the router did not use — it found its own way
  to the pad — is copper on one layer, and KiCad is the judge of that: the
  via goes, then the stub that led to it.
- **pour islands.** Routing cuts the ground pours into pieces; each one that
  has no via of its own gets one, and the tools' plane taps are laid again
  wherever the round trip lost one.

## The schematic

Board A is captured: 136 components, 87 nets, 425 pin connections, across four
hierarchical sheets. ERC is clean — zero errors, zero warnings — and KiCad's
exported netlist matches `schematic.nets()` exactly.

It is **generated, and drawn with labels rather than wires**: parts on a grid,
grouped by function, a 2.54 mm stub and the net's name on every pin. That is an
honest description of what it is. Routing wires between 115 parts automatically
produces something worse to read than a table, and the value here is a netlist
that cannot drift from the placement — the designators, the footprints and the
DNP flags are one source of truth, checked both ways on every build.

What capture found, in the order the schematic asked for it:

- **The RP2350A was missing everything it cannot run without.** No inductor on
  `VREG_LX` for the core buck, no cap on DVDD, no filter on `ADC_AVDD`, no
  pull-up or cap on `RUN`, no series resistor on `XOUT`. Eight parts, now placed
  — the decoupling on the back, directly under the power pins.
- **The bus divider was wrong by 2.3×.** 50 k / 5.1 k puts **5.5 V** on a 3.3 V
  ADC pin at a 60 V bus. It is now 112 k / 4.7 k — 2.42 V at 60 V, 3.18 V at the
  TVS clamp — and the high leg is **two** resistors, because a 0603 is rated
  50 V working and the bus is 60.
- **`FAULT_n` floated.** The overcurrent comparator is an open question, and an
  unfitted comparator leaves a CPU input with nothing on it. A 10 k pull-up is
  the "not fitted" state.
- **`USB_VBUS_DET` arrived from board B and went nowhere.** It is on GPIO22 now,
  which was spare.
- **PD's I²C was on two pins that are not an I²C pair.** SDA on GPIO19 and SCL
  on GPIO20 are I2C1's SCL and I2C0's SDA; the FUSB302 could only have been
  reached by PIO. I2C0 is 20/21, so the three PD names were rotated — INT on
  19, SDA on 20, SCL on 21 — on the routed copper itself, and J9's pins 9, 10
  and 12 went with them. No re-route; DRC, ERC and the netlist diff are clean
  after it. Found 2026-09-22 while planning a single-board variant.
- **The signal link is 20 signals and 8 grounds**, not the "18 + 10" the spec
  claimed. The count was never done.
- **The phase TVS were in the parts table and not on the board.** Three added.
- **The MOSFET's symbol and its footprint disagreed.** KiCad's own
  `Transistor_FET:BSC030N08NS5` numbers its pads 1–3 source, 4 gate, 5 drain;
  the sister project's `PowerPAK_SO-8_123` numbers them 1/2/3 = S/G/D. Using
  both would have wired every FET wrong. Board A now uses KiCad's matched
  symbol/footprint pair — which also has a larger drain pad, so the thermal via
  array got easier.

### Seven things the KiCad file format got to decide

All seven were found by DRC, ERC or the netlist diff, and fixed in the
generator rather than worked around:

- **Every pad needs its own absolute angle.** KiCad does not derive pad
  orientation from the parent footprint. Leave it out and pad *positions* rotate
  while pad *shapes* do not — on a round board, where almost nothing sits at 0°,
  that is every fine-pitch part's copper turned the wrong way. It showed up as a
  1.27 mm-pitch SOIC-8 reporting 0.0000 mm between adjacent pins at 90° and
  nothing at all at 45°.
- **A custom pad needs the angle too**, separately, or its primitive polygon is
  evaluated unrotated — which put the heatsink lands off the board edge.
- **`net_settings.classes` wants `microvia_*`, not `uvia_*`, and a `priority` on
  every class.** Get it wrong and KiCad discards the whole list and silently uses
  its factory 0.2 mm netclass. The symptom is DRC quoting a clearance rule that
  appears nowhere in the project file.
- **`lib_footprint_mismatch` is muted**, and only that one. Every footprint is
  embedded verbatim from its library, but KiCad re-derives geometry through the
  placement angle, so a part at 37.5° mismatches an identical library copy.
  Checked in isolation before muting.
- **A schematic's `lib_symbols` names units by the *bare* symbol name.** The
  entry is `Device:R`, but its units are `R_0_1`, not `Device:R_0_1`. Get it
  wrong and the sheet fails to load — and in a hierarchy that failure is
  **silent**: the root still opens, ERC still reports zero violations, and the
  netlist comes out empty. Nothing but exporting the netlist and counting would
  have caught it.
- **A sheet has two different uuids.** The `(uuid)` inside the `.kicad_sch` is
  the file's; the path a symbol instance quotes is `/` plus the uuid of the
  `(sheet ...)` element in the *root*, with no root uuid in front of it. And the
  root's own sheet entries take `(path "/")`.
- **Two label stubs on the same coordinate weld two nets together**, quietly.
  The first layout pass put +12V and VB_A on the same point. Cells are now sized
  from each symbol's own pin extent, and the emitter refuses to place a label
  where a different net already sits.

## Board S, one board

Board A and the minimum of board B on one Ø65 mm board, built as a
**variant of board A from the same generators** (decided 2026-09-22): the
three phase cells, the CPU wedge and the encoder are board A's, and the two
link wedges and the middle of the board carry the bus input, the bulk, the
TVS, both rails, USB-C, the RS-485 relay and an expansion header for a
stacked PD or Ethernet board. 48 V operational max. It lives in
`hardware/single_board/`; [single.html](single.html) is its page, with the
copper viewer and the 3D viewer.

```sh
python3 tools/placement_s.py            # the floorplan, img/board_s_*.svg, single.html's tables
python3 tools/schematic_s.py            # the netlist and its checks
python3 tools/gen_boards.py --board s --force   # emit, stitch, fan out -- destroys routing
python3 tools/route.py --board s --rounds 1 --first EN_12V,USB_VBUS,LIN_A,EXP_GP19
                                                # the maze on four, freerouting, the maze
python3 tools/route.py --board s --polish       # twice: the second pass reads the first's DRC
```

`gen_boards.py` with no `--board` still writes boards A and B only, and board
A's generated output is byte-for-byte what it was before board S existed --
checked by diffing the emitter's text, not by eye.

### Capture

`tools/schematic_s.py` builds the netlist out of `schematic.py`'s own
functions: the phase cells (clamps to the 54 V grade), `control()` with the
GPIO remapped, `encoder()`, the mechanical parts plus two bus pads, and two
new sheets, `04_power` and `05_io`. 191 components, 111 nets, 577 pin connections; ERC clean, and KiCad's exported netlist matches `schematic_s.nets()` connection for connection.

Reading the LMR38010's datasheet (SNVSC73B) at capture changed three of the
sketch's values, all of which would have been wrong on the board:

- **VREF is 1.000 V.** The feedback bottom legs are 9.09k (12 V) and 24.9k
  (5 V), the datasheet's own table; the sketch's 11k and 27k made 10.1 V and
  4.7 V.
- **RT/SYNC may not float or be grounded.** Each buck has a 64.9k to ground
  now, 400 kHz; the sketch's modules had no RT resistor at all.
- **EN rises at 1.25 V (1.4 V max).** 470k over 22k would not have started
  the gate rail below 26 V. It is 470k over 68k: on at 9.9 V typically, 11.1 V
  worst case, off at 8.7 V -- inside the 12 V floor -- and it holds the
  2N7002's drain to 11 V when the TVS clamps at 87 V.

**The bucks' passives are TI's design points** (table 9-1 and sec. 9.2.2, 400
kHz from a 48 V bus), since 2026-09-23. The sketch's small parts -- 1 µF in,
a 3 × 3 33 µH inductor, one 22 µF 0805 out -- were well off them, and the
internal compensation is tuned for TI's L and C<sub>OUT</sub>: a 22 µF 0805 is
a few µF at 12 V. Now:

| | 12 V gate rail | 5 V logic rail |
|---|---|---|
| Inductor | 68 µH, SWPA4030S680MT (4 × 4, 0.72 A sat.) | 33 µH, SWPA4030S330MT (4 × 4, 1.1 A sat.) |
| Peak current at the spec's load | ~0.3 A at 0.1 A | ~0.7 A at 0.5 A |
| Output | 22 µF/25 V 1206 + 22 µF/25 V 1210 X7R, ~20 µF left at 12 V (TI: 22 nominal, 15 minimum) | 3 × 22 µF/25 V 0805 (TI: 3 nominal, 2 minimum) |
| Input | 100 nF/100 V 0805 at the pins + 4.7 µF/100 V 1206 X7S (TI: 4.7 µF minimum, 100–220 nF at the pins) | same |

L is the datasheet's equation 10 with the ripple taken on the part's 1 A, as
TI says to for light loads; both are well above the subharmonic minimum and
give 0.2–0.35 A of ripple, above the 10 % current mode needs. The effective
capacitances are estimates from typical DC-bias curves; the maker's figures
were not to be had.

**GATE_EN is GATE_OFF on board S.** The 12 V buck's EN sits on a divider from
the bus, up to 8 V, which no GPIO may see, so the 2N7002 the sketch proposed
does the pulling -- and that inverts the GPIO's sense: GPIO14 *high* kills the
gate rail. The net is named for what high does. Through a reset the pin is
pulled down (4k7, against RP2350-E9, like the drivers' pulls), the FET is off,
the rail is on and the low sides brake -- which is what spec §3 asks of a
reset. Firmware writes 1 to kill instead of 0.

Two symbols the stock library does not have: the **LMR38010**, drawn from the
datasheet's pin table (1 GND, 2 EN, 3 VIN, 4 RT/SYNC, 5 FB, 6 PG, 7 BOOT,
8 SW, EP) because the LMR336x0/LMR36510 that share its package do not share
its pins; and the **SIT3088**, derived from the MAX3485 as the sister project
wires it, so the library and the sheet hold the same flattened symbol and ERC
has no stock `extends` to compare against.

### Layout

`placement_s.layout()` is the sketch's search frozen by one set of options
(`LAYOUT`). Building a real board from it found what a courtyard sketch cannot:

- **A through-hole lead is copper on both faces, at 60 V.** The sketch tested
  the cans' leads as circles of half a pad side plus 0.2 mm, and C1001's +
  lead -- a 2 mm square pad, 1.41 mm on its diagonal -- landed on U15's pin 7
  on the other face. Leads are now their pads' bounding circles plus 0.45 mm.
- **The sister project's DFN-8 courtyard is smaller than its pads** (±1.6
  against ±1.72): two SIT3088 sat 0.05 mm pad to pad with their courtyards
  apart. Widened for placing.
- **60 V parts keep 0.25 mm more** from their neighbours: the EN divider's
  top resistor had its VBUS pad 0.36 mm from two ground pads.
- **The buck module was drawn before the pinout was read.** It had its input
  cap by RT and its divider on the input side. It is now laid against the
  pins, TI's own example layout (fig. 9-16): the input cap left of pins 1–3,
  RT under pin 4, the divider at pin 5, the bootstrap cap at 7–8, the
  inductor over the switch corner. EN is between GND and VIN, and on board S
  it leaves as a 0.15 mm track between the input cap's pads -- 0.40 mm from the
  VIN one, if the cap sits 0.03 mm low -- to the divider beside it. On the
  back the module is mirrored, as its footprints are, and it turns in 15°
  steps; with the divider in it, it fitted nowhere rigid, and it places with
  1.2 mm of give per part.
- **TI's parts do not fit a rigid module.** The signal wedge held the small
  one with fractions of a millimetre to spare; a 4 × 4 inductor alone, or the
  4.7 µF alone, left both bucks nowhere. So `put_module` now keeps rigid only
  what has to be on a pin (the 100 nF, bootstrap, RT, feedback divider and EN
  divider) and places **satellites** after them -- the inductor with its SW
  pad within 4.5 mm of pin 8, the first output cap by the inductor's output --
  each at the nearest legal spot, square to the module. The rest are **near
  parts**, placed last, anywhere on either face that puts them on their copper,
  so the USB front end keeps the room it needs by the connector. Three still
  had none in the link wedges -- both 4.7 µF caps and the 12 V rail's second
  22 µF -- and go in the **outward centre** (decided 2026-09-23): ceramics,
  nothing switching or ferrous, and never in front of the CPU: between the
  M3 head at (0, -9.5) and C1002 is the outward face's one way from the
  RP2350 to the header, encoder and LED, and the 12 V cap there left five of
  them unrouted. The 4.7 µF caps are 10–18 mm from their bucks, beside phase
  A's DC-link caps; across the In1/In2 plane pair that is a
  nanohenry or two, nothing at 400 kHz, and the 100 nF at each IC's pins takes
  the edges. The EN divider may slide along its channel from pin 2, not across
  it: flexed a millimetre across, its VBUS pad sat in the EN track's way.
- **The bucks must not stack.** The 12 V module on the signal wedge's back
  over the 5 V one on its front left the divider's VBUS pad with nowhere to put
  a via on either face.
- **The cans' + leads point outward**, onto In2's VBUS at R 17, the short way
  to the bridges; the TVS's cathode faces the VMOT pad.
- **The RS-485 relay is placed for its wiring.** Port IN takes the rim slot at
  241° beside its two transceivers and OUT the one at 219° beside its own (the
  sketch had each port's pairs crossing the other's); every SIT3088 is turned
  so its A/B pins face its port, its 100 n goes past the end of its pin row and
  its 120 Ω beyond its escape vias. The four chips fill the quadrant of the
  motor-facing centre between the two M3 standoffs, C1001's leads and the
  magnet keepout; with the ports the other way round the fourth has no room.

`gen_boards.py --board s` adds three pieces of copper board A never needed:

- **In2** is VBUS everywhere but the CPU wedge (306° round through 0 to
  254°) and +3V3 over the CPU wedge and the centre disc, with a tab reaching
  in over each can's + lead and a finger in under each centre 4.7 µF's VBUS
  pad: VBUS is plane-only, never routed, so that is its way in. Board A's signal-wedge ground on In2 went: the
  bucks are there now, and they want the bus.
- **The VBUS spine** on the outward face: the VMOT pad, the TVS's cathode,
  C1001's + lead and the expansion header's four VMOT pins as one piece of
  copper. The header is on the axis, inside In2's +3V3 disc, and a PD board can
  put 5 A through it; this is its way to the bus.
- **The bus pads**, 14 AWG soldered flat, are arcs like the phase pads. Their
  barrels are not in the footprint: RS-485 port IN's mounting tab is under the
  VMOT pad on the other face, and a fixed array came through onto it.
  `fanout.bus_field` drills a lattice at whichever offset lands the most,
  carrying on into the spine where the far face is clear: 7 barrels for VMOT (5 in its pad, 2 in the spine beside it), 8 for GND.

The fan-out gained two passes and eight escapes: `near_links` lays each buck
module's own connections -- VIN to the input cap, the switch node through the
bootstrap cap to the inductor, BOOT, FB, RT, the output, pin 1 to the input
cap's ground -- as locked copper, since a module turned in 15° steps is off the
grid freerouting needs; `bus_field` above; and the USB-C, the two SH 6 ports,
the USB ESD part and the four SIT3088 get an escape via per pad, as the flash
and the INA241s do. The SIT3088s were found the hard way: 0.28 mm pads at
0.65 mm, and on the first four routes the router reached pins 1, 4, 6 and 7 of
none of them, which is the INA241's trap from board A again. Plane taps now do
supply pins before ground -- a ground tap laid first crossed the only way from
a transceiver's VCC pin to its capacitor -- and a pad that cannot reach its
plane is joined to a neighbour of its net that did.

**Three bugs in the tools, found here and not on board A:**
`fanout.legal` held a 60 V track only 0.15 mm from ordinary copper -- the wide
clearance applied when the *obstacle* was a 60 V net, never when the new copper
was, and board A's tools never laid 60 V copper -- and neither it nor
`stitch.py` knew that a footprint can ask for more than its netclass (TI's
PowerPAD footprint carries 0.2 mm). And **the Specctra session rounds every
part's rotation to a whole degree** -- the DSN says Q1 is at -68.403°, the session
says 292 -- and KiCad's import turns the parts to match: on the routed board A
today 93 of 140 footprints sit up to 0.48° off the generator's angles (the FETs
0.40°). Board A is DRC-clean as it stands and was not touched; `route.import_ses`
now puts every part back, so it straightens the next time it is routed. On
board S it was a USB-C pad moved 0.024 mm onto the fan-out's stub beside it.

### Routing

The board is routed by the same pipeline as board A: `route.py --board s
--rounds 1 --first EN_12V,USB_VBUS,LIN_A,EXP_GP19` has the maze make those
four nets first, hands what the tools did not make to freerouting, one strict
round, and the maze (`finish.py`) takes what comes back in pieces.
**1804 track segments and 405 vias, every one of the 577 pin
connections made, DRC clean** (no errors, no warnings), nothing but ground
under the heatsink land, no right-angle corners. 306 of the vias are the tools'.

It took more than board A did, and what it took is worth keeping:

- **Freerouting is deterministic for a given board.** Four identical runs gave
  four identical results; what changed the outcome between runs was always a
  change to the board. So the tail is attacked by changing the board, and
  variants are run side by side in scratch copies with `route.py --pcb COPY`
  (and `--first NETS` to have the maze make some nets before the router).
- **The last nets were placement, not routing.** The RS-485 pairs were the
  leftovers until the transceivers got escapes; EN_12V until the 2N7002 sat
  beside the divider it pulls; the expansion header's GPIO until its signal
  pads got escapes too -- its north row faces away from the CPU.
- **`route.py` now saves atomically.** pcbnew once segfaulted inside
  `SaveBoard` after a long maze pass and left a 0-byte board; the board is
  written to a temporary name and renamed over.
- `--loose-first` makes the first round loose, for a board that is already
  routed; like the loose rounds on board A, it did not close the last two
  here.
- **With TI's buck parts (2026-09-23) the plain run ends five short**, all of
  them the RP2350's way into the centre -- the header's GPIO, the encoder's
  clock, the LED, FET_TEMP -- and a second strict round changes nothing, a
  loose one runs out the hour. Two things closed it: the 12 V rail's overflow
  cap had landed in the one gap on the outward face between the M3 head at
  (0, -9.5) and C1002, and the centre's overflow now keeps out of the CPU's
  sector; and the maze makes EN_12V, USB_VBUS, LIN_A and EXP_GP19 before the
  router, which with the 2N7002 15 mm from its divider (no room beside it on
  the module's face now) is what leaves the router a board it can finish.
- **`spread_holes` could not see a router's via** at 146.6926 mm, which the DRC
  report gives as 146.693: it matches the nearest hole now, and when the
  offending via is boxed in it moves the other one of the pair, a little off
  straight-apart if it has to (here the header's +5V escape via, 35 µm).

**How the board on disk was made, exactly** (2026-09-23): from scratch, by
the commands at the top of this section, from the generators as committed,
with two footnotes. The route ran on a board generated one change earlier --
In2's +3V3 disc keeping its islands -- and that zone's flag was set in the
routed file to match, the fill redone; nothing else in the generator's output
differs. And the maze left one 0.135 mm stub of EXP_GP21 dangling, which
`route.drop_dangling` took off (`settle` does that after a maze, `--polish`
does not). DRC (all severities, schematic parity at error level), ERC, the
netlist diff and deadcheck all pass on the result. The board before this one,
with the small buck parts, is in `snapshots/2026-09-23-pre-buck-passives.tar.gz`.

### Not checked

- **The bucks' loops, on the bench.** The passives are TI's table-9-1 values now (see Capture), but the effective output capacitance at bias is an estimate, and TI asks for a load-transient test or Bode plot before production. Neither inductor is rated to the LMR38010's 1.9 A high-side limit, which TI calls ideal: a hard short on either rail saturates it until hiccup mode stops the part.
- **The motor-facing centre.** Heights are package maxima, not chosen parts; whatever is bought has to stay under 1.5 mm. Some SOT and DFN lead frames are Alloy 42, which is magnetic; at 7–16 mm from the sensor it should not matter, and the encoder's field budget has not been re-run to prove it.
- **Copper under the standoffs.** On the motor-facing face a few tracks and a via sit inside the M3 standoffs' 3.25 mm circles — on board A as well, where `route.py` keeps copper only from under the screw heads on the outward face. Under solder mask, and not yet a rule.
- **P5S predates the cans' leads being turned.** The encoder check modelled the cans in the same places with + inboard; + is outboard now, onto In2's VBUS. Same two leads, same loop, polarity reversed; not re-run.
- **Height.** The cans are 12–13 mm tall on the outward face; board B's standoffs were 11 mm. A stacked expansion board needs longer standoffs or a cutout over the cans.
- **The heatsink ring.** Board A clamps an aluminium ring on the phase cells' lands. The USB-C reaches the edge in the signal wedge, so a continuous ring would need a gap there.
- **VMOT through the header.** A PD board feeding the bus puts up to 5 A through the header, whose end pins are about 6 mm from the encoder. With VMOT and GND pins paired it passes as a dipole, roughly 0.1 mT against the magnet's 20–100 — estimated, not simulated.
- **The cans' ripple rating.** At a 20 A RMS burst the bulk carries about 12 A RMS, 6 A per can; C2887236's rating is not read yet.
- **RS-485 fail-safe.** Every receiver has a permanent 120 Ω and no bias network; an idle or open link relies on the SIT3088's own fail-safe, which the datasheet has to be shown to give with the termination present (the sister project's F-01).
- **The RGB LED's pinout.** `LED_RGB_1210` with a common anode on pad 4 is an assumption: 1210 RGB parts differ. Check against the part bought.
- **Firmware.** GPIO14 is `GATE_OFF`, active high; UART0 on GPIO0/1 is port IN and a PIO UART on 2/3 port OUT; USB_VBUS_DET is GPIO25; 19–24 go to the expansion header.
- **Stock.** The bucks' parts are checked at JLCPCB (2026-09-23): LMR38010SDDAR C5219310, SWPA4030S680MT C83473, SWPA4030S330MT C83470, 4.7 µF/100 V 1206 C237304, 100 nF/100 V 0805 C28233, 22 µF/25 V 1206 C12891, 1210 C21397, 0805 C45783; C2887236 is stocked. SMDJ54A, TPSMF4L54A, SIT3088 and SM712 are not checked yet.

## Licence

CERN-OHL-P, like its parent. Design by Sequoia Hope Alexander.

## Power sources

VMOT accepts a 12–60 V battery on XT30 **or** a USB-PD contract, ORed by an
LTC4359 ideal diode. The 12 V gate rail's buck cannot boost, which gives three
clean states:

| Source | VMOT | Gate rail | Drive can |
|---|---|---|---|
| USB, no contract | 5 V | down | configure, read the encoder — not energise a phase |
| USB-PD, 12–20 V | 12–20 V | up | run, capped at 100 W by PD |
| XT30 battery | 12–60 V | up | run; full performance at 60 V |

At a 12 V bus the gate rail's buck is at its 97 % maximum duty and the rail
sits near 11.5 V — above EG2103's 9.7 V turn-on, which is why 12 V is the floor.
The rail's enable is pulled **on** and `GATE_EN` can only pull it off, because
a reset is meant to brake and a brake needs the gate rail.

PD **replaces** a pack; the two are not used together. With VMOT down to
12 V a 20 V contract could otherwise back-drive a lower-voltage pack, so that
is an operating rule rather than a circuit: the PD path stays one LTC4359 and
one FET, which keeps VMOT out of the USB host, and a USB cable for
configuration with a pack on the XT30 is still safe because PD without a
contract is 5 V. Firmware should ask for more than 5 V only when the bus reads
what USB alone would give it. The XT30 stays a straight pass-through, so regen
still reaches a pack.

## Sourcing

Stock figures below were checked 2026-09-07 and **rot**. Re-check before ordering —
the sister project's F-40 is a board that cannot be built because one part has nine
in stock.

| Part | LCSC | Stock | MOQ | @100 | Note |
|---|---|---|---|---|---|
| EG2103 | C480654 | 2,225 | 5 | $0.19 | **chosen** — 600 V, VCC 10–20 V, interlock + 560 ns dead time, ±0.3/0.6 A |
| NCP5183DR2G | C904511 | 2,883 | 1 | $0.92 | alternative: 4.3 A, but no interlock or dead time |
| UCC27282DR | C2867844 | 1,227 | 1 | $1.99 | alternative: ±3 A, VDD 5.5–16 V |
| EG2131 | C193777 | — | — | — | **not available** |
| FD2103S | C5187182 | — | 5 | $0.10 | **out of stock** |
| BSC030N08NS5 | C501507 | 27,306 | 1 | $0.60 | inherited, unchanged |

Still unverified, and now more urgent because the layout depends on their packages:

- **INA241A3** — `INA241A3IDDFR`, **TSOT-23-8**, 50 V/V. Not A2 (20 V/V) and not
  SOT-23-8; draft 0.2 had both wrong. LCSC stock unconfirmed. `INA240A2PWR`
  (C129949) is in stock as a fallback, but it is TSSOP-8 and only ±80 V common
  mode against INA241's ±110 V — a different footprint and less margin on a 60 V
  phase node.
- **1.6 mΩ 2010** shunts at 1% — the value changed twice, see above.
- **3.3 µH inductor** for the RP2350A's core buck, and the rest of the CPU
  support parts capture turned up.
- **TPSMF4L64A**, **LMR38010**.
- **Bulk capacitance on board B is no longer a catalogue item.** See spec §8:
  ~9 A RMS of ripple at 20 kHz reaches it, and three ordinary 100 µF/100 V
  electrolytics are not rated for that.
