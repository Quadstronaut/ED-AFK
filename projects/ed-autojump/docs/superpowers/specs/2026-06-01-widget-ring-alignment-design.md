# Widget-Ring Closed-Loop Alignment — Design Spec (v3)

Date: 2026-06-01
Status: council review (spec gate, v3)
Supersedes: v2 (in-workflow only; never committed). v3 fixes blockers F, G, H, K
and closes the coverage gaps the v2 council flagged.

---

## 1. Problem

The compass-based orient loop (`orient_compass` → `align_to_target`,
`src/ed_autojump/executor/align.py`) drives the ship until the **nav-compass**
cyan dot is centred. Three live test flights on 2026-06-01 showed
compass-centred ≠ reticle-on-target: the ship reported the dot centred but the
flight-view **target reticle** sat ~20 % of screen-height off the body. ED's
FSD-charge cone keys on the actual nose-to-target angle, not the compass, so a
compass-aligned ship still drifts out of the cone mid-spool and the charge
silently aborts.

We need an orient loop that closes on what ED actually cares about: the angle
between the ship's nose and the targeted body, measured directly on the
flight view in screen pixels.

## 2. The two on-screen objects (Locked Kinematics Contract)

This section is **load-bearing and verbatim-locked**. All three v2 council
members confirmed agreement (kinematics_agrees = 3/3). Do not paraphrase it in
code or tests — quote it.

> **The mouse widget** is the small orange dot ED renders when the HUD mouse
> widget is set to **"point"** mode. Because the bot does **not** bind mouse
> axes, the widget never moves — it stays fixed at **screen centre
> (≈ 960, 540 at 1920×1080)**. The widget **is the direction the ship is
> flying** — the nose vector projected to screen.
>
> **The orange circle** is the flight-view **target reticle**: the hollow
> orange ring ED draws around the currently *targeted* body's lead indicator.
> Because it is locked to a world object, ship **rotation moves the ring across
> the screen relative to the widget**. The ring is the *target*; the widget is
> the *reference*.
>
> Alignment = drive the ring onto the widget by rotating the ship. When the
> ring sits on the widget, the nose points at the target.

Consequences that flow from the contract (used throughout):

- The reference is **static**; the **ring** is what we measure and chase.
- **Image coordinates**: x grows right, **y grows down** (OpenCV convention).
  No pixel-y inversion anywhere in this module (unlike `compass.py`, which
  inverts because it reports "up-is-positive" offsets).
- `delta = ring_centre − widget_centre`, in raw screen pixels.

### Sign convention (derived from the contract; opus-verified in v2)

| measurement            | meaning                          | correction      |
|------------------------|----------------------------------|-----------------|
| `delta_y > 0`          | ring **below** widget            | `PitchDownButton` |
| `delta_y < 0`          | ring **above** widget            | `PitchUpButton`   |
| `delta_x > 0`          | ring **right** of widget         | `YawRightButton`  |
| `delta_x < 0`          | ring **left** of widget          | `YawLeftButton`   |

Rationale for `delta_y > 0 → PitchDown`: the ring is below the nose, so the
nose must come **down** to meet it. This is the **opposite** sign to
`compass.py`'s `offset_y` (which is pre-inverted to "up positive"). The two
modules must never share a `_correct`; widget-ring gets its own.

## 3. Scope — where this runs, and where it must NOT

Widget-ring alignment requires an **orange target reticle**, which only exists
when a body is **targeted** in the flight view. It is therefore valid only at
orient sites where a route star is locked, and **invalid** where nothing is
targeted.

| procedure          | step (index)                    | targeted body? | widget-ring? |
|--------------------|---------------------------------|----------------|--------------|
| `arrival.toml`     | 7 `orient_compass`              | next-route star (step 4) | **yes** |
| `smack_recovery.toml` | 6 `orient_compass` (escape vector) | **none** (deselected step 4) | **NO** — compass only |
| `smack_recovery.toml` | 11 `orient_compass`          | next-route star (step 9) | **yes** |

