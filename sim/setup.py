#!/usr/bin/env python3
"""P0: build the solvers this machine does not have.

There is no passwordless sudo here, so nothing is apt-installed: FastHenry2 and
FastCap2 are built from the FastFieldSolvers sources into ~/opt, openEMS is
built inside a container, and ngspice is used through the `libngspice.so.0`
that KiCad already installs (see spice/ngspice.py).  Everything the spec's
sec.5.3 asks for is therefore present, by a different route, and this script
records the route so the build reproduces.

    python3 sim/setup.py            # build whatever is missing
    python3 sim/setup.py --force    # rebuild everything
    python3 sim/setup.py --check    # report only
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import paths                                        # noqa: E402

OPT = paths.OPT
LOG = paths.WORK / "setup"

# GCC 15 defaults to C23; these are 1990s K&R sources.  -std=gnu89 restores the
# old function-declaration rules, -fcommon the old tentative-definition rules,
# and the -Wno-error flags undo GCC 14's promotion of implicit declarations to
# errors.  Nothing about the numerics is touched.
LEGACY_C = ("-std=gnu89 -fcommon -w "
            "-Wno-error=implicit-function-declaration -Wno-error=implicit-int "
            "-Wno-error=int-conversion -Wno-error=return-mismatch "
            "-Wno-error=incompatible-pointer-types "
            "-Wno-error=declaration-missing-parameter-type")

REPOS = {
    "FastHenry2": "https://github.com/ediloren/FastHenry2.git",
    "FastCap2": "https://github.com/ediloren/FastCap2.git",
}


def sh(cmd, cwd=None, check=True, log=None):
    p = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str),
                       capture_output=True, text=True)
    if log:
        log.write_text((p.stdout or "") + "\n--- stderr ---\n" + (p.stderr or ""))
    if check and p.returncode != 0:
        raise RuntimeError(f"{cmd} failed ({p.returncode})\n{p.stdout[-2000:]}\n{p.stderr[-2000:]}")
    return p


def clone(name):
    d = OPT / name
    if not d.exists():
        OPT.mkdir(parents=True, exist_ok=True)
        sh(["git", "clone", "--depth", "1", REPOS[name], str(d)])
    return d


def build_fasthenry(force=False):
    if paths.FASTHENRY_BIN.exists() and not force:
        return "present"
    d = clone("FastHenry2")
    sh(f"./config default", cwd=d, check=False)
    for mk, old in (("src/fasthenry/Makefile", "-O -DFOUR -m64"),
                    ("src/misc/Makefile", "-O -DFOUR -m64"),
                    ("src/zbuf/Makefile", "-O -DOTHER -m64"),
                    ("src/fasthenry/sparse/Makefile", "-O -m64")):
        p = d / mk
        if not p.exists():
            continue
        t = p.read_text()
        t = re.sub(r"^CFLAGS *=.*$",
                   lambda m: m.group(0).split("=")[0] + "= "
                   + m.group(0).split("=", 1)[1].strip().replace("-O ", "-O2 ")
                   + " " + LEGACY_C if "-std=gnu89" not in m.group(0) else m.group(0),
                   t, count=1, flags=re.M)
        p.write_text(t)
    sh("make clean", cwd=d, check=False)
    sh("make all", cwd=d, log=LOG / "fasthenry.log")
    if not paths.FASTHENRY_BIN.exists():
        raise RuntimeError("fasthenry did not build")
    return "built"


def build_fastcap(force=False):
    if paths.FASTCAP_BIN.exists() and not force:
        return "present"
    d = clone("FastCap2")
    mk = d / "src" / "Makefile"
    t = mk.read_text()
    t = re.sub(r"^CFLAGS *=.*$", f"CFLAGS = -O2 -DOTHER -m64 {LEGACY_C}", t,
               count=1, flags=re.M)
    mk.write_text(t)

    # FastCap's own sbrk-based allocator (`ualloc`) is declared implicitly and
    # so returns int: on 64-bit its pointers are truncated and the solver
    # segfaults.  The source ships the malloc alternative commented out; use it.
    g = d / "src" / "mulGlobal.h"
    t = g.read_text(errors="replace")
    t = t.replace("#define CALCORE(NUM, TYPE) ualloc((unsigned)(NUM)*sizeof(TYPE))",
                  "#define CALCORE(NUM, TYPE) calloc((size_t)(NUM),sizeof(TYPE))")
    t = t.replace("#define MALCORE ualloc", "#define MALCORE malloc")
    if "#include <stdlib.h>" not in t:
        t = t.replace("#define VERSION 2.0",
                      "#include <stdlib.h>\n#include <string.h>\n"
                      "#include <unistd.h>\n\n#define VERSION 2.0")
    g.write_text(t)

    # Functions called before they are defined pick up an implicit non-static
    # declaration, which then clashes with the later `static` definition.
    pat1 = re.compile(r"^static +([A-Za-z_]\w*) *\(", re.M)
    pat2 = re.compile(r"^static +((?:int|void|char|double|float|long|unsigned) "
                      r"+\**[A-Za-z_]\w* *\()", re.M)
    for f in (d / "src").glob("*.c"):
        s = f.read_text(errors="replace")
        s2 = pat2.sub(r"\1", pat1.sub(r"\1(", s))
        if s2 != s:
            f.write_text(s2)

    sh("make clean", cwd=d, check=False)
    sh("make all", cwd=d, log=LOG / "fastcap.log")
    if not paths.FASTCAP_BIN.exists():
        raise RuntimeError("fastcap did not build")
    return "built"


def build_openems(force=False):
    """openEMS inside a container: its dependency list is apt-only."""
    have = subprocess.run(["docker", "image", "inspect", paths.OPENEMS_IMAGE],
                          capture_output=True).returncode == 0
    if have and not force:
        return "present"
    ctx = paths.FIELD / "docker"
    p = sh(["docker", "build", "-t", paths.OPENEMS_IMAGE, str(ctx)],
           check=False, log=LOG / "openems.log")
    if p.returncode != 0:
        return "FAILED (see work/setup/openems.log)"
    return "built"


def pip_packages(force=False):
    want = ["magpylib", "shapely", "meshio", "scikit-fem", "pyamg", "h5py", "gmsh"]
    missing = []
    for mod, pkg in [("magpylib", "magpylib"), ("shapely", "shapely"),
                     ("meshio", "meshio"), ("skfem", "scikit-fem"),
                     ("pyamg", "pyamg"), ("h5py", "h5py"), ("gmsh", "gmsh")]:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        sh([sys.executable, "-m", "pip", "install", "--user",
            "--break-system-packages", *missing], log=LOG / "pip.log")
    return "ok" if not missing else f"installed {missing}"


def check():
    import importlib
    st = {}
    st["fasthenry"] = str(paths.FASTHENRY_BIN) if paths.FASTHENRY_BIN.exists() else None
    st["fastcap"] = str(paths.FASTCAP_BIN) if paths.FASTCAP_BIN.exists() else None
    try:
        from spice.ngspice import NgSpice
        ng = NgSpice()
        st["ngspice"] = ng.libpath
    except Exception as e:
        st["ngspice"] = f"FAILED: {e}"
    st["openems_image"] = (paths.OPENEMS_IMAGE
                           if subprocess.run(["docker", "image", "inspect",
                                              paths.OPENEMS_IMAGE],
                                             capture_output=True).returncode == 0
                           else None)
    for mod in ("numpy", "scipy", "matplotlib", "magpylib", "shapely",
                "meshio", "skfem", "pyamg", "h5py", "gmsh", "pcbnew"):
        try:
            m = importlib.import_module(mod)
            st[mod] = getattr(m, "__version__", "ok")
        except Exception as e:
            st[mod] = f"MISSING ({e.__class__.__name__})"
    return st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    paths.ensure_dirs()
    LOG.mkdir(parents=True, exist_ok=True)
    if not a.check:
        print("pip        :", pip_packages(a.force))
        print("FastHenry2 :", build_fasthenry(a.force))
        print("FastCap2   :", build_fastcap(a.force))
        print("openEMS    :", build_openems(a.force))
    st = check()
    print(json.dumps(st, indent=1))
    (paths.WORK / "toolchain.json").write_text(json.dumps(st, indent=1))
    return st


if __name__ == "__main__":
    main()
