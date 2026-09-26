#!/usr/bin/env python3
"""schdraw.py -- the netlist drawn as a schematic a person would draw.

schematic.py and schematic_s.py hold the circuit as data: every part's
pins and the nets on them. This module draws it. A sheet's layout is written
by hand, part by part (tools/schlayout_s.py) -- where each part sits, which
way it faces, which pins are joined by a wire -- and everything the layout
does not draw is finished here the way a person finishes a sheet: a ground
or supply symbol on a power pin, a label on a signal that leaves the block,
a no-connect flag on an unused pin.

The drawing cannot change the circuit. Every sheet is checked against the
netlist it was drawn from before it is written (Sheet.check: every pin on
the net the netlist gives it, no two nets touching), and `python3
tools/schdraw.py --check s` exports KiCad's own netlist and compares it with
the Python one net by net.

Coordinates: layouts work in grid units of 2.54 mm (half units allowed), on
KiCad's 1.27 mm connection grid. Library y is up and sheet y is down:

    sheet_point = (X + rx, Y - ry)

for a symbol at (X, Y) turned counter-clockwise by A, (rx, ry) the library
point turned by A.
"""
import math
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import sexp
import schematic as SCH

U = 2.54                       # one grid unit
PAGES = {"A4": (297, 210), "A3": (420, 297), "A2": (594, 420), "A1": (841, 594)}
MARGIN = 20.0                  # page edge to drawing
TITLE_BLOCK = (112.0, 36.0)    # KiCad's title block, lower right, inside the border
BORDER = 10.0
SUPPLY = {"+12V": "power:+12V", "+5V": "power:+5V", "+3V3": "power:+3V3",
          "+3V3A": "power:+3V3", "+1V1": "power:+1V1", "VBUS": "power:VBUS",
          "GND": "power:GND"}


def snap(v):
    return round(round(v / 1.27) * 1.27, 4)


class Pt:
    """A point on the sheet, in mm."""
    __slots__ = ("x", "y")

    def __init__(self, x, y):
        self.x, self.y = snap(x), snap(y)

    def key(self):
        return (self.x, self.y)

    def go(self, dx, dy):
        """Moved by (dx, dy) grid units."""
        return Pt(self.x + dx * U, self.y + dy * U)

    def __repr__(self):
        return f"({self.x / U:g}, {self.y / U:g})u"


class Pin(Pt):
    """A symbol pin's connection point, and which way it leaves the symbol."""
    __slots__ = ("part", "num", "d", "net", "etype")

    def __init__(self, part, num, x, y, d, net, etype):
        super().__init__(x, y)
        self.part, self.num, self.d, self.net, self.etype = part, num, d, net, etype

    def out(self, n=1):
        """n grid units out from the pin, the way it points."""
        return self.go(self.d[0] * n, self.d[1] * n)

    def __repr__(self):
        return f"{self.part.ref}.{self.num}"


def _turn(px, py, ang):
    a = math.radians(ang)
    return (px * math.cos(a) - py * math.sin(a), px * math.sin(a) + py * math.cos(a))


def _dir(theta):
    a = math.radians(theta)
    return (int(round(math.cos(a))), -int(round(math.sin(a))))


def body_box(lib_id):
    """(x0, y0, x1, y1) of the symbol's drawing, pins left out, library units."""
    xs, ys = [], []
    for head in ("rectangle", "polyline", "circle", "arc"):
        for g in sexp.walk(SCH.symbol(lib_id), head):
            if head == "circle":
                c, r = sexp.find(g, "center"), sexp.find(g, "radius")
                if c and r:
                    cx, cy, rr = float(c[1]), float(c[2]), float(r[1])
                    xs += [cx - rr, cx + rr]; ys += [cy - rr, cy + rr]
                continue
            for k in ("start", "end", "mid", "xy"):
                for c in sexp.walk(g, k):
                    try:
                        xs.append(float(c[1])); ys.append(float(c[2]))
                    except (ValueError, IndexError):
                        pass
    if not xs:
        return (0, 0, 0, 0)
    return (min(xs), min(ys), max(xs), max(ys))


