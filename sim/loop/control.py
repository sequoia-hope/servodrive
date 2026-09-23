#!/usr/bin/env python3
"""SimpleFOC's control law, reimplemented exactly.

Taken line for line from the library the sibling firmware builds against
(`Simple FOC/src/common/pid.cpp` and `lowpass_filter.cpp`, v2.3.5): a Tustin
integral, anti-windup by constraining both the integral and the output, an
output ramp, and a first-order filter with alpha = Tf/(Tf + dt).  Reproducing
the discretisation rather than an idealised PI is the point: the loop's
behaviour at 20 kHz with a 5 ms filter is set by these details.
"""
import math

import numpy as np


def _constrain(x, lo, hi):
    return lo if x < lo else (hi if x > hi else x)


class PID:
    def __init__(self, P, I, D=0.0, ramp=np.inf, limit=np.inf, Ts=1e-3):
        self.P, self.I, self.D = P, I, D
        self.output_ramp = ramp
        self.limit = limit
        self.Ts = Ts
        self.error_prev = 0.0
        self.output_prev = 0.0
        self.integral_prev = 0.0

    def __call__(self, error, dt=None):
        dt = self.Ts if dt is None else dt
        proportional = self.P * error
        integral = self.integral_prev + self.I * dt * 0.5 * (error + self.error_prev)
        if np.isfinite(self.limit):
            integral = _constrain(integral, -self.limit, self.limit)
        derivative = self.D * (error - self.error_prev) / dt if self.D else 0.0
        output = proportional + integral + derivative
        if np.isfinite(self.limit):
            output = _constrain(output, -self.limit, self.limit)
        if np.isfinite(self.output_ramp) and self.output_ramp > 0:
            rate = (output - self.output_prev) / dt
            if rate > self.output_ramp:
                output = self.output_prev + self.output_ramp * dt
            elif rate < -self.output_ramp:
                output = self.output_prev - self.output_ramp * dt
        self.integral_prev = integral
        self.output_prev = output
        self.error_prev = error
        return output

    def reset(self):
        self.integral_prev = self.output_prev = self.error_prev = 0.0


class LPF:
    def __init__(self, Tf, Ts=1e-3):
        self.Tf, self.Ts = Tf, Ts
        self.y_prev = 0.0

    def __call__(self, x, dt=None):
        dt = self.Ts if dt is None else dt
        if self.Tf <= 0:
            self.y_prev = x
            return x
        alpha = self.Tf / (self.Tf + dt)
        y = alpha * self.y_prev + (1.0 - alpha) * x
        self.y_prev = y
        return y

    def reset(self):
        self.y_prev = 0.0


def clarke(ia, ib, ic):
    al = ia
    be = (ia + 2 * ib) / np.sqrt(3.0)
    return al, be


def park(al, be, theta):
    c, s = np.cos(theta), np.sin(theta)
    return al * c + be * s, -al * s + be * c


def inv_park(d, q, theta):
    c, s = np.cos(theta), np.sin(theta)
    return d * c - q * s, d * s + q * c


def sine_pwm(ud, uq, theta, v_supply, centered=True):
    """SimpleFOC's SinePWM, `modulation_centered = 1`: the inverse Park, then
    the phase voltages centred on V_supply/2.  Not SVPWM -- `foc_modulation`
    is never set in the sibling firmware, so this is what runs."""
    ua, ub = inv_park(ud, uq, theta)
    u_a = ua
    u_b = -0.5 * ua + np.sqrt(3) / 2 * ub
    u_c = -0.5 * ua - np.sqrt(3) / 2 * ub
    if centered:
        centre = v_supply / 2
        return (u_a + centre, u_b + centre, u_c + centre)
    mn = min(u_a, u_b, u_c)
    return (u_a - mn, u_b - mn, u_c - mn)


def tuned_gains(motor, bandwidth_hz=1000.0, Tf=None, f_loop=20e3,
                voltage_limit=30.0, ramp=float("inf")):
    """The gains the sibling's own tuner would arrive at for a given plant.

    `tune.py` measures R from the DC gain and L from the step time constant,
    then sets Kp = omega_c * L and Ki = omega_c * R -- the textbook internal
    model PI for an RL plant, which places the closed-loop pole at omega_c.
    Reproducing that rule rather than hand-picking gains is what lets a
    result be read as "what this firmware would do".
    """
    w = 2 * math.pi * bandwidth_hz
    Tf = (1.0 / (2 * math.pi * bandwidth_hz * 3)) if Tf is None else Tf
    return dict(Pq=w * motor["L"], Iq=w * motor["R"],
                Pd=w * motor["L"], Id=w * motor["R"],
                Tf=Tf, ramp=ramp, voltage_limit=voltage_limit,
                bandwidth_hz=bandwidth_hz)
