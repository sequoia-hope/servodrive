"""results/*.json is the interface between the solvers and the report.

The report reads only from here, so a re-run can never show yesterday's number
beside today's figure (SPEC.md sec.8.1).
"""
import json
import datetime
import subprocess
import numpy as np
from . import paths


class _Enc(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, (np.bool_,)):
            return bool(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, complex):
            return {"re": o.real, "im": o.imag}
        if isinstance(o, (datetime.date, datetime.datetime)):
            return o.isoformat()
        return super().default(o)


def _provenance():
    return {
        "written": datetime.datetime.now().isoformat(timespec="seconds"),
        "board_mtime": datetime.datetime.fromtimestamp(
            paths.BOARD_SRC.stat().st_mtime).isoformat(timespec="seconds")
        if paths.BOARD_SRC.exists() else None,
    }


def write(phase, data):
    """Write results/<phase>.json.  `data` is flat and named, keyed by question."""
    paths.RESULTS.mkdir(parents=True, exist_ok=True)
    out = dict(data)
    out["_meta"] = _provenance()
    p = paths.RESULTS / f"{phase}.json"
    p.write_text(json.dumps(out, indent=1, cls=_Enc, sort_keys=False))
    return p


def read(phase):
    p = paths.RESULTS / f"{phase}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def read_all():
    out = {}
    for p in sorted(paths.RESULTS.glob("P*.json")):
        out[p.stem] = json.loads(p.read_text())
    return out


def model(name):
    """Load sim/models/<name>.json -- a part with its datasheet citations."""
    p = paths.MODELS / f"{name}.json"
    if not p.exists():
        raise FileNotFoundError(f"no component model {name}: {p}")
    return json.loads(p.read_text())
