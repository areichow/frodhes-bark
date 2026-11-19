#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Generate the Wurlitzer 140B & 200A physical-model tables for Frodhes-Bark.

  tools/gen_wurli.py           write firmware/gen/wurli_tables.h & docs/model/data/wurli_spec.json

Physics & table architecture (shared MC3 + Hermite pickup + RLC/body pipeline in rhodes.c):
  - 88 keys (MIDI 21..108): fundamental V1 (shares RH_KEYS[n].m[0].{sh,ch,th,cr} at exact 12-TET f0,
    own d/drel/b) + reed overtone/clank mode O1 (~5.65..6.12 x f0, fast decay; inactive >= 0.44 * fs).
  - Electrostatic comb pickup curve WU_PHIM[2*(512+1)] over s = u^2 in [0, 8.0) gap^2 (same 1/64 gap^2
    cell width as Rhodes RH_PHIM, so rh_hermite_u runs unmodified; WU_U_MAX = 94890000 Q25 keeps i <= 511).
  - 6 register bands x 8 velocity layers of Hunt-Crossley nonlinear wedge-felt hammer pulses (p = 2.5),
    emitted as WU_PULSE for tools/gen_pulse_pz.py to pack into WU_PZB (raw WU_PULSE dead-stripped).
  - Band velocity excursion/gain/level laws (WU_PDRV, WU_PGF, WU_PLEV) + per-key gain calibrated to
    -32.0 dBFS at v100, negative attack pitch-dip table WU_KAP (keys 21..68), and 140B / 200A voicings
    (WU_RLC, WU_BODY, WU_TYPE) loudness-matched to Rhodes STAGE 77 at v100.
