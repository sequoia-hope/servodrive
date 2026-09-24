#!/usr/bin/env python3
"""export_3d.py — the board as a GLB, for the 3D viewer on the project pages.

    python3 tools/export_3d.py              # board A
    python3 tools/export_3d.py --board s    # board S

Writes into img/3d/:

    <board>.glb   the board, its copper and every part that has a model
    <board>.json  what the viewer's caption says: part counts, which parts
                  have no model, which models were substituted, file size
    <board>.parts.json
                  what the viewer's component pane says about a clicked part:
                  its role note, LCSC number (hardware/parts/lcsc.csv, or the
                  footprint's own LCSC field), footprint and its description, where it sits
                  (mm from the board centre, y up, as the copper viewer reads
                  out), rotation, attributes, other fields, and the net on
                  every pin. Read from the board file alone, so
                  `--parts-only` rewrites it without re-exporting the model.

`kicad-cli pcb export glb` does the work, from a temporary copy of the board
in which the three models this machine does not have are pointed at ones it
does, and the one it has but cannot mesh at a flattened copy (SUBST below) --
the board files themselves are not touched. A part whose model loads but comes
out with no geometry is reported, since kicad-cli is silent about it. What comes
out of kicad-cli is then rewritten, because as exported it is 24-30 MB and
~32,000 primitives, one per track, pad and via, each its own draw call:

  * primitives are merged per mesh and material, in chunks that keep 16-bit
    indices;
  * positions are quantized to 16 bits per axis over each mesh's box
    (KHR_mesh_quantization -- 1 um steps across the whole board, which
    three.js reads natively), the dequantizing scale on a child node;
  * the board's own meshes (copper, pads, vias, laminate, mask, silk) lose
    their normals and are welded: they are all flat faces, and a glTF
    without normals is drawn flat-shaded by definition. Parts keep theirs,
    as 8-bit normals, because a capacitor can is round;
  * the board's colours, which KiCad writes as sRGB where glTF means linear,
    are converted (the parts' come through OCC already linear and are left
    alone), and the mask is made translucent the way pcbnew draws it.

Each part's node carries its reference, value, footprint and side in its
extras, which is what the viewer shows when a part is hovered; the rest of
what it knows about a part is in <board>.parts.json.

The result is the same bytes for the same board: kicad-cli's timestamp is
dropped, so a re-export after a change that did not move copper is not a diff.
"""
import argparse, csv, json, re, shutil, struct, subprocess, sys, tempfile
from datetime import date
from pathlib import Path

import numpy as np

import sexp

ROOT = Path(__file__).resolve().parent.parent
HW = ROOT / "hardware"
MODELS = HW / "parts" / "3dmodels"
LCSC = HW / "parts" / "lcsc.csv"          # the part numbers chosen so far
OUT = ROOT / "img" / "3d"

BOARDS = {"a": ("motor_board", "servodrive_A"),
          "s": ("single_board", "servodrive_S")}

