"""openEMS scripts for the two L2 known-answer tests in SPEC.md sec.5.4.

They are kept as source strings because they run in the container's Python,
not in ours.
"""

MICROSTRIP = r'''
# Microstrip characteristic impedance: 0.2 mm wide on 0.1 mm FR4 (eps_r 4.5),
# ground as the z = 0 PEC boundary.  Structured after openEMS' own MSL example;
# the comparison against Hammerstad/Wheeler happens in our process.
unit = 1e-3
MSL_len, MSL_w, SUB_t, EPS_R = 20.0, 0.2, 0.1, 4.5
res = float(os.environ.get("MSL_RES", "0.03"))
f_max = 10e9

FDTD = openEMS(NrTS=int(os.environ.get("MSL_NRTS", "30000")), EndCriteria=1e-4)
FDTD.SetGaussExcite(f_max / 2, f_max / 2)
FDTD.SetBoundaryCond(["PML_8", "PML_8", "MUR", "MUR", "PEC", "MUR"])
CSX = ContinuousStructure()
FDTD.SetCSX(CSX)
mesh = CSX.GetGrid()
mesh.SetDeltaUnit(unit)

Y = 20 * MSL_w
mesh.AddLine("x", [0.0, MSL_len])
mesh.SmoothMeshLines("x", 4 * res)
mesh.AddLine("y", [-Y, -MSL_w / 2 - res, -MSL_w / 2, MSL_w / 2,
                   MSL_w / 2 + res, Y])
mesh.SmoothMeshLines("y", res, 1.4)
mesh.AddLine("z", [0.0, SUB_t, 20 * SUB_t])
mesh.SmoothMeshLines("z", res, 1.4)

sub = CSX.AddMaterial("FR4", epsilon=EPS_R)
sub.AddBox([0.0, -Y, 0.0], [MSL_len, Y, SUB_t], priority=1)

from openEMS.ports import MSLPort
port = FDTD.AddMSLPort(1, CSX.AddMetal("msl"),
                       [0.0, -MSL_w / 2, SUB_t], [MSL_len, MSL_w / 2, 0.0],
                       "x", "z", excite=-1,
                       FeedShift=10 * res, MeasPlane_Shift=MSL_len / 3,
                       priority=10)

FDTD.Run(OUT, verbose=1, cleanup=True)
f = np.linspace(0.5e9, 8e9, 300)
port.CalcPort(OUT, f)
Zref = np.real(port.Z_ref)
band = (f > 1e9) & (f < 5e9)
save(f=f, Z_ref=Zref, beta=np.real(port.beta))
save_json({"Z0_mean_1_5GHz": float(np.mean(Zref[band])),
           "Z0_std_1_5GHz": float(np.std(Zref[band])),
           "res_mm": res})
print("Z0 (1-5 GHz):", np.mean(Zref[band]), "+-", np.std(Zref[band]))
'''


STRIP_OVER_PLANE = r"""
# The same strip-over-plane as the FastHenry known-answer test, as a lumped
# port so that L = Im(Z)/omega can be compared with L1.  15.4 x 5 mm strip,
# 70 um copper, 0.1524 mm above a wide ground plane (F.Cu to In1 mid-planes),
# shorted to the plane at the far end.  The fit band is 0.2-1 GHz: the loop is
# still electrically tiny there (15 mm is lambda/20 at 1 GHz) but the reactance
# is large enough to measure, which an FDTD at 10 MHz would not be.
unit = 1e-3
L_s, W_s = 15.4, 5.0
H = 0.1524
PLX = [-8.0, 23.4]
PLY = [-11.0, 11.0]
fc = 3e9

FDTD = openEMS(NrTS=40000, EndCriteria=1e-4)
FDTD.SetGaussExcite(fc / 2, fc / 2)
FDTD.SetBoundaryCond(["MUR"] * 6)
CSX = ContinuousStructure()
FDTD.SetCSX(CSX)
mesh = CSX.GetGrid()
mesh.SetDeltaUnit(unit)

gnd = CSX.AddMetal("gnd")
gnd.AddBox([PLX[0], PLY[0], -H], [PLX[1], PLY[1], -H], priority=10)
strip = CSX.AddMetal("strip")
strip.AddBox([0.0, -W_s / 2, 0.0], [L_s, W_s / 2, 0.0], priority=10)
short = CSX.AddMetal("short")
short.AddBox([L_s, -W_s / 2, -H], [L_s, W_s / 2, 0.0], priority=10)

port = FDTD.AddLumpedPort(1, 50, [0.0, -W_s / 2, -H], [0.0, W_s / 2, 0.0],
                          "z", excite=1, priority=5)

mesh.AddLine("x", [PLX[0], 0.0, L_s, PLX[1]])
mesh.SmoothMeshLines("x", 0.6, 1.35)
mesh.AddLine("y", [PLY[0], -W_s / 2, W_s / 2, PLY[1]])
mesh.SmoothMeshLines("y", 0.6, 1.35)
mesh.AddLine("z", [-H, 0.0])
mesh.SmoothMeshLines("z", H / 3)
mesh.AddLine("z", [-6.0, 8.0])
mesh.SmoothMeshLines("z", 0.8, 1.4)

FDTD.Run(OUT, verbose=1, cleanup=True)
f = np.linspace(50e6, 1.5e9, 400)
port.CalcPort(OUT, f)
Z = port.uf_tot / port.if_tot
Lf = np.imag(Z) / (2 * np.pi * f)
band = (f > 2e8) & (f < 1e9)
save(f=f, Z_re=np.real(Z), Z_im=np.imag(Z), L=Lf)
save_json({"L_nH": float(np.mean(Lf[band]) * 1e9),
           "L_nH_std": float(np.std(Lf[band]) * 1e9)})
print("L =", np.mean(Lf[band]) * 1e9, "nH")
"""
