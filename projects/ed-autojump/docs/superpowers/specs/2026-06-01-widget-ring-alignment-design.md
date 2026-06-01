# Widget-Ring Closed-Loop Alignment — Design Spec (v4)

Date: 2026-06-01
Status: re-spin after operator correction of the capture/pipeline model.
v3/v3.1 PASSED the council (kinematics 3/3, A–K fixed, L–S folded) but assumed
a near-full-screen ROI and that widget-ring *replaces* compass orient. The
operator corrected both: **compass orient is the coarse stage that brings the
ring into the widget's neighbourhood; widget-ring is the fine close on a small
CENTRE CROP. Nothing needs full-screen vision.** v4 reworks §1–§4 and §7 around
that; §2's locked kinematics contract and the council-validated helper code are
preserved verbatim.

## 0. History

- **v3 council verdict (2026-06-01)** — sonnet-architect + sonnet-implementer +
  opus-holistic: **3/3 `kinematics_agrees`**, **3/3 `fix-blockers`**, all of
  v2's A–K independently re-verified fixed (K's annulus arithmetic recomputed by
  hand by two seats). New blockers L–S folded into v3.1 (registry-L fatal,
  `_hold_for`-M, bind-catch-N, `_find_widget`-O, circularity-P, nits Q/R/S).
- **v4 operator correction** — the capture is a centre crop, not full screen,
  and the loop is two-stage (compass coarse → widget fine, additive). New
  ledger rows T–V in §9. The council must re-gate v4 because the pipeline shape
  changed, even though the kinematics and per-frame CV are unchanged.
- **v4 council re-gate verdict (2026-06-01)** — sonnet-architect + sonnet-implementer
  + opus-holistic: **3/3 `kinematics_agrees`**, **3/3 `pipeline_agrees`**,
  **0 regressions** (A–S all held). Verdicts split 2 fix-blockers / 1 ship-it,
  all blockers wiring-spec gaps or arithmetic nits, none fatal. Folded into this
  revision as ledger rows **W–Z + N1–N3** (§9). Architect: *"architecturally
  sound and physically realisable… none of the A–S blockers regressed."*

---

## 1. Problem & approach

The compass-based orient loop (`orient_compass` → `align_to_target`,
`src/ed_autojump/executor/align.py`) drives the ship until the **nav-compass**
cyan dot is centred. Three live flights on 2026-06-01 showed compass-centred ≠
reticle-on-target: the dot read centred but the flight-view **target reticle**
sat ~20 % of screen-height off the body. ED's FSD-charge cone keys on the actual
nose-to-target angle, not the compass, so the charge silently aborts.