class Part:
    """A placed symbol."""

    def __init__(self, sheet, comp, at, rot, fields):
        self.sheet, self.comp, self.ref = sheet, comp, comp.ref
        self.at, self.rot, self.fields = at, rot % 360, fields
        self.pins = {}
        for num, (px, py, r, _, et) in SCH.pins(comp.lib_id).items():
            rx, ry = _turn(px, py, self.rot)
            self.pins[num] = Pin(self, num, at.x + rx, at.y - ry,
                                 _dir(r + 180 + self.rot), comp.nets.get(num, ""), et)

    def __getitem__(self, num):
        return self.pins[str(num)]

    def box(self):
        """The body's box on the sheet, (x0, y0, x1, y1) in mm."""
        x0, y0, x1, y1 = body_box(self.comp.lib_id)
        pts = [_turn(x, y, self.rot) for x, y in ((x0, y0), (x1, y1), (x0, y1), (x1, y0))]
        xs = [self.at.x + p[0] for p in pts]
        ys = [self.at.y - p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))


class Sheet:
    def __init__(self, name, comps, glob):
        self.name, self.glob = name, glob
        self.comps = {c.ref: c for c in comps}
        self.parts = {}
        self.powers = []           # (lib_id, net, Pt, rot)
        self.wires = []            # [Pt, Pt]
        self.labels = []           # (net, Pt, theta, kind, shape)
        self.ncs = []              # Pt
        self.texts = []            # (text, Pt, size, bold)
        self.boxes = []            # (title, x0, y0, x1, y1) in mm
        self.flags = []            # PWR_FLAG: (net, Pt)

    # -------------------------------------------------------------- parts --
    def place(self, ref, x, y, rot=0, fields=None):
        """Put part `ref` at grid (x, y), turned `rot` degrees counter-clockwise.
        fields: where the reference and value go -- 'right', 'left', 'above',
        'below', or None for the part's own default."""
        if ref in self.parts:
            raise ValueError(f"{self.name}: {ref} placed twice")
        c = self.comps.pop(ref)
        p = Part(self, c, Pt(x * U, y * U), rot, fields)
        self.parts[ref] = p
        return p

    def __getitem__(self, ref):
        return self.parts[ref]

    def g(self, x, y):
        return Pt(x * U, y * U)

    # ------------------------------------------------------------- wiring --
    def _pt(self, p):
        return p if isinstance(p, Pt) else Pt(p[0] * U, p[1] * U)

    def wire(self, *pts):
        """A polyline through the points (Pins, Pts or grid tuples); a pair
        not on one line gets a corner, horizontal first."""
        ps = [self._pt(p) for p in pts]
        for a, b in zip(ps, ps[1:]):
            if a.x != b.x and a.y != b.y:
                m = Pt(b.x, a.y)
                self.wires += [[a, m], [m, b]]
            elif a.key() != b.key():
                self.wires.append([a, b])
        return ps[-1]

    def join(self, a, b, first="h"):
        """Pin a to pin b with at most one corner: 'h' leaves a horizontally,
        'v' vertically."""
        a, b = self._pt(a), self._pt(b)
        if a.x == b.x or a.y == b.y:
            return self.wire(a, b)
        m = Pt(b.x, a.y) if first == "h" else Pt(a.x, b.y)
        return self.wire(a, m, b)

    def power(self, p, net=None, stub=0, d=None):
        """A supply or ground symbol at p, after a stub of `stub` units. A
        supply symbol points up and ground down; `d` turns it (as a
        direction) when a pin faces sideways."""
        net = net or p.net
        start = p
        if stub:
            dd = d or p.d
            q = p.go(dd[0] * stub, dd[1] * stub)
            self.wire(p, q)
            p = q
        lib = SUPPLY[net]
        if d is None:
            d = (0, 1) if net == "GND" else (0, -1)
        # which way the symbol's body points, for each rotation
        natural = (0, 1) if net == "GND" else (0, -1)
        rot = next(r for r in (0, 90, 180, 270)
                   if _dir(math.degrees(math.atan2(-natural[1], natural[0])) + r) == tuple(d))
        self.powers.append((lib, net, self._pt(p), rot))
        return start

    def label(self, p, net=None, stub=1, d=None, kind=None, text_dir=None, shape=None):
        """A net label at the end of a stub out of pin p."""
        net = net or p.net
        d = d or p.d
        q = p.go(d[0] * stub, d[1] * stub) if stub else self._pt(p)
        if stub:
            self.wire(p, q)
        theta = {(1, 0): 0, (0, -1): 90, (-1, 0): 180, (0, 1): 270}[tuple(text_dir or d)]
        if kind is None:
            kind = "global_label" if net in self.glob else "label"
        shape = shape or {"output": "output", "input": "input",
                          "bidirectional": "bidirectional", "tri_state": "tri_state"
                          }.get(getattr(p, "etype", ""), "passive")
        self.labels.append((net, q, theta, kind, shape))
        return q

    def nc(self, p):
        self.ncs.append(self._pt(p))

    def text(self, s, x, y, size=1.27, bold=False):
        self.texts.append((s, self.g(x, y), size, bold))

    def block(self, title, x0, y0, x1, y1):
        """A dashed frame round a group of parts, its title at the top left."""
        self.boxes.append((title, x0 * U, y0 * U, x1 * U, y1 * U))

    def flag(self, p, net=None, d=(0, -1), stub=1):
        """PWR_FLAG on a rail that reaches the board through a passive pin."""
        net = net or p.net
        q = p.go(d[0] * stub, d[1] * stub) if stub else self._pt(p)
        if stub:
            self.wire(p, q)
        self.flags.append((net, q))

    # ------------------------------------------------------------- finish --
    def _touched(self):
        """Every point something connects at: wire ends and interiors, power
        and flag pins, labels."""
        pts = set()
        for a, b in self.wires:
            pts.add(a.key()); pts.add(b.key())
        pts |= {p.key() for _, _, p, _ in self.powers}
        pts |= {p.key() for _, p in self.flags}
        pts |= {p.key() for _, p, _, _, _ in self.labels}
        pts |= {p.key() for p in self.ncs}
        return pts

    def _on_wire(self, k):
        for a, b in self.wires:
            if a.x == b.x == k[0] and min(a.y, b.y) <= k[1] <= max(a.y, b.y):
                return True
            if a.y == b.y == k[1] and min(a.x, b.x) <= k[0] <= max(a.x, b.x):
                return True
        return False

    def finish(self):
        """Every pin the layout left alone: ground and supply symbols on power
        pins, a label on anything else, a flag on an unused one. Pins that sit
        on one point (a FET's three sources) are one connection."""
        if self.comps:
            raise ValueError(f"{self.name}: not placed: {', '.join(sorted(self.comps))}")
        auto = []
        done = set()
        for part in self.parts.values():
            for p in part.pins.values():
                k = p.key()
                if k in done or k in self._touched() or self._on_wire(k):
                    continue
                done.add(k)
                if not p.net:
                    self.nc(p)
                elif p.net in SUPPLY:
                    d = p.d
                    if (p.net == "GND") != (d == (0, 1)) and d in ((0, 1), (0, -1)):
                        # a supply pin facing down, or ground facing up: out,
                        # round and back the right way would be a mess -- a
                        # label says it plainly
                        self.label(p)
                        auto.append((p, "label (upside-down power pin)"))
                    else:
                        self.power(p, stub=1 if d[1] == 0 else 0)
                else:
                    self.label(p)
                    if p.net not in self.glob:
                        auto.append((p, p.net))
        return auto

    # -------------------------------------------------------------- check --
    def connectivity(self):
        """Union the sheet's points into nets the way KiCad does, after the
        T-joins are split: [(names, pins)]."""
        wires = self.split()
        parent = {}

        def find(k):
            parent.setdefault(k, k)
            while parent[k] != k:
                parent[k] = parent[parent[k]]
                k = parent[k]
            return k

        def union(a, b):
            parent[find(a)] = find(b)

        for a, b in wires:
            union(a.key(), b.key())
        names = defaultdict(set)
        for lib, net, p, _ in self.powers:
            union(p.key(), ("name", net))
        for net, p in self.flags:
            find(p.key())
        for net, p, _, kind, _ in self.labels:
            union(p.key(), ("name", net))
        pins = defaultdict(list)
        for part in self.parts.values():
            for p in part.pins.values():
                find(p.key())
                pins[find(p.key())].append(p)
        groups = defaultdict(lambda: (set(), []))
        for k in list(parent):
            r = find(k)
            if isinstance(k, tuple) and k and k[0] == "name":
                groups[r][0].add(k[1])
        for r, ps in pins.items():
            groups[r][1].extend(ps)
        return list(groups.values())

    def check(self):
        """Problems with the drawing: a pin off its net, two nets touching, a
        net in pieces, a wire through a pin or a body."""
        bad = []
        where = defaultdict(list)
        for names, ps in self.connectivity():
            nets = {p.net for p in ps if p.net}
            if len(nets) > 1:
                bad.append(f"short: {', '.join(sorted(nets))} at "
                           + ", ".join(repr(p) for p in ps[:6]))
            if names and nets and names != nets:
                bad.append(f"named {sorted(names)} but carries {sorted(nets)}")
            for n in nets:
                where[n].append((names, ps))
            if not nets and not names and ps:
                if any(p.net for p in ps):
                    pass
        for n, gs in where.items():
            if len(gs) > 1 and not all(n in names for names, _ in gs):
                bad.append(f"open: {n} in {len(gs)} pieces: "
                           + " | ".join(",".join(repr(p) for p in ps) for _, ps in gs))
        for part in self.parts.values():
            for p in part.pins.values():
                if not p.net and p.key() not in {q.key() for q in self.ncs}:
                    bad.append(f"{p!r} unused but not flagged")
        # a wire through a pin it does not end on joins it, in KiCad as here
        # -- intended only when the pin's net is the wire's
        lint = []
        pinpts = {}
        for part in self.parts.values():
            for p in part.pins.values():
                pinpts.setdefault(p.key(), []).append(p)
        for a, b in self.wires:
            for part in self.parts.values():
                x0, y0, x1, y1 = part.box()
                if (a.x == b.x and x0 + 0.1 < a.x < x1 - 0.1 and
                        min(a.y, b.y) < y1 - 0.1 and max(a.y, b.y) > y0 + 0.1) or \
                   (a.y == b.y and y0 + 0.1 < a.y < y1 - 0.1 and
                        min(a.x, b.x) < x1 - 0.1 and max(a.x, b.x) > x0 + 0.1):
                    lint.append(f"wire {a}-{b} crosses {part.ref}")
        segs = self.split()
        for i, (a, b) in enumerate(segs):
            for c, d in segs[i + 1:]:
                if a.y == b.y and c.x == d.x and min(a.x, b.x) < c.x < max(a.x, b.x) \
                        and min(c.y, d.y) < a.y < max(c.y, d.y):
                    lint.append(f"wires cross at ({c.x / U:g}, {a.y / U:g})u")
                if a.x == b.x and c.y == d.y and min(c.x, d.x) < a.x < max(c.x, d.x) \
                        and min(a.y, b.y) < c.y < max(a.y, b.y):
                    lint.append(f"wires cross at ({a.x / U:g}, {c.y / U:g})u")
        return bad, lint

    # --------------------------------------------------------------- emit --
    def split(self):
        """The wires, broken wherever something connects part way along one:
        KiCad joins a wire only at its ends."""
        stops = self._touched()
        for part in self.parts.values():
            stops |= {p.key() for p in part.pins.values()}
        out = []
        for a, b in self.wires:
            cuts = [a, b]
            for k in stops:
                if a.x == b.x == k[0] and min(a.y, b.y) < k[1] < max(a.y, b.y):
                    cuts.append(Pt(*k))
                elif a.y == b.y == k[1] and min(a.x, b.x) < k[0] < max(a.x, b.x):
                    cuts.append(Pt(*k))
            cuts.sort(key=lambda p: (p.x, p.y))
            uniq = []
            for c in cuts:
                if not uniq or uniq[-1].key() != c.key():
                    uniq.append(c)
            out += [[p, q] for p, q in zip(uniq, uniq[1:])]
        return out

    def junctions(self):
        """Where three or more things meet."""
        n = defaultdict(int)
        for a, b in self.split():
            n[a.key()] += 1; n[b.key()] += 1
        pinpts = set()
        for part in self.parts.values():
            for p in part.pins.values():
                pinpts.add(p.key())
        for k in pinpts:
            if k in n:
                n[k] += 1
        for _, _, p, _ in self.powers:
            if p.key() in n:
                n[p.key()] += 1
        for _, p, _, _, _ in self.labels:
            if p.key() in n:
                n[p.key()] += 1
        return sorted(k for k, v in n.items() if v >= 3)

    def bbox(self):
        xs, ys = [], []
        for part in self.parts.values():
            x0, y0, x1, y1 = part.box()
            xs += [x0, x1]; ys += [y0, y1]
            for p in part.pins.values():
                xs.append(p.x); ys.append(p.y)
        for a, b in self.wires:
            xs += [a.x, b.x]; ys += [a.y, b.y]
        for net, p, theta, kind, _ in self.labels:
            L = 1.1 * len(net) + (3 if kind == "global_label" else 0)
            d = _dir(theta)
            xs += [p.x, p.x + d[0] * L]; ys += [p.y, p.y + d[1] * L]
        for _, _, p, _ in self.powers:
            xs += [p.x - 3, p.x + 3]; ys += [p.y - 6, p.y + 6]
        for t, x0, y0, x1, y1 in self.boxes:
            xs += [x0, x1]; ys += [y0 - 4, y1]
        for s, p, size, _ in self.texts:
            xs += [p.x, p.x + 0.8 * size * max(len(l) for l in s.split("\\n"))]; ys.append(p.y)
        return (min(xs), min(ys), max(xs), max(ys))

    def emit(self, project, file_uuid, inst_uuid, title, page=None, sheet_no=1,
             root_uuid=None):
        """The sheet as KiCad text. A symbol's instance path is the root
        sheet's uuid then this sheet's: with the root left out KiCad still
        joins labelled nets but drops every net that is only wire."""
        tag = f"{project}/{self.name}"
        x0, y0, x1, y1 = self.bbox()
        w, h = x1 - x0, y1 - y0
        for page in ([page] if page else PAGES):
            W, H = PAGES[page]
            if w + 2 * MARGIN <= W and h + 2 * MARGIN <= H and \
                    not self._hits_title(MARGIN - x0, MARGIN - y0, W, H):
                break
        else:
            raise RuntimeError(f"{self.name}: {w:.0f} x {h:.0f} mm does not fit")
        ox, oy = snap(MARGIN - x0), snap(MARGIN - y0)
        T = lambda p: (p.x + ox, p.y + oy)
        out = []
        path = f"/{root_uuid}/{inst_uuid}" if root_uuid else f"/{inst_uuid}"
        for part in self.parts.values():
            out.append(self._symbol(part, ox, oy, project, path, tag))
        for i, (lib, net, p, rot) in enumerate(self.powers):
            out.append(self._power(lib, net, T(p), rot, f"#PWR{sheet_no}{i + 1:03d}",
                                   project, path, tag, i))
        for i, (net, p) in enumerate(self.flags):
            out.append(self._power("power:PWR_FLAG", "PWR_FLAG", T(p), 0,
                                   f"#FLG{sheet_no}{i + 1:02d}", project, path, tag, f"f{i}"))
        for i, (a, b) in enumerate(self.split()):
            (ax, ay), (bx, by) = T(a), T(b)
            out.append(f'\t(wire (pts (xy {ax:.4f} {ay:.4f}) (xy {bx:.4f} {by:.4f}))\n'
                       f'\t\t(stroke (width 0) (type default)) (uuid "{SCH._uid(tag, "w", i)}"))')
        for i, k in enumerate(self.junctions()):
            out.append(f'\t(junction (at {k[0] + ox:.4f} {k[1] + oy:.4f}) (diameter 0) '
                       f'(color 0 0 0 0) (uuid "{SCH._uid(tag, "j", i)}"))')
        for i, (net, p, theta, kind, shape) in enumerate(self.labels):
            x, y = T(p)
            just = "right" if theta in (180, 270) else "left"
            if kind == "global_label":
                out.append(f'\t(global_label "{net}" (shape {shape})\n'
                           f'\t\t(at {x:.4f} {y:.4f} {theta})\n'
                           f'\t\t(effects (font (size 1.27 1.27)) (justify {just}))\n'
                           f'\t\t(uuid "{SCH._uid(tag, "gl", i)}")\n'
                           f'\t\t(property "Intersheetrefs" "${{INTERSHEET_REFS}}"\n'
                           f'\t\t\t(at {x:.4f} {y:.4f} 0)\n'
                           f'\t\t\t(effects (font (size 1.27 1.27)) (hide yes))))')
            else:
                vj = "bottom"
                out.append(f'\t(label "{net}"\n\t\t(at {x:.4f} {y:.4f} {theta})\n'
                           f'\t\t(effects (font (size 1.27 1.27)) (justify {just} {vj}))\n'
                           f'\t\t(uuid "{SCH._uid(tag, "l", i)}"))')
        for i, p in enumerate(self.ncs):
            x, y = T(p)
            out.append(f'\t(no_connect (at {x:.4f} {y:.4f}) (uuid "{SCH._uid(tag, "nc", i)}"))')
        for i, (t, bx0, by0, bx1, by1) in enumerate(self.boxes):
            out.append(f'\t(rectangle (start {bx0 + ox:.4f} {by0 + oy:.4f}) '
                       f'(end {bx1 + ox:.4f} {by1 + oy:.4f})\n'
                       f'\t\t(stroke (width 0.1524) (type dash) (color 72 72 72 1))\n'
                       f'\t\t(fill (type none)) (uuid "{SCH._uid(tag, "box", i)}"))')
            out.append(f'\t(text "{t}" (exclude_from_sim no)\n'
                       f'\t\t(at {bx0 + ox + 1.27:.4f} {by0 + oy - 1.27:.4f} 0)\n'
                       f'\t\t(effects (font (size 2.0 2.0) (bold yes)) (justify left bottom))\n'
                       f'\t\t(uuid "{SCH._uid(tag, "boxt", i)}"))')
        for i, (s, p, size, bold) in enumerate(self.texts):
            x, y = T(p)
            b = " (bold yes)" if bold else ""
            out.append(f'\t(text "{s}" (exclude_from_sim no)\n'
                       f'\t\t(at {x:.4f} {y:.4f} 0)\n'
                       f'\t\t(effects (font (size {size} {size}){b}) (justify left top))\n'
                       f'\t\t(uuid "{SCH._uid(tag, "t", i)}"))')
        used = {p.comp.lib_id for p in self.parts.values()} | {lib for lib, *_ in self.powers}
        if self.flags:
            used.add("power:PWR_FLAG")
        libs = ["\t(lib_symbols"]
        for lib_id in sorted(used):
            libs.append("\t\t" + sexp.dump(SCH.symbol(lib_id), indent=2))
        libs.append("\t)")
        return (f'(kicad_sch\n\t(version 20250114)\n\t(generator "eeschema")\n'
                f'\t(generator_version "9.0")\n\t(uuid "{file_uuid}")\n'
                f'\t(paper "{page}")\n'
                f'\t(title_block (title "{title}") (date "2026-09-25") (rev "A")\n'
                f'\t\t(company "Sequoia Hope Alexander")\n'
                f'\t\t(comment 1 "Drawn by tools/schlayout_s.py from the netlist in tools/schematic_s.py")\n'
                f'\t\t(comment 2 "CERN-OHL-P"))\n'
                + "\n".join(libs) + "\n" + "\n".join(out) + "\n"
                f'\t(embedded_fonts no)\n)\n')

    def _rects(self):
        """Rough boxes of everything drawn, for keeping clear of the title block."""
        for part in self.parts.values():
            x0, y0, x1, y1 = part.box()
            for p in part.pins.values():
                x0, y0, x1, y1 = min(x0, p.x), min(y0, p.y), max(x1, p.x), max(y1, p.y)
            yield (x0 - 2, y0 - 5, x1 + 12, y1 + 5)
        for a, b in self.wires:
            yield (min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y))
        for net, p, theta, kind, _ in self.labels:
            L = 1.1 * len(net) + 3
            d = _dir(theta)
            yield (min(p.x, p.x + d[0] * L) - 1, p.y - 2, max(p.x, p.x + d[0] * L) + 1, p.y + 2)
        for _, _, p, _ in self.powers:
            yield (p.x - 3, p.y - 6, p.x + 3, p.y + 6)
        for t, x0, y0, x1, y1 in self.boxes:
            yield (x0, y0, x1, y1)
        for s, p, size, _ in self.texts:
            lines = s.split("\\n")
            yield (p.x, p.y, p.x + 0.8 * size * max(len(l) for l in lines),
                   p.y + 1.6 * size * len(lines))

    def _hits_title(self, ox, oy, W, H):
        tx0, ty0 = W - BORDER - TITLE_BLOCK[0] - 3, H - BORDER - TITLE_BLOCK[1] - 3
        return any(x1 + ox > tx0 and y1 + oy > ty0 for x0, y0, x1, y1 in self._rects())

    # ------------------------------------------------------------ symbols --
    def _fields(self, part):
        """(x, y, angle, justify) for the reference and the value."""
        c, (X, Y), rot = part.comp, (part.at.x, part.at.y), part.rot
        f = part.fields
        two = len(SCH.pins(c.lib_id)) == 2 and c.lib_id.startswith("Device:")
        x0, y0, x1, y1 = part.box()
        if f is None:
            if two:
                f = "right" if rot in (0, 180) else "above"
            else:
                f = "lib"
        fa = 90 if rot in (90, 270) else 0
        # KiCad turns a field's justification with the symbol: to read as
        # left-justified on the sheet, a part at 90 or 180 degrees says right
        flip = {"left": "right", "right": "left"}
        def j(s):
            if s is None:
                return None
            return flip[s] if rot in (90, 180) else s
        if f == "lib":
            out = []
            for nm in ("Reference", "Value"):
                prop = next(p for p in sexp.findall(SCH.symbol(c.lib_id), "property")
                            if sexp.unq(p[1]) == nm)
                at = sexp.find(prop, "at")
                rx, ry = _turn(float(at[1]), float(at[2]), rot)
                eff = sexp.find(prop, "effects")
                jj = sexp.find(eff, "justify") if eff else None
                just = " ".join(str(v) for v in jj[1:]) if jj else None
                out.append((X + rx, Y - ry, float(at[3]) + (fa if rot in (90, 270) else 0), just))
            return out
        if f == "right":
            return [(x1 + 1.27, Y - 1.27, fa, j("left")), (x1 + 1.27, Y + 1.27, fa, j("left"))]
        if f == "left":
            return [(x0 - 1.27, Y - 1.27, fa, j("right")), (x0 - 1.27, Y + 1.27, fa, j("right"))]
        if f == "above":
            return [(X, y0 - 3.81, fa, None), (X, y0 - 1.27, fa, None)]
        if f == "below":
            return [(X, y1 + 1.9, fa, None), (X, y1 + 4.44, fa, None)]
        if f == "aboveleft":
            return [(x0, y0 - 3.81, fa, j("left")), (x0, y0 - 1.27, fa, j("left"))]
        if f == "belowleft":
            return [(x0, y1 + 1.27, fa, j("left")), (x0, y1 + 3.81, fa, j("left"))]
        if f == "aboveright":
            return [(x1, y0 - 3.81, fa, j("right")), (x1, y0 - 1.27, fa, j("right"))]
        if isinstance(f, tuple):          # (dx, dy[, just]) grid units from the centre
            dx, dy = f[0] * U, f[1] * U
            jj = j(f[2]) if len(f) > 2 else j("left") if dx >= 0 else j("right")
            return [(X + dx, Y + dy - 1.27, fa, jj), (X + dx, Y + dy + 1.27, fa, jj)]
        raise ValueError(f)

    def _symbol(self, part, ox, oy, project, path, tag):
        c = part.comp
        x, y = part.at.x + ox, part.at.y + oy
        body = [f'\t(symbol\n\t\t(lib_id "{c.lib_id}")\n'
                f'\t\t(at {x:.4f} {y:.4f} {part.rot})\n\t\t(unit 1)\n'
                f'\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n'
                f'\t\t(dnp {"yes" if c.dnp else "no"})\n'
                f'\t\t(uuid "{SCH._uid(tag, c.ref)}")']
        for nm, val, (fx, fy, fa, just) in zip(("Reference", "Value"), (c.ref, c.value),
                                              self._fields(part)):
            jj = f" (justify {just})" if just else ""
            body.append(f'\t\t(property "{nm}" "{val}"\n\t\t\t(at {fx + ox:.4f} {fy + oy:.4f} {fa:g})\n'
                        f'\t\t\t(effects (font (size 1.27 1.27)){jj}))')
        body.append(f'\t\t(property "Footprint" "{c.fp}" (at {x:.4f} {y:.4f} 0)\n'
                    f'\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))')
        body.append(f'\t\t(property "Datasheet" "~" (at {x:.4f} {y:.4f} 0)\n'
                    f'\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))')
        for num in sorted(SCH.pins(c.lib_id), key=lambda s: (len(s), s)):
            body.append(f'\t\t(pin "{num}" (uuid "{SCH._uid(tag, c.ref, "p", num)}"))')
        body.append(f'\t\t(instances\n\t\t\t(project "{project}"\n'
                    f'\t\t\t\t(path "{path}"\n'
                    f'\t\t\t\t\t(reference "{c.ref}") (unit 1)\n'
                    f'\t\t\t\t)\n\t\t\t)\n\t\t)')
        return "\n".join(body) + "\n\t)"

    def _power(self, lib, net, xy, rot, ref, project, path, tag, i):
        x, y = xy
        prop = next(p for p in sexp.findall(SCH.symbol(lib), "property")
                    if sexp.unq(p[1]) == "Value")
        at = sexp.find(prop, "at")
        rx, ry = _turn(float(at[1]), float(at[2]), rot)
        fa = 90 if rot in (90, 270) else 0
        vx, vy = x + rx, y - ry
        jj = ""
        if rot in (90, 270):
            # sideways: the name just beyond the tip, reading across. Which
            # way the body points, on the sheet:
            natural = (0, 1) if lib == "power:GND" else (0, -1)
            a = math.degrees(math.atan2(-natural[1], natural[0])) + rot
            d = _dir(a)
            vx, vy = x + d[0] * 3.3, y
            visual = "left" if d[0] > 0 else "right"
            # KiCad turns the justification with the symbol (see _fields)
            flip = {"left": "right", "right": "left"}
            jj = f" (justify {flip[visual] if rot == 90 else visual})"
        return (f'\t(symbol\n\t\t(lib_id "{lib}")\n\t\t(at {x:.4f} {y:.4f} {rot})\n'
                f'\t\t(unit 1)\n\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n'
                f'\t\t(on_board yes)\n\t\t(dnp no)\n'
                f'\t\t(uuid "{SCH._uid(tag, "pwr", i)}")\n'
                f'\t\t(property "Reference" "{ref}" (at {x:.4f} {y:.4f} 0)\n'
                f'\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))\n'
                f'\t\t(property "Value" "{net}" (at {vx:.4f} {vy:.4f} {fa})\n'
                f'\t\t\t(effects (font (size 1.27 1.27)){jj}))\n'
                f'\t\t(property "Footprint" "" (at {x:.4f} {y:.4f} 0)\n'
                f'\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))\n'
                f'\t\t(pin "1" (uuid "{SCH._uid(tag, "pwr", i, "p")}"))\n'
                f'\t\t(instances (project "{project}" (path "{path}" '
                f'(reference "{ref}") (unit 1))))\n\t)')