"""
import hashlib
import json
import math
import os
import sys

import numpy as np
from scipy.signal import lfilter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import gen_rhodes as gr    # noqa: E402
import hammer as hm        # noqa: E402
import rh_coefs as rc      # noqa: E402

FS = 44100.0
SUB = 32
RAMP_SUB = 5
KEY_LO, KEY_HI = 21, 108
NKEY = KEY_HI - KEY_LO + 1
NBAND = 6
BAND_FIRST = [21, 36, 48, 60, 72, 84]
BAND_REP = [28, 41, 53, 65, 77, 92]
NLAYER = 8
LAYER_V = [8, 24, 40, 56, 72, 88, 104, 120]
PLEN = 64
V_ANCH = [1, 8, 24, 40, 56, 72, 88, 104, 120, 127]
NANCH = len(V_ANCH)
PU_N = 256
S_MAX_GAP2 = 4.0
WU_U_MAX = 67108863        # Q25: < 2.0 -> s_scaled = u^2 < 4.0 -> i in [0, 255]
Z_SCALE = 1.0 / math.sqrt(2.0)  # scale physical z by 1/sqrt(2) so [0, 8) gap^2 maps to [0, 4) in 256 cells
WU_KAP_HI = 69             # keys 21..68 (48 keys)
ROM_LO, ROM_HI = 0xFFC00000, 0xFFCFFFFF
PHI_BIAS = 1 << 28
Q30, Q28, Q25, Q16 = 1 << 30, 1 << 28, 1 << 25, 1 << 16

# Electrostatic comb pickup geometry (half-height a, horizontal gap g)
COMB_A = 1.0
COMB_G = 0.45

# Deterministic per-key jitter seed
JIT_SEED = 200

# Modal & voicing curves fitted to .local/wurli/200a/ acoustic measurements
T60_V1_PTS = [[21, 14.0], [36, 12.5], [45, 11.5], [60, 7.4], [65, 5.3], [69, 3.1], [84, 2.4], [89, 2.1], [108, 1.0]]
T60_REL_PTS = [[21, 0.28], [36, 0.22], [45, 0.16], [60, 0.11], [69, 0.10], [84, 0.08], [108, 0.06]]
O1_RATIO_PTS = [[21, 5.65], [45, 5.85], [60, 6.12], [108, 6.12]]
T60_O1_PTS = [[21, 0.18], [45, 0.12], [60, 0.07], [72, 0.04], [108, 0.025]]
O1_WEIGHT_PTS = [[21, 0.16], [45, 0.13], [60, 0.09], [76, 0.05], [108, 0.03]]

# Target v127 V1 peak tip excursion (gap units) across registers
X127_PTS = [[21, 1.45], [48, 1.45], [60, 1.15], [72, 0.75], [84, 0.35], [108, 0.25]]

# Target negative attack pitch dip at v127 (cents) for keys 21..69
DIP_CENTS_PTS = [[21, 28.0], [41, 28.0], [45, 16.0], [48, 6.0], [60, 2.0], [69, 0.0]]

# Band wedge-felt contact durations at v104 (ms), bands 0..5
BAND_TC104_MS = [2.40, 1.85, 1.40, 1.05, 0.78, 0.55]

# Target loudness calibration (matching Rhodes gain_target_dbfs & global headroom)
GAIN_TARGET_DBFS = -32.0
PEAK_CAP_DBFS = -6.0

# 140B (index 0) and 200A (index 1) type voicings
WU_TYPES = [
    dict(
        name="W140",
        off=-0.07,
        bark=0.90,
        ham=0.85,
        bell=0.85,
        rlc=[3800.0, 0.85],
        body=[["peak", 220.0, 0.75, 2.5], ["hshelf", 4200.0, 0.7, -3.5]],
        drive_row=1,
    ),
    dict(
        name="W200",
        off=0.02,
        bark=1.12,
        ham=1.10,
        bell=1.15,
        rlc=[5800.0, 1.15],
        body=[["lshelf", 180.0, 0.7, -2.0], ["peak", 1800.0, 0.85, 3.0]],
        drive_row=0,
    ),
]


# ---------------------------------------------------------------- helpers
def jit(n, what):
    """Deterministic per-key jitter in [-1, 1) using sha256(seed:midi:what)."""
    h = hashlib.sha256(f"{JIT_SEED}:{n}:{what}".encode()).digest()
    return int.from_bytes(h[:8], "little") / 2.0 ** 63 - 1.0


def log_interp(x, pts):
    xs = [q[0] for q in pts]
    ys = [math.log(q[1]) for q in pts]
    return float(math.exp(np.interp(x, xs, ys)))


def lin_interp(x, pts):
    return float(np.interp(x, [q[0] for q in pts], [q[1] for q in pts]))


def band_of(n):
    b = 0
    for i, k in enumerate(BAND_FIRST):
        if n >= k:
            b = i
    return b


def in_rom(v):
    u = int(v) & 0xFFFFFFFF
    return ROM_LO <= u <= ROM_HI


# ---------------------------------------------------------------- comb pickup
def comb_c(z):
    z = np.asarray(z, float)
    return np.arctan((z + COMB_A) / COMB_G) - np.arctan((z - COMB_A) / COMB_G)


def comb_dc_dz(z):
    z = np.asarray(z, float)
    return COMB_G / (COMB_G * COMB_G + (z + COMB_A) ** 2) - COMB_G / (COMB_G * COMB_G + (z - COMB_A) ** 2)


def comb_dc_ds(s):
    s = np.asarray(s, float)
    out = np.empty_like(s)
    z0 = s <= 0.0
    out[z0] = -2.0 * COMB_A * COMB_G / (COMB_G * COMB_G + COMB_A * COMB_A) ** 2
    sp = s[~z0]
    sq = np.sqrt(sp)
    out[~z0] = comb_dc_dz(sq) / (2.0 * sq)
    return out


def comb_pickup_table():
    ds = S_MAX_GAP2 / PU_N
    s = np.arange(PU_N + 1) * ds
    phi = comb_c(np.sqrt(2.0 * s))
    dds = 2.0 * comb_dc_ds(2.0 * s) * ds
    A = float(max(np.max(np.abs(phi)), np.max(np.abs(dds))))
    P = [PHI_BIAS - int(round(x * Q28 / A)) for x in phi]
    M = [-int(round(x * Q28 / A)) for x in dds]
    assert not any(in_rom(v) for v in P + M), "WU_PHIM word in chip ROM window"
    assert all(0 <= v < (1 << 29) for v in P), "WU_PHIM P out of rh_hermite_u bounds"
    assert all(abs(v) < (1 << 23) for v in M), "WU_PHIM M out of rh_hermite_u bounds"
    interleaved = [x for pq in zip(P, M) for x in pq]
    return interleaved, A


# ---------------------------------------------------------------- hammer pulses
def build_band_pulses():
    """Generate 6 bands x 8 layer shapes + 10 anchor prate/pamp via nonlinear Hunt-Crossley wedge-felt contact."""
    p_exp = 2.5
    m_h = 0.006
    v104 = 2.0
    bands = []
    for b in range(NBAND):
        tc_target = BAND_TC104_MS[b] * 1e-3
        # Calibrate felt stiffness K (including hysteresis damping Rd) so Tc at v104 matches tc_target
        K = 1e9
        for _ in range(4):
            Rd = 0.08 * K * tc_target
            s0 = hm.contact(v104, m_h, K, p=p_exp, Rd=Rd, modes=(), fs=FS, t_max=0.015)
            K *= (s0["Tc"] / tc_target) ** (p_exp + 1.0)
        Rd = 0.08 * K * tc_target

        prates, peaks, tcs = [], [], []
        shapes = []
        for v in V_ANCH:
            # With p=2.5, Tc ~ vel^(-1.5/3.5) = vel^(-0.4286); vel ~ v^0.47 gives Tc(v) ~ v^(-0.20)
            vel_s = v104 * (max(v, 4) / 104.0) ** 0.47
            sim = hm.contact(vel_s, m_h, K, p=p_exp, Rd=Rd, modes=(), fs=FS, t_max=0.015)
            tc = sim["Tc"]
            tcs.append(tc * 1e3)
            pr = int(round(PLEN / (tc * FS) * 4096.0))
            prates.append(pr)
            peaks.append(float(np.max(sim["F"])))
            if v in LAYER_V:
                n_pt = max(2, int(round(tc / sim["dt"])))
                F = sim["F"][:n_pt]
                shapes.append(gr.area_resample(F, PLEN))

        pmax = max(peaks)
        pamp = [max(1, min(32767, int(round(32767.0 * x / pmax)))) for x in peaks]
        b0 = min(prates)
        assert b0 < 65536 and max(prates) - b0 < 65536, (b, prates)
        bands.append(dict(
            band=b,
            first_key=BAND_FIRST[b],
            rep_key=BAND_REP[b],
            shapes=shapes,
            prate_b=b0,
            prate=prates,
            pamp=pamp,
            tc_ms=tcs,
        ))
    return bands


# ---------------------------------------------------------------- Wurlitzer golden model
def layer_of(v):
    if v >= LAYER_V[-1]:
        return NLAYER - 1, 0
    if v <= LAYER_V[0]:
        return 0, 0
    j = max(a for a in range(NLAYER) if LAYER_V[a] <= v)
    return j, int(round(32768 * (v - LAYER_V[j]) / (LAYER_V[j + 1] - LAYER_V[j])))


def blend_shape(shapes, v):
    j, w = layer_of(v)
    a = shapes[j]
    if not w:
        return list(a)
    b = shapes[min(j + 1, NLAYER - 1)]
    return [x + (((y - x) * w) >> 15) for x, y in zip(a, b)]


def vpos_of(v):
    i = max(a for a in range(NANCH - 1) if V_ANCH[a] <= v)
    return i, min(255, int(round(256 * (v - V_ANCH[i]) / (V_ANCH[i + 1] - V_ANCH[i]))))


def vlaw(tab, v):
    i, f = vpos_of(v)
    return tab[i] + (((tab[i + 1] - tab[i]) * f) >> 8)


def vgain_band(bd, v):
    pg = bd.get("pgf")
    if pg is None:
        return 1.0
    q = vlaw(pg, v)
    d = max(1, vlaw(bd["pdrv"], v))
    return min(65535, max(1, (q << 15) // d)) / 65536.0


def mode_ed(f, t60, t60_rel, release=False):
    w = rc.mode_coefs(f, t60, t60_rel)
    if not w["th"]:
        return 0.0, 0.0
    E, eps = rc.coef(w, w["drel"] if release else w["d"])
    return E / Q30, 1.0 - eps / Q30


def apply_biquad(x, c):
    b0, b1, b2, a1, a2 = c
    return lfilter([b0, b1, b2], [1.0, a1, a2], x, axis=-1)


def render_modes_batch(keys, bands, vels, dur, bark_scale=1.0, bell_scale=1.0, ham_scale=1.0, use_o1=True, kap_list=None):
    """MC3 modal batch for Wurlitzer voices (V1 + O1), matching rhodes.c integers."""
    n_samp = int(dur * FS)
    nn = len(keys)
    nm = 2 if use_o1 else 1
    E0 = np.zeros((nn, nm))
    D0 = np.zeros((nn, nm))
    B = np.zeros((nn, nm))
    shapes = np.zeros((nn, PLEN + 2))
    pamp = np.zeros(nn)
    prate = np.zeros(nn)
    kap = np.zeros(nn) if kap_list is not None else None

    for i, (k, v) in enumerate(zip(keys, vels)):
        bd = bands[k["band"]]
        E0[i, 0], D0[i, 0] = mode_ed(k["v1"]["f"], k["v1"]["t60"], k["v1"]["t60_rel"])
        B[i, 0] = (round(k["v1"]["b"] * Q30) / Q30) * bark_scale
        if use_o1 and k["o1"]["f"] > 0.0:
            E0[i, 1], D0[i, 1] = mode_ed(k["o1"]["f"], k["o1"]["t60"], k["o1"]["t60_rel"])
            B[i, 1] = (round(k["o1"]["b"] * Q30) / Q30) * bark_scale * bell_scale if E0[i, 1] else 0.0
        sh = blend_shape(bd["shapes"], v)
        shapes[i, :PLEN] = np.array(sh) / 32768.0
        tab_p = bd.get("pdrv", bd["pamp"])
        pa = vlaw(tab_p, v)
        pr = vlaw(bd["prate"], v) << 4
        # HAMMER / TYPE ham scaling (same as rh_load_pulse under RH_DRIVE)
        pa = min(65535, int(pa * ham_scale))
        pr = int(pr * ham_scale)
        pamp[i] = pa / 32768.0
        prate[i] = pr / 65536.0
        if kap is not None:
            kap[i] = kap_list[i]

    x = np.zeros((nn, nm))
    y = np.zeros((nn, nm))
    out = np.zeros((nn, n_samp))
    ppos = np.zeros(nn)
    rows = np.arange(nn)
    E_cur = E0.copy()

    for sb in range(0, n_samp, SUB):
        if kap is not None:
            # S4 negative pitch dip on V1: E_eff = E0 * (1 - kap * a^2)
            a2 = np.minimum(x[:, 0] ** 2 + y[:, 0] ** 2, 64.0)
            f_dip = np.clip(-kap * a2, -1.0 / 16.0, 1.0 / 16.0)
            E_cur[:, 0] = E0[:, 0] * (1.0 + f_dip)
        for j in range(sb, min(sb + SUB, n_samp)):
            idx = np.floor(ppos).astype(int)
            fr = ppos - idx
            act = idx < PLEN
            F = np.zeros(nn)
            ii = np.minimum(idx, PLEN)
            F[act] = (shapes[rows, ii] + (shapes[rows, ii + 1] - shapes[rows, ii]) * fr)[act] * pamp[act]
            ppos += prate
            y += F[:, None] * B
            x += E_cur * y
            y = D0 * y - E_cur * x
            out[:, j] = x.sum(axis=1)
    return out


def render_wurli_batch(keys, bands, A_pu, vels, dur, ty=None, use_gain=True, use_kap=False):
    """Full Wurlitzer golden model render (MC3 -> comb pickup -> gain -> RLC -> body biquads)."""
    bark = ty["bark"] if ty else 1.0
    bell = ty["bell"] if ty else 1.0
    ham = ty["ham"] if ty else 1.0
    off_d = ty["off"] if ty else 0.0
    bark_g = (10 ** (ty.get("level_db", 0.0) / 20.0) / bark) if ty else 1.0
    rlc_f0, rlc_q = ty["rlc"] if ty else WU_TYPES[1]["rlc"]   # default calibration RLC: 200A
    body_specs = ty["body"] if ty else []

    kap_list = None
    if use_kap:
        # Note: in rhodes.c, kap is divided by (BARK * ty->bark)^2 so BARK doesn't detune
        kap_list = [(k.get("kap_q16", 0) / 65536.0) / (bark * bark) for k in keys]

    tip = render_modes_batch(keys, bands, vels, dur, bark_scale=bark, bell_scale=bell, ham_scale=ham, kap_list=kap_list)
    u_lim = WU_U_MAX / Q25
    dphi = np.zeros_like(tip)
    for i, k in enumerate(keys):
        off = k["off"] + off_d
        u = np.clip(tip[i] + off, -u_lim, u_lim)
        P = comb_c(np.abs(u)) / A_pu
        P0 = float(comb_c(abs(off))) / A_pu
        dphi[i] = np.diff(P, prepend=P0)

    g = np.array([
        (k.get("gain", 1.0) if use_gain else 1.0) * bark_g * vgain_band(bands[k["band"]], v)
        for k, v in zip(keys, vels)
    ])
    sig = dphi * g[:, None] * 32768.0
    sig = apply_biquad(sig, gr.biquad("lp2", rlc_f0, rlc_q, 0.0))
    for bq in body_specs:
        sig = apply_biquad(sig, gr.biquad(*bq))
    return sig


def kill_level_wurli(k, A_pu, gain):
    """Tip amplitude (gap) below 0.5 LSB at rest slope |C'(z0)| / A_pu."""
    slope = abs(float(comb_dc_dz(k["off"]))) / A_pu
    per = 2.0 * math.pi * k["f0"] / FS
    return 0.5 / max(slope * per * gain * 32768.0, 1e-30)