**Blocker F resolution**: the v2 spec inserted widget-ring at
`smack_recovery` step 6. That phase orients on a spawned **escape vector**,
which is a nav-compass-only construct — no body is targeted, so there is no
orange reticle and the widget-ring reader has nothing to lock. Step 6 stays
compass-based, permanently. Only steps with a locked target (arrival 7,
smack_recovery 11) may use widget-ring.

## 4. Components

### 4.1 `src/ed_autojump/vision/widget_ring.py` (new module)

cv2/numpy imports deferred inside methods, matching `cyan_reader.py`, so the
package still imports without the `[vision]` extra.

```python
from dataclasses import dataclass
from typing import Any, List, Optional

class WidgetRingResolutionError(ValueError):
    """Raised at preflight when the frame is not 1920×1080. The pixel ROIs and
    the screen-centre widget anchor are 1080p-calibrated; other resolutions
    would silently mis-locate both objects."""

@dataclass(frozen=True)
class WidgetRingRead:
    found: bool          # both widget AND ring located this frame
    widget_cx: float     # widget centre (≈960, 540); measured, not assumed
    widget_cy: float
    ring_cx: float       # target-reticle ring centre
    ring_cy: float
    ring_radius_px: float
    delta_x: float       # ring_cx - widget_cx  (image px; +right)
    delta_y: float       # ring_cy - widget_cy  (image px; +down)
    deadzone_px: float   # 0.55 * ring_radius_px

    @classmethod
    def not_found(cls) -> "WidgetRingRead":
        return cls(False, 0,0, 0,0, 0, 0,0, 0)

    @property
    def aligned(self) -> bool:
        """Ring within the deadzone of the widget on BOTH axes."""
        return (self.found
                and abs(self.delta_x) <= self.deadzone_px
                and abs(self.delta_y) <= self.deadzone_px)
```

#### WidgetRingReader

Constants (1080p):

```python
class WidgetRingReader:
    # ring search ROI — central screen band, well inside HUD chrome
    ROI_X1, ROI_Y1, ROI_X2, ROI_Y2 = 510, 240, 1410, 840      # 900×600
    # widget search sub-ROI — tight box at screen centre
    WIDGET_CX0, WIDGET_CY0 = 960.0, 540.0
    WIDGET_SUB_ROI_HALF = 60                                   # 120×120 box
    # orange in HSV (ED reticle + widget share the HUD orange)
    _ORANGE_HSV_LO = (10, 140, 140)   # H,S,V
    _ORANGE_HSV_HI = (25, 255, 255)
    # ring acceptance
    _HOUGH_MIN_R, _HOUGH_MAX_R = 18, 90
    _ANNULUS_LO, _ANNULUS_HI = 0.80, 1.20     # orange-fill band, ×r
    _ANNULUS_MIN_FILL = 0.55                  # ≥55 % of the band is orange
    _CIRCULARITY_MIN = 0.75                   # 4πA/p²; perfect circle = 1.0
    EXPECTED_W, EXPECTED_H = 1920, 1080
```

`read(self, frame) -> WidgetRingRead`:

1. **Resolution guard** (cheap, every call): if `frame.shape[:2] != (1080,1920)`
   raise `WidgetRingResolutionError`. (Also surfaced at preflight, §4.3 — the
   per-call check is a backstop, the preflight is the user-facing message.)
2. **Widget**: HSV-threshold orange inside the 120×120 sub-ROI at screen
   centre. Connected components; pick the blob whose centroid is nearest
   `(WIDGET_CX0, WIDGET_CY0)` with area ≥ 4. Its centroid is
   `(widget_cx, widget_cy)`. If none → `not_found()` (fail closed; the loop
   treats this as "no read", and the step fails closed after the budget — see
   §4.2). The widget is *required*; there is **no** assume-centre fallback,
   because a missing widget means the HUD setting is wrong and the operator
   must fix it.
