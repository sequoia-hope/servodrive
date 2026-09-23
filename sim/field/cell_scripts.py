"""openEMS scripts for P7: the cell-A commutation loop, and the board's near
and far field."""

CELL_LOOP = r'''
# Cell A's commutation loop, full wave, as an independent check on the
# FastHenry answer to Q1.  The copper is the real extracted polygons; the
# excitation is a lumped port standing where the high-side 100 n capacitor
# sits, and both FETs are replaced by metal bridges between their pads --
# the same "PCB only" convention the PEEC model uses, so the two numbers are
# comparable.
import json
mm = 1e-3
G = json.load(open(os.path.join(OUT, "geom.json")))
W = G["window"]
stack = G["stack"]
PORT = G["port"]        # [[x,y],[x,y]] : VBUS pad, GND pad
BRIDGE = G["bridges"]   # [[x0,y0,z0,x1,y1,z1], ...] the shorted FETs

# Excitation and extraction band.
#
# The first version of this model drove a Gaussian centred at 750 MHz and then
# read L out of 30-200 MHz, where that pulse has almost no energy: the port
# reactance there is 0.3-2.5 ohm against a numerical noise floor of about
# 0.2 ohm, and two meshes that differed in nothing important came back a
# factor of two apart because each was reading its own noise.  sim/field/
# kat_scripts.py says the same thing about the strip-over-plane case in its
# own comment -- "the reactance is large enough to measure, which an FDTD at
# 10 MHz would not be" -- and fits that one over 0.2-1 GHz.
#
# So: a baseband pulse, all its energy from DC up to `fc`, and a fit band high
# enough that the reactance is worth measuring and low enough to stay below
# this loop's own parallel resonance near 400 MHz.
fc = float(G.get("fc", 5e8))
band_lo = float(G.get("band_lo", 5e7))
band_hi = float(G.get("band_hi", 2.5e8))
FDTD = openEMS(NrTS=int(G.get("nrts", 60000)), EndCriteria=1e-4)
FDTD.SetGaussExcite(0.0, fc)
FDTD.SetBoundaryCond(["MUR"] * 6)
CSX = ContinuousStructure()
FDTD.SetCSX(CSX)
mesh = CSX.GetGrid()
mesh.SetDeltaUnit(1e-3)

sub = CSX.AddMaterial("FR4", epsilon=4.5,
                      kappa=0.02 * 2 * np.pi * 1e8 * 4.5 * EPS0)
sub.AddBox([W[0], W[1], -0.05], [W[2], W[3], 1.65], priority=1)

metals = {}
for p in G["polys"]:
    key = p["net"]
    if key not in metals:
        metals[key] = CSX.AddMetal("m_" + key.replace(".", "_"))
    pts = np.array(p["pts"]).T
    metals[key].AddLinPoly(pts, "z", p["z"] - p["t"] / 2, p["t"], priority=10)
for b in G["barrels"]:
    key = b["net"]
    if key not in metals:
        metals[key] = CSX.AddMetal("m_" + key.replace(".", "_"))
    r = b["dia"] / 2
    metals[key].AddCylinder([b["x"], b["y"], b["z0"]],
                            [b["x"], b["y"], b["z1"]], r, priority=11)
# The two FETs, shorted at their own pads: the same "PCB only" convention the
# PEEC model uses, where the device is a short between its own pads.
#
# It is a strip lying IN the F.Cu copper, not a rod through it.  The first
# version of this model used AddCylinder with a 0.25 mm radius about an axis
# in the F.Cu plane; F.Cu to In1 is 0.1525 mm, so the rod stood 0.25 mm proud
# of its own layer and cut through about 1 mm2 of the In1 ground plane -- and
# metal is metal in FDTD, so the high-side bridge shorted VBUS to GND at the
# drain and the commutation loop measured half its real size.  Nothing in the
# run reported it: the energy decayed, the port impedance was clean, and the
# answer was simply the wrong loop.
br = CSX.AddMetal("bridge")
for s in BRIDGE:
    pts = np.array(s["pts"]).T
    br.AddLinPoly(pts, "z", s["z"] - s["t"] / 2, s["t"], priority=12)

# The port stands where the high-side 100 n capacitor does, between its VBUS
# pad and its GND pad, driven along whichever axis separates them.  Its box is
# sized from the two pads' own copper by phases/p7_fullwave._port_box, so it
# bites the same distance into each; a fixed box about the midpoint of the pad
# CENTRES sat off-centre here and bit twenty times as far into one pad as the
# other.  A lumped port is a material at higher priority, so the two faces
# perpendicular to the drive direction have to land on metal and the rest of
# the box has to sit in the gap.
from openEMS.ports import LumpedPort
px, py = PORT[0]
qx, qy = PORT[1]
z_top = stack["F.Cu"]["z"]
PB = G.get("port_box")
if PB:
    pdir = PB["dir"]
    p0 = [PB["p0"][0], PB["p0"][1], z_top - 0.035]
    p1 = [PB["p1"][0], PB["p1"][1], z_top + 0.035]
else:
    half = 0.3
    gap = float(G.get("port_gap", 0.35))
    if abs(qx - px) >= abs(qy - py):
        pdir = "x"
        mid = (px + qx) / 2
        p0 = [mid - gap, (py + qy) / 2 - half, z_top - 0.035]
        p1 = [mid + gap, (py + qy) / 2 + half, z_top + 0.035]
    else:
        pdir = "y"
        mid = (py + qy) / 2
        p0 = [(px + qx) / 2 - half, mid - gap, z_top - 0.035]
        p1 = [(px + qx) / 2 + half, mid + gap, z_top + 0.035]
port = FDTD.AddLumpedPort(1, 50, p0, p1, pdir, excite=1, priority=20)

res = float(G.get("res", 0.35))
min_step = float(G.get("min_step", 0.10))


def _merge(vals, step, keep=()):
    """Lines no closer together than `step`, with `keep` never dropped.

    Every line has to go through this, not just the ones snapped to the
    copper: a port edge that lands a nanometre from a polygon vertex makes a
    nanometre-wide cell, and the Courant condition then puts the timestep at
    2e-17 s and the run never finishes.  That is a real failure this model
    hit, so the merge is here rather than at the caller.

    The port's own edges are protected, because they are the one place where
    the mesh decides what the model measures rather than how well: at a
    0.2 mm merge none of the four port coordinates survived, the box came out
    19 % longer than it was built, and the answer moved by a factor of two.
    """
    floor = max(1e-4, step / 4.0)
    out = []
    for x in sorted(float(v) for v in keep):
        if not out or x - out[-1] >= floor:
            out.append(x)          # protected, but never on top of each other
    for x in sorted(float(v) for v in vals):
        if all(abs(x - k) >= step for k in out):
            out.append(x)
            out.sort()
    return out


# lines snapped to the copper edges, so a clearance narrower than `res` is
# still a clearance once discretised -- see field/geom_export.fixed_lines
keep_x = list(G.get("keep_x", [])) + [W[0], W[2], p0[0], p1[0]]
keep_y = list(G.get("keep_y", [])) + [W[1], W[3], p0[1], p1[1]]
mesh.AddLine("x", _merge(list(G.get("fx", [])) + [px, qx], min_step, keep_x))
mesh.AddLine("y", _merge(list(G.get("fy", [])) + [py, qy], min_step, keep_y))
mesh.SmoothMeshLines("x", res, 1.4)
mesh.SmoothMeshLines("y", res, 1.4)
zs = [stack[k]["z"] for k in ("F.Cu", "In1.Cu", "In2.Cu", "In3.Cu",
                              "In4.Cu", "B.Cu")]
mesh.AddLine("z", zs + [-0.05, 1.65])
mesh.SmoothMeshLines("z", 0.05, 1.3)
# air all round: copper touching an absorbing boundary makes the FDTD
# diverge, and the first attempt at this model did exactly that
mesh.AddLine("z", [-12.0, 14.0])
mesh.SmoothMeshLines("z", 1.5, 1.5)
mesh.AddLine("x", [W[0] - 14.0, W[2] + 14.0])
mesh.AddLine("y", [W[1] - 14.0, W[3] + 14.0])
mesh.SmoothMeshLines("x", 2.0, 1.5)
mesh.SmoothMeshLines("y", 2.0, 1.5)

FDTD.Run(OUT, verbose=1, cleanup=False)
f = np.logspace(np.log10(5e6), np.log10(1.2e9), 400)
port.CalcPort(OUT, f)
Z = port.uf_tot / port.if_tot
L = np.imag(Z) / (2 * np.pi * f)
band = (f > band_lo) & (f < band_hi)
save(f=f, Z_re=np.real(Z), Z_im=np.imag(Z), L=L)

# The loop inductance, separated from the loop's own capacitance.
#
# A PEEC model has no displacement current, so its L is the quasi-static one
# and flat with frequency.  This model has the dielectric and the planes, so
# the port sees L in parallel with the structure's own C and a resonance near
# 330 MHz; the mean of Im(Z)/w over a band below it is still inflated by
# 1/(1 - (f/f0)^2), which is where most of the band spread comes from.  For a
# lumped LC, w/Im(Z) = 1/L - w^2 C is a straight line, so one least-squares
# fit gives both and makes the two solvers comparable.
w = 2 * np.pi * f
w_ref = 2 * np.pi * 1e8          # the fit is scaled: omega^2 is 1e17 and the
                                 # intercept is 1e8, and an unscaled
                                 # least squares on those two returns nonsense
fit = (f > float(G.get("fit_lo", 2e7))) & (f < float(G.get("fit_hi", 2.5e8)))
fit &= np.abs(np.imag(Z)) > 1e-3
lc = {}
if fit.sum() > 8:
    x = (w[fit] / w_ref) ** 2
    A = np.vstack([np.ones(int(fit.sum())), -x]).T
    y = w[fit] / np.imag(Z[fit])
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    if coef[0] > 0 and coef[1] > 0:
        L_fit = 1.0 / coef[0]
        C_fit = coef[1] / w_ref ** 2
        lc = {"L_nH": float(L_fit * 1e9), "C_pF": float(C_fit * 1e12),
              "f_resonance_MHz": float(
                  1.0 / (2 * np.pi * np.sqrt(L_fit * C_fit)) / 1e6),
              "fit_band_Hz": [float(f[fit][0]), float(f[fit][-1])],
              "rel_residual": float(
                  np.sqrt(np.mean((A @ coef - y) ** 2)) / np.mean(np.abs(y)))}
    else:
        lc = {"error": "the fit did not return a positive L and C"}


def _L_from(frac):
    """L over the band from the first `frac` of the time record.

    The residual energy at the end of a run is a poor gate on this structure:
    a little energy circles in the air box long after the port has settled, so
    the run stops at -17 dB with an answer whose spread across the band is
    under two per cent.  What actually matters is whether the record is long
    enough for the transform, and the way to find out is to transform less of
    it and see if the answer moves.
    """
    ut = np.loadtxt(os.path.join(OUT, "port_ut_1"), comments="%")
    it = np.loadtxt(os.path.join(OUT, "port_it_1"), comments="%")
    n = max(16, int(len(ut) * frac))
    t, u, i = ut[:n, 0], ut[:n, 1], it[:n, 1]
    dt = t[1] - t[0]
    w = 2 * np.pi * f[:, None]
    e = np.exp(-1j * w * t[None, :]) * dt
    U, I = e @ u, e @ i
    Zf = U / I
    return float(np.mean(np.imag(Zf[band]) / (2 * np.pi * f[band])) * 1e9)


trunc = {}
try:
    for frac in (0.5, 0.75, 1.0):
        trunc[f"{frac:.2f}"] = _L_from(frac)
except Exception as _e:
    trunc = {"error": str(_e)}
for _f in ("et", "ht", "port_ut_1", "port_it_1"):
    try:
        os.remove(os.path.join(OUT, _f))
    except OSError:
        pass
# the smallest cell in the mesh sets the timestep; record it, because a
# stray near-duplicate line is invisible in the answer and fatal to the run
_dx = [float(np.min(np.diff(mesh.GetLines(a)))) for a in ("x", "y", "z")]
save_json({"L_nH": (lc.get("L_nH") if lc.get("L_nH")
                    else float(np.mean(L[band]) * 1e9)),
           "L_band_mean_nH": float(np.mean(L[band]) * 1e9),
           "L_nH_std": float(np.std(L[band]) * 1e9),
           "lc_fit": lc,
           "R_mOhm_at_100MHz": float(np.interp(1e8, f, np.real(Z))) * 1e3,
           "band_Hz": [band_lo, band_hi], "fc_Hz": fc,
           "min_cell_mm": {"x": _dx[0], "y": _dx[1], "z": _dx[2]},
           "L_nH_from_record_fraction": trunc})
print("L =", np.mean(L[band]) * 1e9, "nH")
'''


