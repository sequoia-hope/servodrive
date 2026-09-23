#!/usr/bin/env python3
"""A thin ctypes binding to libngspice.

There is no ngspice CLI on this machine and no sudo to install one, but KiCad
ships `libngspice.so.0`, which is the same simulator behind a shared-library
API (SPEC.md sec.5.1 anticipates exactly this).  This module is that API:

    ng = NgSpice()
    ng.circuit(netlist_text)
    ng.command("tran 1n 1u")
    t, v = ng.vector("time"), ng.vector("v(sw)")

Rules that come from the library, not from taste:
  * the library is a singleton -- it keeps global state, so one process gets
    one simulator, and circuits are loaded one at a time.
  * `ngSpice_Circ` takes the netlist as an array of lines *without* newlines,
    NULL-terminated, and the first line is the title, never a card.
  * vectors must be read before the next `run`; they are freed under us.
"""
import ctypes
import ctypes.util
import os
import threading
from pathlib import Path

import numpy as np

LIB_CANDIDATES = [
    "/usr/lib/x86_64-linux-gnu/libngspice.so.0",
    "/usr/lib/x86_64-linux-gnu/libngspice.so",
    "libngspice.so.0", "libngspice.so",
]

VF_REAL = 1 << 0
VF_COMPLEX = 1 << 1


class ngcomplex(ctypes.Structure):
    _fields_ = [("cx_real", ctypes.c_double), ("cx_imag", ctypes.c_double)]


class vector_info(ctypes.Structure):
    _fields_ = [
        ("v_name", ctypes.c_char_p),
        ("v_type", ctypes.c_int),
        ("v_flags", ctypes.c_short),
        ("v_realdata", ctypes.POINTER(ctypes.c_double)),
        ("v_compdata", ctypes.POINTER(ngcomplex)),
        ("v_length", ctypes.c_int),
    ]


pvector_info = ctypes.POINTER(vector_info)

SendChar = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                            ctypes.c_void_p)
SendStat = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                            ctypes.c_void_p)
ControlledExit = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.c_bool,
                                  ctypes.c_bool, ctypes.c_int, ctypes.c_void_p)
SendData = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
                            ctypes.c_int, ctypes.c_void_p)
SendInitData = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
                                ctypes.c_void_p)
BGThreadRunning = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_bool, ctypes.c_int,
                                   ctypes.c_void_p)


class NgSpiceError(RuntimeError):
    pass