3. **Ring**: HSV-threshold orange inside the 900×600 ROI. `HoughCircles`
   (dp=1.2, minDist=80, param1=100, param2=22, minRadius=18, maxRadius=90).
   For each candidate, in descending accumulator order, accept the first that
   passes BOTH gates:
   - **Annulus fill**: ≥ `_ANNULUS_MIN_FILL` of the pixels in the band
     `[0.80r, 1.20r]` are orange (confirms a *ring*, rejects a filled orange
     blob and the widget dot itself, which is far smaller than minRadius).
   - **Circularity**: take the orange contour nearest the candidate centre,
     `4π·area / perimeter²` (`cv2.contourArea` / `cv2.arcLength`) ≥ 0.75.
   Convert the accepted centre from ROI-local to full-frame coords by adding
   `(ROI_X1, ROI_Y1)`.
4. If either object missing → `not_found()`.
5. Compute `delta_x = ring_cx − widget_cx`, `delta_y = ring_cy − widget_cy`,
   `deadzone_px = 0.55 * ring_radius_px`. Return a populated `WidgetRingRead`.

**Why widget centre is measured, not assumed**: the contract says the widget is
*at* centre, but lens/HUD scaling and ultra-wide letterboxing can shift the
rendered dot a few px. We measure it so `delta` is exact; we still *search*
only the 120-px box around centre, so a stray orange pixel elsewhere can't be
mistaken for the widget.

#### Helpers (Blocker G + H resolutions)

```python
def median_of(reads: List[WidgetRingRead]) -> WidgetRingRead:
    """Field-wise temporal median over the FOUND reads in `reads`.

    Blocker-G fix: this was used in v2 pseudocode but never defined.

    - If fewer than half the reads are `.found`, return WidgetRingRead.not_found()
      (same strict-majority rule as align._measure).
    - Otherwise return a synthetic read whose widget_*, ring_*, ring_radius_px,
      delta_*, deadzone_px are the statistics.median of the found reads'
      corresponding fields. `found=True`. (Median per-field is sound here: all
      fields are continuous and the medians stay mutually consistent to within
      sub-pixel noise, which the 0.55r deadzone absorbs.)
    """

def verify_widget_rendered(reader: WidgetRingReader,
                           capture: Callable[[], Any],
                           *, samples: int = 5,
                           min_found: int = 3) -> bool:
    """Blocker-H fix: static, no-input preflight that the mouse widget is on.

    Grab `samples` frames; count how many yield a widget centroid in the
    120-px centre box (ring NOT required — this checks only the widget).
    Return True iff `found_count >= min_found`. Pure observation: presses
    nothing, so it can't perturb the ship. Used by §4.3 preflight to give the
    'enable mouse widget (point mode)' message before any orient runs."""
```

`verify_widget_rendered` lives in `widget_ring.py` next to the reader. It calls
a private `_find_widget(frame) -> Optional[tuple[float,float]]` that step 2 of
`read()` also uses, so the widget-detection logic exists once.

### 4.2 New step: `orient_widget_ring` (in `flow/steps.py`)

A drop-in alternative to `orient_compass` at the §3 sites. **Passthrough when
the feature flag is off**: if `ctx.widget_ring_enabled` is False, it delegates
to `step_orient_compass(ctx, **overrides)` unchanged — so the procedures and
their tests behave exactly as today until the flag is turned on.

```python
def step_orient_widget_ring(
    ctx, *,
    timeout_s: float = 18.0,
    settle_s: float = 0.45,
    samples: int = 3,
    gain_s_per_px: float = 0.18,     # press seconds per (|delta|/ring_r)
    min_press: float = 0.04,
    max_press: float = 0.25,
    **compass_overrides,
) -> bool:
    # Passthrough: flag off → behave exactly like orient_compass.
    if not getattr(ctx, "widget_ring_enabled", False):
        return step_orient_compass(ctx, **compass_overrides)

    # Flag on but unwired → FAIL CLOSED (never jump on a bad orient).
    if ctx.widget_ring_reader is None or ctx.frame_grabber is None:
        ctx.log("WidgetRingNoVision", {})
        return False

    from ..vision.widget_ring import median_of, WidgetRingResolutionError
    start = ctx.clock()
    iterations = 0
    while ctx.clock() - start < timeout_s:
        reads = [ctx.widget_ring_reader.read(ctx.frame_grabber())
                 for _ in range(samples)]
        read = median_of(reads)
        iterations += 1
        if not read.found:
            ctx.sleeper(settle_s)
            continue
        if read.aligned:
            ctx.log("WidgetRingAligned", {"iters": iterations,
                    "dx": read.delta_x, "dy": read.delta_y})
            return True
        _correct_widget_ring(ctx.sender, read,
                             gain_s_per_px=gain_s_per_px,
                             min_press=min_press, max_press=max_press)
        ctx.sleeper(settle_s)
    ctx.log("WidgetRingTimeout", {"iters": iterations})
    return False
```

