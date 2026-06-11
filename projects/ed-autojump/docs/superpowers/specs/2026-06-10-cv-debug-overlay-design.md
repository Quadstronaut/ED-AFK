# CV Debug Overlay — realtime "what is the bot looking at" boxes

**Date:** 2026-06-10
**Status:** DRAFT — pending operator GO, then 3-council unanimous go-gate, then implementation.
**Operator decisions (interview 2026-06-10):** hybrid hook · flash + verdict color · config.local.toml merge AND env overrides · interactive `calibrate-overlay` CLI.

## 1. Goal

Whenever any CV subsystem captures a screen region (compass, widget-ring,
nav-panel OCR, sun probe, station menu, anything future), draw an outlined
box around that region in-game via EDMCOverlay, in realtime, color-coded by
what happened. The operator (and any tester who installed from GitHub) can
SEE what the bot is looking at and whether it found what it wanted.

- **Opt-in for everyone:** ships default **OFF** (`[overlay] cv_debug = false`).
- **On by default locally:** the operator's machine flips it on via the
  non-committed `config.local.toml` (already gitignored) — no repo diff.
- **Cosmetic and fail-soft, like everything overlay:** can never block,
  slow, or crash a flight. Off ⇒ zero overhead (sink is `None`, one
  `is not None` check per grab).

## 2. Constraints (from docs/edmcoverlay-knowledgebase.md — source-verified)

| Constraint | Consequence for this design |
|---|---|
| Overlay coords are a virtual **1280×1024** canvas inset (20,40) from the game window; scale ≈ `win_w/1312`, `win_h/1042` — NOT 1:1 with screen px | Need a **ScreenToOverlay transform** (scale_x, scale_y, off_x, off_y); defaults computed from the math, refined by interactive calibration |
| `InternalGraphic.Update` patches only Text/Color/X/Y + TTL; **W/H/shape are frozen** after slot creation (§5.12) | Same-size box re-flash = in-place update (cheap). Resized box = send `ttl:0` delete, then re-create |
| TTL expiry is the native fade-out (§3.5) | Flash semantics come free: send box with `ttl≈2`, never keepalive it |
| Malformed JSON kills the connection and wipes all slots (§5.11) | Sink serializes via the same `_frame()` path as existing slots |
| Renderer is 20 FPS, foreground-only | Flash boxes only visible when ED is foreground — exactly when the operator is watching anyway |
| `shape:"rect"`: `color` = border, `fill` optional (§4.4) | Outline-only boxes (no fill) so game UI stays readable |

## 3. Components

### 3.1 `vision/debug_overlay.py` — new module

**`ScreenToOverlay`** (frozen dataclass, pure math, fully unit-testable):
`to_virtual(screen_rect) -> (x, y, w, h)` in overlay coords.
NOTE the direction: the knowledgebase §4.1 formulas describe the renderer's
forward Scale() (virtual→physical); `to_virtual` is the INVERSE —
`virtual_x = (screen_x - 20) * 1312 / (win_w - 20)`,
`virtual_y = (screen_y - 40) * 1042 / (win_h - 40)` (i.e.
`scale_x = 1312/(win_w-20)`, `scale_y = 1042/(win_h-40)`), where win_w/h
come from cfg.cv.target_resolution by default. These computed defaults are
approximate (DPI caveat, KB §5.7) — calibration refines scale/offset.
Persisted at `calibration/overlay_transform.json` — VERIFIED ignored via
root `.gitignore:70` (`projects/ed-autojump/calibration/*.json`,
`git check-ignore` confirmed 2026-06-10).

**`CvDebugSink`** — the one object everything talks to:

```
sink.box(name, screen_rect, verdict=None, label=None)
```

- `verdict=None` → **white** `#c0ffffff` ("looked, no verdict"),
  `"hit"` → **green** `#ff00cc44`, `"miss"` → **red** `#ffcc2222`.
- Sends one `shape:"rect"` outline slot (`edafk_cvbox_<name>`) + one small
  text label slot (`edafk_cvlbl_<name>`, e.g. `compass` / `compass ✓` /
  `station_menu ✗`) just above the box. `ttl = cv_debug_ttl_s` (default 2 s),
  **not** kept alive — expiry is the fade.
- Remembers last-sent rect per name: same rect ⇒ in-place update; changed
  rect ⇒ delete-then-recreate (per §5.12).
- Every public method wrapped fail-soft: never raises into a flight.

### 3.2 `overlay.py` — `OverlayWriter` additions

- `send_once(msg: dict)` — queue a message for the I/O thread **without**
  registering it in `_slots` (so keepalive never resurrects a flash box).
  Data structure: a NEW `_oneshot_outbox: List[dict]` guarded by the
  existing `_lock`, drained in `_flush_locked()` after `_clear_outbox` and
  before the `_slots` re-send. Do NOT reuse `_clear_outbox` (that list is
  ttl:0 deletes only) and do NOT register in `_slots`. One-shots do not
  survive reconnect (acceptable: TTL would have expired them anyway).
  Flight thread still never touches the socket.
- Existing status/event slots unchanged.

### 3.3 Hook points (hybrid — operator decision)

**Auto layer:** `ScreenGrabber.__init__` gains optional `name: str | None`.
`grab()` calls the module-registered sink (`set_debug_sink()` at run wiring)
with `box(name, region)` — verdictless white. **Required edits, enumerated**
(all in `vision/capture.py`; an unnamed grabber stays silent by design):

