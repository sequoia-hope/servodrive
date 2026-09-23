#!/usr/bin/env python3
"""Board -> sim/work/geometry.json.

Reads the routed board with pcbnew and writes every piece of copper the
simulation needs, in the tools' frame: millimetres, origin at the shaft axis
(KiCad 148, 105), x to the right, y *up* (KiCad's y is down; we flip it here so
that angles measured counter-clockwise from +x match `tools/placement.py`).

What comes out, per SPEC.md sec.5.5:
  stackup   copper layers with z (mm, F.Cu at z=0 going down) and thickness,
            dielectrics with epsilon_r and tan(delta)
  polys     filled zone polygons, pad polygons and tracks-as-polygons, each
            tagged with net, layer and kind
  vias      PCB vias and plated footprint pads (the FET thermal arrays), with
            drill, diameter, layer span and net
  parts     every footprint: reference, value, position, polar, rotation, side
  cells     the per-phase-cell crop windows

Everything here is geometry as built.  SPEC.md sec.9: a protected via can come
back moved by 25 um and a track can end 0.05 mm inside its pad -- do not clean it.
"""
import sys
import json
import math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import paths                                       # noqa: E402

paths.import_tools()
import sexp                                                 # noqa: E402

import pcbnew                                               # noqa: E402

ORIGIN_MM = (148.0, 105.0)          # shaft axis, MT6701 sensing centre
CU_LAYERS = ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu"]


# ------------------------------------------------------------------ frame --
def to_frame(pt):
    """KiCad VECTOR2I (nm, y down) -> (x, y) mm in the tools' frame (y up)."""
    x, y = pcbnew.ToMM(pt.x), pcbnew.ToMM(pt.y)
    return (round(x - ORIGIN_MM[0], 6), round(ORIGIN_MM[1] - y, 6))


def poly_pts(outline):
    """SHAPE_LINE_CHAIN -> [[x, y], ...] in the tools' frame."""
    return [list(to_frame(outline.CPoint(i))) for i in range(outline.PointCount())]


def polyset_to_list(ps):
    """SHAPE_POLY_SET -> [{"pts": outline, "holes": [...]}, ...]"""
    out = []
    for i in range(ps.OutlineCount()):
        entry = {"pts": poly_pts(ps.Outline(i))}
        holes = [poly_pts(ps.Hole(i, h)) for h in range(ps.HoleCount(i))]
        if holes:
            entry["holes"] = holes
        out.append(entry)
    return out


# --------------------------------------------------------------- stackup --
def read_stackup(pcb_path):
    """Parse the (stackup ...) block out of the board file.

    pcbnew's BOARD_STACKUP is not usefully exposed through swig in 9.0.8
    (GetList() is missing), so the text is the source of truth.  z is measured
    downwards from the top of F.Cu, which puts B.Cu at +1.6 mm.
    """
    text = Path(pcb_path).read_text()
    i = text.index("(stackup")
    depth, j = 0, i
    while True:
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                break
        j += 1
    tree = sexp.parse(text[i:j + 1])

    layers, z = [], 0.0
    for item in tree[1:]:
        if not isinstance(item, list) or item[0] != "layer":
            continue
        name = item[1].strip('"')
        d = {}
        for f in item[2:]:
            if isinstance(f, list):
                d[f[0]] = f[1].strip('"')
        typ = d.get("type", "")
        th = float(d.get("thickness", 0.0))
        if typ == "copper":
            layers.append({"kind": "copper", "name": name, "z_top": round(z, 6),
                           "thickness": th, "z_mid": round(z + th / 2, 6)})
            z += th
        elif typ in ("prepreg", "core"):
            layers.append({"kind": "dielectric", "name": name, "z_top": round(z, 6),
                           "thickness": th, "material": d.get("material", "FR4"),
                           "epsilon_r": float(d.get("epsilon_r", 4.5)),
                           "loss_tangent": float(d.get("loss_tangent", 0.02))})
            z += th
        elif "Solder Mask" in typ:
            layers.append({"kind": "mask", "name": name, "thickness": th,
                           "epsilon_r": 3.5})
    return {"layers": layers, "total_thickness": round(z, 6)}


