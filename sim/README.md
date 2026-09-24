# sim/ — the servodrive electromagnetic simulation

`SPEC.md` is the brief. This is what was built from it.

```
sim/
  SPEC.md          the brief
  FINDINGS.md      the register: every finding, its evidence and its disposition
  HANDBACK.md      the constants to change, the firmware to change, and why
  run.py           reproduces everything:  python3 sim/run.py
  setup.py         builds the solvers this machine does not have
  report/          the report; served by `proj up servodrive`, at /sim/report/
  results/         P0.json … P9.json — the only thing the report reads

  extract/         board -> work/geometry.json, and the pictures to check it by
  lib/             paths, result IO
  models/          one JSON per part: the value, the source, the date read
  fasthenry/       PEEC: the runner, the mesher, and the cell-A models
  fastcap/         the electrostatic runner
  spice/           libngspice binding, the fitted devices, the cell netlist
  conduction/      the L5 finite-difference solver and its rasteriser
  loop/            the L4 system: motor, inverter, SimpleFOC's own control law
  encoder/         L6: the field at the MT6701
  field/           L2: openEMS, in a container
  thermal/         L7, the optional phase
  phases/          one module per phase, each returning the JSON it writes
  tests/           the known-answer tests of SPEC.md §5.4
  work/            regenerable: board copy, geometry, solver scratch
```

## Running it

```sh
python3 sim/setup.py          # build FastHenry2, FastCap2, the openEMS image
python3 sim/run.py            # every phase, in order
python3 sim/run.py --phase P2 # one phase
python3 sim/run.py --list     # what is done
python3 sim/run.py --board s  # board S: results/s/, report/img/s/, work/s/
python3 sim/tests/kat.py      # just the known-answer tests
proj up servodrive && proj url servodrive     # then /sim/report/
```

## Board S

`--board s` runs the same phases on `hardware/single_board/servodrive_S.kicad_pcb`.
Its results go to `results/s/`, its figures to `report/img/s/` and its scratch to
`work/s/`, so board A's numbers are never overwritten. What differs is in
`lib/board.py`, the one place a phase asks for a design constant: a 48 V bus
(the envelope 20/48/56 V, 56 V being the top of the firmware fold-back), the 54 V
clamps (Q2's criterion becomes 57 V, board A's 3 V under the clamp's minimum
breakdown), the 2 × 2 mΩ shunts at 50 mV/A, the Ø6 magnet at a 1.5 mm gap, and a
bulk that is two polymer cans on the board itself: P1 solves the plane path from
the cans to cell A in place of the header, and P4 becomes the cans' ripple current
against their rating, with the battery lead on the far side. P5S is board S's own
encoder check; P9 is `phases/p9s_summary.py`, which grades each question and writes
the simulation section of `single.html` between `<!-- sim:begin -->` and
`<!-- sim:end -->`. `tools/route.py` starts this run (detached, through
`tools/regen.py`) whenever it finishes routing board S with nothing unconnected.

`--quick` coarsens every sweep. It is for developing the code, never for a
result: the report does not record which mode produced a number, so do not mix
them.

## The solvers, and why they are where they are

There is no passwordless `sudo` on this machine, so nothing is apt-installed.

| Level | Solver | How it got here |
|---|---|---|
| L1 | FastHenry2 3.0.1, FastCap 2.0 | built from the FastFieldSolvers sources into `~/opt` by `setup.py`, with the patches a 2026 compiler needs |
| L2 | openEMS | built inside a container; only `sim/` is mounted |
| L3 | ngspice | through `libngspice.so.0`, which KiCad installs — there is no CLI here |
| L4 | own code | `loop/`, with SimpleFOC's PID and low-pass reimplemented line for line |
| L5 | own code | `conduction/`, a coverage-weighted finite-difference Laplacian solved with pyamg |
| L6 | magpylib | pip |
| L7 | own code | `thermal/`, the same Laplacian with k instead of sigma |

Every one is checked against a closed form before it is used; the checks are in
`tests/kat.py` and their results are in `results/P0.json`.

## Rules this code obeys

- It never edits `hardware/`. The board is copied into `work/` before anything
  that could write it, and nothing here runs `gen_boards.py`.
- Every component value in `models/*.json` carries its source and the date it
  was read. A value that could not be found is marked `bracketed` and swept,
  never quietly replaced with a typical.
- `results/*.json` is the only thing the report reads, so a re-run cannot put
  yesterday's number beside today's figure.
- A solver that will not converge is a finding, not a gap: see S-21 and the
  Q11 section of the report. The corollary bit harder than the rule: a solver
  that *looks* converged is not one either. Both solvers answered Q1 on a mesh
  neither had been refined past, from opposite sides, and the disagreement was
  read as a solver difference until each was swept.
- A mesh is snapped to the copper, not laid over it. In FDTD there are no
  nets, only metal: two conductors within one cell of each other are one
  conductor, and this board's tightest clearance is 0.40 mm. A uniform mesh
  coarser than that joins VBUS to GND and returns a commutation loop half its
  size, without warning and without failing any check the run makes on itself.
  `field/geom_export.fixed_lines()` pins a line to every polygon vertex and
  every barrel wall, and `field/cell_scripts` merges every line through one
  minimum spacing — including the port's own edges, because one stray
  near-duplicate put the timestep at 2e-17 s and the run never finished.
- A warning is read before it is believed. openEMS reported 127 "unused
  primitives" in the first cell-A model and they were recorded as lost copper;
  they were via pads lying inside the barrel cylinders that already provide
  that copper at a higher priority. `geom_export.drop_barrel_shadowed()`
  removes them so the warning means what it appears to mean.
- A device is modelled where its terminals actually are. The MOSFET's
  `R_g(int)` is a real resistor between the package pin and the polysilicon
  gate, and `C_gd` and `C_gs` meet behind it, because that is what decides
  whether the channel conducts; hanging `C_gd` on the pin instead inflates the
  induced `V_gs` of Q4 several-fold. The gate driver's output stage is a
  resistance to its rail with the body diodes of its own output MOSFETs across
  it, not a current limit, because external current pushed into the pin has to
  raise it rather than vanish.
- Where a datasheet gives a number without its test condition, the reading is
  bracketed and the bracket is swept. The EG2103's output impedance is the
  largest such bracket in the report and sets the edge rate; see Q2.