`_correct_widget_ring` (module-private in `steps.py`, NOT shared with
`align._correct` — opposite sign convention):

```python
def _correct_widget_ring(sender, read, *, gain_s_per_px, min_press, max_press):
    """One dominant-axis micro-correction. Per-axis deadzone is read.deadzone_px.
    Press duration = clamp(gain_s_per_px * |delta|/ring_radius_px,
                           min_press, max_press)."""
    dx, dy = read.delta_x, read.delta_y
    if abs(dx) >= abs(dy):
        if abs(dx) > read.deadzone_px:
            hold = _hold_for(abs(dx), read.ring_radius_px, gain_s_per_px,
                             min_press, max_press)
            sender.press("YawRightButton" if dx > 0 else "YawLeftButton", hold=hold)
    else:
        if abs(dy) > read.deadzone_px:
            hold = _hold_for(abs(dy), read.ring_radius_px, gain_s_per_px,
                             min_press, max_press)
            # delta_y > 0 (ring below widget) → pitch DOWN. No inversion.
            sender.press("PitchDownButton" if dy > 0 else "PitchUpButton", hold=hold)
```

Dominant-axis only, like the validated compass loop: the proportional press on
the larger error each iteration, the other axis follows. `settle_s=0.45` is the
maintenance-hold cadence (shorter than the 1.4 s acquire settle because the
nudges are tiny and the FSD spool budget is short).

### 4.3 Wiring (Blocker I resolution — concrete locations)

- **`flow/context.py`** — `StepContext` gains two fields:
  ```python
  widget_ring_enabled: bool = False
  widget_ring_reader: Optional[Any] = None
  ```
  Defaults keep every existing construction site (and all current tests)
  passing unchanged.
- **`flow/dispatcher.py`** — `FlowRunner.__init__` gains
  `widget_ring_enabled: bool = False` and `widget_ring_reader=None`; stores
  them; `_make_context()` passes them into `StepContext`. (This is the single
  place a real run builds its context — confirmed by reading dispatcher.py;
  `launcher/flow.py` is the *launch* flow and is unrelated.)
- **`config.py`** — `[vision]` config gains `widget_ring_alignment: bool`
  (default **False**). The CLI/launcher that constructs the `FlowRunner` reads
  it and, when True, builds a `WidgetRingReader`, runs `verify_widget_rendered`
  at preflight, and passes both the flag and the reader to `FlowRunner`. If
  `verify_widget_rendered` returns False, preflight aborts with:
  `"mouse widget not detected — enable HUD mouse widget in 'point' mode (see ED-AFK preset) before running with widget_ring_alignment=on"`.
- **`procedures/arrival.toml`** and **`procedures/smack_recovery.toml`** — at
  the §3 sites only, the step action string changes `orient_compass` →
  `orient_widget_ring`. Because the new step passes through to compass when the
  flag is off, this rename is a no-op until the operator opts in. **Exact
  edits (Blocker J resolution)**:
  - `arrival.toml`: the single line whose comment is `# vision | 7` —
    `{ action = "orient_compass", required = true }` →
    `{ action = "orient_widget_ring", required = true }`.
  - `smack_recovery.toml`: ONLY the line commented `# vision | 11`
    (post-`target_next_route`). Leave the `# vision | 6` escape-vector line as
    `orient_compass` (per §3 / Blocker F).

## 5. Control parameters (defaults)

