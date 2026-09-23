#!/usr/bin/env python3
"""L4: the inverter, the motor, SimpleFOC's law and the ADC, in one loop.

The inverter is not averaged.  Within one 20 kHz period each half-bridge is in
one of three states -- high side on, low side on, or both off with a body
diode conducting -- and the phase voltage is piecewise constant between the
transitions.  The period is therefore cut at every transition and the motor is
integrated exactly across each piece, which is both faster and more honest
than stepping a fixed dt: the dead time, the EG2103's 780/220 ns asymmetry and
the minimum-pulse limit all fall out of the timeline instead of being modelled
as a correction.

The ADC is modelled as the RP2350's: free-running at 500 kS/s round-robin over
the enabled channels, so a channel is refreshed every N x 2 us and whatever it
last converted is what `loopFOC()` reads.  The disturbance the switching puts
on ISENSE comes from P2: during the INA241's 1 us hold the output is the
pre-edge value, which is stale rather than wrong.
"""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loop import control                                     # noqa: E402
from loop.motor import Motor                                 # noqa: E402

HI, LO, DEAD = 0, 1, 2


class Inverter:
    """One three-phase bridge with the EG2103's real timing."""

    def __init__(self, v_bus=60.0, f_sw=20e3, t_on=780e-9, t_off=220e-9,
                 dead_zone=0.02, v_f=0.9, r_on=3.0e-3, min_pulse=560e-9):
        self.v_bus = v_bus
        self.T = 1.0 / f_sw
        self.t_on = t_on
        self.t_off = t_off
        self.dti = dead_zone * self.T          # SimpleFOC's swDti()
        self.v_f = v_f
        self.r_on = r_on
        self.min_pulse = min_pulse
        self.t_dead_hw = t_on - t_off

    def windows(self, duty):
        """(hi_on, hi_off) for each phase within [0, T), centre-aligned.

        Returns None for a phase whose commanded pulse is shorter than the
        driver's minimum -- the duty extremes SPEC.md sec.2.2 says are
        unreachable.
        """
        out = []
        for d in duty:
            d = min(max(d, 0.0), 1.0)
            t1 = self.T / 2 - d * self.T / 2 + self.dti
            t2 = self.T / 2 + d * self.T / 2 - self.dti
            # the driver's own delays: on late by t_on, off late by t_off
            a = t1 + self.t_on
            b = t2 + self.t_off
            if b - a < self.min_pulse:
                out.append(None)                 # never turns on
            elif b - a >= self.T - self.min_pulse:
                out.append((0.0, self.T))        # never turns off
            else:
                out.append((a, b))
        return out

    def timeline(self, duty):
        """Sorted breakpoints and, for each interval, each phase's state."""
        w = self.windows(duty)
        bps = {0.0, self.T}
        for x in w:
            if x:
                bps.add(max(0.0, min(self.T, x[0])))
                bps.add(max(0.0, min(self.T, x[1])))
                # the low side turns on t_dead_hw after the high side goes off
                bps.add(max(0.0, min(self.T, x[1] + self.t_dead_hw)))
                bps.add(max(0.0, min(self.T, x[0] - self.t_dead_hw)))
        bps = sorted(bps)
        out = []
        for a, b in zip(bps[:-1], bps[1:]):
            if b - a < 1e-12:
                continue
            mid = (a + b) / 2
            st = []
            for x in w:
                if x is None:
                    st.append(LO)
                elif x[0] <= mid < x[1]:
                    st.append(HI)
                elif (x[1] <= mid < x[1] + self.t_dead_hw) or \
                     (x[0] - self.t_dead_hw <= mid < x[0]):
                    st.append(DEAD)
                else:
                    st.append(LO)
            out.append((a, b, st))
        return out

    def voltages(self, state, current):
        """Phase voltages referred to the inverter's GND."""
        v = np.zeros(3)
        for k, s in enumerate(state):
            i = current[k]
            if s == HI:
                v[k] = self.v_bus - i * self.r_on
            elif s == LO:
                v[k] = -i * self.r_on
            else:                      # both off: a body diode takes it
                v[k] = (-self.v_f) if i > 0 else (self.v_bus + self.v_f)
        return v


