# Frodhes-Bark Architecture & Implementation Plan

## 1. Project Overview & Hard Platform Rules

**Frodhes-Bark** is a fork of **Frodhes** (v0.9.13 baseline), a physically modeled electric piano (Rhodes & Wurlitzer), vibraphone, and marimba firmware targeting the **M-VAVE FM-1** (JieLi AC791N, dual-core `pi32v2`, no FPU).

### Hard Platform Rules (Enforced by `tools/build.py`)
1. **Integers Only in Firmware**:
   - No `float`, `double`, `_Complex`, or `_Imaginary` in any firmware translation unit (`tools/build.py` runs `float_check` on the preprocessed `firmware/src/frodhes.c`).
   - No 64-bit `/` or `%` (`__udivdi3` / `__divdi3` do not exist in the runtime; linking fails). 64-bit multiplication (`int64_t` / `uint64_t`) and bit shifts are fine; 32-bit `/` and `%` are fine (always guard against zero divisors, as divide-by-zero does not trap on `pi32v2`).
2. **No Chip-ROM Window Constants (`0xFFC00000–0xFFCFFFFF`)**:
   - In 32-bit two's-complement signed integers, `0xFFC00000..0xFFCFFFFF` equals `-4194304 .. -3145729`.
   - Neither instructions nor table words may reference or contain a value in this window (`tools/build.py` scans disassembly and generated tables). Always use `rom_safe()` and `rom_window_b()` in Python generators.
3. **Hardware Register Access in `hal/` Only**:
   - `tools/build.py` (`mmio_check`) forbids raw MMIO addresses, `volatile` pointer casts (except `*(volatile T *)&x` on a C object), inline assembly, or HAL register macros in `firmware/src/` and `firmware/loader/`.
4. **Single-Compilation-Unit App & Two-Core FM-1 Layout (`./build.sh --c1` -> `build/frodhes-c1.fwsc`)**:
   - `firmware/src/frodhes.c` is the single translation unit (`#include`s `platform.c`, `engine.c` -> `rhodes.c` + `bars.h`, `fx.c`, `rev_types.c`, `params.c`, `keyvel.c`, `tempo.c`, `arp.c`, `notes.c`, `seq.c`, `ui.c`, `presets.c`, `bars.c`).
   - Do not modify `platform.c`, `hal/`, `loader/`, or `usb.c` unless strictly necessary.
   - Core 0 renders up to 14 voices; Core 1 runs the effects chain and full-rate reverb from XIP flash (`FX_RAM` is empty in `--c1`).
   - 192 hot functions are pinned in `firmware/c1_hot.txt` starting at `0x02001400` (offset `0x12E0` after `app_audio`) so Core 0 and Core 1 do not evict each other in the shared 32 KB 8-way XIP flash cache.
   - Do not rename or remove functions listed in `firmware/c1_hot.txt` without updating `c1_hot.txt`, and keep `app_audio` within its `0x12E0` byte budget.
   - Innermost loop budgets enforced by `tools/build.py`:
     - Rhodes MC3 mode loops (`rh_mode_f1_l`, `rh_mode_f`, `rh_mode_p1_l`, `rh_mode_p`): `<= 32` instructions, `0` stack stores.
5. **Settings & Presets Compatibility (`firmware/src/params.c`, `firmware/src/presets.c`)**:
   - `settings_t` is fixed at `256 B` (`SET_VER 2`).
   - Parameter IDs (`P_*` in `params.c`) and enum values (`N_TYPE`, etc.) are **append-only** so stored records and presets remain valid.
   - Flash writes only occur when audio is silent (`eng_idle() && fx_quiet()`).
   - Package identity is always `FM-1_958`.

---

## 2. Baseline Resource Budget (v0.9.13 `--c1`)

- **XIP Flash (`APP_SLOT`)**: `564,736 B` used of `581,564 B` (`0x8DFBC`) -> **`16,828 B` (~16.4 KB) free**.
- **RAM (`.data + .bss`)**: `92,492 B` used of `98,304 B` (`96 KiB` limit) -> **`5,812 B` free**.
- **Pool (`.pool`)**: `331,692 B` used of `344,064 B` -> **`12,372 B` free**.
- **RAMTEXT (`.ram_text + .ramx`)**: ends at `0x01C071C0` of `0x01C08000` (`.ramx` = `22,272 B`) -> **`3,648 B` free**.

---

## 3. Phased Engineering Plan

### Phase 0: Reclaim Unused 5th Mode Slot (`RH_NM`: 5 -> 4)

