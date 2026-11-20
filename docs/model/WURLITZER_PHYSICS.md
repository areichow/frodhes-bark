# Wurlitzer 140B & 200A Electric Piano Physical Model — Mathematical Specification

This document specifies the complete mathematical and algorithmic model used in **Frodhes-Bark** for both Wurlitzer electric piano variants:

- **Wurlitzer 140B (`W140`)** — mid-1960s solid-state/tube-era voicing: warmer low-mid body, rounder treble roll-off, slightly softer hammer and pickup drive.
- **Wurlitzer 200A (`W200`)** — 1970s classic voicing: tighter lows, nasal/barky 1.8 kHz speaker-cone presence, sharper reed attack, and aggressive odd-harmonic reed growl.

Both models share the **exact same physical architecture** (wedge-felt hammer contact, 2-mode steel cantilever reed resonator, electrostatic comb capacitance pickup, negative softening attack pitch dip, damper-felt overtone twang, and preamp/speaker biquads). Everything below is written in language-agnostic mathematics suitable for implementation in any language or DSP platform.

---

## 1. Signal Flow Overview

Unlike the Rhodes (which uses a cylindrical rod tine + tonebar and an *inductive* magnetic pickup), a Wurlitzer uses a **flat steel cantilever reed** vibrating inside an **electrostatic comb pickup** (a variable capacitor biased at ~145–170 V DC). This physical difference produces the Wurlitzer's three unmistakable acoustic signatures:
1. **Odd-harmonic dominance ($H_3 > H_2$, $H_5 > H_4$) on hard strikes** — caused by the reed tip entering and exiting the two edges of the electrostatic comb slot symmetrically.
2. **Negative attack pitch dip ($-16\text{ to }-28\text{ cents}$ on bass notes)** — large-amplitude deflection softens the effective reed restoring force (electrostatic negative stiffness + geometric solder-tip inertia), making hard bass strikes start **flat** and bend **up** into pitch as they decay (opposite of a Rhodes's sharp pitch-up!).
3. **Snappy reed-clank overtone (`O1` $\approx 5.65\text{--}6.12\,f_0$)** and shorter, punchier sustain/release envelopes.

```
MIDI Note (k, v_in)
  │
  ▼
[1. 6-Point Piecewise-Linear Velocity Curve]  ──► v ∈ [1, 127]
  │
  ▼
[2. Wedge-Felt Hammer Force Pulse F(t)]       (Hunt–Crossley contact, p = 2.5, 6 bands)
  │
  ▼
[3. 2-Mode Steel Reed Resonator (MC3)]        (V1 fundamental + O1 reed clank overtone)
  │   ├── Negative attack pitch dip (−κ a²)   (electrostatic/geometric softening)
  │   └── Damper-felt crossfade & snappy O1 release twang on NoteOff
  │
  ▼  Total reed tip displacement x(t) = x_V1(t) + x_O1(t)  (in comb-half-height units)
[4. Electrostatic Comb Pickup C(z)]           (Closed-form arctan comb + Cubic Hermite)
  │   y[n] = ΔC(z[n])                         (AC-coupled charge/current signal)
  │
  ▼  Voice gain, band velocity law, optional stereo spread + felt/pedal noise
[5. Shared Preamp RLC + Speaker Cabinet EQ]   (140B warm vs. 200A resonant nasal)
```

**Units and conventions:**
- Sample rate $f_s = 44{,}100\text{ Hz}$, MIDI key $k \in \{21, \dots, 108\}$, $f_0(k) = 440 \cdot 2^{(k - 69)/12}\text{ Hz}$.
- Displacements $x(t)$ and rest offset $z_0$ are expressed in units of the electrostatic comb's half-height $a = 1.0$.

---

## 2. Steel Cantilever Reed Modal Parameters

A Wurlitzer reed is a flat, tapered spring-steel cantilever clamped at one end with a drop of lead-tin solder near the free tip for tuning. Because there is no coupled tonebar and the reed's flat cross-section suppresses orthogonal polarisation whirl, **2 modes per key** accurately capture the reed's entire acoustic behavior:

### 2.1 Mode 0: Fundamental (`V1`)
- Frequency: exact 12-TET $f_0(k)$.
- Sustain $T_{60,V1}(k)$ (log-interpolated across anchor keys, fitted to dry 200A measurements):

| MIDI Key | 21 (A0) | 36 (C2) | 45 (A2) | 60 (C4) | 65 (F4) | 69 (A4) | 84 (C6) | 89 (F6) | 108 (C8) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **$T_{60,V1}$ (s)** | 14.0 | 12.5 | 11.5 | 7.4 | 5.3 | 3.1 | 2.4 | 2.1 | 1.0 |

- Damper-on release $T_{60,\text{rel}}(k)$ (log-interpolated):

| MIDI Key | 21 (A0) | 36 (C2) | 45 (A2) | 60 (C4) | 69 (A4) | 84 (C6) | 108 (C8) |
|---|---:|---:|---:|---:|---:|---:|---:|
| **$T_{60,\text{rel}}$ (s)** | 0.28 | 0.22 | 0.16 | 0.11 | 0.10 | 0.08 | 0.06 |

### 2.2 Mode 1: First Cantilever Overtone / Clank (`O1`)
An ideal uniform cantilever's 2nd bending mode lies at $6.267\,f_0$; the solder mass at the Wurlitzer reed tip lowers this ratio in the bass:
- **Frequency ratio $f_{O1}/f_0$** (linearly interpolated): `5.65` at key 21 → `5.85` at key 45 → `6.12` at keys 60–108. (Omitted when $f_{O1} > 18\text{ kHz}$ or $f_{O1} \ge 0.44 f_s$.)
- **Decay $T_{60,O1}$** (log-interpolated, same for hold and release): `0.18 s` (key 21) → `0.12 s` (45) → `0.07 s` (60) → `0.04 s` (72) → `0.025 s` (108).
- **Excitation weight $w_{O1}$** (relative to `V1`, linearly interpolated): `0.16` (key 21) → `0.13` (45) → `0.09` (60) → `0.05` (76) → `0.03` (108).

Deterministic per-key manufacturing jitter (seeded hash per key, never altering $f_0$) varies $T_{60}$ ($\pm 6\%$), `O1` frequency ($\pm 1.5\%$), and resting offset $z_0$ ($\pm 0.03$).

---

## 3. Wedge-Felt Hammer Contact Model (Hunt–Crossley)

Wurlitzer action uses miniature grand-piano-style felt-covered wooden hammers grouped into **6 register bands** starting at MIDI keys `[21, 36, 48, 60, 72, 84]` (representative keys `[28, 41, 53, 65, 77, 92]`).

### 3.1 Nonlinear Felt Compression Law
Using the **Hunt–Crossley viscoelastic contact law** with wool-felt stiffening exponent $p = 2.5$ and hammer mass $m_h = 6\text{ g}$ against a rigid target:

$$F(e, \dot{e}) = \max\!\left(0,\; K\,e^p + R_d\,p\,e^{p-1}\dot{e}\right), \qquad R_d = 0.08\,K\,T_{c,\text{target}}$$

1. **Stiffness calibration**: For each band $b \in \{0,\dots,5\}$, stiffness $K_b$ is solved iteratively so contact duration $T_c$ at anchor `v = 104` ($v_h = 2.0\text{ m/s}$) matches the band targets:
   $$T_{c,104} = [2.40,\; 1.85,\; 1.40,\; 1.05,\; 0.78,\; 0.55]\text{ ms}$$
2. **Velocity scaling**: Across the 10 velocity anchors `v ∈ {1, 8, 24, 40, 56, 72, 88, 104, 120, 127}`, hammer impact velocity scales as:
   $$v_h(v) = 2.0 \cdot \left(\frac{\max(v, 4)}{104}\right)^{0.47}\text{ m/s}$$
   Since Hertzian contact time scales as $T_c \propto v_h^{(1-p)/(1+p)} = v_h^{-3/7}$, this produces $T_c(v) \propto v^{-0.20}$: softer strikes are wider and warmer, harder strikes are narrower and brighter.
3. **Real-time playback**: 8 normalized 64-sample force pulse shapes per band (layers `v = 8, 24, 40, 56, 72, 88, 104, 120`) are blended linearly at note-on and played back at interpolated rate $r_p(b, v) = \frac{64}{T_c(b, v)\,f_s}$.

---

## 4. Modal Resonator (`MC3`) & Negative Attack Pitch Dip

### 4.1 2-Mode `MC3` Update
Both modes (`V1` and `O1`) use the exact same **Modified Coupled-Form (`MC3`)** recurrence as the Rhodes:

$$y_m[n] \leftarrow y_m[n-1] + b_m \, F[n]$$
$$x_m[n] = x_m[n-1] + E_m \, y_m[n]$$
$$y_m[n] = y_m[n] - \varepsilon_m \, y_m[n] - E_m \, x_m[n]$$
$$x[n] = x_{V1}[n] + x_{O1}[n]$$

with $\varepsilon_m = 1 - r_m^2$, $r_m = e^{-\ln(1000)/(T_{60,m} f_s)}$, and $E_m = 2\sin(\theta_m/2)\sqrt{r_m}\left(1 + \frac{(1-r_m)^2}{8\sin^2(\theta_m/2)}\right)$.

### 4.2 Negative Attack Pitch Dip (Softening Nonlinearity)
Unlike the Rhodes (which pitches **sharp** on hard hits), a Wurlitzer bass reed pitches **flat** at attack and bends **up** to steady pitch as energy decays. Every 32-sample sub-block, `V1`'s frequency coefficient $E_1$ is modulated downward by modal energy $a^2 = x_{V1}^2 + y_{V1}^2$:

$$E_{1,\text{eff}} = E_1 \cdot \left(1 - \operatorname{clamp}\!\left(\kappa_{\text{wu}}(k)\,a^2,\; 0,\; \tfrac{1}{16}\right)\right)$$

For keys 21–68, $\kappa_{\text{wu}}(k) > 0$ is calibrated so a `v = 127` strike produces the measured peak attack dip:

| MIDI Key | 21 (A0) – 41 (F2) | 45 (A2) | 48 (C3) | 60 (C4) | $\ge 69$ (A4+) |
|---|---:|---:|---:|---:|---:|
| **Peak Dip at `v127` (cents)** | `-28.0 c` | `-16.0 c` | `-6.0 c` | `-2.0 c` | `0.0 c` |

*(Time-averaged over the first 10–70 ms, this gives ~`-18 c` on C2 and ~`-8 c` on A2, closely matching dry 200A recordings.)*

---

## 5. Electrostatic Comb Pickup Nonlinearity

### 5.1 Closed-Form Comb Capacitance
A Wurlitzer reed vibrates vertically through a fixed metal comb slot of half-height $a$ and lateral reed-to-comb gap $g$. Modeling the fringing capacitance between the reed tip at vertical position $z = x(t) + z_0$ and the comb plate spanning $[-a, +a]$ yields the closed-form electrostatic capacitance:

$$C(z) = \arctan\!\left(\frac{z + a}{g}\right) - \arctan\!\left(\frac{z - a}{g}\right)$$

with geometry constants **$a = 1.0$, $g = 0.45$**, and nominal resting offset **$z_0 \approx 0.90$** (near the upper edge of the comb slot, where the linear sensitivity $|C'(z_0)|$ is maximized!).

Its spatial derivative is:

$$\frac{dC}{dz} = \frac{g}{g^2 + (z + a)^2} - \frac{g}{g^2 + (z - a)^2}$$

### 5.2 Even-Symmetry Factorization & Cubic Hermite Evaluation
Just like the Rhodes magnetic flux, $C(z)$ is **strictly even** in $z$ ($C(-z) = C(z)$), so it can be written identically as a function of squared displacement $s = z^2 \ge 0$:

$$\tilde{C}(s) = C\!\left(\sqrt{s}\right), \qquad \frac{d\tilde{C}}{ds} = \begin{cases} \dfrac{C'(\sqrt{s})}{2\sqrt{s}}, & s > 0 \\[8pt] -\dfrac{2ag}{(g^2 + a^2)^2}, & s = 0 \end{cases}$$

This allows the Wurlitzer to reuse the **exact same Cubic Hermite spline kernel** as the Rhodes. Tabulating $\tilde{C}(s)$ over physical $s \in [0, 8.0)$ with $N = 256$ uniform knots yields a maximum relative interpolation error of $7.7 \times 10^{-9}$ ($-162\text{ dB}$).

### 5.3 Pickup Output & Why Wurlitzer Bark Is Odd-Harmonic Dominant
At constant bias voltage $V_b$ through a high-impedance preamp load, the induced AC output current/voltage is proportional to $\frac{dC}{dt}$, discretized as the exact first difference:

$$y_{\text{pu}}[n] = \tilde{C}\!\left((x[n] + z_0)^2\right) - \tilde{C}\!\left((x[n-1] + z_0)^2\right)$$

**Physical intuition for the Wurlitzer "bark":**
- Because the reed rests right near the inflection point $z_0 \approx 0.90 \approx a$ of $\arctan((z-a)/g)$:
  - **Soft hits ($|x| \ll g$)**: The reed stays in the linear inflection zone; $C'(z)$ is locally symmetric around $z_0$, producing a clean, slightly reedy fundamental.
  - **Medium & hard hits ($|x| \sim 0.5\text{--}1.45$)**: The reed swings symmetrically *past* the steep inflection slope on **both** positive and negative half-cycles (flattening out both above $+a$ and inside the flat plateau near $z = 0$). Symmetric soft-clipping of the slope $C'(z)$ generates **strong odd harmonics ($H_3 > H_2$, $H_5 > H_4$)** with a characteristic $H_4$ dip—the exact signature Wurlitzer "growl / bark" (as opposed to the Rhodes's asymmetric even-dominant $H_2$ bark).

### 5.4 Register Excursion Targets & Loudness Calibration
Target `v = 127` peak `V1` tip excursion $x_{127}(k)$ (in comb units) is linearly interpolated across the keyboard:

| MIDI Key | 21 (A0) – 48 (C3) | 60 (C4) | 72 (C5) | 84 (C6) | 108 (C8) |
|---|---:|---:|---:|---:|---:|
| **$x_{127}(k)$** | `1.45` | `1.15` | `0.75` | `0.35` | `0.25` |

Per-band excursion/gain/level tables (`PDRV`, `PGF`, `PLEV`) shape velocity dynamics smoothly across the 10 velocity anchors, and per-key gain normalizes `v = 100` single-note RMS (50–300 ms) to **$-32.0\text{ dBFS}$** (matching Rhodes Stage 77 loudness within $0.1\text{ dB}$) with a $-6.0\text{ dBFS}$ single-note peak ceiling.

---

## 6. Damper Release & Key-Off Reed Twang

1. **Damper crossfade**: On `NoteOff` (sustain pedal up), `V1`'s $(E_1, \varepsilon_1)$ crossfades to its short damper-on decay $T_{60,\text{rel}}(k)$ (`0.28 s` in bass down to `0.06 s` in treble), scaled by release velocity and half-pedal position.
2. **Key-off reed twang**: After a short damper-seat delay (~12–16 ms), an impulse scaled by `NOISE`, release velocity, half-pedal weight, register ($k < 84$), and instantaneous `V1` amplitude is injected into `O1` (`~6.0x f0`) at **`1.33x` the Rhodes relative strength** (capturing the snappier Wurlitzer reed-damper click/twang), accompanied by 8 ms of low-passed felt brush noise. At `NOISE = 0`, key-off twang and felt noise are completely silent.

---

## 7. Preamp RLC Load & Speaker Cabinet Voicing (140B vs. 200A)

All active voices sum into a 2nd-order low-pass RLC stage (preamp input capacitance + high-frequency roll-off) followed by two **Audio EQ Cookbook biquads** modeling the distinct amplifier and onboard speaker responses of the **140B** and **200A**:

| Parameter | **Wurlitzer 140B (`W140`)** | **Wurlitzer 200A (`W200`)** |
|---|---:|---:|
| **Comb Offset Shift $\Delta z_0$** | `-0.07` *(deeper inside comb: warmer, more `H2`)* | `+0.02` *(right on comb edge: maximum `H3` bark)* |
| **Excursion Scale (`bark`)** | `0.90` *(rounder, gentler growl)* | `1.12` *(aggressive 200A bite)* |
| **Hammer Rate Scale (`ham`)** | `0.85` *(softer vintage felt)* | `1.10` *(crisper attack)* |
| **Overtone Scale (`bell`, `O1`)** | `0.85` | `1.15` *(pronounced reed clank)* |
| **Preamp RLC Low-Pass `(f_c, Q)`** | `3800 Hz, Q 0.85` *(warm roll-off)* | `5800 Hz, Q 1.15` *(open, slightly resonant)* |
| **Body EQ Stage 1** | Peak `220 Hz, Q 0.75, +2.5 dB` *(wooden cabinet warmth)* | Low Shelf `180 Hz, Q 0.70, -2.0 dB` *(tight small-speaker bass)* |
| **Body EQ Stage 2** | High Shelf `4200 Hz, Q 0.70, -3.5 dB` *(smooth top)* | Peak `1800 Hz, Q 0.85, +3.0 dB` *(classic 4×8" speaker nasal bite)* |

### Representative Acoustic Output (`W200`, Neutral Knobs)

| Note | Soft (`v40`) `H2 / H3 / H5` (dB re `H1`) | Mid (`v88`) `H2 / H3 / H5` | Hard (`v127`) `H2 / H3 / H4 / H5` | Sustain `T60` | Attack Dip (`v127`) |
|---|---:|---:|---:|---:|---:|
| **C2 (36)** | `-18.3 / -19.2 / -43.4` | `-13.5 / -3.2 / -11.6` | `-7.5 / +2.9 / -25.3 / -3.4` | `13.1 s` | `-18 c` *(peak `-28 c`)* |
| **A2 (45)** | `-18.4 / -20.5 / -45.7` | `-13.6 / -4.3 / -13.4` | `-7.6 / +1.8 / -25.6 / -5.2` | `11.4 s` | `-8 c` *(peak `-16 c`)* |
| **C4 (60)** | `-22.6 / -27.2 / -55.8` | `-16.8 / -9.7 / -20.5` | `-13.9 / -3.5 / -21.0 / -9.5` | `7.7 s` | `-2.2 c` |
| **C6 (84)** | `-33.5 / -53.2 / -109.6` | `-23.8 / -33.1 / -69.4` | `-18.4 / -20.2 / -35.5 / -43.4` | `2.4 s` | `0.0 c` |
