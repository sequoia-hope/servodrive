# 3D models

Three STEP models that footprints on the boards name but that KiCad 9's library
does not ship, and one it ships in a form its own GLB exporter cannot draw.
Nothing in the board files points here: `tools/export_3d.py`
substitutes them into a temporary copy of the board when it exports the GLB
for the pages' 3D viewer (its `SUBST` table has the offsets and rotations,
each checked against the footprint in the exported model).

| File | Part | From |
|---|---|---|
| `HRO_TYPE-C-31-M-12.step` | J12, USB-C receptacle (board S) | [Keebio-Parts.pretty](https://github.com/keebio/Keebio-Parts.pretty) `3dmodels/HRO  TYPE-C-31-M-12.step`, MIT licence, Copyright (c) 2018 Keebio |
| `AOTA-B201610SR47MT.STEP` | L701, the RP2350's 2016 core-regulator inductor (boards A and S) | the model `servodrive.pretty/L_pol_2016` names, as `~/pcb/simple_earring_shipped` has it (Open CASCADE export) |
| `XT30PW-M.STEP` | J4, the XT30 bus input (board S) | `~/pcb/rp2040-motor-controller/parts/XT30PW-M--3DModel-STEP-269445.STEP`, the model the rp2040 and rp2350 motor controllers use for the same footprint (a SolidWorks 2021 export of the AMASS part; its licence is not recorded there) |
| `L_Sunlord_SWPA4030S.step` | L1001 and L1002, the bucks' 68 µH and 33 µH (board S) | KiCad 9's own `Inductor_SMD.3dshapes/L_Sunlord_SWPA4030S.step` (CC-BY-SA 4.0, KiCad libraries), flattened by `tools/flatten_step.py`: the library file is a CadQuery assembly nested three deep, which `kicad-cli` 9.0.8 exports as empty nodes without a warning. Same three solids, same colours, same origin |

The SIT3088's DFN-8 (U14, U15, U17, U18) needs no file here: KiCad's own
`DFN-8-1EP_3x3mm_P0.65mm_EP1.55x2.4mm.step` stands in for its EasyEDA model.