# Models the footprints name that are not on this machine, and what stands in
# for them. Keyed by the end of the model path; offset and rotate are KiCad's
# (mm, degrees), None keeps the footprint's own; the scale is reset to 1.
# Each was checked against its footprint in the exported GLB.
#
#   HRO TYPE-C-31-M-12: KiCad's library has the footprint but never shipped a
#     model. Keebio-Parts' STEP (MIT), whose footprint puts the origin 3.65 mm
#     further back than KiCad's does (NPTH pegs at y -6.25 against -2.6), and
#     whose STEP origin is the shell's left edge, not its centre.
#   AOTA-B201610SR47MT: the model servodrive.pretty/L_pol_2016 names, as
#     simple_earring_shipped has it -- modelled z-up standing on z = 0, where
#     the footprint's -90/+0.5 mm transform is for a y-up, centred one.
#   SIT3088's DFN-8: an EasyEDA VRML that is not here, and the GLB exporter
#     cannot read VRML anyway. KiCad's 3x3 mm P0.65 DFN is the same package;
#     the footprint has pin 1 bottom-left with the rows horizontal where
#     KiCad's has pin 1 top-left with them vertical, hence the quarter turn
#     anticlockwise -- which KiCad writes as -90, its model rotations being
#     clockwise-positive. (Checked: the model's pin-1 dot lands over pad 1.)
#   AMASS XT30PW-M: KiCad 9's footprint names a model its library does not
#     ship. The SolidWorks STEP the rp2040/rp2350-motor-controller boards use
#     for the same footprint, with their offset and rotation.
#   Sunlord SWPA4030S: KiCad's own model, but an assembly of sub-assemblies,
#     which kicad-cli 9.0.8 exports as empty nodes without a word. The same
#     solids and colours flattened into one part by tools/flatten_step.py;
#     same origin, so the footprint's own offset and rotation stand.
SUBST = {
    "Connector_USB.3dshapes/USB_C_Receptacle_HRO_TYPE-C-31-M-12.step":
        (MODELS / "HRO_TYPE-C-31-M-12.step", (-4.45, -3.65, 0), (-90, 0, 0)),
    "Inductor_SMD.3dshapes/AOTA-B201610SR47MT.STEP":
        (MODELS / "AOTA-B201610SR47MT.STEP", (0, 0, 0), (0, 0, 0)),
    "Connector_AMASS.3dshapes/AMASS_XT30PW-M_1x02_P2.50mm_Horizontal.step":
        (MODELS / "XT30PW-M.STEP", (-2.5, 10.0, 0), (-90, 0, 0)),
    "Inductor_SMD.3dshapes/L_Sunlord_SWPA4030S.step":
        (MODELS / "L_Sunlord_SWPA4030S.step", None, None),
    "test.3dshapes/DFN-8_L3.0-W3.0-P0.65-BL-EP.wrl":
        ("${KICAD9_3DMODEL_DIR}/Package_DFN_QFN.3dshapes/"
         "DFN-8-1EP_3x3mm_P0.65mm_EP1.55x2.4mm.step", (0, 0, 0), (0, 0, -90)),
}

GLB_FLAGS = ["--force", "--subst-models", "--include-tracks", "--include-pads",
             "--include-zones", "--include-silkscreen", "--include-soldermask"]


def sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit("FAILED: %s\n%s%s" % (" ".join(map(str, cmd)), r.stdout, r.stderr))
    return r


# ---------------------------------------------------------------- board ----
def parts_of(text):
    """ref -> {value, footprint, side, model} for every footprint."""
    parts = {}
    for fp in sexp.findall(sexp.parse(text), "footprint"):
        props = {sexp.unq(p[1]): sexp.unq(p[2]) for p in sexp.findall(fp, "property")}
        model = sexp.find(fp, "model")
        parts[props.get("Reference", "?")] = {
            "value": props.get("Value", ""),
            "footprint": sexp.unq(fp[1]).split(":")[-1],
            "side": "back" if sexp.unq(sexp.find(fp, "layer")[1]) == "B.Cu" else "front",
            "model": sexp.unq(model[1]) if model else None,
        }
    return parts


# fields every footprint has, shown in their own rows rather than as parameters
OWN_FIELDS = {"Reference", "Value", "Footprint", "Datasheet", "Description", "servodrive_role", "LCSC"}


def lcsc_table():
    """(value, footprint) -> {lcsc, mpn} from hardware/parts/lcsc.csv."""
    if not LCSC.exists():
        return {}
    rows = csv.DictReader(l for l in LCSC.read_text().splitlines()
                          if l.strip() and not l.startswith("#"))
    return {(r["value"], r["footprint"]): {"lcsc": r["lcsc"], "mpn": r["mpn"]} for r in rows}


def centre_of(tree):
    """The outline circle's centre: the shaft axis, where the floorplan's
    radii and angles are measured from. None if the outline is not a circle."""
    best = None
    for c in sexp.findall(tree, "gr_circle"):
        if sexp.unq(sexp.find(c, "layer")[1]) != "Edge.Cuts":
            continue
        (cx, cy), (ex, ey) = (tuple(map(float, sexp.find(c, k)[1:3])) for k in ("center", "end"))
        r = ((ex - cx) ** 2 + (ey - cy) ** 2) ** 0.5
        if not best or r > best[2]:
            best = (cx, cy, r)
    return best