#### Objective
In `firmware/src/rhodes.c`, `rh_key_t` allocates 5 mode slots (`m[5]`: `V1, V2, O1, O2, H1`). Slot `m[4]` (`H1`, an unimplemented Phase 3 horizontal-polarization placeholder) is `{0,0,0,0,0,0,0}` across all 88 keys in `firmware/gen/rhodes_tables.h`. Dropping `RH_NM` from `5` to `4` is 100% bit-identical in audio output while reclaiming:
- **2,464 B of XIP flash** in `RH_KEYS[88]` (88 keys x 7 `int32_t` words x 4 B), lifting free XIP flash from `16,828 B` to **~`19,292 B`**.
- **2,816 B of `.ramx`** in the note-on coefficient cache `rh_cc[2][88][RH_NM]` (2 x 88 x 16 B).
- **616 B of `.bss` RAM** in `rv[14].m[RH_NM]` (14 voices x 44 B per `rh_ms_t`).

#### Exact File & Symbol Changes
1. **`tools/gen_rhodes.py`**:
   - Line 37: Change `NM = 5                      # V1 V2 O1 O2 H1` to `NM = 4                      # V1 V2 O1 O2`.
   - Line 225: In `key_physics()`, remove `modes.append(dict(name="H1", f=0.0, t60=1.0, t60_rel=1.0, amp=0.0))      # Phase 3`.
2. **`firmware/src/rhodes.c`**:
   - Line 34: In `typedef struct { rh_mode_t m[5]; ... } rh_key_t;`, change `rh_mode_t m[5];` to `rh_mode_t m[4];` (or move `#define RH_NM 4` / include `rhodes_tables.h` alignment so `m[4]` matches `RH_NM`).
   - Line 511 (comment): Update `2 x 88 x 5 x 16 B in the pool` to `2 x 88 x 4 x 16 B in the pool`.
3. **`tests/py/test_rh_coef.py`**:
   - Lines 55, 62–65: In `modes()`, change `for m in range(5):` to `for m in range(4):` and mode names `['V1', 'V2', 'O1', 'O2'][m]`.
4. **`tests/py/test_rhodes_hq.py`**:
   - Line 163: In `test_dense_table_equals_the_layers()`, change `typedef struct { rh_mode_t m[5]; ... } rh_key_t;` to `rh_mode_t m[4];`.
5. **`tools/pulse_codec_study.py`**:
   - Lines 549–552: Update `RH_KEYS` word count `nf` from `42` to `35` and `raw` byte size from `14784` to `12320`.
6. **Table Regeneration**:
   - Run `python3 tools/gen_rhodes.py` to regenerate `firmware/gen/rhodes_tables.h` and `docs/model/data/rhodes_spec.json`.

#### Phase 0 Verification
- `python3 tools/gen_pulse_pz.py --check` and `python3 tools/gen_phim.py --check` must pass without needing regeneration.
- `./build.sh --c1` must pass all build checks and show:
  - `image` reduced by ~2,464 B (from `564,736 B` to `~562,272 B`).
  - `RAM .data+.bss` reduced by `616 B` (from `92,492 B` to `91,876 B`).
  - `--c1: .ramx` reduced by `2,816 B` (from `22,272 B` to `19,456 B`).
- `make test QUICK=0` (all 79 host scripts) must pass.
- `.local/venv/bin/python -m pytest -q tests/py/test_rh_coef.py tests/py/test_rhodes_hq.py tests/py/test_gate_b.py` must pass.

---

### Phase 1 (Goal 1): Restore and Enhance Rhodes Bark (Especially A0–E3, Keys 21–52)

#### Root Cause Summary
In Frodhes, Rhodes bark comes from the tine tip displacement $x(t)$ swinging across the pole axis ($\text{off} = 0$) of the radial magnetic flux lookup table $\tilde{\Phi}(s)$ (`tools/pickup.py`, `rh_hermite_u` in `firmware/src/rhodes.c`), where $s = (x + \text{off})^2$. When the swing crosses the axis, the flux folds, saturating $H_1$ and throwing energy into $H_2\text{--}H_5$.

Currently, the Rhodes models have too little bark—especially in the bass and low-mid (A0 to C3/E3, keys 21–52)—because of five compounding factors:
1. **`pickup_drive` is bypassed in `docs/model/data/params.json`**:
   - Commit `86ced058` implemented an analysis-by-synthesis excursion solver (`pickup_drive` in `tools/gen_rhodes.py`) fitted to real Rhodes data (`soft_bark_points = -20 dB`, `knee_points = v58` on keys 21–52, `slope_db_per_vel = 0.6`, and fundamental saturation `h1_sat_points = [[21, 3.0], [50, 3.0], [57, 6.0], [62, 6.0], [76, 14.5]]`).
   - Commit `4370bb25` set `"bark_physical": true` and `"h1_sat_points": []`, disabling the bark targets and reverting to the linear physical hammer force ratio.
