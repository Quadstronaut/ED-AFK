# Widget-Ring Alignment — Implementation Plan (v4.1 spec)

Date: 2026-06-01
Spec: `docs/superpowers/specs/2026-06-01-widget-ring-alignment-design.md` (v4.1,
council-gated 3/3 kinematics + 3/3 pipeline, fixes W–Z + N1–N3 folded).
Method: strict TDD — write the test, watch it fail, write the code, watch it
pass. No step lands without a test asserting its contract.

## Guiding constraints (from the council, do not violate)

- **Implementation order (council Z):** `steps.py` (define + register) →
  `context.py` (3 fields) → `dispatcher.py` (3 params) → `config.py` (2 fields)
  → `capture.py` (`build_widget_vision`) → `cli.py` (build + wire) → **TOML
  inserts LAST**. The TOML reference to `orient_widget_ring` must never exist
  before the step is in `STEP_REGISTRY`, or `validate_procedure` exits 2 on
  every CLI run. Each commit leaves the tree green.
- **`build_widget_vision` returns the bound `.grab` callable (council X)** —
  `(WidgetRingReader, grabber.grab)`, never the `ScreenGrabber` object.
- **Config home is `VisionConfig` fields, TOML `[vision]` (council W).**
- **Sign convention is verbatim-locked (spec §2):** OpenCV y-down, NO inversion;
  `delta_y>0` → `PitchDownButton`, `delta_x>0` → `YawRightButton`. OPPOSITE to
  `compass.py`. `widget_ring.py` and `steps.py` widget helpers must NOT share
  `_correct`/`_press_for` with `align.py`.

## Step 0 — branch & scaffolding

Work on `master` (user convention). No new deps: cv2/numpy already gated behind
the `[vision]` extra; `widget_ring.py` defers both imports inside methods, like
`cyan_reader.py`. New test dirs already exist (`tests/vision/`, `tests/flow/`).

## Step 1 — `src/ed_autojump/vision/widget_ring.py` (new module)

Mirror `cyan_reader.py`: deferred `import cv2, numpy` inside `read()` /
`_find_widget`; module imports clean without `[vision]`.

Implement exactly as spec §4.1:
- `WidgetRingResolutionError(ValueError)`.
- `@dataclass(frozen=True) WidgetRingRead` — fields + `not_found()` classmethod
  (kwargs form) + `aligned` property (`found and |dx|<=dz and |dy|<=dz`).
- `WidgetRingReader` with the §4.1 constants (CROP_W/H=900/600, WIDGET_CX0/CY0=
  450/300, orange HSV [10,140,140]–[25,255,255], Hough r 18–90, annulus
  [0.80,1.20]×r ≥0.55 fill, circularity ≥0.75, EXPECTED_W/H).
  - `read(frame)`: (1) crop-size guard → `WidgetRingResolutionError`; (2)
    `_find_widget` or `not_found`; (3) HSV orange + `HoughCircles(dp=1.2,
    minDist=80, param1=100, param2=22, minRadius=18, maxRadius=90)`, accept first
    candidate (descending accumulator) passing BOTH annulus-fill ≥0.55 and
    circularity ≥0.75 (RETR_EXTERNAL contour nearest the Hough centre); (4)
    either missing → `not_found`; (5) compute `delta_*`, `deadzone=0.55*r`.
  - `_find_widget(frame)`: HSV orange in the 120×120 box at crop centre;
    connected components; centroid nearest centre, area ≥4; `(cx,cy)` or None.
- `median_of(reads)`: strict-majority `.found`; else field-wise
  `statistics.median` synthetic read (`found=True`).
- `verify_widget_rendered(reader, capture, *, samples=5, min_found=3)`: grab
  `samples` crops, count widget hits (ring NOT required), True iff ≥ min_found.
  Calls `reader._find_widget` (DRY with `read` step 2).