def plain(descr):
    """A footprint description without its sources. KiCad's library ones carry
    a body-size reference, a URL and "generated with kicad-footprint-generator",
    none of which says what the package is."""
    d = re.sub(r"\s*\((?:Body size|see)[^)]*\)", "", descr)
    d = re.sub(r",?\s*generated (?:with|by) kicad-footprint-generator.*$", "", d)
    d = re.sub(r"(?:,\s*|\s+)?(?:(?:see|datasheet:?)\s*)?https?://[^\s)]+", "", d, flags=re.I)
    d = re.sub(r"\(\s*[,;-]?\s*\)", "", d)                 # brackets the URL left empty
    d = re.sub(r"\(\s*[,;]?\s*", "(", d)
    return re.sub(r"\s{2,}", " ", d).strip(" ,")


def details_of(text):
    """ref -> everything the component pane shows, for every footprint."""
    tree = sexp.parse(text)
    ring = centre_of(tree)
    table = lcsc_table()
    cx, cy = ring[:2] if ring else (0.0, 0.0)
    out = {}
    for fp in sexp.findall(tree, "footprint"):
        props = {sexp.unq(p[1]): sexp.unq(p[2]) for p in sexp.findall(fp, "property")}
        at = sexp.find(fp, "at")
        x, y = float(at[1]) - cx, cy - float(at[2])           # board mm, y up
        descr, attr = sexp.find(fp, "descr"), sexp.find(fp, "attr")
        pins = {}
        for pad in sexp.findall(fp, "pad"):
            num = sexp.unq(pad[1])
            if not num:                                      # thermal and paste pads
                continue
            net = sexp.find(pad, "net")
            name = sexp.unq(net[-1]) if net else ""
            if not pins.get(num):
                pins[num] = name
        lib, _, name = sexp.unq(fp[1]).rpartition(":")
        row = {"value": props.get("Value", ""), "footprint": name, "lib": lib,
               "descr": plain(sexp.unq(descr[1])) if descr else "",
               "side": "back" if sexp.unq(sexp.find(fp, "layer")[1]) == "B.Cu" else "front",
               "x": round(x, 3), "y": round(y, 3), "rot": float(at[3]) if len(at) > 3 else 0.0,
               "attrs": attr[1:] if attr else [],
               "role": props.get("servodrive_role", ""),
               "datasheet": props.get("Datasheet", "").strip("~"),
               "description": props.get("Description", ""),
               "fields": {k: v for k, v in props.items() if k not in OWN_FIELDS and v},
               **({"lcsc": props["LCSC"], "mpn": ""} if props.get("LCSC")
                  else table.get((props.get("Value", ""), name), {"lcsc": "", "mpn": ""})),
               "pins": [[n, pins[n]] for n in sorted(pins, key=natural)]}
        out[props.get("Reference", "?")] = row
    return out, ring


