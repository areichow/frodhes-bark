# SPDX-License-Identifier: GPL-3.0-only
"""Regression test for low-end Rhodes S4 (Duffing) pitch stiffening (Build 1965):
A0..C3 on STAGE 77 (RAW, neutral knobs) must stay within [-3c, +10c] on attack (20..200 ms)
and within [-3c, +4c] in sustain (500..1000 ms) at v63, v95, v127."""
import math
import os
import subprocess
import tempfile
import wave
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BIN = os.path.join(ROOT, "build", "host", "frodhes")
FS = 44100
KEYS = (21, 28, 36, 41, 45, 48)  # A0, E1, C2, F2, A2, C3
VELS = (63, 95, 127)


def _measure_cents(seg, f0):
    win = np.hanning(len(seg))
    n = max(131072, 1 << int(math.ceil(math.log2(len(seg) * 8))))
    p = np.abs(np.fft.rfft(seg * win, n)) ** 2
    fr = np.fft.rfftfreq(n, 1.0 / FS)
    # Search +-150 cents around f0 (or 2*f0 for lowest notes if H2 is stronger, divided by 2)
    lo, hi = f0 * 0.91, f0 * 1.09
    m = (fr >= lo) & (fr <= hi)
    idx = np.where(m)[0]
    pk = idx[int(np.argmax(p[idx]))]
    # Parabolic interpolation on log power
    if 0 < pk < len(p) - 1:
        y0, y1, y2 = math.log(p[pk - 1] + 1e-30), math.log(p[pk] + 1e-30), math.log(p[pk + 1] + 1e-30)
        d = 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2) if (y0 - 2 * y1 + y2) != 0 else 0.0
        f_est = (pk + d) * FS / n
    else:
        f_est = fr[pk]
    return 1200.0 * math.log2(f_est / f0)


def test_bass_pitch_within_bounds():
    with tempfile.TemporaryDirectory() as td:
        script = os.path.join(td, "bass_pitch.txt")
        out_base = os.path.join(td, "bass_pitch")
        ls = [
            "10 midi 0xB0 119 127",  # RAW
            "20 midi 0xB0 104 0",    # NOISE 0
            "30 midi 0xB0 80 0",     # STAGE 77
            "30 midi 0xB0 28 64",    # TONE 64
            "30 midi 0xB0 29 64",    # BARK 64
        ]
        ev = []
        t = 200
        for k in KEYS:
            for v in VELS:
                ev.append((k, v, t))
                ls.append(f"{t} midi 0x90 {k} {v}")
                ls.append(f"{t + 1200} midi 0x80 {k} 0")
                t += 1600
        ls.append(f"{t + 200} end")
        with open(script, "w") as f:
            f.write("\n".join(ls) + "\n")
        subprocess.run([BIN, out_base, script], check=True, capture_output=True)
        with wave.open(out_base + ".wav") as w:
            x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2).astype(float).mean(1)
        for k, v, t_ms in ev:
            f0 = 440.0 * 2.0 ** ((k - 69) / 12.0)
            t0 = t_ms / 1000.0
            c_atk = _measure_cents(x[int((t0 + 0.02) * FS):int((t0 + 0.20) * FS)], f0)
            c_sus = _measure_cents(x[int((t0 + 0.50) * FS):int((t0 + 1.00) * FS)], f0)
            assert -3.0 <= c_atk <= 10.0, f"k={k} v={v} attack {c_atk:+.2f}c out of [-3, +10]c"
            assert -3.0 <= c_sus <= 4.0, f"k={k} v={v} sustain {c_sus:+.2f}c out of [-3, +4]c"
