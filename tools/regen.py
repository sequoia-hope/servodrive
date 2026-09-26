#!/usr/bin/env python3
"""regen.py -- remake what the pages show, from the board as it now stands.

    python3 tools/regen.py --board s               # plots, 3D, page status; sim in the background
    python3 tools/regen.py --board s --sim wait    # ... and wait for the simulation
    python3 tools/regen.py --board s --sim no      # everything but the simulation

This is the hook.  route.py calls it at the end of every run on the real board
(not on a --pcb copy), and gen_boards.py after it emits one, so a re-route or a
re-placement cannot leave a page describing the board before it:

  - the copper viewer's layer plots and the 3D viewer's GLB and parts file
    (tools/plot_layers.py, tools/export_3d.py);
  - board S's schematic as the page shows it, an SVG per sheet and a PDF
    (img/sch/s/, schematic());
  - board S's page: the build-status numbers between <!-- status:begin --> and
    <!-- status:end -->, counted off the board, its DRC and its ERC;
  - board S's simulation (sim/run.py --board s).  It takes about two hours, so
    by default it is started detached, logs to sim/work/s/run_all.log, and
    writes the page's simulation section itself when its last phase (P9)
    finishes.
    A run already going on an older board is stopped and started again on this
    one; a run that started after the board was saved is left alone.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

BOARDS = {"a": ("motor_board", "servodrive_A"), "s": ("single_board", "servodrive_S")}
PAGES = {"s": ROOT / "single.html"}
SIM_LOG = ROOT / "sim" / "work" / "s" / "run_all.log"
SIM_LOCK = ROOT / "sim" / "work" / "s" / "run_all.pid"


def _files(key):
    hw, stem = BOARDS[key]
    d = ROOT / "hardware" / hw
    return d / f"{stem}.kicad_pcb", d / f"{stem}.kicad_sch"


# ------------------------------------------------------------ board status --
def status(key):
    """What the board file, KiCad's DRC and KiCad's ERC say about it now."""
    pcb, sch = _files(key)
    out = {"board": pcb.name, "saved": datetime.datetime.fromtimestamp(
        pcb.stat().st_mtime).isoformat(timespec="minutes")}
    # counts, in a child: pcbnew is not always itself again in a process that
    # has already exported or imported a board (see route.py)
    code = (
        "import pcbnew, json, sys\n"
        f"b = pcbnew.LoadBoard({str(pcb)!r})\n"
        "fps = list(b.GetFootprints())\n"
        "placed = [f for f in fps if not f.IsExcludedFromBOM()]\n"
        "t = list(b.GetTracks())\n"
        "print(json.dumps(dict(footprints=len(fps),\n"
        "  front=sum(1 for f in fps if not f.IsFlipped()),\n"
        "  back=sum(1 for f in fps if f.IsFlipped()),\n"
        "  placed=len(placed), dnp=sum(1 for f in placed if f.IsDNP()),\n"
        "  segments=sum(1 for x in t if x.GetClass() in ('PCB_TRACK', 'PCB_ARC')),\n"
        "  vias=sum(1 for x in t if x.GetClass() == 'PCB_VIA'),\n"
        "  nets=b.GetNetCount() - 1)))\n"
        "sys.stdout.flush()\n"
        "import os; os._exit(0)\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    try:
        out.update(json.loads(r.stdout.strip().splitlines()[-1]))
    except (ValueError, IndexError):
        out["count_error"] = r.stderr[-400:]
    with tempfile.TemporaryDirectory() as td:
        rep = Path(td) / "drc.json"
        subprocess.run(["kicad-cli", "pcb", "drc", "--format", "json", "--severity-all",
                        "-o", str(rep), str(pcb)], capture_output=True, text=True,
                       cwd=str(pcb.parent))
        if rep.exists():
            d = json.loads(rep.read_text())
            out["drc"] = len(d.get("violations", []))
            out["unconnected"] = len(d.get("unconnected_items", []))
        erc = Path(td) / "erc.json"
        subprocess.run(["kicad-cli", "sch", "erc", "--format", "json", "--severity-all",
                        "-o", str(erc), str(sch)], capture_output=True, text=True,
                       cwd=str(sch.parent))
        if erc.exists():
            d = json.loads(erc.read_text())
            out["erc"] = sum(len(s.get("violations", [])) for s in d.get("sheets", []))
    if key == "s":
        import schematic_s
        comps = schematic_s.board_s()
        nets = {}
        for c in comps:
            for n in c.nets.values():
                if n:
                    nets[n] = nets.get(n, 0) + 1
        out["captured"] = len(comps)
        out["captured_nets"] = len(nets)
        out["connections"] = sum(nets.values())
    return out


def _status_html(s):
    def n(k):
        return s.get(k, "?")
    ok = (s.get("drc") == 0 and s.get("unconnected") == 0 and s.get("erc") == 0)
    rows = [
        ("Parts on the board",
         f"{n('footprints')} footprints ({n('front')} outward, {n('back')} motor-facing): "
         f"{n('placed')} placed parts, {n('dnp')} of them DNP (the snubbers, and cell C's "
         f"unused amplifier and sense taps), plus test pads and holes"),
        ("Netlist", f"{n('captured')} components, {n('captured_nets')} nets, "
                    f"{n('connections')} connections"),
        ("Routing", f"{n('segments')} track segments, {n('vias')} vias; "
                    f"{n('unconnected')} unconnected"),
        ("Checks", f"DRC {n('drc')}, ERC {n('erc')} (every severity)"
                   + ("" if ok else " &mdash; <strong>not clean</strong>")),
        ("Sourcing", "every placed part has an LCSC number (<code>hardware/parts/lcsc.csv</code>), "
                     "shown in the 3D viewer's part pane"),
    ]
    body = "\n".join(f"        <tr><td>{a}</td><td>{b}</td></tr>" for a, b in rows)
    return (f"    <table class=\"num\">\n      <tbody>\n{body}\n      </tbody>\n    </table>\n"
            f"    <p class=\"sub\">Counted off <code>{s['board']}</code> as saved "
            f"{s['saved'].replace('T', ' ')} by <code>tools/regen.py</code>, which "
            f"<code>route.py</code> runs after every routing run.</p>")


def write_status(key, s):
    page = PAGES.get(key)
    if not page or not page.exists():
        return False
    t = page.read_text()
    a, b = "<!-- status:begin -->", "<!-- status:end -->"
    if a not in t or b not in t:
        return False
    t = t[:t.index(a) + len(a)] + "\n" + _status_html(s) + "\n    " + t[t.index(b):]
    page.write_text(t)
    return True


# -------------------------------------------------------------- schematic --
SCH_OUT = {"s": ROOT / "img" / "sch" / "s"}


def schematic(key):
    """The schematic as the page shows it: KiCad's own plot of every sheet
    (SVG), the whole of it as one PDF, and sheets.json, the viewer's index."""
    if key not in SCH_OUT:
        return
    import schematic_s
    _, sch = _files(key)
    out = SCH_OUT[key]
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*"):
        f.unlink()
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["kicad-cli", "sch", "export", "svg", "-o", td, str(sch)],
                       capture_output=True, text=True, cwd=str(sch.parent))
        sheets = [("", "Top level", "the five sheets and what is on each")]
        names = {"power_stage": "Power stage", "io": "I/O"}
        sheets += [(nm, names.get(nm.split("_", 1)[1], nm.split("_", 1)[1].capitalize()), desc)
                   for nm, desc in schematic_s.SHEETS]
        index = []
        for nm, title, desc in sheets:
            src = Path(td) / (f"{sch.stem}-{nm}.svg" if nm else f"{sch.stem}.svg")
            if not src.exists():
                continue
            dst = out / (f"{nm}.svg" if nm else "00_top.svg")
            dst.write_bytes(src.read_bytes())
            page = sch.parent / (f"{nm}.kicad_sch" if nm else sch.name)
            import re
            m = re.search(r'\(paper "([^"]+)"', page.read_text())
            index.append({"file": dst.name, "sheet": nm or "top", "title": title,
                          "desc": desc, "paper": m.group(1) if m else "A4"})
    pdf = out / f"{sch.stem}.pdf"
    subprocess.run(["kicad-cli", "sch", "export", "pdf", "-o", str(pdf), str(sch)],
                   capture_output=True, text=True, cwd=str(sch.parent))
    (out / "sheets.json").write_text(json.dumps(
        {"pdf": pdf.name if pdf.exists() else None, "sheets": index}, indent=1) + "\n")
    print(f"  schematic: {len(index)} sheets plotted to {out.relative_to(ROOT)}")


