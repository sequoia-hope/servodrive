#!/usr/bin/env python3
"""flatten_step.py — a STEP assembly as one flat part, for kicad-cli's GLB export.

    python3 tools/flatten_step.py IN.step OUT.step [--name NAME]

kicad-cli 9.0.8's GLB exporter writes a model whose STEP is an assembly of
sub-assemblies as empty nodes: the file loads, the nodes mirror its product
structure, not one triangle is meshed, and it prints no warning. Every flat
model on the boards exports; the one nested model -- the library's
L_Sunlord_SWPA4030S.step, CadQuery's "CQ assembly" three levels deep -- came
out invisible. OCC itself meshes the same solids without complaint.

This keeps the file's own solids and the colours styled onto them, and drops
the assembly around them: one product, one ADVANCED_BREP_SHAPE_REPRESENTATION
holding every solid. The entities kept are written as they were, under their
own numbers. It refuses a file in which any assembly placement is not the
identity, since flattening would then move a solid.
"""
import argparse, re, sys
from pathlib import Path

SOLIDS = ("MANIFOLD_SOLID_BREP", "BREP_WITH_VOIDS")


def entities(data):
    """#id -> body text, from a DATA section. Splits on the ';' that ends each
    instance, outside quoted strings."""
    out, i, n = {}, 0, len(data)
    while True:
        m = re.compile(r"#(\d+)\s*=\s*").search(data, i)
        if not m:
            return out
        j, quoted = m.end(), False
        while j < n:
            c = data[j]
            if c == "'":
                quoted = not quoted
            elif c == ";" and not quoted:
                break
            j += 1
        out[int(m.group(1))] = data[m.end():j].strip()
        i = j + 1


def refs(body):
    return [int(r) for r in re.findall(r"#(\d+)", re.sub(r"'[^']*'", "''", body))]


def kind(body):
    return body.lstrip("( ").split("(", 1)[0].strip()


def args(body):
    """Top-level arguments of NAME(...), as text."""
    inner = body[body.index("(") + 1:body.rindex(")")]
    out, depth, cur, quoted = [], 0, "", False
    for c in inner:
        if c == "'":
            quoted = not quoted
        if not quoted and c == "(":
            depth += 1
        elif not quoted and c == ")":
            depth -= 1
        if c == "," and depth == 0 and not quoted:
            out.append(cur.strip()); cur = ""
        else:
            cur += c
    return out + [cur.strip()]


def placement(ents, i):
    """AXIS2_PLACEMENT_3D -> (origin, axis, ref direction), defaults filled."""
    a = args(ents[i])
    num = lambda j: tuple(float(v) for v in re.findall(r"[-+.\dEe]+", args(ents[j])[1]))
    org = num(int(a[1][1:]))
    axis = num(int(a[2][1:])) if a[2] != "$" else (0.0, 0.0, 1.0)
    ref = num(int(a[3][1:])) if a[3] != "$" else (1.0, 0.0, 0.0)
    return org, axis, ref


def flatten(text, name=None):
    head, rest = text.split("DATA;", 1)
    data, tail = rest.split("ENDSEC;", 1)
    ents = entities(data)
    by = lambda k: [i for i, b in ents.items() if kind(b) == k]

    # every placement the assembly applies must be the identity
    for i in by("ITEM_DEFINED_TRANSFORMATION"):
        a, b = (int(x[1:]) for x in args(ents[i])[2:4])
        pa, pb = placement(ents, a), placement(ents, b)
        if any(abs(u - v) > 1e-9 for p, q in zip(pa, pb) for u, v in zip(p, q)):
            sys.exit(f"#{i} places a part somewhere other than where it is modelled: "
                     "flattening would move it")

    # the top product: a definition that is a parent in the assembly, never a child
    nauo = [[int(x[1:]) for x in args(ents[i])[3:5]] for i in by("NEXT_ASSEMBLY_USAGE_OCCURRENCE")]
    children = {c for _, c in nauo}
    tops = {p for p, _ in nauo if p not in children} or set(by("PRODUCT_DEFINITION"))
    if len(tops) != 1:
        sys.exit(f"expected one top product definition, found {sorted(tops)}")
    top = tops.pop()
    pds = [i for i in by("PRODUCT_DEFINITION_SHAPE") if refs(ents[i]) == [top]]
    sdr = [i for i in by("SHAPE_DEFINITION_REPRESENTATION") if refs(ents[i])[0] in pds][0]
    rep = refs(ents[sdr])[1]
    items, ctx = refs(ents[rep])[:-1], refs(ents[rep])[-1]
    frame = [i for i in items if kind(ents[i]) == "AXIS2_PLACEMENT_3D"][:1]
    solids = sorted(i for i, b in ents.items() if kind(b) in SOLIDS)
    if not solids:
        sys.exit("no solids in the file")
    ents[rep] = "ADVANCED_BREP_SHAPE_REPRESENTATION('',(%s),#%d)" % (
        ",".join("#%d" % i for i in frame + solids), ctx)

    product = refs(ents[refs(ents[top])[0]])[0]           # definition -> formation -> product
    if name:
        a = args(ents[product])
        ents[product] = "PRODUCT('%s','%s',%s)" % (name, name, ",".join(a[2:]))

    # keep what the flat part reaches, and the colours
    roots = by("APPLICATION_PROTOCOL_DEFINITION") + [sdr] + \
        by("MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION") + \
        [i for i in by("PRODUCT_RELATED_PRODUCT_CATEGORY") if refs(ents[i]) == [product]]
    keep, todo = set(), list(roots)
    while todo:
        i = todo.pop()
        if i not in keep:
            keep.add(i)
            todo.extend(refs(ents[i]))
    body = "".join("#%d = %s;\n" % (i, ents[i]) for i in sorted(keep))
    return head + "DATA;\n" + body + "ENDSEC;" + tail, len(solids), len(ents) - len(keep)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--name", help="the product's name in the flat file")
    a = ap.parse_args()
    text, n, dropped = flatten(a.src.read_text(), a.name)
    a.out.write_text(text)
    print(f"  wrote  {a.out}  {n} solids, {dropped} assembly entities dropped")


if __name__ == "__main__":
    main()