| param            | value     | why |
|------------------|-----------|-----|
| `timeout_s`      | 18.0 s    | acquire budget; longer than compass hold (spool starts after) |
| `settle_s`       | 0.45 s    | maintenance cadence; momentum from a ≤0.25 s nudge decays fast |
| `samples`        | 3         | 3-frame median rejects a transient orange flicker |
| `gain_s_per_px`  | 0.18      | press-seconds per normalised error `|delta|/ring_r` |
| `min_press`      | 0.04 s    | shortest reliable key tap |
| `max_press`      | 0.25 s    | cap; a maintenance nudge, never a swing |
| deadzone         | 0.55·ring_r | "widget inside the circle" — the user's stated success criterion |

Deadzone rationale: the user's success criterion is literally "get the mouse
widget **inside** the orange circle." Inside = `|delta| < ring_radius`. We use
`0.55·r` so the widget sits comfortably within the ring rather than grazing its
edge, leaving margin for the residual sway during the FSD spool.

## 6. Failure modes & fail-closed behaviour

- **Widget undetectable** → reader returns `not_found`; orient loop keeps
  trying until `timeout_s`, then returns False. A required orient returning
  False trips `on_required_fail` (retry/backoff), and the jump never fires on
  an unconfirmed orient. **No compass fallback** in the on-path loop (the flag
  being on means the operator chose widget-ring); the *passthrough* only
  applies when the flag is off.
- **Wrong resolution** → `WidgetRingResolutionError` at preflight (clear
  message) and as a per-call backstop.
- **Ring detected but widget missing** → `not_found` (both required).
- **Bind missing** (`YawRightButton` etc. unbound) → `sender.press` raises
  `KeyError`; the loop must catch it the same way `_press` does, log
  `BindMissing`, and treat the iteration as a no-op correction (continue to
  next iteration; eventual timeout → fail closed). *(Spec note: wrap the
  `_correct_widget_ring` call in try/except KeyError in the step.)*

## 7. Test plan

All synchronous, no game, no real sleeps. Fakes: a `_FakeRingReader` queuing
`WidgetRingRead`s; the shared `FakeSender`; a `clock`/`sleeper` pair like
`test_hold_alignment.py`.

### Reader unit tests (`tests/vision/test_widget_ring.py`)

1. `test_resolution_guard_raises` — a 1280×720 frame → `WidgetRingResolutionError`.
2. `test_widget_found_at_centre` — synthetic frame, orange dot at (962,539) →
   `widget_cx≈962, widget_cy≈539`.
3. `test_ring_and_widget_delta` — ring centred at (1000,600), widget at
   (960,540) → `delta_x≈40, delta_y≈60`, `deadzone_px=0.55*r`.
4. `test_orange_filled_blob_rejected` — a *solid* orange disc (no hole) fails
   the annulus-fill gate → ring not found. *(coverage gap: orange-on-orange
   false positive.)*
5. `test_star_glare_inside_ring_ignored` — bright white/orange star blob
   *inside* the ring does not break ring acceptance (annulus band is `[0.8r,
   1.2r]`, the star sits near centre, outside the band). *(coverage gap:
   star-inside-ring.)*
6. `test_widget_missing_returns_not_found` — orange ring present, no centre
   dot → `not_found` (widget required, no assume-centre).
7. `test_circularity_rejects_arc` — a 120° orange arc (partial reticle) fails
   `_CIRCULARITY_MIN` → not found.

### `median_of` tests (Blocker G coverage)

8. `test_median_of_all_found` — three reads with delta_x [38,40,42] →
   `delta_x==40` (median), `found=True`.
9. `test_median_of_minority_found` — 1-of-3 found → `not_found()` (strict
   majority). *(coverage gap: partial-dropout median.)*
10. `test_median_of_field_consistency` — medians taken per field; ring_radius
    and deadzone stay mutually consistent (`deadzone == 0.55*ring_radius` holds
    on the synthetic median read within tolerance).

### Annulus-mask math test (Blocker K fix)