**Two-stage approach (operator's method):**

1. **Coarse — `orient_compass` (unchanged).** Rotates the ship until the compass
   dot is centred. This is good to ~20 % of screen height, which is enough to
   bring the **orange target reticle into the widget's neighbourhood** — i.e.
   into a small region around screen centre.
2. **Fine — `orient_widget_ring` (new, additive).** Runs *immediately after*
   compass. It looks ONLY at a centre crop (where compass already put the ring)
   and nudges the ship until the widget sits inside the ring. This closes the
   residual angle the compass can't.

Widget-ring does **not** replace compass and does **not** scan the screen — it
refines what compass already roughed in, within the widget's area. This is the
operator's stated goal from the start: *"the finer corrections should get the
mouse widget inside the orange circle we're almost aimed at."*

## 2. The two on-screen objects (Locked Kinematics Contract)

This section is **load-bearing and verbatim-locked**. All three v3 council
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
- Because the capture is a centre crop (§2.5), the static widget sits at the
  **crop's centre**. All pixel maths is crop-local — and since `delta` is a
  *difference* of two crop-local points, it is identical whether measured in
  crop coords or full-frame coords (the crop offset cancels).
- **Image coordinates**: x grows right, **y grows down** (OpenCV convention).
  No pixel-y inversion anywhere in this module (unlike `compass.py`, which
  inverts because it reports "up-is-positive" offsets).
- `delta = ring_centre − widget_centre`, in pixels.

### Sign convention (derived from the contract; opus-verified in v3)

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

## 2.5 Capture model — centre crop, not full screen (v4 correction)

Widget-ring captures a single fixed **centre-anchored crop** of the screen, big
enough to hold the ring once compass has brought it near centre, and no bigger.

- **Crop region (1080p): `CROP_W × CROP_H = 900 × 600`, centred on screen
  centre** → screen rect x∈[510, 1410], y∈[240, 840]. (This is the same band the
  v3 spec called its "ROI"; v4 makes it the actual capture, not a sub-window of
  a full-screen grab.)
- **The widget is at the crop's centre: `(CROP_W/2, CROP_H/2) = (450, 300)`**
  in crop coords. We still *measure* it (±a few px for HUD scaling) but only
  search a 120×120 box at the crop centre.
- **Why 900×600 is enough**: compass aligns to ~20 % of screen height ≈ 216 px
  of error. The crop spans ±300 px vertically and ±450 px horizontally from
  centre, so the ring **centre** (≤216 px off) always lands inside the crop
  (margin 84 px at the worst case). [council nit N1, implementer] Ring *edges*
  may clip the crop boundary at maximum radius (216 + 90 = 306 > 300), but
  `HoughCircles` detects by centre position — not edges — and the annulus fill
  stays ≥ 88 % of the band, well above the 0.55 threshold, so a real ring is
  still accepted. [council nit N2, holistic] If compass under-aligns so badly
  the ring *centre* is outside the crop, the reader returns `not_found` and the
  step fails closed (§6) — it never invents a target.
- **Annulus gate may clip near the crop edge** (accepted, fail-closed): at the
  worst case the band's far edge reaches `216 + 1.2·90 = 324 px > 300 px`
  half-height, so a few annulus rows are off-crop and the orange-fill ratio
  drops. If that ever drops a *real* ring to `not_found`, the step times out and
  `on_required_fail` re-runs compass (§6) — the intended fail-closed path, not a
  silent miss. We keep `CROP_H = 600` rather than bumping to 700: the extra rows
  buy nothing HoughCircles needs, and the clip case is already self-healing.
- **No full-screen capture, ever.** A dedicated centre-crop `ScreenGrabber`
  feeds `read()`; it is distinct from the compass-region grabber.

## 3. Scope — additive fine stage at targeted sites only

Widget-ring needs an **orange target reticle**, which exists only when a body is
**targeted**. So it is added **only** at orient sites where a route star is
locked, and **never** where nothing is targeted. It is inserted as a **new step
immediately after** the existing `orient_compass` (coarse) — `orient_compass` is
left exactly as-is.

| procedure          | existing orient (coarse)         | targeted body? | add widget-ring fine after? |
|--------------------|----------------------------------|----------------|-----------------------------|
| `arrival.toml`     | step 7 `orient_compass`          | next-route star (step 4) | **yes** — new step 8 |
| `smack_recovery.toml` | step 6 `orient_compass` (escape vector) | **none** (deselected step 4) | **NO** — escape vector has no reticle |
| `smack_recovery.toml` | step 11 `orient_compass`      | next-route star (step 9) | **yes** — new step after 11 |

**Blocker F (still honoured)**: the escape-vector orient (smack step 6) targets
no body, so there is no reticle — no widget-ring there, permanently.

## 4. Components

### 4.1 `src/ed_autojump/vision/widget_ring.py` (new module)

cv2/numpy imports deferred inside methods, matching `cyan_reader.py`, so the
package still imports without the `[vision]` extra.

```python
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

class WidgetRingResolutionError(ValueError):
    """Raised at preflight when the captured CROP is not the expected
    CROP_W×CROP_H. The widget anchor (crop centre) and the orange-ring sizing
    are 1080p-crop-calibrated; a wrong crop size silently mis-locates both."""

@dataclass(frozen=True)
class WidgetRingRead:
    found: bool          # both widget AND ring located this frame
    widget_cx: float     # widget centre in CROP coords (≈450, 300); measured
    widget_cy: float
    ring_cx: float       # target-reticle ring centre in CROP coords
    ring_cy: float
    ring_radius_px: float
    delta_x: float       # ring_cx - widget_cx  (px; +right)
    delta_y: float       # ring_cy - widget_cy  (px; +down)
    deadzone_px: float   # 0.55 * ring_radius_px

    @classmethod
    def not_found(cls) -> "WidgetRingRead":
        # keyword args (matches CompassRead.not_found convention; robust to
        # future field insertion). [council nit, architect]
        return cls(found=False, widget_cx=0.0, widget_cy=0.0,
                   ring_cx=0.0, ring_cy=0.0, ring_radius_px=0.0,
                   delta_x=0.0, delta_y=0.0, deadzone_px=0.0)

    @property
    def aligned(self) -> bool:
        """Ring within the deadzone of the widget on BOTH axes."""
        return (self.found
                and abs(self.delta_x) <= self.deadzone_px
                and abs(self.delta_y) <= self.deadzone_px)
```

#### WidgetRingReader

Constants (1080p centre crop). All ROIs are **crop-local**.

```python
class WidgetRingReader:
    CROP_W, CROP_H = 900, 600                 # the captured centre crop (§2.5)
    WIDGET_CX0, WIDGET_CY0 = 450.0, 300.0     # widget anchor = crop centre
    WIDGET_SUB_ROI_HALF = 60                   # 120×120 widget search box
    # orange in HSV (ED reticle + widget share the HUD orange)
    _ORANGE_HSV_LO = (10, 140, 140)   # H,S,V
    _ORANGE_HSV_HI = (25, 255, 255)
    # ring acceptance
    _HOUGH_MIN_R, _HOUGH_MAX_R = 18, 90
    _ANNULUS_LO, _ANNULUS_HI = 0.80, 1.20     # orange-fill band, ×r
    _ANNULUS_MIN_FILL = 0.55                  # ≥55 % of the band is orange
    _CIRCULARITY_MIN = 0.75                   # 4πA/p²; perfect circle = 1.0
    EXPECTED_W, EXPECTED_H = CROP_W, CROP_H   # the guard compares against these
```

`read(self, frame) -> WidgetRingRead` — `frame` is the **900×600 crop**:

1. **Crop-size guard** (cheap, every call): if
   `frame.shape[:2] != (CROP_H, CROP_W)` raise `WidgetRingResolutionError`.
   (Also surfaced at preflight, §4.3 — the per-call check is a backstop.)
2. **Widget**: `self._find_widget(frame)` (below). If None → `not_found()`
   (fail closed; the widget is *required*, there is **no** assume-centre
   fallback — a missing widget means the HUD setting is wrong).
3. **Ring**: HSV-threshold orange over the whole crop. `HoughCircles`
   (dp=1.2, minDist=80, param1=100, param2=22, minRadius=18, maxRadius=90).
   For each candidate, in descending accumulator order, accept the first that
   passes BOTH gates:
   - **Annulus fill**: ≥ `_ANNULUS_MIN_FILL` of the pixels in the band
     `[0.80r, 1.20r]` are orange (confirms a *ring*, rejects a filled orange
     blob and the widget dot, which is far smaller than minRadius).
   - **Circularity**: `cv2.findContours(mask, cv2.RETR_EXTERNAL,
     cv2.CHAIN_APPROX_SIMPLE)`. `RETR_EXTERNAL` returns only outer boundaries,
     so the reticle ring yields ONE contour (its inner hole is not a separate
     external contour). Pick the contour whose centroid (`cv2.moments` →
     `m10/m00, m01/m00`) is nearest the Hough candidate centre by Euclidean
     distance, then require `4π·area / perimeter²` (`cv2.contourArea` /
     `cv2.arcLength(closed=True)`) ≥ 0.75. [council spec-level, implementer]
   Centre stays in **crop coords** (no full-frame conversion — everything is
   crop-local and `delta` is offset-invariant).
4. If either object missing → `not_found()`.
5. Compute `delta_x = ring_cx − widget_cx`, `delta_y = ring_cy − widget_cy`,
   `deadzone_px = 0.55 * ring_radius_px`. Return a populated `WidgetRingRead`.

```python
def _find_widget(self, frame) -> Optional[tuple[float, float]]:
    """Private METHOD on WidgetRingReader. The single home of widget-detection
    logic — step 2 of read() AND verify_widget_rendered both call it (DRY).

    HSV-threshold orange inside the 120×120 box at the CROP centre
    (WIDGET_CX0, WIDGET_CY0); connected components; pick the blob with area >= 4
    whose centroid is nearest the crop centre. Returns (cx, cy) in CROP coords,
    or None. [council spec-level, architect]"""
```

#### Helpers (Blocker G + H resolutions; unchanged from v3.1)

```python
def median_of(reads: List[WidgetRingRead]) -> WidgetRingRead:
    """Field-wise temporal median over the FOUND reads in `reads`.
    - If fewer than half the reads are `.found`, return WidgetRingRead.not_found()
      (strict-majority rule, same as align._measure).
    - Otherwise return a synthetic read whose widget_*, ring_*, ring_radius_px,
      delta_*, deadzone_px are the statistics.median of the found reads'
      corresponding fields; found=True. (Per-field median is sound: all fields
      are continuous and stay mutually consistent to sub-pixel, which the 0.55r
      deadzone absorbs.)"""

def verify_widget_rendered(reader: WidgetRingReader,
                           capture: Callable[[], Any],
                           *, samples: int = 5,
                           min_found: int = 3) -> bool:
    """Static, no-input preflight that the mouse widget is on. Grab `samples`
    crops; count how many yield a widget centroid (ring NOT required). Return
    True iff found >= min_found. Presses nothing — can't perturb the ship.
    Drives the §4.3 preflight 'enable mouse widget (point mode)' message."""
```

`verify_widget_rendered` lives in `widget_ring.py`; both it and `read()` step 2
call `self._find_widget(frame)`, so the logic is written once.

### 4.2 New step: `orient_widget_ring` (in `flow/steps.py`)

Additive **fine** stage. It runs *after* `orient_compass` (which stays a
separate, unchanged step). **Flag off → no-op success** (compass already
oriented at the prior step; there is nothing to passthrough to).

```python
def step_orient_widget_ring(
    ctx, *,
    timeout_s: float = 18.0,
    settle_s: float = 0.45,
    samples: int = 3,
    gain_s_per_px: float = 0.18,     # press seconds per (|delta|/ring_r)
    min_press: float = 0.04,
    max_press: float = 0.25,
) -> bool:
    # Flag off → no-op success. The coarse orient_compass step already ran;
    # the fine pass is simply skipped. (NOT a passthrough — compass is its own
    # prior step now, so passing through would double-run it.)
    if not getattr(ctx, "widget_ring_enabled", False):
        return True

    # Flag on but unwired → FAIL CLOSED (never jump on a bad orient).
    if ctx.widget_ring_reader is None or ctx.widget_frame_grabber is None:
        ctx.log("WidgetRingNoVision", {})
        return False

    from ..vision.widget_ring import median_of
    start = ctx.clock()
    iterations = 0
    while ctx.clock() - start < timeout_s:
        reads = [ctx.widget_ring_reader.read(ctx.widget_frame_grabber())
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
        # Bind-missing catch lives HERE in the loop — an unbound Yaw/Pitch key
        # must log and continue to the timeout, never propagate a KeyError out
        # of the step. [council spec-level, holistic; test 19 depends on this]
        try:
            _correct_widget_ring(ctx.sender, read,
                                 gain_s_per_px=gain_s_per_px,
                                 min_press=min_press, max_press=max_press)
        except KeyError as e:
            ctx.log("BindMissing", {"action": str(e), "step": "orient_widget_ring"})
        ctx.sleeper(settle_s)
    ctx.log("WidgetRingTimeout", {"iters": iterations})
    return False
```

`_hold_for` and `_correct_widget_ring` (module-private in `steps.py`, NOT shared
with `align._press_for` / `align._correct` — opposite sign convention and a
different normalisation, `/ring_r` vs `abs(offset)`):

```python
def _hold_for(delta_px: float, ring_r: float, gain_s_per_px: float,
              min_press: float, max_press: float) -> float:
    """Proportional press seconds for a pixel error, normalised by ring radius.
    Distinct from align._press_for (normalises by abs(offset)). Guards ring_r
    against 0 so a degenerate median read can't div-by-zero."""
    return max(min_press, min(max_press, gain_s_per_px * delta_px / max(ring_r, 1.0)))


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

Dominant-axis only, like the validated compass loop: proportional press on the
larger error each iteration, the other axis follows. `settle_s=0.45` is the
maintenance-hold cadence (shorter than compass's 1.4 s acquire settle — the
nudges are tiny and the FSD spool budget is short).

### 4.3 Wiring

- **`flow/steps.py` — REGISTER THE STEP (fatal if omitted).** Immediately after
  the `step_orient_widget_ring` definition, in the same `STEP_REGISTRY.update`
  block as the other vision steps:
  ```python
  STEP_REGISTRY.update({"orient_widget_ring": step_orient_widget_ring})
  ```
  The interpreter resolves a TOML `action` through `STEP_REGISTRY`, and
  `cli.py`'s `validate_procedure(proc, known_actions=STEP_REGISTRY.keys())`
  runs at startup over every loaded TOML. An unregistered action → unknown-action
  error at load. [council FATAL, unanimous 3/3]
  - **Ordering (council B4, architect).** Register `orient_widget_ring` in
    `steps.py` **before** (or in the *same commit* as) the `arrival.toml` /
    `smack_recovery.toml` inserts. If the TOMLs reference the action before it's
    in `STEP_REGISTRY`, `validate_procedure` makes **every** CLI invocation exit
    2. The implementation order is: **steps.py (define + register) → context.py
    (3 fields) → dispatcher.py (3 params) → cli.py (build + wire) → TOML
    inserts**, so no intermediate commit is ever broken.

- **`flow/context.py`** — `StepContext` gains **three** fields:
  ```python
  widget_ring_enabled: bool = False
  widget_ring_reader: Optional[Any] = None
  widget_frame_grabber: Optional[Callable[[], Any]] = None   # centre-crop source
  ```
  Defaults keep every existing construction site (and all current tests)
  passing unchanged. (v4: the third field is the centre-crop grabber — distinct
  from `frame_grabber`, which is the compass-region crop.)
- **`flow/dispatcher.py`** — `FlowRunner.__init__` gains
  `widget_ring_enabled: bool = False`, `widget_ring_reader=None`,
  `widget_frame_grabber=None`; stores them; `_make_context()` passes all three
  into `StepContext`. (The single place a real run builds its context.)
- **`config.py` — config home, PINNED (council B1, architect).** The two new
  keys are **fields on the existing `VisionConfig` dataclass** (`config.py`,
  the `VisionConfig` near line 214) — *not* a new TOML section:
  ```python
  widget_ring_alignment: bool = False
  widget_crop: tuple[int, int, int, int] = (510, 240, 900, 600)  # x, y, w, h
  ```
  TOML key is therefore `[vision].widget_ring_alignment` (and
  `[vision].widget_crop`). Every consumer reads `cfg.vision.widget_ring_alignment`
  and `cfg.vision.widget_crop`. This pins the access path so the CLI and factory
  below are unambiguous.
- **`vision/capture.py` — `build_widget_vision(cfg)` factory** (sibling of
  `build_vision`). Returns **`(WidgetRingReader, centre_crop_grabber.grab)`**
  when `cfg.vision.widget_ring_alignment` is on, else `(None, None)`. It NEVER
  raises (missing deps → off, like `build_vision`).
  - **Returns the bound `.grab` callable, NOT the `ScreenGrabber` object**
    (council B3, holistic). `build_vision` returns `grabber.grab`; a
    `ScreenGrabber` instance is **not** callable, and every call site does
    `ctx.widget_frame_grabber()` / `capture()`. Returning the object →
    `TypeError` at the first grab. The `widget_frame_grabber` StepContext field
    holds the bound `.grab`, matching `frame_grabber`'s contract exactly.
  - **It constructs its OWN `ScreenGrabber` over `cfg.vision.widget_crop`** (the
    900×600 centre rect) — it NEVER wraps or delegates to the compass-region
    grabber from `build_vision` (council nit, architect). `WidgetRingResolutionError`
    (per-call crop-size guard) is the runtime backstop if the wrong grabber is
    ever passed in by a wiring mistake.
- **`cli.py` — exact insertion (council B2, architect + implementer).** Inside
  the existing `if args.engage_keys:` block, **after** the `build_vision(cfg)`
  call (which today sits ~line 337) and **before** the `FlowRunner(...)`
  construction (~lines 376–388), add:
  ```python
  widget_ring_reader = widget_frame_grabber = None
  if cfg.vision.widget_ring_alignment:
      from .vision.capture import build_widget_vision
      widget_ring_reader, widget_frame_grabber = build_widget_vision(cfg)
      if widget_ring_reader is None or widget_frame_grabber is None:
          print("widget_ring_alignment=on but vision is unavailable "
                "(install the [vision] extra)", file=sys.stderr)
          return 2
      if not verify_widget_rendered(widget_ring_reader, widget_frame_grabber):
          print("mouse widget not detected — enable HUD mouse widget in "
                "'point' mode (see ED-AFK preset) before running with "
                "widget_ring_alignment=on", file=sys.stderr)
          return 2
  ```
  Then add to the existing `FlowRunner(...)` call:
  ```python
  widget_ring_enabled=cfg.vision.widget_ring_alignment,
  widget_ring_reader=widget_ring_reader,
  widget_frame_grabber=widget_frame_grabber,
  ```
  (`verify_widget_rendered` is imported from `.vision.widget_ring`.)
- **`procedures/arrival.toml` / `procedures/smack_recovery.toml`** — **INSERT a
  new step** right after the targeted `orient_compass` lines (do NOT rename
  compass). With the flag off the new step is an instant no-op success, so the
  procedures behave exactly as today until opt-in. **Exact edits:**
  - `arrival.toml`: after the `# vision | 7` line insert
    `{ action = "orient_widget_ring", required = true }   # vision | 8 fine`.
    (Downstream comment indices shift by one; `engage_jump` becomes step 9.)
  - `smack_recovery.toml`: after the `# vision | 11` line insert
    `{ action = "orient_widget_ring", required = true }`. Leave the
    `# vision | 6` escape-vector line untouched (§3 / Blocker F).

## 5. Control parameters (defaults)

| param            | value     | why |
|------------------|-----------|-----|
| `timeout_s`      | 18.0 s    | fine-acquire budget before the spool starts |
| `settle_s`       | 0.45 s    | maintenance cadence; a ≤0.25 s nudge's momentum decays fast |
| `samples`        | 3         | 3-frame median rejects a transient orange flicker |
| `gain_s_per_px`  | 0.18      | press-seconds per normalised error `|delta|/ring_r` |
| `min_press`      | 0.04 s    | shortest reliable key tap |
| `max_press`      | 0.25 s    | cap; a maintenance nudge, never a swing |
| deadzone         | 0.55·ring_r | "widget inside the circle" — the operator's success criterion |
| crop             | 900×600 centre | holds the ring after compass coarse (~216 px error) |

Deadzone rationale: success = "get the mouse widget **inside** the orange
circle." Inside = `|delta| < ring_radius`; `0.55·r` keeps the widget comfortably
within the ring, leaving margin for residual sway during the spool.

## 6. Failure modes & fail-closed behaviour

- **Flag off** → the step is a no-op `True`; compass orient (prior step) is the
  whole alignment, exactly as today.
- **Widget undetectable** (flag on) → reader `not_found`; loop retries to
  `timeout_s`, then returns False → `on_required_fail` retry/backoff; the jump
  never fires on an unconfirmed orient. **No compass fallback inside the fine
  loop** — compass already ran as its own step.
- **Ring outside the crop** (compass under-aligned worse than ~300 px) →
  `not_found` → timeout → fail closed; `on_required_fail` re-runs from an
  earlier step (which re-runs compass), giving the fine pass another chance.
  **In `smack_recovery` specifically** (council nit N3, holistic): its
  `on_required_fail.retry_from = "pitch_compass"` (step 2), so a fine-step
  failure re-runs the **whole escape-vector spawn** — pitch-up, throttle,
  Supercruise press, re-target, re-orient — not just compass. This is
  intentional and unchanged from v3.1 (the prior `orient_compass` at step 11 had
  the same retry target), and is bounded by `max_retries = 3`. In `arrival` the
  retry target is the plain compass orient, so there it really does just re-run
  compass.
- **Wrong crop size** → `WidgetRingResolutionError` (preflight message + per-call
  backstop).
- **Ring detected but widget missing** → `not_found` (both required).
- **Bind missing** (`YawRightButton` etc. unbound) → `sender.press` raises
  `KeyError`; caught in the loop (§4.2), logged `BindMissing`, iteration is a
  no-op, eventual timeout → fail closed.

## 7. Test plan

All synchronous, no game, no real sleeps. Fakes: a `_FakeRingReader` queuing
`WidgetRingRead`s; the shared `FakeSender`; a `clock`/`sleeper` pair like
`test_hold_alignment.py`. Synthetic frames are **900×600 crops**.

### Reader unit tests (`tests/vision/test_widget_ring.py`)

1. `test_crop_size_guard_raises` — a 1280×720 frame → `WidgetRingResolutionError`.
2. `test_widget_found_at_crop_centre` — orange dot at (452,299) in the crop →
   `widget_cx≈452, widget_cy≈299` (near crop centre 450,300).
3. `test_ring_and_widget_delta` — ring drawn at **r=50** centred at (490,360) in
   the crop, widget at (450,300) → `delta_x≈40, delta_y≈60`, `ring_radius_px≈50`,
   `deadzone_px≈27.5` (0.55·50). Fixture pins r so the deadzone is deterministic.
4. `test_orange_filled_blob_rejected` — a solid orange disc fails the
   annulus-fill gate → ring not found.
5. `test_star_glare_inside_ring_ignored` — bright blob inside the ring (near
   centre, outside the `[0.8r,1.2r]` band) doesn't break acceptance.
6. `test_widget_missing_returns_not_found` — ring present, no centre dot →
   `not_found` (widget required, no assume-centre).
7. `test_circularity_rejects_arc` — a 120° orange arc fails `_CIRCULARITY_MIN`.

### `median_of` tests

8. `test_median_of_all_found` — delta_x [38,40,42] → `delta_x==40`, found True.
9. `test_median_of_minority_found` — 1-of-3 found → `not_found()`.
10. `test_median_of_field_consistency` — `deadzone == 0.55*ring_radius` holds on
    the synthetic median read within tolerance.

### Annulus-mask math test (Blocker K fix — arithmetic re-verified by 2 seats)

11. `test_annulus_band_membership` — annulus mask for `r=50` at `(60,60)` in a
    120×120 grid, band `[0.80r,1.20r]=[40,60]`. Assert:
    - `mask[60, 105]` (dist 45) is **True** (45 ∈ [40,60]).
    - `mask[60, 90]` (dist 30) is **False** (inside inner radius 40).
    - `mask[105, 105]` (dist ≈63.6) is **False** (>60, outer).

### Step tests (`tests/flow/test_orient_widget_ring.py`)

12. `test_noop_true_when_flag_off` — `widget_ring_enabled=False` → returns True,
    **zero presses, reader never called** (the fine pass is skipped; compass
    already ran). *(v4: replaces v3's passthrough test.)*
13. `test_flag_on_no_reader_fails_closed` — flag on, `widget_ring_reader=None`
    (or `widget_frame_grabber=None`) → False, zero presses, logs `WidgetRingNoVision`.
14. `test_aligns_then_returns_true` — reader queues a not-aligned then an aligned
    read → one correction press, then True.
15. `test_dominant_axis_yaw` — delta_x=80,delta_y=20,r=40 (deadzone 22) →
    exactly `["YawRightButton"]`.
16. `test_dominant_axis_pitch_down` — delta_x=10,delta_y=60,r=40 → exactly
    `["PitchDownButton"]` (delta_y>0, no inversion).
17. `test_deadzone_arithmetic` — delta_x=18,delta_y=15,r=40 → deadzone 22 →
    both within deadzone → `aligned` True, zero presses.
18. `test_timeout_fails_closed` — reader always not-aligned → False at timeout.
19. `test_bind_missing_is_caught` — `FakeSender` raising `KeyError` on
    `YawRightButton` → logs `BindMissing`, continues, times out False (no crash).

### `verify_widget_rendered` tests

20. `test_verify_widget_happy` — 4-of-5 crops yield a centre widget → True.
21. `test_verify_widget_sad` — 1-of-5 → False (drives the preflight abort).

### Procedure integration

22. `test_arrival_has_widget_ring_after_compass` — load `arrival.toml`; the
    `orient_widget_ring` step exists and immediately follows `orient_compass`;
    with the flag off, running the procedure presses the same keys as before
    (the new step no-ops). Confirms the insert didn't disturb the flow.

## 8. Out of scope (deferred)

- Per-ship gain calibration (Task #20 — separate).
- Multi-resolution / non-1080p crops (1080p only; guarded).
- Widget-ring at the escape-vector site (impossible — no target).
- Touching `align.py`'s compass loop or `orient_compass` (untouched; widget-ring
  is purely additive).

## 9. Ledger (for the council)

v2→v3 blockers A–K and v3 council blockers L–S are resolved and unchanged (see
§0 history + the code blocks above). v4 adds:

| id | issue (operator correction) | resolution |
|----|------------------------------|------------|
| T | v3 implied near-full-screen ROI | §2.5: capture is a 900×600 centre crop; crop-local coords; `widget_frame_grabber` |
| U | v3 *replaced* compass orient (rename) | §1/§3/§4.3: widget-ring is an **additive** fine step *after* `orient_compass`; TOML INSERTS, doesn't rename |
| V | v3 flag-off passthrough would double-run compass | §4.2: flag-off is a **no-op True**; test 12 rewritten |

### v4 council re-gate fixes (folded into this revision)

| id | issue (council) | seat | resolution |
|----|------------------|------|------------|
| W | config home for `widget_ring_alignment`/`widget_crop` unpinned | architect (plan-gate) | §4.3: **fields on existing `VisionConfig`**, TOML key `[vision]`, access `cfg.vision.*` |
| X | `build_widget_vision` return type ambiguous — object isn't callable | holistic (spec-level) | §4.3: returns **`.grab` bound callable**, not the `ScreenGrabber`; mirrors `build_vision` |
| Y | cli.py wiring insertion point + ordering vague; flag inert at runtime | architect + implementer (plan-gate) | §4.3: exact `if args.engage_keys` block + `FlowRunner` args; impl order steps→context→dispatcher→cli→TOML |
| Z | registration-vs-TOML ordering (re-flag of fatal L) | architect (spec-level) | §4.3: register in steps.py **before/same-commit** as TOML inserts |
| N1 | §2.5 crop-margin math wrong (clips 6 px at r=90) | implementer (nit) | §2.5: reworded — Hough votes on centre; annulus ≥88 % ≫ 0.55 |
| N2 | annulus band samples off-crop near edge | holistic (nit) | §2.5: acknowledged, fail-closed → compass re-run; keep `CROP_H=600` |
| N3 | §6 understated smack retry (whole escape-vector spawn) | holistic (nit) | §6: smack `retry_from=pitch_compass` re-runs full spawn, bounded `max_retries=3` |
