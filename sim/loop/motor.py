#!/usr/bin/env python3
"""The motor as a parameter set, and an exact integrator for it.

SPEC.md sec.3 is explicit that the motor is deliberately undefined, so this is
a parameter set with two named instances and everything else swept.

The electrical model is a three-phase wye with a floating neutral:

    v_x = R i_x + L di_x/dt + e_x + v_n ,  sum i_x = 0

so v_n = mean(v_x) for a balanced back-EMF, and each phase obeys a
first-order equation whose input is piecewise constant between switching
events.  That is integrated exactly rather than stepped, which is what makes
a 50 ms run at 20 kHz with dead time cheap: six to twelve intervals per PWM
period instead of hundreds of time steps.
"""
import math

import numpy as np

BENCH = dict(
    name="bench (what the sibling actually ran)",
    pole_pairs=11, R=0.35, L=1.0e-3, ke=0.05,
    J=1e-3, B=1e-4,
    note=("R from main.cpp's voltage_sensor_align: 2.0 V saturated a +-4.1 A "
          "chain and 1.0 V did not, so R is 0.25-0.5 Ohm; L is unknown and "
          "swept 0.1-2 mH (SPEC.md sec.3.1)"))

DESIGN = dict(
    name="design point (what the servodrive is built for)",
    pole_pairs=11, R=0.040, L=60e-6, ke=0.0,
    J=1e-3, B=1e-4, rated_rpm=2000.0,
    note="SPEC.md sec.3.2 ranges: 7-21 pole pairs, R 20-60 mOhm, L 20-150 uH")


def with_ke_for(params, v_bus=60.0, rated_rpm=2000.0, headroom=0.8):
    """Back-EMF constant sized so that the rated speed leaves headroom.

    Sine PWM centred on V_supply/2 can make a phase amplitude of V_bus/2; the
    back-EMF is given `headroom` of that, and what is left pays for R.i and
    omega.L.i.  Sizing it to the full V_bus/2, as "21.2 V RMS at rated speed"
    would, leaves nothing to drive current with and the controller simply
    saturates -- which is a real limit, but not an operating point to measure
    ripple at.
    """
    p = dict(params)
    w_e = 2 * math.pi * rated_rpm / 60.0 * p["pole_pairs"]
    p["ke"] = headroom * (v_bus / 2) / w_e            # V.s/rad electrical
    p["rated_rpm"] = rated_rpm
    p["v_emf_peak_at_rated"] = headroom * v_bus / 2
    return p


class Motor:
    def __init__(self, p):
        self.p = dict(p)
        self.R = p["R"]
        self.L = p["L"]
        self.ke = p["ke"]
        self.pp = p["pole_pairs"]
        self.J = p.get("J", 1e-3)
        self.B = p.get("B", 1e-4)
        self.i = np.zeros(3)
        self.theta_e = 0.0
        self.omega_m = 0.0

    def emf(self, theta_e):
        """Back-EMF, in the sign convention that puts it on the +q axis of the
        Park transform used in loop/control.py:

            (e_alpha, e_beta) = E (-sin th, cos th)  ->  e_d = 0, e_q = +E

        which is d(flux linkage)/dt for a rotor flux at angle th.  Getting this
        sign wrong makes the controller regulate a regenerating operating point
        and look perfectly stable while doing it.
        """
        E = self.ke * self.omega_m * self.pp
        return -E * np.array([
            math.sin(theta_e), math.sin(theta_e - 2 * math.pi / 3),
            math.sin(theta_e + 2 * math.pi / 3)])

    def iq(self):
        ia, ib = self.i[0], self.i[1]
        al = ia
        be = (ia + 2 * ib) / math.sqrt(3.0)
        th = self.theta_e
        return -al * math.sin(th) + be * math.cos(th)

    def torque(self):
        """T = 3/2 . p . lambda_m . i_q, valid at standstill too."""
        return 1.5 * self.pp * self.ke * self.iq()

    def step(self, v, dt, load_torque=0.0, mechanical=True):
        """Advance by dt with the phase voltages v (to the inverter's GND) held
        constant.  Exact for the electrical part."""
        e = self.emf(self.theta_e)
        vn = float(np.mean(v))                      # floating neutral
        drive = np.asarray(v, float) - vn - e
        i_ss = drive / self.R
        a = math.exp(-self.R / self.L * dt)
        self.i = i_ss + (self.i - i_ss) * a
        self.i -= np.mean(self.i)                   # enforce sum i = 0 exactly
        if mechanical:
            T = self.torque()
            dw = (T - load_torque - self.B * self.omega_m) / self.J * dt
            self.omega_m += dw
        # the rotor turns whether or not the mechanical state is integrated:
        # `mechanical=False` means the speed is held, not that the shaft stops
        self.theta_e += self.omega_m * self.pp * dt
        self.theta_e %= 2 * math.pi
        return self.i