**Tests `tests/vision/test_widget_ring.py` (spec tests 1–11, 20–21):** synthetic
900×600 BGR crops drawn with cv2 (filled disc = widget, `cv2.circle` thickness>0
= ring). Skip-guard the whole module on `cv2` import like existing vision tests.
1 crop-size guard, 2 widget@centre, 3 ring+widget delta (r=50@(490,360),
widget@(450,300) → dx≈40,dy≈60,dz≈27.5), 4 filled-blob rejected, 5
glare-inside-ring ignored, 6 widget-missing not_found, 7 circularity rejects
120° arc, 8 median all-found, 9 median minority→not_found, 10 median field
consistency, 11 annulus band membership (r=50@(60,60) in 120² grid: dist45 True,
dist30 False, dist≈63.6 False), 20 verify happy 4/5, 21 verify sad 1/5.

Commit: `feat(vision): widget-ring reader (HSV orange + Hough + annulus/circularity)`.

## Step 2 — `step_orient_widget_ring` + helpers in `flow/steps.py`

Add `step_orient_widget_ring`, `_hold_for`, `_correct_widget_ring` verbatim from
spec §4.2. **Register in the existing `STEP_REGISTRY.update({...})` block** at the
bottom of `steps.py` (council FATAL-L / Z):
```python
"orient_widget_ring": step_orient_widget_ring,
```
Helpers are module-private, NOT shared with `align._correct`/`_press_for`
(opposite sign, `/ring_r` normalisation). `getattr(ctx, "widget_ring_enabled",
False)` flag gate; flag-off → `return True` (no-op, reader never touched);
flag-on + no reader/grabber → log `WidgetRingNoVision`, `return False`;
`KeyError` from `sender.press` caught INSIDE the loop → log `BindMissing`,
continue.

**Tests `tests/flow/test_orient_widget_ring.py` (spec tests 12–19):** a
`_FakeRingReader` queuing `WidgetRingRead`s; shared `FakeSender`; fake
clock/sleeper. 12 noop-true-when-flag-off (zero presses, reader never called),
13 flag-on-no-reader fails closed, 14 aligns-then-true, 15 dominant-axis-yaw
(dx80,dy20,r40 → `["YawRightButton"]`), 16 dominant-axis-pitch-down (dx10,dy60
→ `["PitchDownButton"]`), 17 deadzone arithmetic (dx18,dy15,r40 dz22 → aligned,
0 presses), 18 timeout fails closed, 19 bind-missing caught (FakeSender raising
KeyError → `BindMissing`, times out False, no crash).

Commit: `feat(flow): orient_widget_ring fine step + registration`.

## Step 3 — `flow/context.py`: 3 new StepContext fields

Append after `compass_samples`:
```python
widget_ring_enabled: bool = False
widget_ring_reader: Optional[Any] = None
widget_frame_grabber: Optional[Callable[[], Any]] = None  # centre-crop .grab
```
Defaults keep every existing construction site green. No new test (covered by
step tests 12–13 constructing StepContext with these kwargs); run the full
suite to confirm no regression.

Commit: `feat(flow): StepContext widget-ring fields`.

## Step 4 — `flow/dispatcher.py`: 3 new FlowRunner params

`FlowRunner.__init__` gains `widget_ring_enabled=False, widget_ring_reader=None,
widget_frame_grabber=None`; store on `self`; `_make_context()` passes all three
into `StepContext`. Add one dispatcher test: a `FlowRunner` built with the three
params produces a context carrying them (assert `_make_context()` fields).

Commit: `feat(flow): FlowRunner widget-ring wiring`.

## Step 5 — `config.py`: `VisionConfig` 2 new fields

Append to `VisionConfig` (after `align_samples`):
```python
widget_ring_alignment: bool = False
widget_crop: tuple[int, int, int, int] = (510, 240, 900, 600)  # x, y, w, h
```
Test: `load_config(None)` yields `cfg.vision.widget_ring_alignment is False` and
the default crop; a TOML with `[vision] widget_ring_alignment = true` round-trips.

Commit: `feat(config): [vision].widget_ring_alignment + widget_crop`.

## Step 6 — `capture.py`: `build_widget_vision(cfg)` factory