| Callsite | name passed |
|---|---|
| `build_vision()` → `ScreenGrabber(tuple(v.region), ...)` | `"compass"` |
| `build_widget_vision()` → `ScreenGrabber(tuple(v.widget_crop), ...)` | `"widget_ring"` |
| `build_navpanel_vision()` → `ScreenGrabber(region, ...)` | `"navpanel"` |
| `build_sun_grabber()` → `ScreenGrabber(escape_region, ...)` | `"sun"` |

`station_menu` and future readers get boxes for free the moment they use a
named grabber.

**Detail layer (incremental):** call sites that *know* the outcome upgrade
the box with a verdict — e.g. the align loop re-emits `box("compass", r,
verdict="hit")` when the needle is read, `"miss"` on a failed read;
station-menu detection emits hit/miss with the matched row label. First
implementation wires compass + widget_ring + station_menu verdicts; others
follow as touched.

### 3.4 Config

`OverlayConfig` additions: `cv_debug: bool = False`, `cv_debug_ttl_s: float = 2.0`.

**ALL of the following is NEW WORK delivered by this spec** — none of it
exists in `load_config()` today (config.py:369-394 reads exactly one TOML;
the docstring's env-override claim is currently FALSE and gets fixed here):

1. **`config.local.toml` merge:** `load_config(path)` merges, in order:
   defaults → `config.toml` → `config.local.toml` (derived from the primary
   path's directory — no signature change, all call sites pick it up
   automatically). The file is already ignored: root `.gitignore:53`
   (unanchored `config.local.toml`, `git check-ignore` verified 2026-06-10).
2. **Env overrides:** `ED_AUTOJUMP_<SECTION>_<KEY>` for flat scalar keys
   (str/int/float/bool — tuple fields deliberately unreachable via env;
   document that in the loader), applied LAST (env > local > toml >
   defaults). Update the config.py docstring to match reality once built.
3. **`.env` loading:** read from the project dir at startup if present
   (hand-rolled ~10-line parser, no new dependency).
4. **`.gitignore` deliverable:** add `.env` — verified NOT currently
   ignored at either level (`git check-ignore` returned nothing for it).

Operator's machine: `config.local.toml` → `[overlay] cv_debug = true`. Done.

### 3.5 `calibrate-overlay` CLI

`ed-autojump calibrate-overlay` (matches calibrate-compass / calibrate-menu
pattern; NEW subparser + entry in cli.py's dispatch dict — not pre-wired).
Requires ED + overlay running. Loop:

1. Draws a long-TTL test box at a known screen rect — the calibrated compass
   region if present, else a centered reference rect — plus corner markers.
2. Console hotkeys: arrows = offset ±1 (PgUp/PgDn step ×10), `WASD`-shift =
   scale nudge, `r` reset to computed defaults, `s` save, `q` quit.
   (msvcrt.getch loop — calibration runs in its own console; no global
   hotkey hook needed. NOTE: extended keys (arrows/PgUp/PgDn) arrive as TWO
   bytes — a `\xe0`/`\x00` prefix then the scancode; call getch() again on
   the prefix.)
3. Each keypress re-sends the box (delete+recreate) through the live
   transform so the operator nudges until the outline hugs the real region.
4. `s` writes `calibration/overlay_transform.json`; runs pick it up
   automatically.

### 3.6 Failure modes

| Failure | Behavior |
|---|---|
| Overlay disabled/unreachable | sink built as `None`; grabs skip in one branch |
| cv_debug off | identical — `None` sink |
| Transform file missing | computed defaults (boxes roughly right, tune later) |
| Overlay connection drops mid-run | flash boxes silently lost (TTL would have eaten them anyway); status slots reconnect per existing logic |
| Exception anywhere in sink | swallowed + debug-logged, flight unaffected |

## 4. Testing

- Pure: `ScreenToOverlay` math (corner cases, clamping, round-trips at
  1920×1080 and one other resolution).
- `CvDebugSink` against a fake `OverlayWriter`: verdict→color mapping,
  same-rect update vs resize delete+recreate ordering, label text.
- `send_once` does not enter `_slots` (keepalive never re-sends it).
- Config: local-toml merge precedence, env-override precedence + bool/num
  parsing, `.env` loading.
- ScreenGrabber: named grabber notifies sink; unnamed/`None`-sink is a no-op.
- Live verification (operator): calibrate-overlay session + one real run
  with cv_debug on — confirm boxes track compass/widget/station reads.

## 5. Out of scope (YAGNI)

- No needle/vector drawing, no OCR row highlights in v1 (detail layer is
  designed for them, added when a session needs them).
- No per-box color customization config.
- No EDMCModernOverlay support (knowledgebase §7 — unverified protocol).
- No overlay-based interactive tuning of CV regions themselves (transform
  only) — region calibration stays in the existing calibrate-* tools.

## 6. Rollout

1. Implement behind `cv_debug=false` default. 2. Operator flips local flag,
runs `calibrate-overlay`, tunes transform. 3. One live run validates flash
behavior + 20 FPS delete/recreate flicker tolerance (knowledgebase open
question §9.8). 4. README gains a short "CV debug overlay" section for the
GitHub tester (how to opt in WITHOUT a local file: `[overlay] cv_debug=true`
in config.toml or `ED_AUTOJUMP_OVERLAY_CV_DEBUG=1` — the env path only
exists once §3.4 item 2 ships; both land in the same release).
