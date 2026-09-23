#!/usr/bin/env python3
"""Run openEMS (L2) inside the container built by sim/setup.py.

openEMS needs a dozen apt packages that cannot be installed on this machine
without a password, so it lives in an image and talks to the rest of the
simulation through the filesystem: a generated script goes into
`sim/work/openems/<name>/`, the container runs it with only `sim/` mounted
(SPEC.md sec.5.3), and results come back as .npz / .json in the same directory.

    out = run("microstrip", SCRIPT, timeout=1800)
    data = result(out)
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import paths                                        # noqa: E402

IMAGE = paths.OPENEMS_IMAGE
PYTHON = "/opt/openEMS/venv/bin/python3"
PREAMBLE = '''
import os, sys, json
import numpy as np
from openEMS import openEMS
from openEMS.physical_constants import C0, EPS0, MUE0
from CSXCAD import ContinuousStructure
OUT = os.path.dirname(os.path.abspath(__file__))
def save(**kw):
    np.savez(os.path.join(OUT, "result.npz"),
             **{k: np.asarray(v) for k, v in kw.items()})
def save_json(d):
    with open(os.path.join(OUT, "result.json"), "w") as f:
        json.dump(d, f, indent=1, default=float)
'''


def available():
    return subprocess.run(["docker", "image", "inspect", IMAGE],
                          capture_output=True).returncode == 0


def run(name, source, timeout=7200, clean=True, nthreads=None, env=None):
    """Write `source` into work/openems/<name>/run.py and execute it."""
    if not available():
        raise RuntimeError(f"openEMS image {IMAGE} not built; run sim/setup.py")
    wd = paths.WORK / "openems" / name
    if clean and wd.exists():
        shutil.rmtree(wd)
    wd.mkdir(parents=True, exist_ok=True)
    # a previous run's answer must never survive into this one's directory:
    # clean=False keeps the geometry and the logs, not the result
    for stale in ("result.json", "result.npz", "stdout.txt", "stderr.txt"):
        (wd / stale).unlink(missing_ok=True)
    (wd / "run.py").write_text(PREAMBLE + "\n" + source)
    rel = wd.relative_to(paths.SIM)
    uid = f"{os.getuid()}:{os.getgid()}"
    cmd = ["docker", "run", "--rm", "-u", uid,
           "-v", f"{paths.SIM}:/work",
           "-w", f"/work/{rel}",
           "-e", "HOME=/tmp"]
    if nthreads:
        cmd += ["-e", f"OMP_NUM_THREADS={nthreads}"]
    for k, v in (env or {}).items():
        cmd += ["-e", f"{k}={v}"]
    cmd += [IMAGE, PYTHON, "run.py"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    (wd / "stdout.txt").write_text(p.stdout)
    (wd / "stderr.txt").write_text(p.stderr)
    if p.returncode != 0:
        raise RuntimeError(f"openEMS run '{name}' failed ({p.returncode})\n"
                           f"{p.stdout[-3000:]}\n{p.stderr[-3000:]}")
    return wd


def result(wd):
    import numpy as np
    npz = wd / "result.npz"
    js = wd / "result.json"
    out = {}
    if npz.exists():
        out.update({k: v for k, v in np.load(npz).items()})
    if js.exists():
        out.update(json.loads(js.read_text()))
    return out