def write_details(board="a"):
    """img/3d/<board>.parts.json, one part to a line, in reference order, and no
    date in it: re-running it on an unchanged board is not a diff."""
    key, name = BOARDS[board]
    src = HW / key / f"{name}.kicad_pcb"
    if not src.exists():
        sys.exit(f"no board at {src}")
    parts, ring = details_of(src.read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{board}.parts.json"
    head = {"board": src.name,
            "centre_mm": [ring[0], ring[1]] if ring else None,
            "dia_mm": round(2 * ring[2], 3) if ring else None}
    lines = [json.dumps(r, separators=(",", ":")) + ": " + json.dumps(parts[r], separators=(",", ":"))
             for r in sorted(parts, key=natural)]
    path.write_text(json.dumps(head)[:-1] + ',\n "parts": {\n  '
                    + ",\n  ".join(lines) + "\n }\n}\n")
    json.loads(path.read_text())                             # it is JSON
    print("  wrote  %s  %d parts, %d pins" % (path.relative_to(ROOT), len(parts),
          sum(len(p["pins"]) for p in parts.values())))
    # every part the assembler places wants a number; copper features the
    # footprint library calls parts (lands, lead pads, bosses) do not
    unbought = sorted((r for r, p in parts.items() if not p["lcsc"] and
                       not {"exclude_from_bom", "exclude_from_pos_files"} & set(p["attrs"])),
                      key=natural)
    if unbought:
        print("  ! no LCSC number for %s -- add a row to %s"
              % (" ".join(unbought), LCSC.relative_to(ROOT)))


# a model block, up to the paren closing it: the one at its own indentation
MODEL_BLOCK = re.compile(r'^(\t*)\(model "([^"]*)"(.*?)\n\1\)', re.S | re.M)


def substitute(text):
    """The board text with SUBST applied to its model blocks, and the count."""
    done = [0]

    def one(m):
        for tail, (path, offset, rotate) in SUBST.items():
            if m.group(2).endswith(tail):
                break
        else:
            return m.group(0)
        ind, body = m.group(1), m.group(3)
        if offset is not None:
            body = re.sub(r"\(offset\s*\(xyz [^)]*\)\s*\)",
                          "(offset (xyz %g %g %g))" % offset, body)
        if rotate is not None:
            body = re.sub(r"\(rotate\s*\(xyz [^)]*\)\s*\)",
                          "(rotate (xyz %g %g %g))" % rotate, body)
        body = re.sub(r"\(scale\s*\(xyz [^)]*\)\s*\)", "(scale (xyz 1 1 1))", body)
        done[0] += 1
        return '%s(model "%s"%s\n%s)' % (ind, path, body, ind)

    return MODEL_BLOCK.sub(one, text), done[0]


# ------------------------------------------------------------------ glb ----
def parts_meshed(g):
    """name -> meshes under that node, for every node a part could be."""
    nodes = g["nodes"]

    def count(i):
        n = nodes[i]
        return ("mesh" in n) + sum(count(c) for c in n.get("children", []))
    return {n["name"]: count(i) for i, n in enumerate(nodes) if n.get("name")}


def read_glb(path):
    d = path.read_bytes()
    magic, _, total = struct.unpack_from("<III", d, 0)
    assert magic == 0x46546C67, f"{path} is not a GLB"
    off, chunks = 12, []
    while off < total:
        n, kind = struct.unpack_from("<II", d, off)
        chunks.append(d[off + 8: off + 8 + n])
        off += 8 + n
    return json.loads(chunks[0]), chunks[1]


CTYPE = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16,
         5125: np.uint32, 5126: np.float32}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def accessor(g, blob, i):
    """Accessor i as an (count, width) array, whatever its stride."""
    a = g["accessors"][i]
    v = g["bufferViews"][a["bufferView"]]
    dt, w = np.dtype(CTYPE[a["componentType"]]), WIDTH[a["type"]]
    stride = v.get("byteStride", dt.itemsize * w)
    start = v.get("byteOffset", 0) + a.get("byteOffset", 0)
    return np.ndarray((a["count"], w), dt, buffer=blob, offset=start,
                      strides=(stride, dt.itemsize)).copy()


class Writer:
    """The new binary chunk: one bufferView per accessor, 4-byte aligned."""

    def __init__(self):
        self.blob, self.views, self.accessors = bytearray(), [], []

    def add(self, arr, ctype, kind, target, stride=None, normalized=False,
            lo=None, hi=None):
        self.blob += b"\0" * (-len(self.blob) % 4)
        view = {"buffer": 0, "byteOffset": len(self.blob),
                "byteLength": arr.nbytes, "target": target}
        if stride:
            view["byteStride"] = stride
        self.blob += arr.tobytes()
        self.views.append(view)
        acc = {"bufferView": len(self.views) - 1, "componentType": ctype,
               "count": len(arr), "type": kind}
        if normalized:
            acc["normalized"] = True
        if lo is not None:
            acc["min"], acc["max"] = lo, hi
        self.accessors.append(acc)
        return len(self.accessors) - 1