11. `test_annulus_band_membership` — build the boolean annulus mask for a ring
    of `r=50` centred at `(60,60)` in a 120×120 grid with band `[0.80r, 1.20r]
    = [40, 60]`. Assert:
    - cell at distance **45** from centre (e.g. `mask[60, 105]`, dx=45,dy=0) is
      **True** (45 ∈ [40,60]).
    - cell at distance **30** (`mask[60, 90]`, dx=30) is **False** (inside inner
      radius 40).
    - cell at distance **70** (`mask[60, 130]` clipped — use `mask[60, 119]`,
      dx=59) … use a cell genuinely outside: distance **62** is impossible in a
      120-box from (60,60) on-axis (max 59); instead test the outer edge with a
      diagonal cell `mask[105, 105]` (dx=45,dy=45,dist≈63.6) is **False**
      (63.6 > 60).
    > v2's test asserted `mask[60,90] is True` with the band `[40,55]`, but
    > dist=30 is *inside* the inner radius → the assertion was wrong and would
    > fail on correct code. Fixed: in-band cell is dist=45, out-of-band cells
    > are dist=30 (inner) and dist≈63.6 (outer).

### Step tests (`tests/flow/test_orient_widget_ring.py`)

12. `test_passthrough_when_flag_off` — `widget_ring_enabled=False` → calls
    `orient_compass` path; with `compass_reader=None` it fails closed exactly
    like compass does. *(proves the no-op rename is safe.)*
13. `test_flag_on_no_reader_fails_closed` — flag on, `widget_ring_reader=None`
    → False, zero presses, logs `WidgetRingNoVision`.
14. `test_aligns_then_returns_true` — reader queues a not-aligned read then an
    aligned read → one correction press, then True.
15. `test_dominant_axis_yaw` — delta_x=80,delta_y=20,r=40 (deadzone 22) →
    exactly `["YawRightButton"]`.
16. `test_dominant_axis_pitch_down` — delta_x=10,delta_y=60,r=40 → exactly
    `["PitchDownButton"]` (delta_y>0, no inversion).
17. `test_deadzone_arithmetic` — delta_x=18,delta_y=15,r=40 →
    deadzone=22 → both within deadzone → `aligned` True, zero presses.
    *(coverage gap: deadzone arithmetic.)*
18. `test_timeout_fails_closed` — reader always not-aligned, clock exhausts
    `timeout_s` → False.
19. `test_bind_missing_is_caught` — `FakeSender` raising `KeyError` on
    `YawRightButton` → loop logs `BindMissing`, continues, times out False (no
    crash). *(coverage gap: BindMissing branch.)*

### `verify_widget_rendered` tests (Blocker H coverage)

20. `test_verify_widget_happy` — 4-of-5 frames yield a centre widget → True.
21. `test_verify_widget_sad` — 1-of-5 → False (would drive the preflight abort
    message). *(coverage gap: calibration happy/sad path.)*

## 8. Out of scope (deferred, not in this spec)

- Per-ship gain calibration (Task #20 — separate).
- Multi-resolution support (1080p only; guarded).
- Replacing compass orient at the escape-vector site (impossible — no target).
- Touching `align.py`'s compass loop (untouched; widget-ring is additive).

## 9. Blocker ledger (for the council)

| id | v2 blocker | v3 resolution |
|----|------------|---------------|
| A  | circularity metric undefined | §4.1: `4πA/p²` via arcLength, ≥0.75 |
| B  | calibration did jitter-press | §4.1: `verify_widget_rendered` is no-input |
| C  | StepContext flag vs reader conflated | §4.3: two distinct fields |
| D  | resolution guard unenforced | §4.1 + §4.3: error + preflight |
| E  | ogrid var-name/role mismatch | §4.1 written with explicit roles |
| F  | widget-ring on escape-vector orient | §3: that site stays compass; only targeted sites use it |
| G  | `median_of` undefined | §4.1: full signature + semantics + tests 8–10 |
| H  | `verify_widget_rendered` prose-only | §4.1: signature + location + tests 20–21 |
| I  | FlowRunner wiring unspecified | §4.3: context.py + dispatcher.py + config.py named |
| J  | TOML insertions lacked line refs | §4.3: exact step lines named, F-corrected |
| K  | test-9 annulus cell math wrong | §7 test 11: in-band dist=45, out dist=30/63.6 |