# ---------------------------------------------------------------- build pipeline
def build():
    phim, A_pu = comb_pickup_table()
    bands = build_band_pulses()

    # 1. Per-key reed modal physics
    keys = []
    for n in range(KEY_LO, KEY_HI + 1):
        f0 = gr.midi_hz(n)
        off = 0.90 + 0.02 * jit(n, "off")
        t60_v1 = log_interp(n, T60_V1_PTS) * (1.0 + 0.06 * jit(n, "t60"))
        t60_rel = log_interp(n, T60_REL_PTS)
        o1_f = f0 * lin_interp(n, O1_RATIO_PTS)
        if o1_f >= 0.44 * FS:
            o1_f, t60_o1, t60_o1_rel, w_o1 = 0.0, 1.0, 1.0, 0.0
        else:
            t60_o1 = log_interp(n, T60_O1_PTS) * (1.0 + 0.06 * jit(n, "t60_o1"))
            t60_o1_rel = min(t60_o1, t60_rel)
            w_o1 = lin_interp(n, O1_WEIGHT_PTS)
        life = int(min(6891, math.ceil((2.0 * t60_v1 + 1.0) * FS / 256.0)))
        keys.append(dict(
            midi=n,
            f0=f0,
            band=band_of(n),
            off=off,
            v1=dict(f=f0, t60=t60_v1, t60_rel=t60_rel, b=1e-4),
            o1=dict(f=o1_f, t60=t60_o1, t60_rel=t60_o1_rel, w_rel=w_o1, b=0.0),
            x127_target=lin_interp(n, X127_PTS),
            life_blocks=life,
        ))

    # 2. Scale each key's V1 b so a v127 strike reaches exact target x127(n) (with pdrv[-1] = 32767)
    for bd in bands:
        bd["pdrv"] = list(bd["pamp"])
        bd["pdrv"][-1] = 32767
    tip_unit = render_modes_batch(keys, bands, [127] * NKEY, 0.15, use_o1=False)
    for i, k in enumerate(keys):
        pk = float(np.max(np.abs(tip_unit[i])))
        sc = k["x127_target"] / max(pk, 1e-12)
        k["v1"]["b"] *= sc
        k["o1"]["b"] = k["v1"]["b"] * k["o1"]["w_rel"] if k["o1"]["f"] > 0.0 else 0.0

    # 3. Shape band excursion drive law (pdrv) & solve output gain law (pgf) + level law (plev)
    # Soft playing (v <= 48) stays clean (H3/H1 < -18 dB); mid (v=88) hits hollow snarl (H3/H1 ~ -10..-6 dB);
    # hard (v=127) reaches full reed bark, while overall note loudness rises ~25..27.5 dB from v8 to v127.
    # Excursion ratio per V_ANCH [1, 8, 24, 40, 56, 72, 88, 104, 120, 127] relative to v127:
    # Smooth convex-knee curve: low below v48, rising smoothly through v88 (~0.56) to 1.00 at v127.
    exc_curve_db = {
        # band 0..2 (bass/low-mid: strong snarl at v88, full bark at v127)
        0: [-27.0, -24.5, -20.0, -15.8, -11.8, -8.2, -5.0, -2.5, -0.7, 0.0],
        1: [-27.0, -24.5, -20.0, -15.8, -11.8, -8.2, -5.0, -2.5, -0.7, 0.0],
        2: [-27.0, -24.5, -20.0, -15.8, -11.8, -8.2, -5.0, -2.5, -0.7, 0.0],
        # band 3 (midrange C4..B4)
        3: [-27.0, -24.5, -20.2, -16.2, -12.4, -8.8, -5.4, -2.7, -0.8, 0.0],
        # band 4..5 (upper mid & clean treble)
        4: [-27.5, -25.0, -21.0, -17.2, -13.5, -9.8, -6.2, -3.2, -0.9, 0.0],
        5: [-28.0, -25.5, -21.6, -18.0, -14.4, -10.8, -7.2, -3.8, -1.1, 0.0],
    }
    # Target monotonic RMS loudness curve re v127 (dB) per band:
    # ~25 dB v8..v127 in bass (gentle saturation compression above v88) -> ~27.5 dB in treble
    lvl_range_v8 = [25.0, 25.0, 25.5, 26.5, 27.0, 27.5]

    for b, bd in enumerate(bands):
        rep = keys[bd["rep_key"] - KEY_LO]
        # Measure V1 peak at each anchor with unit pamp to convert target excursion -> pdrv (compensating tc(v)!)
        bd["pdrv"] = [32767] * NANCH
        bd.pop("pgf", None)
        t_ref = render_modes_batch([rep] * NANCH, bands, V_ANCH, 0.15, use_o1=False)
        pks = np.max(np.abs(t_ref), axis=1)
        # Desired V1 peak at each anchor a: x127 * 10^(exc_db / 20)
        pdrv_raw = []
        for a in range(NANCH):
            want_pk = rep["x127_target"] * 10 ** (exc_curve_db[b][a] / 20.0)
            pdrv_raw.append(32767.0 * want_pk / max(pks[a], 1e-12))
        sc_p = max(pdrv_raw) / 32767.0
        bd["pdrv"] = [max(1, min(32767, int(round(x / sc_p)))) for x in pdrv_raw]
        # Scale b for all keys in band b by sc_p so v127 peak stays exact
        for k in keys:
            if k["band"] == b:
                k["v1"]["b"] *= sc_p
                k["o1"]["b"] *= sc_p

        # Measure raw RMS (50..300 ms) of rep key at all anchors with pdrv and gain_law = 1.0
        y_raw = render_wurli_batch([rep] * NANCH, bands, A_pu, V_ANCH, 0.35, use_gain=False)
        rms_raw = np.array([20.0 * math.log10(float(np.sqrt(np.mean(r[2205:13230] ** 2))) + 1e-12) for r in y_raw])

        # Target monotonic loudness curve re v127 (gentle compression above v88 in bass/low-mid)
        r8 = lvl_range_v8[b]
        comp = 2.0 if b <= 2 else (1.0 if b == 3 else 0.0)
        target_rel = []
        for v in V_ANCH:
            u = (v - 8.0) / (127.0 - 8.0)
            # linear dB ramp from -r8 at v8 to 0 at v127, with slight convex top compression in bass
            lin = -r8 * (1.0 - u)
            if comp > 0.0 and u > 0.0:
                lin += comp * (1.0 - (1.0 - u) ** 2 - u) * 2.0
            target_rel.append(lin)
        target_rel = np.array(target_rel)
        target_rel -= target_rel[-1]

        # Required gain law G (dB), shifted so max(G) = 0 dB (gain_law <= 1.0)
        G_db = target_rel - (rms_raw - rms_raw[-1])
        G_db -= float(np.max(G_db))
        pgf = [max(1, min(65535, int(round(65536.0 * 10 ** (g / 20.0) * d / 32768.0))))
               for g, d in zip(G_db, bd["pdrv"])]

        # Re-strike headroom: pdrv(v2) * G(v2) / G(v1) <= 65535
        gl = [q / (2.0 * d) for q, d in zip(pgf, bd["pdrv"])]
        hk = min(1.0, 65535.0 * min(gl) / max(d * g for d, g in zip(bd["pdrv"], gl)))
        if hk < 1.0:
            bd["pdrv"] = [max(1, int(round(d * hk))) for d in bd["pdrv"]]
            pgf = [max(1, min(65535, int(round(q * hk)))) for q in pgf]
            for k in keys:
                if k["band"] == b:
                    k["v1"]["b"] /= hk
                    k["o1"]["b"] /= hk
        bd["pgf"] = pgf

        # Measure final monotonic note level law plev (Q15 re v127)
        y_fin = render_wurli_batch([rep] * NANCH, bands, A_pu, V_ANCH, 0.35, use_gain=False)
        lv = np.array([20.0 * math.log10(float(np.sqrt(np.mean(r[2205:13230] ** 2))) + 1e-12) for r in y_fin])
        lv = np.maximum.accumulate(lv) - float(np.max(lv))
        bd["plev"] = [max(1, min(32767, int(round(32767.0 * 10 ** (x / 20.0))))) for x in lv]

    # 4. Calibrate per-key gain so v100 RMS (50..300 ms) hits GAIN_TARGET_DBFS (-32.0 dBFS) + global peak cap
    y100 = render_wurli_batch(keys, bands, A_pu, [100] * NKEY, 0.35, use_gain=False)
    target_rms = 10 ** (GAIN_TARGET_DBFS / 20.0) * 32767.0
    gains = np.array([target_rms / (float(np.sqrt(np.mean(r[2205:13230] ** 2))) + 1e-12) for r in y100])
    for k, g in zip(keys, gains):
        k["gain"] = float(g)

    # Check global v127 peak headroom across all 88 keys
    y127 = render_wurli_batch(keys, bands, A_pu, [127] * NKEY, 0.25, use_gain=True)
    peak_max = float(np.max(np.abs(y127))) / 32767.0
    peak_lim = 10 ** (PEAK_CAP_DBFS / 20.0)
    cap = min(1.0, peak_lim / max(peak_max, 1e-12))
    for k in keys:
        k["gain"] *= cap
        k["kill"] = kill_level_wurli(k, A_pu, k["gain"])

    # 5. ROM-window check on b (if any b word ever hit ROM window, scale key b & gain safely)
    for k in keys:
        b0 = int(round(k["v1"]["b"] * Q30))
        b1 = int(round(k["o1"]["b"] * Q30)) if k["o1"]["f"] > 0.0 else 0
        assert abs(b0) < (1 << 31) and abs(b1) < (1 << 31), (k["midi"], b0, b1)
        assert not in_rom(b0) and not in_rom(b1), (k["midi"], b0, b1)

    # 6. Negative attack pitch-dip table WU_KAP (keys 21..68)
    tip127_v1 = render_modes_batch(keys[:WU_KAP_HI - KEY_LO], bands, [127] * (WU_KAP_HI - KEY_LO), 0.15, use_o1=False)
    kap_tab = []
    for i, k in enumerate(keys[:WU_KAP_HI - KEY_LO]):
        a127 = float(np.max(np.abs(tip127_v1[i])))
        cents = lin_interp(k["midi"], DIP_CENTS_PTS)
        frac = 2.0 ** (cents / 1200.0) - 1.0
        kap_q16 = max(0, min(65535, int(round(65536.0 * frac / max(a127 * a127, 1e-12)))))
        k["kap_q16"] = kap_q16
        k["a127"] = a127
        kap_tab.append(kap_q16)

    # 7. Calibrate W140 and W200 level_db so both play at matched median v100 loudness (-32.0 dBFS)
    types_out = []
    sample_keys = keys[7:83:3]     # E1..G7 stride 3 (same range as test_types_level_matched)
    for ty in WU_TYPES:
        t_meas = dict(ty, level_db=0.0)
        yt = render_wurli_batch(sample_keys, bands, A_pu, [100] * len(sample_keys), 0.35, ty=t_meas, use_gain=True)
        rms_db = [20.0 * math.log10(float(np.sqrt(np.mean(r[2205:13230] ** 2))) / 32767.0 + 1e-12) for r in yt]
        med_db = float(np.median(rms_db))
        t_fin = dict(ty, level_db=round(GAIN_TARGET_DBFS - med_db, 3))
        types_out.append(t_fin)

    spec = dict(
        fs=FS,
        key_lo=KEY_LO,
        key_hi=KEY_HI,
        nband=NBAND,
        nlayer=NLAYER,
        plen=PLEN,
        pu_n=PU_N,
        u_max=WU_U_MAX,
        kap_hi=WU_KAP_HI,
        pickup=dict(a=COMB_A, g=COMB_G, s_max=S_MAX_GAP2, A=A_pu),
        bands=bands,
        keys=keys,
        kap=kap_tab,
        types=types_out,
        peak_cap_db=20.0 * math.log10(cap),
    )
    return spec, phim