def pieces(tris, limit=65535):
    """Split a triangle list so no piece indexes more than `limit` vertices."""
    if tris.max(initial=-1) < limit:
        yield tris
        return
    seen, start = set(), 0
    for k, t in enumerate(tris.tolist()):       # corners are distinct: no degenerates
        new = [v for v in t if v not in seen]
        if len(seen) + len(new) > limit:
            yield tris[start:k]
            seen, start = set(t), k
        else:
            seen.update(new)
    yield tris[start:]


def rebuild_mesh(g, blob, mesh, flat, w):
    """One mesh merged, welded and quantized: (primitives, centre, half, triangles)."""
    groups = {}
    for p in mesh["primitives"]:
        assert p.get("mode", 4) == 4, "only triangle lists are handled"
        pos = accessor(g, blob, p["attributes"]["POSITION"]).astype(np.float64)
        nrm = accessor(g, blob, p["attributes"]["NORMAL"]).astype(np.float64) \
            if "NORMAL" in p["attributes"] else np.zeros_like(pos)
        idx = accessor(g, blob, p["indices"]).reshape(-1, 3).astype(np.int64)
        groups.setdefault(p.get("material"), []).append((pos, nrm, idx))
    every = np.concatenate([pos for grp in groups.values() for pos, _, _ in grp])
    lo, hi = every.min(0), every.max(0)
    centre, half = (lo + hi) / 2, max(float((hi - lo).max()) / 2, 1e-9)

    prims, tris = [], 0
    for mat, grp in groups.items():
        base, P, N, T = 0, [], [], []
        for pos, nrm, idx in grp:
            P.append(pos); N.append(nrm); T.append(idx + base); base += len(pos)
        P, N, T = np.concatenate(P), np.concatenate(N), np.concatenate(T)
        qp = np.round((P - centre) / half * 32767).clip(-32767, 32767).astype(np.int16)
        qn = None
        if flat:
            key = qp
        else:
            n = N / np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)
            qn = np.round(n * 127).clip(-127, 127).astype(np.int8)
            key = np.concatenate([qp, qn.astype(np.int16)], axis=1)
        _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
        T = inv.reshape(-1)[T]
        T = T[(T[:, 0] != T[:, 1]) & (T[:, 1] != T[:, 2]) & (T[:, 0] != T[:, 2])]
        vp, vn = qp[first], None if qn is None else qn[first]
        for piece in pieces(T):
            used, local = np.unique(piece, return_inverse=True)
            pp = np.zeros((len(used), 4), np.int16)      # padded to 8-byte stride
            pp[:, :3] = vp[used]
            attrs = {"POSITION": w.add(pp, 5122, "VEC3", 34962, stride=8, normalized=True,
                                       lo=pp[:, :3].min(0).tolist(),
                                       hi=pp[:, :3].max(0).tolist())}
            if vn is not None:
                nn = np.zeros((len(used), 4), np.int8)    # padded to 4-byte stride
                nn[:, :3] = vn[used]
                attrs["NORMAL"] = w.add(nn, 5120, "VEC3", 34962, stride=4, normalized=True)
            prim = {"attributes": attrs, "mode": 4,
                    "indices": w.add(local.reshape(-1).astype(np.uint16), 5123,
                                     "SCALAR", 34963)}
            if mat is not None:
                prim["material"] = mat
            prims.append(prim)
            tris += len(piece)
    return prims, centre.tolist(), half, tris