class NgSpice:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *a, **kw):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._started = False
            return cls._instance

    def __init__(self, verbose=False, libpath=None):
        if self._started:
            self.verbose = verbose
            return
        self.verbose = verbose
        self.log = []
        self.error = None
        self._load(libpath)
        self._init()
        self._started = True

    def _load(self, libpath):
        last = None
        for cand in ([libpath] if libpath else []) + LIB_CANDIDATES:
            try:
                self.lib = ctypes.CDLL(cand)
                self.libpath = cand
                break
            except OSError as e:
                last = e
        else:
            raise NgSpiceError(f"cannot load libngspice: {last}")
        L = self.lib
        L.ngSpice_Init.restype = ctypes.c_int
        L.ngSpice_Circ.argtypes = [ctypes.POINTER(ctypes.c_char_p)]
        L.ngSpice_Circ.restype = ctypes.c_int
        L.ngSpice_Command.argtypes = [ctypes.c_char_p]
        L.ngSpice_Command.restype = ctypes.c_int
        L.ngGet_Vec_Info.argtypes = [ctypes.c_char_p]
        L.ngGet_Vec_Info.restype = pvector_info
        L.ngSpice_CurPlot.restype = ctypes.c_char_p
        L.ngSpice_AllVecs.argtypes = [ctypes.c_char_p]
        L.ngSpice_AllVecs.restype = ctypes.POINTER(ctypes.c_char_p)
        L.ngSpice_running.restype = ctypes.c_bool

    def _init(self):
        def _send_char(msg, _id, _u):
            s = msg.decode(errors="replace")
            self.log.append(s)
            if s.startswith("stderr Error") or "Error:" in s:
                self.error = s
            if self.verbose:
                print(s)
            return 0

        def _send_stat(msg, _id, _u):
            return 0

        def _exit(status, immediate, quit_exit, _id, _u):
            self.error = f"ngspice asked to exit, status {status}"
            return 0

        # keep references: ctypes callbacks must outlive the library
        self._cb = (SendChar(_send_char), SendStat(_send_stat),
                    ControlledExit(_exit), SendData(), SendInitData(),
                    BGThreadRunning())
        rc = self.lib.ngSpice_Init(self._cb[0], self._cb[1], self._cb[2],
                                   None, None, None, None)
        if rc != 0:
            raise NgSpiceError(f"ngSpice_Init returned {rc}")

    # -------------------------------------------------------------- circuits
    def circuit(self, netlist):
        """Load a netlist (a string).  Replaces whatever was loaded."""
        self.error = None
        self.log = []
        lines = [ln for ln in netlist.splitlines()]
        arr = (ctypes.c_char_p * (len(lines) + 1))()
        for i, ln in enumerate(lines):
            arr[i] = ln.encode()
        arr[len(lines)] = None
        rc = self.lib.ngSpice_Circ(arr)
        if rc != 0 or self.error:
            raise NgSpiceError(f"ngSpice_Circ failed ({rc}): {self.error}\n"
                               + "\n".join(self.log[-30:]))
        return rc

    def command(self, cmd):
        self.error = None
        rc = self.lib.ngSpice_Command(cmd.encode())
        if self.error:
            raise NgSpiceError(f"'{cmd}': {self.error}\n"
                               + "\n".join(self.log[-30:]))
        return rc

    def destroy(self):
        try:
            self.command("destroy all")
        except NgSpiceError:
            pass

    # --------------------------------------------------------------- vectors
    def plot(self):
        p = self.lib.ngSpice_CurPlot()
        return p.decode() if p else None

    def vectors(self, plot=None):
        plot = plot or self.plot()
        arr = self.lib.ngSpice_AllVecs(plot.encode())
        out, i = [], 0
        while arr[i]:
            out.append(arr[i].decode())
            i += 1
        return out

    def vector(self, name):
        vi = self.lib.ngGet_Vec_Info(name.encode())
        if not vi:
            raise NgSpiceError(f"no vector {name!r}; have {self.vectors()}")
        v = vi.contents
        n = v.v_length
        if v.v_flags & VF_COMPLEX:
            buf = np.ctypeslib.as_array(
                ctypes.cast(v.v_compdata, ctypes.POINTER(ctypes.c_double)),
                shape=(2 * n,)).copy()
            return buf[0::2] + 1j * buf[1::2]
        return np.ctypeslib.as_array(v.v_realdata, shape=(n,)).copy()

    def all(self, plot=None, skip_internal=True):
        """Every readable vector.  Some internal ones (transmission-line
        states, for instance) are listed but cannot be fetched; skip them
        rather than failing the whole read."""
        out = {}
        for n in self.vectors(plot):
            if skip_internal and ("#int" in n or "#i1" in n or "#i2" in n):
                continue
            try:
                out[n] = self.vector(n)
            except NgSpiceError:
                continue
        return out

    # ------------------------------------------------------------- shortcuts
    def tran(self, netlist, step, stop, start=0.0, uic=False, maxstep=None):
        self.circuit(netlist)
        cmd = f"tran {step:g} {stop:g} {start:g}"
        if maxstep:
            cmd += f" {maxstep:g}"
        if uic:
            cmd += " uic"
        self.command(cmd)
        return self.all()

    def ac(self, netlist, fstart, fstop, n_per_dec=20):
        self.circuit(netlist)
        self.command(f"ac dec {n_per_dec} {fstart:g} {fstop:g}")
        return self.all()

    def dc(self, netlist, src, start, stop, step, src2=None, s2=None):
        self.circuit(netlist)
        cmd = f"dc {src} {start:g} {stop:g} {step:g}"
        if src2:
            cmd += f" {src2} {s2[0]:g} {s2[1]:g} {s2[2]:g}"
        self.command(cmd)
        return self.all()

    def op(self, netlist):
        self.circuit(netlist)
        self.command("op")
        return self.all()


def version():
    ng = NgSpice()
    ng.command("version")
    return "\n".join(ng.log[-12:])