2. **High-velocity clamp in `tools/rh_top.py` (`firmware/gen/rh_top.h`)**:
   - `rh_top.py` generates `RH_TOP_R[4][88]`, which cuts tine swing at high velocities so $H_2/H_1$ and attack $aH_2/H_1$ at `v127` never exceed `TARGET = -14.0 dB` (`-18.0 dB` on SUITCASE). On a real barking low Rhodes note, $H_2$ and $H_3$ reach $-6\text{ to }0\text{ dB}$ re $H_1$ on attack.
3. **Conservative bass pickup voicing (`voicing.h2_v100_points` in `params.json`)**:
   - `gen_rhodes.voice_pickups()` calibrates each key's mode force-to-displacement scale `b` at `v = 100` to `-20.0 dB` at key 21 and `-18.0 dB` at key 40 (vs `-13.0 dB` at key 52 and `-11.0 dB` at key 72), keeping bass tip swing 7–9 dB below midrange relative to the pickup threshold.
4. **Wide-gap pickup family 0 on keys 21–32 (`pickup.family0_keys` in `params.json`)**:
   - Keys 21–32 use the $1/16''$ gap family (`R = 0.5`, rest offset halved to `-0.15` gaps).
5. **Post-hammer `pamp` clamp at `32767` in `firmware/src/rhodes.c` (`rh_load_pulse`)**:
   - Line 840 clamps `v->pamp` to `32767` after `HAMMER * ty->ham` scaling instead of `65535`, preventing `BELL DYNO` (`ham = 1.2`) and `HAMMER > 64` from driving the tine harder than `STAGE 77` at `v127`.

#### Exact Changes for Phase 1
1. **`docs/model/data/params.json`**:
   - **`pickup.family0_keys`**: Remove `[21, 32]` so bass keys `21–32` use Family 1 ($1/32''$ gap, `R = 1.0`, full `-0.30` rest offset). (Also evaluate unifying `[96, 108]` onto Family 1 so Rhodes uses a single pickup table in `rhodes_phim.h`, freeing `16,392 B` of flash for the Wurlitzer pickup table in Phase 2.)
   - **`voicing.h2_v100_points`**: Lift bass and low-mid `v100` pickup voicing from `[[21, -20.0], [40, -18.0], [52, -13.0], [72, -11.0], [103, -16.0], [108, -16.0]]` to `[[21, -15.5], [36, -14.0], [52, -12.0], [72, -11.0], [103, -16.0], [108, -16.0]]`.
   - **`pickup_drive`**:
     - Set `"bark_physical": false`.
     - Restore fundamental saturation `"h1_sat_points": [[21, 3.5], [50, 3.5], [57, 6.0], [62, 6.0], [76, 14.5]]`.
     - Set `"knee_points": [[21, 54.0], [52, 56.0], [57, 64.0], [64, 72.0], [69, 90.0], [76, 108.0], [84, 132.0]]` and `"slope_db_per_vel": 0.65`, while keeping `"soft_bark_points"` at `-20.0 dB` on keys 21–52 so soft playing (`v < 55`) stays warm and mellow.
2. **`firmware/src/rhodes.c`**:
   - In `rh_load_pulse()` (around line 840): allow `v->pamp` under `RH_DRIVE` (`!RH_IS_MAP(v)`) to reach `65535` after `HAMMER * ty->ham` scaling (`int32_t pmax = RH_IS_MAP(v) ? 32767 : 65535; if (v->pamp > pmax) v->pamp = pmax;`).
3. **`tools/rh_top.py`**:
   - Replace the flat `TARGET = -14.0` ceiling below `CAP_K0 = 72` with a register-dependent target curve `TOP_POINTS = [[21, -3.5], [40, -4.0], [52, -5.0], [64, -10.0], [72, -14.0]]` so bass/low-mid keys (`21–52`) can reach $-5\text{ to }-3.5\text{ dB}$ $H_2/H_1$ on hard `v127` strikes while keys $> 72$ remain strictly governed by `CAP_POINTS` (`-30` to `-35 dB`) for a clean, non-glassy treble.
   - Set `TYPE_D = (0.0, -4.0, -2.5, +1.5)` so `BELL DYNO > STAGE 77 > SUITCASE > EARLY FELT`.
