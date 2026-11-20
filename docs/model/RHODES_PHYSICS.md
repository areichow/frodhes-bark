# Rhodes Electric Piano Physical Model — Mathematical Specification

This document specifies the complete mathematical and algorithmic model used in **Frodhes-Bark** for all four Rhodes electric piano variants:

- **Stage 77** (late-1970s Mark I Stage 73/88, direct harp output)
- **Suitcase** (Suitcase 73/88 through Peterson preamp and 4×12" closed cabinet)
- **Early Felt** (1970–71 felt-tip hammers, mellow off-axis voicing)
- **Bell Dyno** (Dyno-My-Piano active treble-boost preamp and close, bell-forward pickup voicing)

All four models share the **exact same physical architecture and equations**; they differ only in physical voicing parameters (pickup offset, tip-to-pickup distance, hammer tip compliance, overtone balance, pickup RLC resonance, and preamp/cabinet EQ). Everything below is written in language-agnostic mathematics suitable for implementation in any language (Python, Rust, C++, TypeScript/AudioWorklet, Swift, FAUST, etc.).

---

## 1. Signal Flow Overview

Each polyphonic voice models one struck tine–tonebar assembly and its individual magnetic pickup; all active voices sum into a shared passive pickup load and cabinet/preamp voicing stage:

```
MIDI Note (k, v_in)
  │
  ▼
[1. 6-Point Piecewise-Linear Velocity Curve]  ──► v ∈ [1, 127]
  │
  ▼
[2. Hammer–Tine Contact Force Pulse F(t)]     (Hunt–Crossley nonlinear viscoelastic contact)
  │
  ▼
[3. 4-Mode Coupled Modal Resonator (MC3)]     (V1 fundamental, V2 whirl/tonebar, O1, O2)
  │   ├── Amplitude-dependent pitch-up (+κ a²)  (Duffing geometric tension stiffening)
  │   └── Damper-felt crossfade & O1/O2 release twang on NoteOff
  │
  ▼  Total tine tip displacement x(t) = Σ x_m(t)  (in pickup-gap units)
[4. Radial Magnetic Flux Pickup Φ̃(s)]         (Closed-form pole flux + Cubic Hermite spline)
  │   y[n] = Φ̃(s[n]) − Φ̃(s[n−1])             (Faraday's law: induced EMF ∝ dΦ/dt)
  │
  ▼  Voice gain, excursion makeup, dynamics stage, attack lift, optional stereo spread
  ├── + Mechanical noises (felt brush, key-down knock, pedal rail thud)
  ▼
[5. Shared Pickup RLC Load + Amp/Cabinet EQ]  (2nd-order biquad cascade)
```

**Units and conventions:**
- Sample rate $f_s = 44{,}100\text{ Hz}$, sample period $T = 1/f_s$.
- MIDI key $k \in \{21, \dots, 108\}$ (A0 to C8), equal-temperament fundamental frequency:
  $$f_0(k) = 440 \cdot 2^{(k - 69)/12}\text{ Hz}$$
- **Gap units**: All tine tip displacements $x(t)$ and pickup vertical offsets $x_{\text{off}}$ are normalized to the horizontal tine-to-pickup gap $L_{\text{hor}}$ of that register ($1.0\text{ gap} = L_{\text{hor}}$).

---

## 2. Offline Structural Physics (Per-Key Modal Parameters)

Each key's 4 resonant modes are derived offline from the mechanics of a clamped–free steel rod (the **tine**) coupled to a steel **tonebar** via a mounting bracket.

### 2.1 Cantilever Tine + Tuning Spring (Euler–Bernoulli Finite Elements)
A Rhodes tine is a slender circular steel rod of length $L(k)$ with a movable tuning spring of mass $m_s$ clamped at relative position $\xi_s = 0.65\,L(k)$. Its transverse bending vibration obeys the **Euler–Bernoulli beam equation**:

$$\rho A \frac{\partial^2 w}{\partial t^2} + E I \frac{\partial^4 w}{\partial z^4} = 0, \qquad z \in [0, L]$$

with clamped–free boundary conditions ($w(0) = w'(0) = 0$, $w''(L) = w'''(L) = 0$) and a point mass $m_s$ at $z = \xi_s L$.

Discretizing the beam into Hermitian cubic finite elements yields the generalized eigenvalue problem:

$$(\mathbf{K} - \omega_m^2 \mathbf{M}(m_s)) \boldsymbol{\phi}_m = \mathbf{0}$$

1. **Spring tuning**: Solve numerically for $m_s \ge 0$ such that the lowest eigenfrequency $\omega_1(m_s)/(2\pi)$ matches the target uncoupled tine pitch.
2. **Mode shapes**: Normalize each eigenmode to unit modal mass ($\boldsymbol{\phi}_m^\top \mathbf{M} \boldsymbol{\phi}_m = 1$) and evaluate:
   - $\psi_{c,m} = \phi_m(z_c)$ at the hammer strike point $z_c(k)$, linearly interpolated from $57.15\text{ mm}$ at key 21 to $3.175\text{ mm}$ at key 108 (clamped to $\le 0.9 L$).
   - $\psi_{t,m} = \phi_m(L)$ at the free tip (adjacent to the pickup).
3. **Overtone modes (`O1`, `O2`)**: The 2nd and 3rd cantilever bending modes sit inharmonically around $\approx 6.3\text{--}6.9\,f_0$ (`O1`) and $\approx 17.6\text{--}20\,f_0$ (`O2`), producing the characteristic metallic "bell" attack transient. Their impulse residue at the tip is proportional to $\psi_{c,m}\psi_{t,m}$. Modes above $18\text{ kHz}$ are omitted.

### 2.2 Coupled 2-DOF Tine–Tonebar System
To capture the sustain and energy exchange between the tine and the heavier tonebar, the fundamental is modeled as a **2-degree-of-freedom coupled linear oscillator** (unit tine mass $1$, tonebar mass ratio $\mu = 3.0$, coupling stiffness $k_c = k_{\text{rel}}\,\omega_t^2$ with $k_{\text{rel}} = 0.02$):

$$\mathbf{M} = \begin{bmatrix} 1 & 0 \\ 0 & \mu \end{bmatrix}, \quad
\mathbf{C} = \begin{bmatrix} 2\sigma_t & 0 \\ 0 & 2\mu\sigma_b \end{bmatrix}, \quad
\mathbf{K} = \begin{bmatrix} \omega_t^2 + k_c & -k_c \\ -k_c & \mu\omega_b^2 + k_c \end{bmatrix}$$

Writing the first-order state-space system $\dot{\mathbf{q}} = \mathbf{A}\mathbf{q} + \mathbf{B}F(t)$ with $\mathbf{B} = [0, 0, 1, 0]^\top$ and tip output $\mathbf{C}_{\text{out}} = [1, 0, 0, 0]$:

$$\mathbf{A} = \begin{bmatrix} \mathbf{0} & \mathbf{I} \\ -\mathbf{M}^{-1}\mathbf{K} & -\mathbf{M}^{-1}\mathbf{C} \end{bmatrix}$$

Eigendecomposition $\mathbf{A} = \mathbf{V}\boldsymbol{\Lambda}\mathbf{V}^{-1}$ gives two complex-conjugate pole pairs $\lambda_m = -\sigma_m \pm j\,2\pi f_m$ and tine-to-tine impulse residues $r_m = (\mathbf{C}_{\text{out}}\mathbf{V})_m (\mathbf{V}^{-1}\mathbf{B})_m$, so the tine displacement impulse response is:

$$h_{\text{tine}}(t) = \sum_{m=1}^{2} 2\,\operatorname{Re}\!\left(r_m e^{\lambda_m t}\right)$$

- **Retuning**: All frequencies are iteratively scaled so the tine-dominated mode (`V1`) lands at exact 12-TET $f_0(k)$.
- **Damper-on eigenvalues**: Adding damper-felt dissipation $\sigma_d = \frac{\ln 1000}{T_{60,\text{rel}}} - \sigma_t$ to the tine only (not the tonebar) and tracking eigenvalues continuously from $0 \to \sigma_d$ yields the exact release decay rate $\sigma_{m,\text{rel}}$ for each mode.

### 2.3 Polarisation "Whirl" Beat Partner (`V2` Slot)
On real tines, slight asymmetry between vertical and horizontal bending polarisations causes slow amplitude beating ("whirl") and double decay. Across the mid-range (`MIDI 40..84`), slot `V2` holds a beat partner mode at:

$$f_{V2} = f_0 \pm f_{\text{beat}}(k), \qquad w_{V2} \approx 0.12 \cdot \text{taper}(k), \qquad T_{60,V2} \in [0.8,\,1.1]\,T_{60,V1}$$

with $f_{\text{beat}}(k)$ rising from $0.30\text{ Hz}$ (E2) to $1.30\text{ Hz}$ (C6), capped so the instantaneous pitch excursion at beat troughs never exceeds $0.8\text{ cents}$. Outside keys 40–84, `V2` is dropped where its contribution is $< -60\text{ dB}$ (keys 21–39, 85–99) and retains the tonebar mode on top keys (100–108).

### 2.4 Register Decay & Damping Laws
Sustain $T_{60}$ of `V1` is log-interpolated between anchor keys:

| MIDI Key | 31 (G1) | 39 (D#2) | 45 (A2) | 57 (A3) | 69 (A4) | 75 (D#5) | 81 (A5) | 87 (D#6) | 96 (C7) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **$T_{60,V1}$ (s)** | 28.0 | 23.0 | 18.0 | 20.0 | 18.0 | 13.0 | 6.5 | 4.2 | 2.8 |

Above key 96, $T_{60}$ halves every 4 semitones down to a $0.8\text{ s}$ floor. Overtones decay faster by a power law:
$$T_{60,m} = \frac{T_{60,\text{ot}}(k)}{(f_m / f_1)^2}$$
Damper-on release $T_{60,\text{rel}}$ steps from $0.25\text{ s}$ ($\le 33$) → $0.18\text{ s}$ ($\le 60$) → $0.14\text{ s}$ ($\le 83$) → $0.12\text{ s}$ ($\le 108$).

Small deterministic per-key manufacturing jitter (seeded hash per key, never altering $f_0$) varies $T_{60}$ ($\pm 8\%$), gain ($\pm 0.15\text{ dB}$), pickup offset ($\pm 0.05\text{ gap}$), hammer contact rate ($\pm 3\%$), and whirl beat rate ($\pm 25\%$).

---

## 3. Hammer–Tine Contact Model (Hunt–Crossley)

Rhodes hammers use neoprene tips graded in hardness across 5 register bands (30, 50, 70, 90 Shore A durometer, and wrapped maple wood above key 85).

### 3.1 Nonlinear Viscoelastic Contact ODE
Let $x_h(t)$ be hammer position, $m_h(k)$ effective hammer mass ($11\text{ g}$ in bass, scaling down as $\min(11\text{ g},\, m_{\text{tine},1}/3)$ in treble to avoid unrealistic multi-bounces), and $x_t(t) = \sum_{m=1}^{6} \psi_{c,m} q_m(t)$ the tine displacement at the strike point. Defining tip compression $e = x_h - x_t$, the **Hunt–Crossley contact force** is:

$$F(e, \dot{e}) = \begin{cases} \max\!\left(0,\; K\,e^p\,(1 + \alpha\,\dot{e})\right), & e > 0 \\ 0, & e \le 0 \end{cases}$$

with Hertzian exponent $p = 1.5$ (neoprene cylinder/sphere contact) and viscoelastic hysteresis $\alpha = 0.2\text{ s/m}$. The coupled ODEs:

$$m_h \ddot{x}_h = -F, \qquad \ddot{q}_m + 2\sigma_m \dot{q}_m + \omega_m^2 q_m = \psi_{c,m} F \quad (m = 1 \dots 6)$$

are integrated offline with **4th-order Runge–Kutta (RK4)** at $16\times f_s$ (or up to $256\times f_s$ for stiff treble tips) from initial hammer velocity $v_h \in [v_{\max}\cdot 10^{-30/20},\; v_{\max}]$ ($v_{\max} = 4.0\text{ m/s}$).

### 3.2 Real-Time Pulse Synthesis
Rather than running a $16\times$ oversampled ODE per voice in real time, each key/band stores 8 normalized 64-sample force pulse shapes $P_{\ell}[u]$ across velocity layers (`v = 8, 24, 40, 56, 72, 88, 104, 120`) plus contact playback rate $r_p(k, v) = \frac{64}{T_c(k, v)\,f_s}$ and peak force amplitude $A_p(k, v)$. At note-on:
1. Linearly blend the two bracketing velocity layers into the voice's 64-sample pulse buffer.
2. Advance a fractional phase accumulator $\phi_p \leftarrow \phi_p + r_p$ each sample with linear interpolation to produce the instantaneous hammer force $F[n] = A_p \cdot P(\phi_p)$ until $\phi_p \ge 64$.
3. Because $T_c \propto v_h^{(1-p)/(1+p)} = v_h^{-0.2}$, harder hits produce both higher peak force and **shorter, brighter** contact pulses that excite more `O1`/`O2` bell energy.

---

## 4. Modal Resonator: Modified Coupled-Form Oscillator (`MC3`)

Each active mode $m \in \{\text{V1}, \text{V2}, \text{O1}, \text{O2}\}$ runs a **second-order damped coupled-form ("Magic Circle" / `MC3`) oscillator** with state $(x_m, y_m)$:

$$y_m[n] \leftarrow y_m[n-1] + b_m \, F[n]$$
$$x_m[n] = x_m[n-1] + E_m \, y_m[n]$$
$$y_m[n] = y_m[n] - \varepsilon_m \, y_m[n] - E_m \, x_m[n]$$

Total tip displacement at sample $n$ is the linear superposition:
$$x[n] = \sum_{m=1}^{4} x_m[n]$$

### 4.1 Exact Coefficient Mapping
Given mode frequency $f_m$, angular increment $\theta_m = 2\pi f_m / f_s$, and per-sample pole radius $r_m = e^{-\ln(1000)/(T_{60,m} f_s)}$:

$$\varepsilon_m = 1 - r_m^2$$
$$E_m = 2 \sin\!\left(\frac{\theta_m}{2}\right) \cdot \sqrt{r_m} \cdot \left(1 + \frac{(1 - r_m)^2}{8 \sin^2(\theta_m/2)}\right)$$

*(The exact eigenvalue of the `MC3` matrix has magnitude $\sqrt{1-\varepsilon_m} = r_m$ and phase $\theta_m$.)*

**Why `MC3`?**
- **Frequency–damping orthogonality**: Changing $\varepsilon_m$ (for sustain decay or damper-felt release) or $E_m$ (for pitch bend, tuning, or attack pitch shift) never causes coefficient blow-up or gain spikes, even at $27.5\text{ Hz}$ (`A0`).
- **In fixed-point implementations**: Carry first-order **error-feedback remainders** $(e_x, e_y)$ on the state updates so low-frequency bass modes decay smoothly all the way to $-120\text{ dB}$ without limit-cycle quantization noise. In 64-bit floating point (`double`), plain arithmetic suffices.

### 4.2 Amplitude-Dependent Pitch-Up (Geometric Tension / Duffing Stiffening)
A large-amplitude cantilever tine experiences nonlinear geometric stiffening, causing hard bass strikes to start slightly **sharp** and relax down to $f_0$ as the amplitude decays. Every 32-sample sub-block, `V1`'s frequency coefficient $E_1$ is modulated by instantaneous modal energy $a^2 = x_1^2 + y_1^2$:

$$E_{1,\text{eff}} = E_1 \cdot \left(1 + \operatorname{clamp}\!\left(\kappa(k)\,a^2,\; -\tfrac{1}{16},\; +\tfrac{1}{16}\right)\right)$$

with $\kappa(k) > 0$ (positive stiffening).

---

## 5. Nonlinear Electromagnetic Pickup (The Source of Rhodes Bark)

### 5.1 Closed-Form Radial Magnetic Flux
A Rhodes pickup is a single-coil cylindrical magnet pole of normalized radius $R$ positioned at horizontal gap $1.0$ from the tine tip and vertical resting offset $x_{\text{off}}$ from the pole axis.

Integrating the dipolar flux gradient across the pole face (with $u = x + x_{\text{off}}$ the total vertical displacement from the pole axis) yields the **closed-form magnetic flux**:

$$\Phi(u;\,R) = \frac{u + R}{(u + R)^2 + 1} - \frac{u - R}{(u - R)^2 + 1}$$

Notice that $\Phi(u)$ is **strictly even** in $u$ ($\Phi(-u) = \Phi(u)$), so it depends only on the squared radial distance $s = u^2 \ge 0$:

$$\tilde{\Phi}(s;\,R) = \Phi\!\left(\sqrt{s};\,R\right)$$

Two pickup geometry families cover the 88-key harp:
- **Family 0** (`1/16"` gap, extreme bass keys 21–32 & extreme treble 96–108): $R = 0.5$, resting offsets halved in gap units.
- **Family 1** (`1/32"` gap, keys 33–95): $R = 1.0$, default resting offset $x_{\text{off}} = -0.30\text{ gap}$.

### 5.2 Faraday's Law Output & Cubic Hermite Evaluation
By Faraday's law of induction, the open-circuit coil voltage is the time derivative of magnetic flux, discretized as the **exact first difference** (guaranteed zero DC drift and zero aliasing of the differentiator):

$$y_{\text{pu}}[n] = \tilde{\Phi}\!\left((x[n] + x_{\text{off}})^2\right) - \tilde{\Phi}\!\left((x[n-1] + x_{\text{off}})^2\right)$$

For fast runtime evaluation, $\tilde{\Phi}(s)$ and its analytic derivative $\frac{d\tilde{\Phi}}{ds} = \frac{\Phi'(\sqrt{s})}{2\sqrt{s}}$ are tabulated on $N = 2048$ uniform cells over $s \in [0, 32.0)\text{ gap}^2$ ($\Delta s = 1/64$) and evaluated with a **Cubic Hermite spline**:

$$H(t) = p_0 + t\Big(m_0 + t\big(c_2 + t\,c_3\big)\Big), \qquad t \in [0, 1)$$
$$c_2 = 3(p_1 - p_0) - 2m_0 - m_1, \qquad c_3 = 2(p_0 - p_1) + m_0 + m_1$$

where $p_0 = \tilde{\Phi}(s_i)$, $p_1 = \tilde{\Phi}(s_{i+1})$, $m_0 = \Delta s\,\tilde{\Phi}'(s_i)$, $m_1 = \Delta s\,\tilde{\Phi}'(s_{i+1})$.

### 5.3 How Bark Emerges Physically
- **Soft hits ($|x| \ll |x_{\text{off}}|$)**: The tine oscillates on one monotonic flank of $\tilde{\Phi}(s)$. The transfer curve is nearly linear, yielding a pure, warm fundamental ($H_2/H_1 \approx -20\text{ dB}$).
- **Hard hits ($|x| > |x_{\text{off}}|$)**: The tine tip swings **across the pole axis ($u = 0$)** where $\tilde{\Phi}(s)$ folds symmetrically. Each cycle crosses the maximum twice, saturating the fundamental $H_1$ and throwing massive energy into **$H_2, H_3, H_4, H_5$** (`+8` to `+13 dB` re $H_1$ on hard low-register Stage 73 strikes).
- **Moving the `TONE` knob** shifts $x_{\text{off}}$:
  - Lowering `TONE` moves $x_{\text{off}}$ further off-axis (down to $-0.8\text{ gap}$): rounder, more fundamental.
  - Raising `TONE` moves $x_{\text{off}}$ closer to the axis (capped at $-0.10\text{ gap}$ so $H_1$ never cancels completely): brighter, stronger even harmonics (`H2`), with velocity-blended gain compensation so loudness stays even.
- **Moving the `BARK` knob** scales the tine tip excursion $x$ by $s_{\text{bark}}$ while compensating output gain by $1/s_{\text{bark}}$, directly controlling how deeply hard strikes fold across the pole axis.

---

## 6. Excursion Calibration (`pickup_drive`) & Dynamics Stage

Because real Rhodes harps exhibit a sharp velocity "knee" (clean bell below `mf`, aggressive bark above `mf`), an offline **analysis-by-synthesis bisection solver** calibrates each key's excursion multiplier $D(k, v)$ and output gain $G(k, v)$ at 10 velocity anchors (`v = 1, 8, 24, 40, 56, 72, 88, 104, 120, 127`):

1. **Target Bark Curve (fitted to dry Stage 73 reference recordings)**:
   - Below the register-dependent velocity knee $v_{\text{knee}}(k)$ (`v48` on A0–D3, rising to `v66` at E4, `v77` at B4, `v102` at E5, no rise $\ge$ C#6), total bark $B = 20\log_{10}(\sqrt{\sum_{h=2}^5 H_h^2}/H_1)$ stays flat at $B_{\text{soft}}(k)$ (`-20 dB` bass, `-22 dB` mid, `-35 dB` treble).
   - Above $v_{\text{knee}}(k)$, target bark rises smoothly at **$0.46\text{ dB}$ per velocity step** (soft corner width $10$ velocity steps), reaching `+8.5` to `+13 dB` $H_2/H_1$ at `v127` in the bass/low-mid while treble ($\ge$ E5) remains clean ($\le -14\text{ dB}$).
2. **Fundamental Saturation & Type Ceiling**:
   - Output gain $G(k, v)$ shapes the fundamental's dynamic range above `v64` (soft-saturating by `4.0 dB` in the bass up to `14.5 dB` at E5).
   - A per-type high-velocity excursion ceiling (`TOP_POINTS + TYPE_D`) ensures strict voicing order across the four models: **Bell Dyno (`+1.5 dB`) > Stage 77 (`0.0 dB`) > Suitcase (`-2.5 dB`) > Early Felt (`-4.5 dB`)**.
3. **6-Point Piecewise-Linear User Velocity Curve**:
   - Before note-on, incoming MIDI velocity $v_{\text{in}} \in [1, 127]$ passes through a user-editable 6-point piecewise-linear map $(x_0, y_0) \dots (x_5, y_5)$ ($0 \le x_0 \le \dots \le x_5 \le 127$, output clamped to $[1, 127]$), allowing any keyboard controller to be matched to the bark knee.

---

## 7. Damper Settling, Key-Off Harmonic Twang & Mechanical Noise

### 7.1 Damper Crossfade
When a key is released (with sustain pedal up), each mode's $(E_m, \varepsilon_m)$ crossfades linearly over $N_{\text{settle}}(k, v_{\text{rel}})$ sub-blocks (longer in bass where heavy tines take longer to seat against the felt, faster for brisk release velocity $v_{\text{rel}}$, scaled by half-pedal weight $w_{\text{ped}} \in [0, 1]$) from its held value to its damper-on value.

### 7.2 Key-Off Harmonic Twang
When a real Rhodes damper felt snaps onto a vibrating tine, it chokes the fundamental `V1` while plucking the ~6.3× cantilever overtone `O1` (and subtly `O2`), creating a brief metallic "twang" (~30–60 ms):
- Triggered on `NoteOff` when pedal is up, `NOISE > 0`, and $k < 84$ (rolls off linearly from key 72 to 84).
- Delayed by ~16–22 sub-blocks (~12–16 ms, matching damper-arm travel/seat time), then injected directly into the velocity state $y$ of `O1` (and $0.35\times$ into `O2` if active):
  $$\Delta y_{O1} \propto \max(|x_{V1}|, |y_{V1}|) \cdot g_{\text{noise}} \cdot g_{\text{rel}}(v_{\text{rel}}) \cdot w_{\text{ped}} \cdot g_{\text{reg}}(k)$$
- Because $\Delta y_{O1}$ is proportional to the instantaneous `V1` amplitude and capped well below the struck peak, it never creates a "double note" and is completely absent at `NOISE = 0`.

### 7.3 Mechanical Noises
Three optional mechanical components scale with the `NOISE` parameter:
- **Felt brush noise**: 8 ms burst of low-passed white noise on key-off, scaled by remaining note energy.
- **Key-down mechanism knock**: A short filtered copy of the lowest struck key's hammer pulse (enabled on Suitcase, Early Felt, Bell Dyno; off on DI Stage 77).
- **Sustain pedal rail thud**: 10 ms one-pole low-passed ($\approx 900\text{ Hz}$) exponentially decaying noise burst when the damper rail lifts or drops.

---

## 8. Shared Output Voicing: Pickup RLC Load & Amp/Cabinet Biquads

All voices sum into a 2nd-order **low-pass RLC resonance** (modeling pickup coil inductance $L_p$ driving cable + preamp input capacitance $C_c$ and load resistance $R_L$), followed by up to two **Audio EQ Cookbook biquads** (peaking, low-shelf, or high-shelf) modeling the instrument's preamp and speaker cabinet, plus a gentle loudness trim:

$$H(z) = \frac{b_0 + b_1 z^{-1} + b_2 z^{-2}}{1 + a_1 z^{-1} + a_2 z^{-2}}$$

### Model Comparison Table (All 4 Rhodes Variants)

| Parameter | **Stage 77** | **Suitcase** | **Early Felt** | **Bell Dyno** |
|---|---:|---:|---:|---:|
| **Pickup Offset $\Delta x_{\text{off}}$ (gap)** | `0.00` | `-0.05` *(rounder)* | `-0.20` *(very mellow)* | `+0.10` *(closer to axis)* |
| **Tip Excursion Scale (`bark`)** | `1.00` | `0.80` *(cleaner)* | `0.75` *(mildest bark)* | `1.10` *(hottest bark)* |
| **Hammer Rate Scale (`ham`)** | `1.00` | `1.00` | `0.70` *(soft felt tips)* | `1.20` *(crisp attack)* |
| **Overtone Scale (`bell`, `O1`/`O2`)** | `1.00` | `0.90` | `0.45` *(muted bell)* | `1.80` *(pronounced bell)* |
| **High-Vel Bark Ceiling (`TYPE_D`)** | `0.0 dB` | `-2.5 dB` | `-4.5 dB` | `+1.5 dB` |
| **Key-Down Knock** | Off *(DI harp)* | On | On | On |
| **Pickup RLC Low-Pass `(f_c, Q)`** | `5000 Hz, Q 1.0` | `4500 Hz, Q 1.3` | `3200 Hz, Q 0.8` | `7000 Hz, Q 1.3` |
| **Body EQ Stage 1** | *(none)* | Peak `250 Hz, Q 0.7, +3.5 dB` | Peak `300 Hz, Q 0.8, +1.5 dB` | Low Shelf `150 Hz, Q 0.7, -2.0 dB` |
| **Body EQ Stage 2** | *(none)* | High Shelf `5500 Hz, Q 0.7, -5.0 dB` | *(none)* | High Shelf `3500 Hz, Q 0.7, +6.0 dB` |
| **Output Level Trim** | `0.0 dB` | `-3.2 dB` | `-5.0 dB` | `+3.5 dB` |

*(For all variants, a final DC-blocking high-pass filter removes any sub-audible offset before effects or output.)*