# ------------------------------------------------------------- simulation --
def sim_running():
    try:
        pid = int(SIM_LOCK.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


def start_sim(wait=False):
    pid = sim_running()
    if pid:
        # a run that started before the board was last saved is simulating an
        # older board: stop it (its whole process group, docker client
        # included) and start again; one that started after is left alone
        try:
            started = datetime.datetime.fromisoformat(SIM_LOCK.read_text().split()[1])
        except (OSError, ValueError, IndexError):
            started = datetime.datetime.min
        saved = datetime.datetime.fromtimestamp(_files("s")[0].stat().st_mtime)
        if started >= saved:
            print(f"  simulation already running on this board (pid {pid}); see {SIM_LOG}")
            return None
        import signal
        try:
            os.killpg(pid, signal.SIGTERM)
            print(f"  stopped the simulation of the older board (pid {pid})")
        except OSError:
            pass
    SIM_LOG.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-u", str(ROOT / "sim" / "run.py"), "--board", "s",
           "--keep-going"]
    env = dict(os.environ, SIM_BOARD="s")
    if wait:
        return subprocess.run(cmd, env=env).returncode
    log = open(SIM_LOG, "w")
    log.write(f"started {datetime.datetime.now().isoformat(timespec='seconds')} "
              f"by tools/regen.py: {' '.join(cmd)}\n")
    log.flush()
    p = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True,
                         cwd=str(ROOT))
    SIM_LOCK.write_text(f"{p.pid} {datetime.datetime.now().isoformat(timespec='seconds')}\n")
    print(f"  simulation started in the background (pid {p.pid}), about two hours; "
          f"log {SIM_LOG.relative_to(ROOT)}")
    return p.pid


# ------------------------------------------------------------------- main --
def run(key, sim="background", plots=True):
    print(f"regen: board {key.upper()}", flush=True)
    if plots:
        import plot_layers
        import export_3d
        plot_layers.run(key)
        export_3d.run(key)
    schematic(key)
    if key in PAGES:
        s = status(key)
        print(f"  status: {s.get('segments')} segments, {s.get('vias')} vias, "
              f"{s.get('unconnected')} unconnected, DRC {s.get('drc')}, ERC {s.get('erc')}")
        if write_status(key, s):
            print(f"  wrote {PAGES[key].name} build status")
    if key == "s" and sim != "no":
        start_sim(wait=(sim == "wait"))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", choices=sorted(BOARDS), default="s")
    ap.add_argument("--sim", choices=("background", "wait", "no"), default="background")
    ap.add_argument("--no-plots", action="store_true")
    ap.add_argument("--sch", action="store_true", help="only re-plot the schematic")
    a = ap.parse_args()
    if a.sch:
        schematic(a.board)
    else:
        run(a.board, sim=a.sim, plots=not a.no_plots)


if __name__ == "__main__":
    main()