def rewrite(g, blob, name, parts):
    """The exported glTF made small; returns (gltf, binary chunk, stats)."""
    w, meshes, frames = Writer(), [], []
    stats = {"triangles": 0, "primitives": 0}
    for mesh in g["meshes"]:
        flat = mesh.get("name", "").startswith(name + "_")
        prims, centre, half, tris = rebuild_mesh(g, blob, mesh, flat, w)
        meshes.append({"name": mesh.get("name", ""), "primitives": prims})
        frames.append((centre, half))
        stats["triangles"] += tris
        stats["primitives"] += len(prims)

    # each mesh hangs off a child node that undoes the quantization; the scale
    # is uniform so the normals need no correcting
    nodes = [dict(n) for n in g["nodes"]]
    for n in list(nodes):
        if "mesh" in n:
            m = n.pop("mesh")
            centre, half = frames[m]
            nodes.append({"mesh": m, "translation": centre, "scale": [half] * 3})
            n.setdefault("children", []).append(len(nodes) - 1)

    # the parts and the board's layers, named for the viewer
    tops = [c for r in g["scenes"][g.get("scene", 0)]["nodes"]
            for c in [r] + nodes[r].get("children", [])]
    for i in tops:
        n = nodes[i]
        ref = n.get("name")
        if ref in parts:
            p = parts[ref]
            n["extras"] = {"ref": ref, "value": p["value"],
                           "footprint": p["footprint"], "side": p["side"]}
        elif ref and ref.startswith("=>"):
            kid = nodes[n["children"][-1]]
            layer = meshes[kid["mesh"]]["name"][len(name) + 1:]
            n["name"] = layer
            n["extras"] = {"layer": layer}

    # The board's own colours are KiCad's display colours -- sRGB, the mask's
    # #143324 is (0.08, 0.2, 0.14) -- written where glTF means linear, so they
    # come out a shade lighter than pcbnew draws them and are converted here.
    # The parts' colours are not: kicad-cli reads a model's STEP colour
    # through OCC, which does convert (a body's 0.148 arrives as 0.0192), so
    # converting those again drew every IC near black and every pin dull.
    # Base colours only, too, which glTF reads as fully metallic: parts and
    # laminate are not, copper is a bit.
    of_board = {p.get("material") for m in meshes if m["name"].startswith(name + "_")
                for p in m["primitives"]}
    of_parts = {p.get("material") for m in meshes if not m["name"].startswith(name + "_")
                for p in m["primitives"]}
    if of_board & of_parts:
        sys.exit("a material is shared by the board and a part: its colour space is ambiguous")
    shiny = {p.get("material") for m in meshes
             if m["name"][len(name) + 1:] in ("copper", "pad", "via")
             for p in m["primitives"]}
    materials = []
    for i, mat in enumerate(g.get("materials", [])):
        mat = json.loads(json.dumps(mat))
        pbr = mat.setdefault("pbrMetallicRoughness", {})
        pbr["metallicFactor"], pbr["roughnessFactor"] = \
            (0.55, 0.35) if i in shiny else (0.0, 0.6)
        rgba = pbr.get("baseColorFactor", [1] * 4)
        if i in of_board:
            pbr["baseColorFactor"] = [round(linear(c), 6) for c in rgba[:3]] + rgba[3:]
        # the mask comes with KiCad's alpha, which glTF ignores unless asked
        if rgba[3] < 1:
            mat["alphaMode"] = "BLEND"
            pbr["roughnessFactor"] = 0.3                 # and mask is glossy
        materials.append(mat)

    asset = dict(g["asset"])
    asset["extras"] = {k: v for k, v in asset.get("extras", {}).items()
                       if k in ("pcb_name", "generator")}
    out = {"asset": asset, "scene": g.get("scene", 0), "scenes": g["scenes"],
           "nodes": nodes, "meshes": meshes, "materials": materials,
           "accessors": w.accessors, "bufferViews": w.views,
           "buffers": [{"byteLength": len(w.blob)}],
           "extensionsUsed": ["KHR_mesh_quantization"],
           "extensionsRequired": ["KHR_mesh_quantization"]}
    return out, bytes(w.blob), stats


def linear(c):
    """An sRGB component, 0..1, as glTF's linear."""
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def write_glb(path, gltf, blob):
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    blob += b"\0" * (-len(blob) % 4)
    path.write_bytes(struct.pack("<III", 0x46546C67, 2, 28 + len(js) + len(blob))
                     + struct.pack("<II", len(js), 0x4E4F534A) + js
                     + struct.pack("<II", len(blob), 0x004E4942) + blob)