# ------------------------------------------------------------------ check ---
def kicad_netlist(root_sch):
    """{net name: {(ref, pin)}} from KiCad's own export of the schematic."""
    out = Path(root_sch).with_suffix(".check.net")
    r = subprocess.run(["kicad-cli", "sch", "export", "netlist", "--format", "kicadsexpr",
                        "-o", str(out), str(root_sch)], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr)
    tree = sexp.parse(out.read_text())
    out.unlink()
    nets = {}
    for n in sexp.walk(tree, "net"):
        name = sexp.find(n, "name")
        if not name:
            continue
        nodes = {(sexp.unq(sexp.find(x, "ref")[1]), sexp.unq(sexp.find(x, "pin")[1]))
                 for x in sexp.findall(n, "node")}
        nets[sexp.unq(name[1])] = nodes
    return nets


def compare(kicad, comps):
    """Differences between KiCad's netlist and the Python one: the pin sets
    must match net for net (names aside: KiCad prefixes a sheet-local net
    with its sheet path)."""
    want = defaultdict(set)
    for c in comps:
        if c.ref.startswith("#"):
            continue
        for pin, net in c.nets.items():
            if net:
                want[net].add((c.ref, pin))
    got = {frozenset(v): k for k, v in kicad.items()
           if not k.startswith("unconnected-") and not all(r.startswith("#") for r, _ in v)}
    bad = []
    for net, pins in want.items():
        if frozenset(pins) not in got:
            near = [k for k, v in kicad.items() if pins & v]
            bad.append(f"{net}: {len(pins)} pins; KiCad has them in {near}")
    for k, v in kicad.items():
        if k.startswith("unconnected-"):
            (ref, pin), = v
            c = next((c for c in comps if c.ref == ref), None)
            if c is not None and c.nets.get(pin):
                bad.append(f"{ref}.{pin} unconnected in KiCad, {c.nets[pin]} in the netlist")
    return bad


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", choices=("s",), help="compare KiCad's netlist with the Python one")
    a = ap.parse_args()
    if a.check == "s":
        import schematic_s as S
        root = SCH.ROOT / "hardware/single_board/servodrive_S.kicad_sch"
        bad = compare(kicad_netlist(root), S.board_s())
        print("\n".join(bad) if bad else f"netlist: KiCad's export matches schematic_s.nets()")
        sys.exit(1 if bad else 0)