class ADC:
    """The RP2350 ADC as the sibling's patch configures it."""

    def __init__(self, n_channels=4, t_conv=2e-6, bits=12, vref=3.3,
                 synchronised=False, f_sw=20e3, t_aperture=4 / 48e6,
                 inl_lsb=0.0, noise_lsb=0.0, seed=0):
        self.n = n_channels
        self.t_conv = t_conv
        self.bits = bits
        self.vref = vref
        self.lsb = vref / 2 ** bits
        self.sync = synchronised
        self.T = 1.0 / f_sw
        self.t_ap = t_aperture
        self.inl = inl_lsb
        self.noise = noise_lsb
        self.rng = np.random.default_rng(seed)

    def sample_times(self, t0, t1, channel=0, phase=0.0):
        """When channel `channel` is actually converted between t0 and t1."""
        if self.sync:
            # one conversion per phase at the PWM counter's zero, when all
            # three low sides are on and the current equals its average
            k0 = math.ceil((t0 - phase) / self.T)
            k1 = math.floor((t1 - phase) / self.T)
            return np.array([phase + k * self.T for k in range(k0, k1 + 1)])
        step = self.n * self.t_conv
        k0 = math.ceil((t0 - phase - channel * self.t_conv) / step)
        k1 = math.floor((t1 - phase - channel * self.t_conv) / step)
        return np.array([phase + channel * self.t_conv + k * step
                         for k in range(k0, k1 + 1)])

    def quantise(self, v):
        q = np.round(np.asarray(v) / self.lsb)
        if self.inl:
            q = q + self.inl * np.sin(2 * math.pi * q / 1024.0)
        if self.noise:
            q = q + self.rng.normal(0, self.noise, np.shape(q))
        return np.round(q) * self.lsb