# ------------------------------------------------------------------ run ----
def run(board="a", parts_only=False):
    """Export one board's GLB. gen_boards.py and route.py call this after the plots."""
    write_details(board)
    if parts_only:
        return
    if not shutil.which("kicad-cli"):
        print("  ! kicad-cli not found, skipping the 3D model"); return
    key, name = BOARDS[board]
    src = HW / key / f"{name}.kicad_pcb"
    if not src.exists():
        sys.exit(f"no board at {src}")
    text = src.read_text()
    parts = parts_of(text)
    want = sum(1 for p in parts.values()
               if p["model"] and any(p["model"].endswith(t) for t in SUBST))
    text, done = substitute(text)
    if done != want:
        sys.exit(f"substituted {done} model blocks, expected {want}: MODEL_BLOCK "
                 "no longer matches how the board file writes a model")

    tmp = Path(tempfile.mkdtemp(prefix="servodrive_3d_"))
    try:
        copy = tmp / src.name                  # same name: the GLB's extras use it
        copy.write_text(text)
        raw = tmp / f"{name}.glb"
        r = sh(["kicad-cli", "pcb", "export", "glb", *GLB_FLAGS, "-o", str(raw), str(copy)])
        missing = sorted(set(re.findall(r"Could not add 3D model for (\S+)\.", r.stdout + r.stderr)),
                         key=natural)
        g, blob = read_glb(raw)
        # a model that loads can still come out with nothing in it, and
        # kicad-cli says nothing (the SWPA4030S did): count what each part got
        empty = sorted((ref for ref, n in parts_meshed(g).items()
                        if n == 0 and parts.get(ref, {}).get("model") and ref not in missing),
                       key=natural)
        raw_bytes = raw.stat().st_size
    finally:
        shutil.rmtree(tmp)

    gltf, blob, stats = rewrite(g, blob, name, parts)
    OUT.mkdir(parents=True, exist_ok=True)
    glb = OUT / f"{board}.glb"
    write_glb(glb, gltf, blob)

    none = sorted((r for r, p in parts.items() if not p["model"]), key=natural)
    subst = sorted((r for r, p in parts.items()
                    if p["model"] and any(p["model"].endswith(t) for t in SUBST)), key=natural)
    meta = {"board": src.name, "generated": date.today().isoformat(),
            "glb": glb.name, "bytes": glb.stat().st_size, "kicad_bytes": raw_bytes,
            "parts": len(parts), "modelled": len(parts) - len(none) - len(missing) - len(empty),
            "no_model": none, "missing": missing, "empty": empty, "substituted": subst,
            "triangles": stats["triangles"], "primitives": stats["primitives"]}
    (OUT / f"{glb.stem}.json").write_text(json.dumps(meta, indent=1) + "\n")
    print("  wrote  %s  %.1f MB (kicad-cli's %.1f MB), %d triangles in %d primitives"
          % (glb.relative_to(ROOT), glb.stat().st_size / 1e6, raw_bytes / 1e6,
             stats["triangles"], stats["primitives"]))
    print("         %d of %d parts modelled; no model: %s; substituted: %s%s"
          % (meta["modelled"], len(parts), " ".join(none) or "none",
             " ".join(subst) or "none",
             ("; STILL MISSING: " + " ".join(missing)) if missing else ""))
    if empty:
        print("  ! exported with no geometry: %s -- flatten the model (tools/flatten_step.py)"
              " and add a SUBST row" % " ".join(empty))


def natural(ref):
    m = re.match(r"([A-Za-z_]*)(\d*)", ref)
    return (m.group(1), int(m.group(2) or 0), ref)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", default="a", choices=sorted(BOARDS),
                    help="which board to export (default: a)")
    ap.add_argument("--parts-only", action="store_true",
                    help="rewrite <board>.parts.json only; leave the GLB alone")
    a = ap.parse_args()
    run(a.board, a.parts_only)


if __name__ == "__main__":
    main()
