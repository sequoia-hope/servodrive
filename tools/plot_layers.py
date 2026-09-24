#!/usr/bin/env python3
"""plot_layers.py — one SVG per copper layer, for the copper viewer on index.html.

    python3 tools/plot_layers.py            # board A
    python3 tools/plot_layers.py --board b  # board B

Writes into img/layers/<board>/:

    f.svg in1.svg in2.svg in3.svg in4.svg b.svg
                one transparent SVG per copper layer, all on the same page and
                cropped to the same rectangle, so they stack in register; all
                drawn from the top, unmirrored (the page mirrors on demand)
    edge.svg    the outline, the viewer's base image
    fab_f.svg fab_b.svg
                part outlines and reference designators, front and back, the
                only way to tell what a piece of copper belongs to
    layers.json the panel's facts: colour, track count and length, pad count,
                pour nets, and the geometry the viewer needs to place the
                board body and turn a cursor into a radius and an angle

`kicad-cli pcb export svg --page-size-mode 2` crops every export to the same
board-area page, which is what puts the layers in register; this then narrows
all of them to the outline's own bounding box so the board fills the frame.
The export keeps KiCad's theme colours, so a layer is the colour it is in
pcbnew; drill holes come out white on copper and black elsewhere and are
repainted in the viewer's plate colour, so a hole reads as a hole.

SVG rather than PNG because the point of the thing is to zoom in on a track.
"""
import argparse, json, re, subprocess, shutil, sys, tempfile
from datetime import date
from math import atan2, degrees, hypot, isclose
from pathlib import Path

import sexp

ROOT = Path(__file__).resolve().parent.parent
HW = ROOT / "hardware"

BOARDS = {"a": ("motor_board", "servodrive_A"),
          "s": ("single_board", "servodrive_S"),
          "b": ("power_board", "servodrive_B")}

# Extra layers drawn alongside the copper: the outline is the viewer's base
# image, the fab layers are the optional "what part is this" overlay.
EXTRA = [("Edge.Cuts", "edge", "outline", "Board outline"),
         ("F.Fab", "fab_f", "fab", "Parts, front"),
         ("B.Fab", "fab_b", "fab", "Parts, back")]

# The plate the viewer draws the board on -- the grey canvas of Altium 365's
# viewer, which the pages are styled after. Drill holes are painted this
# colour so they punch through the board body to the background; copper.js
# reads it back out of layers.json for the canvas itself.
PLATE = "#c8c8c8"

# A layer keeps the colour pcbnew draws it in, so the page and the editor
# agree -- except the fab layers, which are annotation drawn over whatever
# copper is underneath and have to stay readable there. KiCad's B.Fab in
# particular is darker than the copper it has to be read against.
OVERRIDE = {"F.Fab": "#DDE1E8", "B.Fab": "#96A2CE"}

# What each layer is for. Shown in the panel under the layer's name; the
# copper ones are the captions the four static layer figures used to carry.
NOTES = {
    "F.Cu": "Outward face, 2 oz. Three phase cells, three utility wedges, and "
            "the three arcs of bare copper the heatsink ring clamps.",
    "In1.Cu": "Solid ground, directly under F.Cu — the return for everything "
              "switching above it, and the other plate of the plane capacitor "
              "In2 forms.",
    "In2.Cu": "The power plane. VBUS over the phase block and the link that "
              "feeds it, +3V3 over the CPU wedge.",
    "In3.Cu": "The signal layer, and the only one that crosses the whole board, "
              "so the long hauls between the RP2350A and the phase cells live "
              "here.",
    "In4.Cu": "Solid ground again, under the signal layer and over the "
              "motor-facing copper.",
    "B.Cu": "Motor-facing face, 2 oz. MT6701 at the centre, the shunts and the "
            "sense amplifiers, and the FET drain thermal vias coming through. "
            "Motor leads land here, so the outward perimeter stays thermal.",
    "Edge.Cuts": "The outline and every hole in it.",
    "F.Fab": "Part bodies and reference designators, front.",
    "B.Fab": "Part bodies and reference designators, back.",
}


def sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit("FAILED: %s\n%s%s" % (" ".join(map(str, cmd)), r.stdout, r.stderr))
    return r


# ------------------------------------------------------------------ svg ----
NUM = r"-?\d+(?:\.\d+)?"


def page_of(text):
    """(width mm, height mm, viewBox) of an exported svg."""
    w = re.search(r'width="(%s)mm"' % NUM, text)
    h = re.search(r'height="(%s)mm"' % NUM, text)
    vb = re.search(r'viewBox="([^"]+)"', text)
    if not (w and h and vb):
        sys.exit("cannot read the page size out of the export")
    return float(w.group(1)), float(h.group(1)), vb.group(1)