Sibling of `build_vision`. Returns `(None, None)` when flag off OR on any
exception (degrade-to-off, never raise). When on, build a `WidgetRingReader` and
a `ScreenGrabber(tuple(cfg.vision.widget_crop), backend=cfg.vision.capture_backend)`
and return `(reader, grabber.grab)` — the **bound `.grab`** (council X). It
constructs its OWN ScreenGrabber over the centre crop; never reuses the compass
grabber.

Test `tests/vision/test_build_widget_vision.py`: flag off → `(None, None)`; flag
on (monkeypatch `WidgetRingReader` + `ScreenGrabber` to dummies) → second element
is callable and identity-equals the dummy `.grab`; an exception in construction →
`(None, None)`, no raise.

Commit: `feat(vision): build_widget_vision factory (returns bound .grab)`.

## Step 7 — `cli.py`: build + verify + wire

Inside `if args.engage_keys:`, after the existing `build_vision` block, add the
spec §4.3 block: `build_widget_vision(cfg)` when `cfg.vision.widget_ring_alignment`;
`return 2` if reader/grabber None (vision unavailable) OR
`verify_widget_rendered` False (with the exact preflight message). Pass
`widget_ring_enabled/_reader/_frame_grabber` into the `FlowRunner(...)` call.
Import `verify_widget_rendered` from `.vision.widget_ring`. `sys` already imported
(used at line 361). No unit test for the CLI block (integration-tested via the
existing CLI smoke + the procedure test below); manual `--help`/`doctor` sanity.

Commit: `feat(cli): wire widget-ring fine pass behind preflight gate`.

## Step 8 — TOML inserts (LAST) + integration test

Now that the step is registered, insert into both procedures (spec §4.3):
- `arrival.toml`: after `# vision | 7` (orient_compass) →
  `{ action = "orient_widget_ring", required = true }   # vision | 8 fine`.
  `engage_jump` comment becomes `| 9`.
- `smack_recovery.toml`: after `# vision | 11` →
  `{ action = "orient_widget_ring", required = true }`. **Leave `# vision | 6`
  (escape vector) untouched** (§3 / Blocker F).

**Test 22 `tests/flow/test_orient_widget_ring.py` (or integration):**
`load_procedures(PROC_DIR)`; assert `arrival` has `orient_widget_ring`
immediately after `orient_compass`; with flag off, running the procedure presses
the SAME keys as the existing `test_arrival_aborts_without_jump_when_orient_fails`
baseline (new step no-ops). Re-run `tests/flow/test_integration.py` — must still
pass (flag defaults off → no behavioural change).

Commit: `feat(procedures): insert orient_widget_ring fine step after compass`.

## Step 9 — full-suite green + doctor

`pytest` whole suite = 0 failures. `ed-autojump doctor` exits 0. `ed-autojump`
with default config (flag off) loads procedures clean (validate_procedure passes
now that the action is registered). End-to-end: the existing arrival/smack
integration tests prove the procedures still abort-without-jump on orient
failure and the no-op insert didn't disturb the flow.

## Risk register

| risk | mitigation |
|---|---|
| TOML inserted before step registered → CLI exits 2 | Step 8 is LAST; Step 2 registers. Each commit run through `pytest -q`. |
| `build_widget_vision` returns object not `.grab` → TypeError | council X codified; Step 6 test asserts callable. |
| synthetic ring frames don't trip Hough param2=22 | tune fixture thickness/contrast until test 3 passes; params match `cyan_reader` neighbourhood. |
| annulus clip near crop edge spuriously not_found | accepted, fail-closed (spec §2.5/§6); not load-bearing for tests. |
| sign error (pitch/yaw inverted) | tests 15/16 pin exact button names; spec §2 table is the oracle. |

## Done criteria (quorum must confirm)

1. All 22 spec tests pass + full existing suite green (0 failures).
2. `validate_procedure` passes with both TOMLs referencing the registered action.
3. Flag OFF = byte-identical runtime behaviour (integration tests unchanged).
4. Council review gate: 2-of-3 `ship-it`, 0 regressions, sign convention traced.
