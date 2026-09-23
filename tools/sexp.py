#!/usr/bin/env python3
"""sexp.py — a small reader/writer for KiCad's s-expression files.

Regex got the board files placed, and then cost a day: a pattern that assumed
what sits between a property's name and its layer silently missed one footprint
and left six MOSFETs designated "AH2". Symbol libraries are worse — pins live
inside per-unit sub-symbols — so from here the schematic work parses properly.

    >>> t = parse('(a (b "x") 3)')
    >>> t[0], t[1], t[2]
    ('a', ['b', '"x"'], '3')

Atoms stay as written, quotes included, so a round trip is byte-exact.
"""

def parse(text):
    """Parse one s-expression. Returns nested lists of str."""
    stack, cur, i, n = [], [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '(':
            stack.append(cur); cur = []
            i += 1
        elif c == ')':
            done = cur
            if not stack:
                raise ValueError(f"unbalanced ) at {i}")
            cur = stack.pop()
            cur.append(done)
            i += 1
        elif c == '"':
            j = i + 1
            while j < n:
                if text[j] == '\\':
                    j += 2
                    continue
                if text[j] == '"':
                    break
                j += 1
            cur.append(text[i:j + 1])
            i = j + 1
        elif c.isspace():
            i += 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            cur.append(text[i:j])
            i = j
    if stack:
        raise ValueError("unbalanced (")
    if len(cur) != 1:
        raise ValueError(f"expected one top-level form, got {len(cur)}")
    return cur[0]

def parse_all(text):
    """Parse a file that may hold several top-level forms."""
    out, i = [], 0
    while True:
        j = text.find('(', i)
        if j < 0:
            return out
        depth, k, in_str = 0, j, False
        while k < len(text):
            c = text[k]
            if in_str:
                if c == '\\':
                    k += 2
                    continue
                if c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    break
            k += 1
        out.append(parse(text[j:k + 1]))
        i = k + 1

def unq(a):
    """Strip the quotes off an atom, if it has any."""
    if isinstance(a, str) and len(a) >= 2 and a[0] == '"' and a[-1] == '"':
        return a[1:-1].replace('\\"', '"').replace('\\\\', '\\')
    return a

def q(s):
    """Quote a string the way KiCad does."""
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"') + '"'

def find(node, head):
    """First direct child list whose head is `head`."""
    for c in node:
        if isinstance(c, list) and c and c[0] == head:
            return c
    return None

def findall(node, head):
    """Every direct child list whose head is `head`."""
    return [c for c in node
            if isinstance(c, list) and c and c[0] == head]

def walk(node, head):
    """Every descendant list whose head is `head`, at any depth."""
    out = []
    if isinstance(node, list):
        if node and node[0] == head:
            out.append(node)
        for c in node:
            out.extend(walk(c, head))
    return out

def dump(node, indent=0):
    """Render back to text. Not byte-identical to KiCad's own formatting, but
    valid, stable, and diff-able."""
    if isinstance(node, str):
        return node
    pad = "\t" * indent
    simple = all(not isinstance(c, list) for c in node)
    if simple:
        return "(" + " ".join(node) + ")"
    head = node[0] if isinstance(node[0], str) else ""
    parts = [head]
    for c in node[1:]:
        if isinstance(c, list):
            parts.append("\n" + "\t" * (indent + 1) + dump(c, indent + 1))
        else:
            parts.append(" " + c)
    return "(" + "".join(parts) + "\n" + pad + ")"