NEAR_FAR = r'''
# The whole board's near and far field from one switch node's edge (Q11).
# The board is modelled as its six copper planes with the phase-cell pours on
# them; the excitation is a lumped port across the switch node driven with a
# pulse whose spectrum covers the 118 MHz ring the circuit model found.
import json
mm = 1e-3
G = json.load(open(os.path.join(OUT, "geom.json")))
stack = G["stack"]
PORT = G["port"]
LEAD = G.get("lead", None)
fc = float(G.get("fc", 500e6))

FDTD = openEMS(NrTS=int(G.get("nrts", 50000)), EndCriteria=1e-4)
FDTD.SetGaussExcite(fc / 2, fc / 2)
FDTD.SetBoundaryCond(["MUR"] * 6)
CSX = ContinuousStructure()
FDTD.SetCSX(CSX)
mesh = CSX.GetGrid()
mesh.SetDeltaUnit(1e-3)

R = 32.5
sub = CSX.AddMaterial("FR4", epsilon=4.5,
                      kappa=0.02 * 2 * np.pi * 1e8 * 4.5 * EPS0)
sub.AddCylinder([0, 0, -0.05], [0, 0, 1.65], R, priority=1)

metals = {}
for p in G["polys"]:
    key = p["net"]
    if key not in metals:
        metals[key] = CSX.AddMetal("m_" + key.replace(".", "_"))
    pts = np.array(p["pts"]).T
    metals[key].AddLinPoly(pts, "z", p["z"] - p["t"] / 2, p["t"], priority=10)
for b in G["barrels"]:
    key = b["net"]
    if key not in metals:
        metals[key] = CSX.AddMetal("m_" + key.replace(".", "_"))
    metals[key].AddCylinder([b["x"], b["y"], b["z0"]],
                            [b["x"], b["y"], b["z1"]], b["dia"] / 2,
                            priority=11)
if LEAD:
    w = CSX.AddMetal("lead")
    for seg in LEAD:
        w.AddCylinder(seg[0:3], seg[3:6], 0.8, priority=12)

from openEMS.ports import LumpedPort
px, py = PORT[0]
qx, qy = PORT[1]
z_top = stack["F.Cu"]["z"]
port = FDTD.AddLumpedPort(1, 50, [px - 0.5, py - 0.5, z_top],
                          [qx + 0.5, qy + 0.5, z_top + 0.8],
                          "z", excite=1, priority=20)

res = float(G.get("res", 1.2))
mesh.AddLine("x", [-R, R, px, qx]); mesh.AddLine("y", [-R, R, py, qy])
mesh.SmoothMeshLines("x", res, 1.4)
mesh.SmoothMeshLines("y", res, 1.4)
zs = [stack[k]["z"] for k in ("F.Cu", "In1.Cu", "In2.Cu", "In3.Cu",
                              "In4.Cu", "B.Cu")]
mesh.AddLine("z", zs + [-0.05, 1.65])
mesh.SmoothMeshLines("z", 0.12, 1.4)
mesh.AddLine("z", [-60.0, 80.0])
mesh.SmoothMeshLines("z", 4.0, 1.5)
mesh.AddLine("x", [-70, 70]); mesh.AddLine("y", [-70, 70])
mesh.SmoothMeshLines("x", 4.0, 1.5)
mesh.SmoothMeshLines("y", 4.0, 1.5)

f_probe = [float(x) for x in G.get("f_probe", [20e3 * 5, 1e6, 2e7, 1.18e8])]
nf2ff = FDTD.CreateNF2FFBox()
dump = CSX.AddDump("Hf_top", dump_type=11, dump_mode=2, file_type=1,
                   frequency=f_probe)
dump.AddBox([-R, -R, 3.0 + 1.65], [R, R, 3.0 + 1.65])
dump2 = CSX.AddDump("Hf_bot", dump_type=11, dump_mode=2, file_type=1,
                    frequency=f_probe)
dump2.AddBox([-R, -R, -3.0], [R, R, -3.0])

FDTD.Run(OUT, verbose=1, cleanup=True)
f = np.array(f_probe)
port.CalcPort(OUT, f)
res_ff = nf2ff.CalcNF2FF(OUT, f, np.array([0, 30, 60, 90]) * np.pi / 180,
                         np.array([0, 90]) * np.pi / 180, center=[0, 0, 0])
E = {}
for k, fq in enumerate(f):
    Emax = float(np.max(np.abs(res_ff.E_norm[k])))
    # E at 3 m from the maximum radiation intensity
    E3 = Emax / 3.0
    E[f"{fq:.4g}"] = {"E_at_3m_V_per_m": E3,
                      "dBuV_per_m": 20 * np.log10(max(E3, 1e-12) * 1e6),
                      "Dmax": float(np.max(res_ff.Dmax[k]))}
save_json({"far_field": E,
           "port_Z_ohm": [float(x) for x in
                          np.real(port.uf_tot / port.if_tot)],
           "f_probe": [float(x) for x in f]})
print(json.dumps(E, indent=1))
'''