# ------------------------------------------------------------------ board --
def extract(pcb_path=None, verbose=True):
    pcb_path = Path(pcb_path or paths.BOARD)
    board = pcbnew.LoadBoard(str(pcb_path))
    lid = {board.GetLayerName(l): l for l in board.GetEnabledLayers().CuStack()}

    out = {
        "source": str(pcb_path),
        "origin_mm": list(ORIGIN_MM),
        "frame": "mm, origin at the shaft axis, +x right, +y up (KiCad y flipped)",
        "stackup": read_stackup(pcb_path),
        "copper_layers": CU_LAYERS,
        "layer_ids": {k: lid[k] for k in CU_LAYERS},
    }

    # ---- zones -----------------------------------------------------------
    zones = []
    for z in board.Zones():
        net = z.GetNetname()
        for l in z.GetLayerSet().Seq():
            lname = board.GetLayerName(l)
            if lname not in CU_LAYERS:
                continue
            ps = z.GetFilledPolysList(l)
            if ps.OutlineCount() == 0:
                continue
            for entry in polyset_to_list(ps):
                entry.update({"net": net, "layer": lname, "kind": "zone"})
                zones.append(entry)
    out["zones"] = zones

    # ---- pads ------------------------------------------------------------
    pads, th_pads, parts = [], [], []
    for fp in board.GetFootprints():
        x, y = to_frame(fp.GetPosition())
        parts.append({
            "ref": fp.GetReference(), "value": fp.GetValue(),
            "fp": str(fp.GetFPID().GetUniStringLibItemName()),
            "x": x, "y": y,
            "r": round(math.hypot(x, y), 4),
            "ang": round(math.degrees(math.atan2(y, x)) % 360.0, 4),
            # KiCad orientation is clockwise-positive in its y-down frame,
            # which is counter-clockwise-positive in ours.
            "rot": round(fp.GetOrientationDegrees(), 4),
            "side": "B.Cu" if fp.IsFlipped() else "F.Cu",
            "dnp": bool(fp.IsDNP()),
        })
        for p in fp.Pads():
            ls = [board.GetLayerName(l) for l in p.GetLayerSet().Seq()
                  if board.GetLayerName(l) in CU_LAYERS]
            if not ls:
                continue
            drill = pcbnew.ToMM(p.GetDrillSizeX())
            px, py = to_frame(p.GetPosition())
            rec = {"ref": fp.GetReference(), "pad": p.GetNumber(),
                   "net": p.GetNetname(), "layers": ls,
                   "x": px, "y": py, "drill": round(drill, 4)}
            if drill > 0:
                # A plated footprint pad: a barrel, like a via.  The 16-via FET
                # drain arrays are these, not PCB vias (SPEC.md sec.1.4).
                rec["dia"] = round(pcbnew.ToMM(p.GetSize(p.GetLayerSet().Seq()[0]).x), 4)
                th_pads.append(rec)
            shapes = {}
            for lname in ls:
                ps = p.GetEffectivePolygon(lid[lname])
                shapes[lname] = polyset_to_list(ps)
            rec2 = dict(rec)
            rec2["shapes"] = shapes
            pads.append(rec2)
    out["pads"] = pads
    out["through_pads"] = th_pads
    out["parts"] = parts

    # ---- tracks and vias --------------------------------------------------
    tracks, vias = [], []
    for t in board.GetTracks():
        cls = t.GetClass()
        if cls == "PCB_VIA":
            x, y = to_frame(t.GetStart())
            vias.append({
                "net": t.GetNetname(), "x": x, "y": y,
                "drill": round(pcbnew.ToMM(t.GetDrill()), 4),
                "dia": round(pcbnew.ToMM(t.GetWidth(t.TopLayer())), 4),
                "top": board.GetLayerName(t.TopLayer()),
                "bottom": board.GetLayerName(t.BottomLayer()),
                "via_type": int(t.GetViaType()),
            })
        else:
            lname = board.GetLayerName(t.GetLayer())
            if lname not in CU_LAYERS:
                continue
            sx, sy = to_frame(t.GetStart())
            ex, ey = to_frame(t.GetEnd())
            rec = {"net": t.GetNetname(), "layer": lname,
                   "start": [sx, sy], "end": [ex, ey],
                   "width": round(pcbnew.ToMM(t.GetWidth()), 4),
                   "kind": "arc" if cls == "PCB_ARC" else "track"}
            if cls == "PCB_ARC":
                mx, my = to_frame(t.GetMid())
                rec["mid"] = [mx, my]
            tracks.append(rec)
    out["tracks"] = tracks
    out["vias"] = vias

    # ---- outline ---------------------------------------------------------
    edge = []
    for d in board.GetDrawings():
        if board.GetLayerName(d.GetLayer()) != "Edge.Cuts":
            continue
        sh = d.GetShapeStr() if hasattr(d, "GetShapeStr") else ""
        sx, sy = to_frame(d.GetStart())
        rec = {"shape": sh, "start": [sx, sy]}
        if sh.lower() == "circle":
            rec["radius"] = round(pcbnew.ToMM(d.GetRadius()), 4)
        else:
            ex, ey = to_frame(d.GetEnd())
            rec["end"] = [ex, ey]
        edge.append(rec)
    out["edge"] = edge

    # ---- nets ------------------------------------------------------------
    out["nets"] = sorted({z["net"] for z in zones}
                         | {p["net"] for p in pads}
                         | {t["net"] for t in tracks}
                         | {v["net"] for v in vias})

    # ---- cells -----------------------------------------------------------
    # Phase cells span 68 deg centred on 34 / 102 / 170.  The crop windows are
    # a little wider so that the neighbouring wedge copper that closes the
    # return path is inside the extraction (SPEC.md sec.5.5).
    out["cells"] = {
        name: {"axis_deg": ax, "a0": ax - 39.0, "a1": ax + 39.0,
               "r0": 15.0, "r1": 32.5}
        for name, ax in (("A", 34.0), ("B", 102.0), ("C", 170.0))
    }

    if verbose:
        print(f"zones {len(zones)}  pads {len(pads)} ({len(th_pads)} plated)  "
              f"tracks {len(tracks)}  vias {len(vias)}  parts {len(parts)}  "
              f"nets {len(out['nets'])}")
    return out


def main():
    paths.ensure_dirs()
    if not paths.BOARD.exists():
        import shutil
        for suffix in (".kicad_pcb", ".kicad_pro", ".kicad_prl"):
            src = paths.BOARD_SRC.with_suffix(suffix)
            if src.exists():
                shutil.copy2(src, paths.WORK / src.name)
    data = extract()
    paths.GEOMETRY.write_text(json.dumps(data, separators=(",", ":")))
    mb = paths.GEOMETRY.stat().st_size / 1e6
    print(f"wrote {paths.GEOMETRY} ({mb:.1f} MB)")
    return data


if __name__ == "__main__":
    main()