4. **Regenerate Dependent Tables in Order**:
   1. `python3 tools/gen_rhodes.py` (`firmware/gen/rhodes_tables.h`, `docs/model/data/rhodes_spec.json`)
   2. `python3 tools/gen_phim.py` (`firmware/gen/rhodes_phim.h`)
   3. `python3 tools/gen_pulse_pz.py` (`firmware/gen/pulse_pz.h`)
   4. `python3 tools/rh_top.py --write` (`firmware/gen/rh_top.h`)
   5. `python3 tools/drive_makeup.py --write` (`firmware/gen/rh_drive_mk.h`)
   6. `python3 tools/tone_level.py --write` (`firmware/gen/rh_tone_g.h`)

---

### Phase 2 (Goal 2): Add Physically Modeled Wurlitzer (140B & 200A) In-Place

#### Architecture & Flash Budget Strategy (~12–15 KB Total Footprint)
- **Shared Runtime Engine in `rhodes.c`**:
  - Wurlitzer voices use the **exact same modal resonator (`MC3`) + cubic-Hermite pickup lookup (`rh_hermite_u`) + post-mix load/body biquad pipeline** in `rhodes.c`.
  - No standalone 100 KB engine duplication; zero new hot loops in the 32 KB XIP instruction cache.
- **Physics & Table Differences for Wurlitzer**:
  1. **Reference Analysis (`tools/wurli_ref_analyze.py`)**:
     - Measure `.local/wurli/200a/Long notes/{Low,Mid,High} vel/` (octaves 2, 4, 6) to extract $H_2..H_6 / H_1$ vs. velocity, sustain $T_{60}(k)$, release $T_{60,\text{rel}}(k)$, overtone clank ratio ($f_{O1}/f_0 \approx 5.4\text{--}5.9$), and attack pitch dip (`kap < 0`). (Diagnostic fit only; no sample data embedded.)
  2. **Reed Modal Table (`88 keys`)**:
     - Clamped flat steel reed with soldered lead tuning pyramid mounted to a rigid steel reed bar (no coupled tonebar, no 2-DOF beating/whirl -> `V2` inactive).
     - 3 active modes per key: fundamental $V_1$ at $f_0$ (with negative `kap` amplitude-to-pitch dip on hard attacks) and 1–2 inharmonic overtone/clank modes ($O_1, O_2$ at $\sim 5.2\text{--}6.0 \times f_0$ with fast decay).
  3. **Electrostatic Comb Pickup Table**:
     - 1-D Hermite pickup table in `RH_PHIM` modeling the rectangular reed swinging into and out of the biased electrostatic pickup plate slot.
     - With positive rest bias $\text{off}_0 > \max(|x|)$, $u = x + \text{off}_0 > 0$ so $s = u^2$ is a smooth, strictly monotonic coordinate on $(0, 32)$, allowing `rh_hermite_u` to evaluate the asymmetric slot capacitance $\tilde{C}(s) = C(\sqrt{s})$ with **zero code changes to `rh_hermite_u`**.
  4. **Band-Grouped Wedge-Felt Hammer Pulses (~2.5 KB)**:
     - Group Wurlitzer hammer pulses into 6 register bands x 8 velocity layers packed into `pulse_pz.h` (`WU_PZB`), with per-key/per-band excursion, gain, level, and contact-rate laws.
  5. **140B vs. 200A Voicings (`rh_type_t`)**:
     - **140B (`W140`)**: Heavier 1960s reed voicing, slightly softer felt hammer contact (`ham < 1.0`), warmer pickup offset/load, and single 8" speaker cabinet `body` biquads.
     - **200A (`W200`)**: 1970s reed voicing fitted to `.local/wurli/200a/`, tighter pickup slot excursion (`bark > 1.0`), faster hammer attack (`ham > 1.0`), brighter solid-state preamp presence + twin 4"x8" speaker `body` biquads.
  6. **UI / Params / MIDI (`params.c`, `engine.c`, `eng_pages.h`, `frodhes.c`)**:
     - Append `W140` (`"WURLI 140B"`) and `W200` (`"WURLI 200A"`) at indices 6 and 7 in `N_TYPE` / `N_TYPE_LONG` (`N_TYPE_N = 8`, `PARAMS[P_TYPE].hi = 7`, `TYPE_ENGINE = {0, 0, 0, 0, 1, 1, 0, 0}`, `TYPE_SUB = {0, 1, 2, 3, 0, 1, 4, 5}`) while keeping `SET_VER 2` settings and preset records 100% compatible.