class Run:
    """One closed-loop run."""

    def __init__(self, motor_params, inverter, adc, gains, v_bus=60.0,
                 i_q_target=0.0, load_torque=0.0, omega0=0.0,
                 sense_gain=40e-3, sense_ref=1.65, ina_hold=1e-6,
                 f_loop=20e3, mechanical=True, i_limit=None,
                 adc_phase=0.0):
        self.m = Motor(motor_params)
        self.m.omega_m = omega0
        self.inv = inverter
        self.adc = adc
        self.v_bus = v_bus
        self.iq_t = i_q_target
        self.load = load_torque
        self.k_sense = sense_gain
        self.v_ref = sense_ref
        self.hold = ina_hold
        self.T_loop = 1.0 / f_loop
        self.mechanical = mechanical
        self.adc_phase = adc_phase
        Ts = self.T_loop
        lim = gains.get("voltage_limit", v_bus / 2)
        self.pid_q = control.PID(gains["Pq"], gains["Iq"], 0.0,
                                 gains.get("ramp", np.inf), lim, Ts)
        self.pid_d = control.PID(gains["Pd"], gains["Id"], 0.0,
                                 gains.get("ramp", np.inf), lim, Ts)
        self.lpf_q = control.LPF(gains["Tf"], Ts)
        self.lpf_d = control.LPF(gains["Tf"], Ts)
        self.i_limit = i_limit

    def run(self, t_end, record=True):
        inv = self.inv
        T = inv.T
        t = 0.0
        duty = [0.5, 0.5, 0.5]
        rec = {"t": [], "i": [], "v": [], "theta": [], "omega": [],
               "torque": [], "iq": [], "id": [], "ud": [], "uq": [],
               "iq_meas": [], "id_meas": [], "sample_t": [], "sample_i": []}
        edges = []            # switch-node edge times, for the hold model
        next_loop = 0.0
        n_per = int(round(t_end / T))
        for p in range(n_per):
            tl = inv.timeline(duty)
            for (a, b, st) in tl:
                v = inv.voltages(st, self.m.i)
                dt = b - a
                if record:
                    rec["t"].append(t + a)
                    rec["i"].append(self.m.i.copy())
                    rec["v"].append(v.copy())
                    rec["theta"].append(self.m.theta_e)
                    rec["omega"].append(self.m.omega_m)
                    rec["torque"].append(self.m.torque())
                self.m.step(v, dt, self.load, self.mechanical)
                if record:
                    # the end of the interval as well, so that interpolating
                    # to a sample instant never has to extrapolate past the
                    # last recorded point -- which is exactly where a
                    # PWM-synchronised sample lands
                    rec["t"].append(t + b)
                    rec["i"].append(self.m.i.copy())
                    rec["v"].append(v.copy())
                    rec["theta"].append(self.m.theta_e)
                    rec["omega"].append(self.m.omega_m)
                    rec["torque"].append(self.m.torque())
                if a > 0:
                    edges.append(t + a)
            t += T
            # --- the control loop, once per PWM period -------------------
            i_meas = self._sense(t, edges, rec)
            al, be = control.clarke(*i_meas)
            d_, q_ = control.park(al, be, self.m.theta_e)
            d_f = self.lpf_d(d_)
            q_f = self.lpf_q(q_)
            ud = self.pid_d(0.0 - d_f)
            uq = self.pid_q(self.iq_t - q_f)
            ua, ub, uc = control.sine_pwm(ud, uq, self.m.theta_e, self.v_bus)
            duty = [min(max(x / self.v_bus, 0.0), 1.0) for x in (ua, ub, uc)]
            if record:
                rec["iq"].append(q_); rec["id"].append(d_)
                rec["iq_meas"].append(q_f); rec["id_meas"].append(d_f)
                rec["ud"].append(ud); rec["uq"].append(uq)
        for k in ("t", "theta", "omega", "torque", "iq", "id", "ud", "uq",
                  "iq_meas", "id_meas", "sample_t"):
            rec[k] = np.array(rec[k])
        rec["i"] = np.array(rec["i"])
        rec["v"] = np.array(rec["v"])
        rec["sample_i"] = np.array(rec["sample_i"])
        rec["edges"] = np.array(edges)
        return rec

    def _sense(self, t_now, edges, rec):
        """What the ADC last converted for each of the three phases.

        Phase C has no amplifier on this board -- cell C carries 0 Ohm links
        -- so i_c is inferred as -(i_a + i_b), exactly as the firmware does.
        """
        out = []
        for ch in (0, 1):
            ts = self.adc.sample_times(t_now - self.T_loop, t_now, channel=ch,
                                       phase=self.adc_phase)
            if len(ts) == 0:
                out.append(self._last(ch))
                continue
            ts_last = ts[-1]
            i_true = self._interp_current(rec, ts_last, ch)
            # the INA241 holds its output for 1 us after a switch-node edge:
            # inside that window the sample is the pre-edge value, not garbage
            if len(edges):
                e = np.asarray(edges)
                recent = e[(e <= ts_last) & (e > ts_last - self.hold)]
                if len(recent):
                    i_true = self._interp_current(rec, recent[0] - 1e-9, ch)
            v = self.v_ref + self.k_sense * i_true
            v = min(max(v, 0.0), self.adc.vref)
            v = float(self.adc.quantise(v))
            out.append((v - self.v_ref) / self.k_sense)
            self._store(ch, out[-1])
        ia, ib = out
        return np.array([ia, ib, -(ia + ib)])

    def _interp_current(self, rec, t, ch):
        if not len(rec["t"]):
            return float(self.m.i[ch])
        tt = np.asarray(rec["t"])
        ii = np.asarray(rec["i"])[:, ch]
        return float(np.interp(t, tt, ii))

    def _last(self, ch):
        return getattr(self, f"_last{ch}", 0.0)

    def _store(self, ch, v):
        setattr(self, f"_last{ch}", v)