def emit_header(spec, phim):
    L = []
    w = L.append
    w("/* SPDX-License-Identifier: GPL-3.0-only */")
    w("/* GENERATED by tools/gen_wurli.py - do not edit. */")
    w("#ifndef WURLI_TABLES_H")
    w("#define WURLI_TABLES_H")
    w(f"#define WU_KEY_LO {KEY_LO}")
    w(f"#define WU_NKEY {NKEY}")
    w(f"#define WU_NBAND {NBAND}")
    w(f"#define WU_NLAYER {NLAYER}")
    w(f"#define WU_PLEN {PLEN}")
    w(f"#define WU_NANCH {NANCH}")
    w(f"#define WU_PU_N {PU_N}")
    w(f"#define WU_U_MAX {WU_U_MAX}")
    w(f"#define WU_KAP_HI {WU_KAP_HI}u")
    w("")
    w("typedef struct {")
    w("    int32_t d, drel, b;        /* V1 decay & force gain (sh, ch, th, cr shared with RH_KEYS[n].m[0] at exact f0) */")
    w("    rh_mode_t o1;              /* O1 reed overtone/clank mode (sh, ch, th, d, drel, cr, b) */")
    w("    int32_t off, gain, kill;   /* Q25 rest offset, Q16 output gain, Q25 kill level */")
    w("    uint16_t life;             /* max life in 256-sample blocks */")
    w("    uint8_t band, pad;         /* pulse band 0..5 */")
    w("} wu_key_t;")
    w("")

    all_i32 = []

    def chk(v):
        v = gr.rom_safe(int(v))
        assert not in_rom(v), f"ROM-window word {v}"
        all_i32.append(v)
        return v

    # WU_KEYS[88] (z-domain fields b, o1.b, off, kill scaled by Z_SCALE = 1/sqrt(2) to match 256-knot WU_PHIM)
    w("static const wu_key_t WU_KEYS[WU_NKEY] = {")
    for k in spec["keys"]:
        c0 = rc.mode_coefs(k["v1"]["f"], k["v1"]["t60"], k["v1"]["t60_rel"])
        b0 = chk(int(round(k["v1"]["b"] * Z_SCALE * Q30)))
        d0, dr0 = chk(c0["d"]), chk(c0["drel"])
        c1 = rc.mode_coefs(k["o1"]["f"], k["o1"]["t60"], k["o1"]["t60_rel"])
        b1 = chk(int(round(k["o1"]["b"] * Z_SCALE * Q30))) if c1["th"] else 0
        o1_s = "{%d,%d,%d,%d,%d,%d,%d}" % (
            chk(c1["sh"]), chk(c1["ch"]), chk(c1["th"]),
            chk(c1["d"]), chk(c1["drel"]), chk(c1["cr"]), b1,
        )
        off_q = chk(int(round(k["off"] * Z_SCALE * Q25)))
        gain_q = chk(int(round(k["gain"] * Q16)))
        kill_q = chk(int(round(k["kill"] * Z_SCALE * Q25)))
        w(f"    {{{d0}, {dr0}, {b0}, {o1_s}, {off_q}, {gain_q}, {kill_q}, {k['life_blocks']}, {k['band']}, 0}},")
    w("};")
    w("")

    # WU_PHIM[2 * (WU_PU_N + 1)]
    for v in phim:
        assert not in_rom(v)
        all_i32.append(v)
    w("static const int32_t WU_PHIM[2 * (WU_PU_N + 1)] = {")
    for i in range(0, len(phim), 16):
        w("    " + ",".join(str(v) for v in phim[i:i + 16]) + ",")
    w("};")
    w("")

    # WU_PULSE[WU_NBAND][WU_NLAYER][WU_PLEN + 2] (raw for gen_pulse_pz.py; dead-stripped in firmware)
    w("static const int16_t WU_PULSE[WU_NBAND][WU_NLAYER][WU_PLEN + 2] = {")
    for bd in spec["bands"]:
        w("  {" + ",".join("{" + ",".join(map(str, sh)) + ",0,0}" for sh in bd["shapes"]) + "},")
    w("};")
    w("static const uint16_t WU_PRATE_B[WU_NBAND] = {" + ",".join(str(bd["prate_b"]) for bd in spec["bands"]) + "};")
    w("static const uint16_t WU_PRATE[WU_NBAND][WU_NANCH] = {")
    for bd in spec["bands"]:
        b0 = bd["prate_b"]
        w("    {" + ",".join(str(x - b0) for x in bd["prate"]) + "},")
    w("};")
    w("static const uint16_t WU_PAMP[WU_NBAND][WU_NANCH] = {")
    for bd in spec["bands"]:
        w("    {" + ",".join(map(str, bd["pamp"])) + "},")
    w("};")
    w("static const uint16_t WU_PDRV[WU_NBAND][WU_NANCH] = {")
    for bd in spec["bands"]:
        w("    {" + ",".join(map(str, bd["pdrv"])) + "},")
    w("};")
    w("static const uint16_t WU_PGF[WU_NBAND][WU_NANCH] = {")
    for bd in spec["bands"]:
        w("    {" + ",".join(map(str, bd["pgf"])) + "},")
    w("};")
    w("static const uint16_t WU_PLEV[WU_NBAND][WU_NANCH] = {")
    for bd in spec["bands"]:
        w("    {" + ",".join(map(str, bd["plev"])) + "},")
    w("};")
    w("static const uint16_t WU_KAP[WU_KAP_HI - WU_KEY_LO] = {")
    kap = [min(65535, int(v * 2)) for v in spec["kap"]]
    for i in range(0, len(kap), 16):
        w("    " + ",".join(str(v) for v in kap[i:i + 16]) + ",")
    w("};")
    w("")

    # WU_RLC, WU_BODY, WU_TYPE (140B = 0, 200A = 1)
    q28 = lambda c: "{" + ",".join(str(chk(int(round(x * Q28)))) for x in c) + "}"
    w("static const int32_t WU_RLC[2][5] = {")
    for ty in spec["types"]:
        f0, q = ty["rlc"]
        w("    " + q28(gr.biquad("lp2", f0, q, 0.0)) + ",")
    w("};")
    w("static const int32_t WU_BODY[2][2][5] = {")
    for ty in spec["types"]:
        bq = [gr.biquad(*b) for b in ty["body"]] + [(1.0, 0.0, 0.0, 0.0, 0.0)] * 2
        w("    {" + ",".join(q28(c) for c in bq[:2]) + "},")
    w("};")
    w("static const rh_type_t WU_TYPE[2] = {")
    for ty in spec["types"]:
        off_q = chk(int(round(ty["off"] * Z_SCALE * Q25)))
        bark_q = chk(int(round(ty["bark"] * Q16)))
        bark_g = chk(int(round(Q16 * 10 ** (ty["level_db"] / 20.0) / ty["bark"])))
        bell_q = chk(int(round(ty["bell"] * Q16)))
        ham_q = chk(int(round(ty["ham"] * Q16)))
        w(f"    {{{off_q}, {bark_q}, {bark_g}, {bell_q}, {ham_q}, {len(ty['body'])}, {ty['drive_row']}}},")
    w("};")
    w("#endif")
    w("")

    assert not any(in_rom(v) for v in all_i32), "ROM-window word detected!"
    return "\n".join(L)


def main():
    spec, phim = build()
    hdr_path = os.path.join(ROOT, "firmware", "gen", "wurli_tables.h")
    spec_path = os.path.join(ROOT, "docs", "model", "data", "wurli_spec.json")
    open(hdr_path, "w").write(emit_header(spec, phim))
    json.dump(spec, open(spec_path, "w"), indent=1)
    print(f"wrote {os.path.relpath(hdr_path, ROOT)} & {os.path.relpath(spec_path, ROOT)}")
    print(f"peak cap: {spec['peak_cap_db']:.2f} dB; types level_db: "
          + ", ".join(f"{t['name']}={t['level_db']:+.2f} dB" for t in spec["types"]))
    for bd in spec["bands"]:
        print(f"  band {bd['band']} (k{bd['first_key']}.., rep k{bd['rep_key']}): "
              f"tc@v104={bd['tc_ms'][7]:.2f} ms  pdrv={bd['pdrv'][0]}..{bd['pdrv'][-1]}  "
              f"plev_v8={20*math.log10(bd['plev'][1]/32767.0):.1f} dB")


if __name__ == "__main__":
    main()