def circles(text):
    """Every <circle> as (cx, cy, r)."""
    return [(float(a), float(b), float(c)) for a, b, c in re.findall(
        r'<circle cx="(%s)" cy="(%s)" r="(%s)"' % (NUM, NUM, NUM), text)]


def body(text):
    """The drawing, without the xml preamble, the doctype and the titles."""
    i = text.index(">", text.index("<svg")) + 1
    t = text[i:text.rindex("</svg>")]
    t = re.sub(r"<(title|desc)>.*?</\1>", "", t, flags=re.S)
    return t


def shrink(text):
    """Three decimals is a micron; the exporter writes four and a lot of air."""
    text = re.sub(r"(\d+\.\d{4,})", lambda m: ("%.3f" % float(m.group(1))).rstrip("0").rstrip("."), text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return re.sub(r"\n{2,}", "\n", text).strip() + "\n"


def recolour(text, kind):
    """Repaint the drill holes in the plate colour. KiCad draws them white on
    a copper layer and black everywhere else; on a copper layer black is only
    the exporter's empty opening group, which draws nothing."""
    text = text.replace("#FFFFFF", PLATE)
    if kind != "copper":
        text = text.replace("#000000", PLATE)
    return text


def dominant(text, skip):
    """The colour the layer is actually drawn in: the one used by the most
    elements, ignoring the plate and the exporter's boilerplate."""
    counts = {}
    for c in re.findall(r"#[0-9A-Fa-f]{6}", text):
        c = c.upper()
        if c not in skip:
            counts[c] = counts.get(c, 0) + 1
    return max(counts, key=counts.get) if counts else "#888888"


# ---------------------------------------------------------- board facts ----
def facts_of(board, copper):
    """Track count, copper length, pad count and pour nets, per layer."""
    doc = sexp.parse(board.read_text())
    f = {ly: {"tracks": 0, "length_mm": 0.0, "pads": 0, "zones": []}
         for ly in copper}
    vias = 0

    def xy(node, head):
        n = sexp.find(node, head)
        return (float(n[1]), float(n[2])) if n else None

    def layer_of(node):
        n = sexp.find(node, "layer")
        return sexp.unq(n[1]) if n else None

    def add_pads(fp, flip):
        for pad in sexp.findall(fp, "pad"):
            lys = sexp.find(pad, "layers") or []
            names = [sexp.unq(a) for a in lys[1:]]
            for ly in copper:
                if ly in names or "*.Cu" in names:
                    f[ly]["pads"] += 1

    for n in doc:
        if not isinstance(n, list) or not n:
            continue
        head = n[0]
        if head == "segment":
            ly = layer_of(n)
            if ly in f:
                (x1, y1), (x2, y2) = xy(n, "start"), xy(n, "end")
                f[ly]["tracks"] += 1
                f[ly]["length_mm"] += hypot(x2 - x1, y2 - y1)
        elif head == "arc":
            ly = layer_of(n)
            if ly in f:
                f[ly]["tracks"] += 1
                f[ly]["length_mm"] += arc_len(xy(n, "start"), xy(n, "mid"),
                                              xy(n, "end"))
        elif head == "via":
            vias += 1
        elif head == "zone":
            name = sexp.find(n, "net_name")
            net = sexp.unq(name[1]) if name and len(name) > 1 else ""
            if not net:
                continue                     # a rule area carries no net
            lys = sexp.find(n, "layers") or sexp.find(n, "layer") or []
            for a in lys[1:]:
                ly = sexp.unq(a)
                if ly in f and net not in f[ly]["zones"]:
                    f[ly]["zones"].append(net)
        elif head == "footprint":
            add_pads(n, layer_of(n) == "B.Cu")
    return f, vias


def arc_len(a, m, b):
    """Length of the arc through three points, chord if they are collinear."""
    if not (a and m and b):
        return 0.0
    (x1, y1), (x2, y2), (x3, y3) = a, m, b
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if isclose(d, 0.0, abs_tol=1e-9):
        return hypot(x3 - x1, y3 - y1)
    ux = ((x1 ** 2 + y1 ** 2) * (y2 - y3) + (x2 ** 2 + y2 ** 2) * (y3 - y1)
          + (x3 ** 2 + y3 ** 2) * (y1 - y2)) / d
    uy = ((x1 ** 2 + y1 ** 2) * (x3 - x2) + (x2 ** 2 + y2 ** 2) * (x1 - x3)
          + (x3 ** 2 + y3 ** 2) * (x2 - x1)) / d
    r = hypot(x1 - ux, y1 - uy)
    a1, a2, a3 = (atan2(y - uy, x - ux) for x, y in (a, m, b))
    sweep = (a3 - a1) % (2 * 3.141592653589793)
    mid = (a2 - a1) % (2 * 3.141592653589793)
    if mid > sweep:                          # the arc goes the other way round
        sweep = 2 * 3.141592653589793 - sweep
    return r * sweep


# ----------------------------------------------------------------- main ----
def run(board="a", margin=0.75):
    """Plot one board's layers. gen_boards.py and route.py call this."""
    if not shutil.which("kicad-cli"):
        print("  ! kicad-cli not found, skipping the copper viewer"); return
    key, name = BOARDS[board]
    src = HW / key / f"{name}.kicad_pcb"
    if not src.exists():
        sys.exit(f"no board at {src}")
    out = ROOT / "img" / "layers" / board
    out.mkdir(parents=True, exist_ok=True)

    copper = [sexp.unq(l[1]) for l in
              sexp.find(sexp.parse(src.read_text()), "layers")[1:]
              if sexp.unq(l[1]).endswith(".Cu")]
    plan = [(ly, ly.split(".")[0].lower(), "copper", ly) for ly in copper] + EXTRA

    # every export is the same board-area page, or the layers do not register
    # (kicad-cli has no stdout mode: "-o -" writes a file called "-")
    raw, page = {}, None
    tmp = Path(tempfile.mkdtemp(prefix="servodrive_layers_"))
    for ly, slug, kind, _ in plan:
        svg = tmp / f"{slug}.svg"
        sh(["kicad-cli", "pcb", "export", "svg", "--mode-single",
            "--layers", ly, "--page-size-mode", "2",
            "--exclude-drawing-sheet", "-o", str(svg), str(src)])
        text = svg.read_text()
        p = page_of(text)
        if page and p != page:
            sys.exit(f"layer {ly} is on page {p}, not {page}: they would not register")
        page, raw[slug] = p, text
    shutil.rmtree(tmp)

    # crop to the outline's own box, so the board fills the frame
    outline = max(circles(raw["edge"]), key=lambda c: c[2], default=None)
    if outline:
        cx, cy, r = outline
        vx, vy, vw, vh = cx - r - margin, cy - r - margin, \
            2 * (r + margin), 2 * (r + margin)
    else:                                     # not a round board: keep the page
        cx = cy = r = 0.0
        vx, vy, vw, vh = 0.0, 0.0, page[0], page[1]
    view = 'width="%.4fmm" height="%.4fmm" viewBox="%.4f %.4f %.4f %.4f"' % (
        vw, vh, vx, vy, vw, vh)

    facts, vias = facts_of(src, copper)
    layers, total = [], 0
    for ly, slug, kind, label in plan:
        text = raw[slug]
        drawn = dominant(text, {"#FFFFFF", "#000000", PLATE.upper()})
        colour = OVERRIDE.get(ly, drawn)
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" version="1.1" %s>%s</svg>'
               % (view, recolour(body(text), kind).replace(drawn, colour)))
        path = out / f"{slug}.svg"
        path.write_text(shrink(svg))
        total += path.stat().st_size
        row = {"name": ly, "slug": slug, "kind": kind, "file": path.name,
               "color": colour, "note": NOTES.get(ly, "")}
        if kind == "copper":
            f = facts[ly]
            row.update(tracks=f["tracks"], length_mm=round(f["length_mm"], 1),
                       pads=f["pads"], zones=f["zones"])
        layers.append(row)
        print("  %-9s %-10s %7d bytes  %s" % (ly, colour, path.stat().st_size,
              ("%d tracks, %.0f mm, %d pads, pour %s"
               % (row["tracks"], row["length_mm"], row["pads"],
                  ", ".join(row["zones"]) or "none")) if kind == "copper" else label))

    meta = {"board": src.name, "generated": date.today().isoformat(),
            "page_mm": [round(page[0], 4), round(page[1], 4)],
            "view_mm": [round(v, 4) for v in (vx, vy, vw, vh)],
            "centre_mm": [round(cx - vx, 4), round(cy - vy, 4)],
            "dia_mm": round(2 * r, 3), "plate": PLATE,
            "vias": vias, "layers": layers}
    (out / "layers.json").write_text(json.dumps(meta, indent=1) + "\n")
    print("  view %.2f x %.2f mm, board Ø%.1f mm, %d vias, %.1f MB in %s"
          % (vw, vh, 2 * r, vias, total / 1e6,
             (out / "layers.json").parent.relative_to(ROOT)))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", default="a", choices=sorted(BOARDS),
                    help="which board to plot (default: a)")
    ap.add_argument("--margin", type=float, default=0.75,
                    help="mm of air around the outline (default: 0.75)")
    args = ap.parse_args()
    run(args.board, args.margin)


if __name__ == "__main__":
    main()
