# EDMCOverlay Knowledgebase

**Purpose:** Complete reference for building a very advanced AFK-status display on EDMCOverlay. Every protocol claim carries a source citation. Anything unverified in source is filed under Open Questions.

**Audience:** An implementer who will build new overlay features on the first attempt.

---

## 1. Sources Examined

| Source | Version / Commit | Examined |
|--------|-----------------|---------|
| `inorton/EDMCOverlay` GitHub repo | tag `1.0.6` (released 2022-01-06); master branch HEAD | All C# source files fetched via `gh api` |
| `EDMCOverlay/EDMCOverlay/Graphic.cs` | master | Authoritative protocol model — field-by-field |
| `EDMCOverlay/EDMCOverlay/GraphicType.cs` | master | Shape and font size constants |
| `EDMCOverlay/EDMCOverlay/VectorPoint.cs` | master | Vector drawing point struct |
| `EDMCOverlay/EDMCOverlay/OverlayJsonServer.cs` | master | TCP server, framing, client lifecycle, TTL constants |
| `EDMCOverlay/EDMCOverlay/OverlayRenderer.cs` | master | Coordinate system, scale math, font sizes, color parsing, draw logic |
| `EDMCOverlay/EDMCOverlay/EDGlassForm.cs` | master | Transparent window, click-through, process-follow |
| `EDMCOverlay/EDMCOverlay/EDMCOverlay.cs` | master | Entry point, CLI flags, startup flow |
| `EDMCOverlay/EDMCOverlay/InternalGraphic.cs` | master | TTL expiry and slot update semantics |
| `edmcoverlay.py` (repo root) | master | Canonical Python client library |
| `inorton/EDMCOverlayDemo` — `load.py` | master | Animated demo patterns |
| Local install | v1.0.6.0 (FileVersion), installed at `%LOCALAPPDATA%\EDMarketConnector\plugins\EDMCOverlay\` | exe + config confirmed |
| `src/ed_autojump/overlay.py` | project master HEAD | Our existing client |
| `src/ed_autojump/config.py` — `OverlayConfig` | project master HEAD | Our config schema |
| GitHub issues #41–#48 | open issues, June 2026 | Known bugs |

URLs:
- https://github.com/inorton/EDMCOverlay
- https://github.com/inorton/EDMCOverlayDemo
- https://github.com/SweetJonnySauce/EDMCModernOverlay (alternative — see §7)

---

## 2. Architecture

### Component diagram

```
Elite Dangerous (EliteDangerous64.exe)
        |  (game window handle, GetWindowRect)
        v
EDMCOverlay.exe  [.NET 4.6.1, WinForms, C# 85%]
  ├── EDGlassForm          transparent WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE window
  │   └── follows ED's window rect + XOffset/YOffset margin
  ├── OverlayRenderer      20 FPS render loop (GDI+), double-buffered
  │   ├── EUROCAPS.TTF     embedded font (Elite's Eurocaps)
  │   └── DrawText / DrawShape / DrawVector
  └── OverlayJsonServer    TCP listener on port 5010, loopback only
        └── one thread per client (max 5 simultaneous)

Our bot / any external process
  └── TCP socket → 127.0.0.1:5010 → newline-delimited JSON messages
```

### Startup flow (EDMCOverlay.cs `Main`)

1. Logger writes to `%LOCALAPPDATA%\EDMarketConnector\edmcoverlay.log`.
2. `GetGame()` searches for process named `EliteDangerous64`. If not found **and** not in `--standalone` / `--test` / `--foreground` mode → `Environment.Exit(2)`.
3. TCP server starts on port 5010 (`OverlayJsonServer` constructor hard-codes the port).
4. On startup, server inserts a self-promoting banner: `{ id:"_", text:"/EDMC Overlay Vx.y.z /", color:"yellow", size:LARGE, x:30, y:130, ttl:15 }`.
5. `EDGlassForm` created and `Application.Run` called — message loop starts.

### Who starts the exe?

- Normally EDMC (the plugin host) starts it when EDMC itself launches.
- Our bot can launch it directly if EDMC is not running (strategy B in our `overlay.py`).
- The exe has a `--standalone` mode where it manages its own window geometry, shows a resize grip, and does not require ED to be foreground.

---

## 3. Complete Wire Protocol Reference

### 3.1 Transport

| Property | Value | Source |
|----------|-------|--------|
| Protocol | TCP | `OverlayJsonServer.cs` — `TcpListener` |
| Bind address | `127.0.0.1` (loopback only) | `OverlayJsonServer(IPAddress.Loopback, port)` |
| Port | **5010** (hard-coded) | `EDMCOverlay.cs:Main` → `new OverlayJsonServer(5010, renderer)` |
| Framing | **Newline-delimited JSON** — one JSON object per line, terminated by `\n` | `OverlayJsonServer.cs:ServerThread` → `reader.ReadLine()` |
| Encoding | UTF-8 | `new StreamReader(client.GetStream(), Encoding.UTF8)` |
| Max clients | 5 simultaneous | `OverlayJsonServer.cs: const int MaxClients = 5` (note: MaxClients is declared but the server does not enforce it — it accepts unlimited clients in practice; see §5) |
| Client disconnect | On disconnect, **all graphics belonging to that client are removed** | `OverlayJsonServer.cs:ServerThread finally` block → removes by `clientId` |

### 3.2 Message types

There are **three** message types, distinguished by which fields are present:

| Type | Discriminator | Required fields |
|------|-------------|----------------|
| Text | `text` non-empty, `shape` absent/empty | `id`, `text`, `color`, `x`, `y`, `ttl` |
| Shape | `shape` non-empty | `id`, `shape`, `x`, `y`, `ttl` |
| Command | `command` non-empty | `command` |

Rendered in `OverlayRenderer.cs:Draw()`:
```csharp
if (!String.IsNullOrEmpty(g.Shape))   DrawShape(g);
else if (!String.IsNullOrEmpty(g.Text)) DrawText(g);
```

### 3.3 Complete field reference (`Graphic.cs`)

All fields are JSON keys (Newtonsoft.Json deserializes them case-insensitively by default). All are optional at the wire level — unset fields receive C# defaults (null strings, 0 ints).

| JSON key | C# type | Purpose | Notes |
|----------|---------|---------|-------|
| `id` | `string` | Slot identifier | Same `id` updates the existing slot in place (see §3.6). If null/empty, every send creates an anonymous slot that can never be updated — avoid. |
| `text` | `string` | Text to display | Setting to `""` or null **deletes** the slot (`SendGraphic` check: `String.IsNullOrWhiteSpace(request.Text) && String.IsNullOrEmpty(request.Shape)` → remove). |
| `size` | `string` | Font size | `"normal"` (12pt Eurocaps) or `"large"` (19pt Bold Eurocaps). Anything else → falls back to `normalFont`. Source: `OverlayRenderer.cs:LoadFonts` and `DrawTextEx`. |
| `color` | `string` | Foreground color | Named: `"red"`, `"yellow"`, `"green"`, `"blue"`, `"black"`. Hex: `"#rrggbb"` (7 chars) or `"#aarrggbb"` (9 chars, alpha supported). Source: `OverlayRenderer.cs:GetBrush`. |
| `fill` | `string` | Shape fill color | Same color syntax as `color`. Used only for `shape:"rect"`. |
| `x` | `int` | X position | Virtual canvas coordinates (see §4). |
| `y` | `int` | Y position | Virtual canvas coordinates (see §4). |
| `w` | `int` | Shape width | Virtual canvas units. Used for `shape:"rect"`. |
| `h` | `int` | Shape height | Virtual canvas units. Used for `shape:"rect"`. |
| `ttl` | `int` | Time-to-live in seconds | See §3.5 for exact behavior. |
| `shape` | `string` | Shape type | `"rect"` or `"vect"`. Source: `GraphicType.cs`. |
| `vector` | `VectorPoint[]` | Points for vect shapes | See §3.4. Used only when `shape:"vect"`. |
| `anchor` | `string` | Screen anchor | Declared in `Graphic.cs` as `public string Anchor`. **Not implemented in `OverlayRenderer.cs`** — the field is parsed but never read during rendering. Filed under Open Questions. |
| `command` | `string` | Control command | `"exit"` → calls `Environment.Exit(0)`. `"noop"` → no-op ping. Anything else → logged as unknown. |
| `oldX` / `oldY` | `int` | Previous position | Set by `InternalGraphic.Update` for server bookkeeping. Not a client-supplied field. |

### 3.4 Vector (`vect`) shape

When `shape:"vect"`, the `vector` array contains `VectorPoint` objects:

| JSON key | C# type | Purpose |
|----------|---------|---------|
| `x` | `int` | Point X (virtual coords) |
| `y` | `int` | Point Y (virtual coords) |
| `color` | `string` | Color of the marker and label text at this point |
| `marker` | `string` | `"cross"` (×) or `"circle"` (○). If absent/empty, no marker is drawn. |
| `text` | `string` | Label drawn at `(x+2, y+7)` relative to the point, always in `"normal"` size. |

Source: `VectorPoint.cs`, `OverlayRenderer.cs:DrawVector`, `DrawMarker`, `DrawTextEx`.

**Rendering algorithm** (`DrawVector`):
1. Iterates points in order; draws a line from each point to the next using the **parent Graphic's `color`** (not the point's color).
2. At each point (including the last): draws the marker (cross = 2 lines ±3px; circle = ellipse 8×8px), then draws the label text.
3. A single-point vector array draws only the marker and label — no line.

**Cross marker size:** 6×6 virtual pixels (±3 from center), scaled.
**Circle marker size:** 8×8 virtual pixels, scaled.

### 3.5 TTL behavior

Source: `InternalGraphic.cs:Update`, `OverlayRenderer.cs:Draw`.

```csharp
// InternalGraphic.Update:
expires = DateTime.Now.AddSeconds(g.TTL);

// OverlayRenderer.Draw — expiry check:
if (gfx.Expired)  { Graphics.Remove(id); continue; }
// Expired = !(lifeleft > 0)  →  expires when lifeleft <= 0
```

| TTL value | Behavior |
|-----------|---------|
| `> 0` | Slot expires after that many seconds from last update. |
| `= 0` | `DateTime.Now.AddSeconds(0)` = expires immediately on the next render frame. Effective "delete this slot" signal. Our `clear()` sends `{"id":"x","ttl":0}`. |
| `< 0` | **Bug (issue #42):** spec says "display forever"; in practice `AddSeconds(negative)` sets expiry in the past → slot expires immediately and fails to render. **Do not use negative TTL.** |
| Default | `OverlayJsonServer.cs: const int DefaultTtl = 5` — but this constant is never applied to incoming messages; if the client sends TTL 0 from a missing field, the slot expires immediately. Always send explicit TTL > 0. |

**Keepalive requirement:** Slots expire. You must re-send any slot you want to persist before its TTL runs out. Our client uses `keepalive_s: 4.0` with `ttl: 6` — re-sends every 4 s so the 6 s TTL never reaches zero.

### 3.6 Slot update semantics

Source: `OverlayJsonServer.cs:SendGraphic`:

```csharp
if (String.IsNullOrWhiteSpace(request.Text) && String.IsNullOrEmpty(request.Shape))
    _graphics.Remove(request.Id);          // delete
else if (_graphics.ContainsKey(request.Id))
    _graphics[request.Id].Update(request); // update in place
else
    _graphics.Add(request.Id, new InternalGraphic(request, clientId)); // create
```

`InternalGraphic.Update` only copies `Text`, `Color`, `X`, `Y`, and resets `expires`. **It does not update `Size`, `Shape`, `Fill`, `W`, `H`, or `Vector`.** To change those fields on an existing slot you must first delete the slot (send `ttl:0` or empty text) then re-create it.

Source: `InternalGraphic.cs:Update`.

### 3.7 Commands

| Command value | Effect |
|---------------|--------|
| `"exit"` | `Environment.Exit(0)` — kills the overlay process entirely |
| `"noop"` | No-op; useful as a connectivity check |
| Anything else | Logged as "Unknown command", counter incremented |

Send as: `{"command": "noop"}\n`

### 3.8 Framing details

- Each message: valid JSON object + exactly one `\n` byte.
- Server uses `StreamReader.ReadLine()` which handles `\r\n` too, but send `\n` only.
- Blank lines and whitespace-only lines are skipped (`String.IsNullOrWhiteSpace(line)`).
- Malformed JSON causes `JsonConvert.DeserializeObject<Graphic>` to throw; the exception propagates to the catch in `ServerThread`, increments `messageErrorCount`, and **closes that client connection**. Send valid JSON or you lose the connection.
- No max message size is enforced beyond what .NET's `StreamReader` can handle (effectively unlimited for practical purposes).
- No response is sent back to the client — the protocol is send-only.

---

## 4. Coordinate System and Rendering Capabilities

### 4.1 Virtual canvas

Source: `OverlayRenderer.cs` constants:

```csharp
public const int VIRTUAL_ORIGIN_X = 20;
public const int VIRTUAL_ORIGIN_Y = 40;
public const int VIRTUAL_WIDTH = 1280;
public const int VIRTUAL_HEIGHT = 1024;
```

The virtual canvas is **1280 × 1024**. The overlay window itself is inset by `(20, 40)` from the game window's top-left (set in `StartUpdate`: `Glass.XOffset = VIRTUAL_ORIGIN_X; Glass.YOffset = VIRTUAL_ORIGIN_Y`).

**Coordinate (0, 0) in the virtual canvas = pixel (0, 0) inside the already-inset overlay window**, which is pixel (20, 40) from the game window's top-left corner. There is no additional origin offset applied in `ScalePosition`.

**Scaling math** (`OverlayRenderer.cs:Scale`):

```csharp
double x_factor = csize.Width  / (double)(VIRTUAL_WIDTH  + 32);  // 1312
double y_factor = csize.Height / (double)(VIRTUAL_HEIGHT + 18);  // 1042
p.X = (int)Math.Round(x * x_factor);
p.Y = (int)Math.Round(y * y_factor);
```

For a 1920×1080 game window, the overlay window (after inset) is approx 1880×1000 px:
- `x_factor ≈ 1880/1312 ≈ 1.433`
- `y_factor ≈ 1000/1042 ≈ 0.960`

So at 1080p, virtual X values are magnified and virtual Y values are compressed slightly. **Coordinates are not 1:1 with screen pixels.**

**Safe origin:** `(20, 40)` is the conventional top-left safe margin (used by our bot and by the startup banner at `(30, 130)`). There is nothing preventing negative coordinates in the wire protocol — the renderer clips at the buffered-graphics boundary implicitly.

**Text overflow protection** (`DrawTextEx`): if a text string would extend past the right edge or bottom edge of the overlay window, its position is nudged left or up. The estimate is `textwidth = 8 * text.Length` (fixed-width approximation, not actual font metrics).

### 4.2 Fonts

Source: `OverlayRenderer.cs:LoadFonts`:

| Size name | Font | Point size | Style |
|-----------|------|-----------|-------|
| `"normal"` | EUROCAPS (embedded) | 12pt | Regular |
| `"large"` | EUROCAPS (embedded) | 19pt | Bold |

Only these two sizes exist. There is no medium, small, xlarge, or numeric size. Sending any other string for `size` silently uses `normalFont`.

The font is Elite Dangerous's own "Eurocaps" typeface, embedded as a resource (`EUROCAPS.TTF`). Text rendering hint: `SystemDefault`.

### 4.3 Colors

Named colors (exact C# `Color` enum values):

| String | Color |
|--------|-------|
| `"red"` | `Color.Red` |
| `"yellow"` | `Color.Yellow` |
| `"green"` | `Color.Green` |
| `"blue"` | `Color.Blue` |
| `"black"` | `Color.Black` |

Hex colors: `"#rrggbb"` (opaque) or `"#aarrggbb"` (with alpha channel, 0x00=transparent, 0xff=opaque). Source: `GetBrush` switch on `colour.Length` — 7 chars = `#rrggbb`, 9 chars = `#aarrggbb`. Any other length is silently ignored (brush not added, returns null → element not drawn).

Alpha support is **real and useful**: `#00rrggbb` is fully transparent, `#80rrggbb` is 50% transparent. This enables subtle backgrounds and depth layering.

Color objects are **cached** in a `Dictionary<String, Brush>` on first use — no per-frame allocation overhead.

### 4.4 Shape capabilities

**`shape:"rect"`** — filled and/or outlined rectangle:
- `x, y` = top-left corner (virtual coords)
- `w, h` = size (virtual coords)
- `fill` = fill color (null/empty = no fill, outline only)
- `color` = border color (null/empty = no border)
- Can have both, either, or neither (though neither renders nothing visible)
- No rounded corners, no line width control

**`shape:"vect"`** — polyline with optional markers and labels:
- `color` on the parent Graphic = line color
- `vector` array = ordered list of VectorPoint
- Lines connect adjacent points; no automatic closing of the shape
- Each point can independently have a `marker` (cross/circle) and a `text` label
- The per-point `color` applies to the marker and label at that point only; the connecting lines use the parent's color

**No other shape types exist.** No circle/ellipse, triangle, arc, polygon, image/bitmap, progress bar, or gradient — those are not in `GraphicType.cs` or `DrawShape`.

### 4.5 Render loop

- **20 FPS** (`const int FPS = 20`), fixed interval ~50ms between frames.
- Double-buffered with `BufferedGraphics` (WinForms).
- When nothing needs rendering, the loop sleeps 1000ms between frames.
- When there are graphics to draw AND the game window is in the foreground (or `--standalone`/`--foreground` flags are active), it renders at full 20 FPS.
- Background/unfocused: loop still ticks but clears the overlay — nothing is shown.

---

## 5. Operational Constraints and Gotchas

### 5.1 Game window mode

The overlay **only works in Windowed or Borderless-Windowed mode**. True exclusive fullscreen is not supported because the overlay window cannot be placed over it. ED must be running when the overlay starts (or `--standalone`/`--test` flag must be passed).

### 5.2 Foreground requirement

Source: `OverlayRenderer.cs:StartUpdate`:

```csharp
bool foreground = (activeWindow == Glass.Follow.MainWindowHandle);
bool render = ((this.Standalone || foreground) && (Graphics.Values.Count > 0)) || this.ForceRender;
```

The overlay only paints when ED's window handle is the active foreground window. If you Alt+Tab away, the overlay goes blank. This is by design. CLI flags `--standalone` and `--foreground` bypass this.

### 5.3 Connection ownership and slot lifetime

Slots are **per-connection**. When a TCP client disconnects (graceful or crash), all slots that client created are purged. This means:
- You cannot reconnect and "pick up" your existing slots — you must re-send everything.
- Keeping one persistent connection per session and re-sending all slots on reconnect is the correct pattern (what our bot does).
- Multiple clients can coexist; their slots are independent but share the same id-space — two clients sending the same `id` will fight over that slot (last write wins, expiry resets).

### 5.4 MaxClients not enforced

`const int MaxClients = 5` is declared but `OverlayJsonServer.Start()` unconditionally accepts every incoming connection with `_listener.AcceptTcpClient()`. In practice there is no enforced limit.

### 5.5 Negative TTL bug (issue #42)

Do not send negative TTL. See §3.5. Use a very large positive TTL (e.g. 3600) if you want near-permanent slots, and keepalive-resend before expiry.

### 5.6 Partial rendering at startup (issue #48)

If EDMC (and thus EDMCOverlay.exe) is launched before the game, the overlay window may only cover part of the game window. The root cause appears to be a timing issue: the overlay window positions itself based on the game window's geometry at launch time, but the game window may not have settled to its final size yet.

**Workaround:** Quit EDMC after ED is fully loaded (at the main menu), then relaunch EDMC. Or use our bot's `launch_if_absent` path which spawns EDMCOverlay.exe after ED is already running.

### 5.7 DPI scaling

`EDGlassForm` sets `this.AutoScaleMode = AutoScaleMode.None` — no automatic DPI scaling. The overlay window follows the game window rectangle raw. If Windows display scaling is active (125%, 150%, etc.) and ED does not account for it, there may be misalignment. No DPI fix is implemented in the source.

### 5.8 What happens when ED isn't running

Without `--standalone`/`--test`: `GetGame()` returns null → `Environment.Exit(2)`. The exe exits silently. The TCP listener never even starts. Our bot's `_establish()` will time out and disable itself.

### 5.9 Runtime requirement

.NET Framework 4.6.1 (`app.config: supportedRuntime v4.0 sku=".NETFramework,Version=v4.6.1"`). This is pre-installed on all Win10/Win11 machines.

### 5.10 Log file location

`%LOCALAPPDATA%\EDMarketConnector\edmcoverlay.log`. Useful for diagnosing startup crashes.

### 5.11 Malformed JSON kills the client connection

Any JSON parse error in `JsonConvert.DeserializeObject<Graphic>(line)` throws, is caught in `ServerThread`, and the connection is closed. All that client's slots are wiped. Validate JSON on your side before sending.

### 5.12 Update only patches Text, Color, X, Y

`InternalGraphic.Update` patches only those four fields plus `expires`. Size, shape type, fill, W, H, and Vector are frozen after initial creation. To change them: delete then re-create (send TTL=0 first, then send the new message).

### 5.13 Text width estimation is approximate

`DrawTextEx` uses `textwidth = 8 * text.Length` for overflow nudging. Eurocaps is not monospaced, so this is a rough heuristic. Long strings near the right or bottom edge may shift position unexpectedly.

---

## 6. Ecosystem Patterns (What Others Have Built)

### 6.1 Dependent plugins

Confirmed plugins using EDMCOverlay as a service:
- **EDR (lekeno/edr):** ED Recon — multi-line threat intel, commander info. Heavy user.
- **HITS (inorton/EDMCHits):** System safety reports.
- **Cargo-Manifest:** Cargo tracking overlay.
- Installed locally alongside EDMCOverlay: EDR, HITS, Cargo-Manifest (confirmed in `%LOCALAPPDATA%\EDMarketConnector\plugins\`).

### 6.2 Patterns from the demo plugin (EDMCOverlayDemo)

The demo (`load.py`) shows a **30 FPS animation loop** using `time.sleep(1.0/30)` combined with short TTLs (3s) on every re-sent frame. Key patterns:

- **Scrolling text:** Move x per frame, mod screen width; re-send with new x each tick.
- **Sine-wave text:** Each character is a separate slot (`"sine0"`, `"sine1"`, …); position recomputed per frame.
- **Multi-actor system:** Each `id` is a stable slot key; position is re-sent every frame.
- Virtual width used: 1360, height 990 — slightly larger than `VIRTUAL_WIDTH=1280/VIRTUAL_HEIGHT=1024`. The renderer clamps via the overflow nudge.

### 6.3 Pattern: background panels using `shape:"rect"` with alpha

Semi-transparent rectangles work as panel backgrounds:
```json
{"id":"bg_status","shape":"rect","x":10,"y":30,"w":400,"h":200,"fill":"#aa000000","color":"#ff444444","ttl":60}
```
A `"#aa000000"` fill is ~67% transparent black — readable as a dark HUD background. Border with a dim color for a panel edge. This is the primary way to create a visually distinct display panel.

### 6.4 Pattern: status lines with fixed IDs

Use a stable `id` per display region and re-send (update) the `text` field at whatever frequency your data changes. The slot update is in-place (no flicker from delete/recreate). Our current bot uses `"ed_afk_status"` and `"ed_afk_event"` for two stacked lines.

### 6.5 Pattern: progress bars via `shape:"rect"`

A filled rect can represent a progress bar. Two rects (background + foreground, stacked with the same Y):
```json
{"id":"fuel_bg","shape":"rect","x":20,"y":100,"w":200,"h":12,"fill":"#88222222","ttl":10}
{"id":"fuel_bar","shape":"rect","x":20,"y":100,"w":140,"h":12,"fill":"#ff00cc44","ttl":10}
```
Width of the fill bar = `(fuel_pct / 100.0) * 200`. Update `fuel_bar` with new `w` — but **note §5.12**: `w` is not updated by `InternalGraphic.Update`. You must delete and recreate the slot every time the bar width changes. Send `{"id":"fuel_bar","text":"","ttl":0}` to delete, then send the new rect. This is slightly janky but workable at low update frequency.

**Preferred workaround for progress bar:** Use multiple thin rect slices (one per percentage point), toggle by TTL. Or accept the delete-recreate pattern at the meter update cadence (e.g., once per second for fuel).

### 6.6 Pattern: vect for borders and frames

A 4-point vect is a bordered box (not filled). Useful for framing a display panel or showing a compass direction:
```json
{"id":"frame","shape":"vect","color":"#ff556688","ttl":30,"vector":[
  {"x":10,"y":30},{"x":410,"y":30},{"x":410,"y":230},{"x":10,"y":230},{"x":10,"y":30}
]}
```
Closing the loop manually (repeat the first point as the last) completes the rectangle.

---

## 7. Alternatives to inorton/EDMCOverlay

### EDMCModernOverlay (SweetJonnySauce)
- Cross-platform (Windows + Linux).
- Claims **backward compatibility** with the inorton protocol on TCP 5010.
- Adds Overlay Controller for placement, text justification, background colors.
- Replaces both EDMCOverlay and edmcoverlay2 (Linux port).
- **Not installed locally.** Our bot targets inorton v1.0.6.

### edmcoverlay_for_linux (alexzk1)
- Linux port. Irrelevant for this Windows-only bot.

---

## 8. Our Current Client: Capabilities and Gaps

### 8.1 What we currently send

File: `src/ed_autojump/overlay.py`

| Method | Slot ID | Content |
|--------|---------|---------|
| `status(text)` | `"ed_afk_status"` | Procedure/step string in configured color/size at (x, y) |
| `step(proc, action, idx, total)` | `"ed_afk_status"` | Formatted `"proc > action (n/N)"` |
| `event(text)` | `"ed_afk_event"` | Journal event text at (x, y+24) in `#88ccff` |
| `clear(slot_id)` | caller-specified | Sends `{"id":"...","ttl":0}` — immediate expiry |

That is **2 text slots** (status + event) and **nothing else**. No shapes. No background. No progress bars. No multi-column layout.

### 8.2 Protocol capabilities currently unused

| Capability | Not used | Notes |
|-----------|---------|-------|
| `shape:"rect"` background panels | Not used | Would let us add a readable dark backdrop |
| `shape:"rect"` progress bars | Not used | Fuel level, heat, hull — all data we have |
| `shape:"vect"` border frames | Not used | Visual separation of display regions |
| `"#aarrggbb"` alpha colors | Not used | Currently only named colors and `#rrggbb` |
| `size:"large"` | Not used | Could emphasize critical states |
| Multiple slots (>2) | Not used | Protocol supports unlimited slots per connection |
| Per-slot arbitrary position | Fixed x/y from config | Each slot could be individually positioned |
| Vector labels (`VectorPoint.text`) | Not used | Could annotate positions |
| Horizontal/vertical layout grids | Not built | Need coordinate arithmetic in client |
| Delete-and-recreate for shape updates | Not built | Required for changing W/H |

### 8.3 Client architecture gaps for advanced display

1. **No slot registry with positions.** Currently `_STATUS_ID` and `_EVENT_ID` are module-level constants. A proper multi-panel display needs a slot registry with `(id, x, y, w, h, color, size)` per display element.

2. **No progress bar primitive.** Needs delete-then-recreate pattern (send TTL=0, then new message). No helper exists for this.

3. **No shape support at all.** `overlay.py` has no `send_shape` / `send_rect` / `send_vect` method.

4. **No alpha color support.** `_text_message` hardcodes config-level color strings; no way to pass `#aarrggbb` per-slot.

5. **No layout engine.** For an AFK display with N panels (fuel, heat, hull, jumps, procedure, step, star class, next system, etc.), coordinates must be computed consistently. Currently: single hardcoded `(x, y)` with `y+24` for the second line.

6. **Keepalive resends all slots indiscriminately.** This is correct behavior but means you pay bandwidth proportional to slot count. For ~10–15 slots at keepalive_s=4, this is negligible.

7. **No `size` per slot.** All slots share the config-level `size`. Advanced display wants `large` for the procedure name and `normal` for detail lines.

8. **No background panel.** The text is floating on the game's visuals. A `shape:"rect"` with alpha fill behind the text block would make it readable in bright nebulae.

---

## 9. Open Questions (Unverified — Live Testing Required)

1. **`anchor` field is it ever rendered?** `Graphic.cs` declares `public string Anchor` with comment `"anchor the graphic to an edge of the screen, N E S W NE NW SE SW"`. But `OverlayRenderer.cs` reads only `g.X`, `g.Y` — `g.Anchor` is never accessed. Either it's dead code, or an unmerged feature. Verify with `--test` mode to see if it does anything.

2. **Exact `TextRenderingHint.SystemDefault` appearance.** The font looks fine on the operator's screen, but DPI scaling and ClearType settings can change anti-aliasing. Verify with the actual game window at configured resolution (1920×1080, borderless).

3. **Update semantics for shape fields.** Confirmed in source that `InternalGraphic.Update` does not patch `W`, `H`, `Fill`, `Shape`, or `Vector`. The delete-then-recreate pattern avoids this, but it needs live verification to confirm there is no flicker or race between the `ttl:0` purge frame and the recreation frame at 20 FPS.

4. **Inter-client slot collision behavior.** If EDMC's plugins (EDR, HITS, Cargo-Manifest) happen to use the same slot `id` as our bot, the last write wins. Verify that `"ed_afk_status"` and `"ed_afk_event"` do not collide with any installed plugin. Prefix with `edafk_` for extra safety.

5. **MaxClients behavior in practice.** The `MaxClients = 5` constant is unused in the Accept loop. Confirm there is no OS-level backlog limit that becomes relevant if EDMC plugins + our bot each hold a connection.

6. **Startup banner TTL.** The server injects a `ttl:15` banner at `(30, 130)` with `id:"_"`. This expires after 15 s and will not block our layout if we start at `(20, 40)`. Verify with `--test` that the `_` slot id is safe to overlap intentionally if needed.

7. **`--standalone` flag behavior for our bot's launch path.** Our `_default_launch` Popen does not pass `--standalone`. Without it, the exe exits if ED is not running. If ED is running, this is fine. Confirm that our bot never races with the ED process check.

8. **Frame rate sensitivity of delete-then-recreate.** The 20 FPS render loop means the gap between the `ttl:0` (expiry) and the new create message could be up to 50 ms during which the slot is invisible. At a progress-bar update rate of ~1 Hz, this is a 5% blank blink. Measure whether this is visually acceptable.

9. **Per-pixel font metrics vs the `8 * length` heuristic.** Eurocaps is not monospaced. Long text strings (e.g. a system name with special characters) may be shifted by `DrawTextEx` when they approach screen edges. Verify with the longest expected strings (Beagle Point region names, 25–30 chars).

10. **`EDMCModernOverlay` protocol compatibility.** Claimed backward compatible but not source-verified. If the operator ever switches to EDMCModernOverlay, re-verify the entire protocol against that repo's source before assuming it works.

---

## 10. Quick Reference: Advanced Display Slot Map (Design Proposal)

A suggested layout for a 1920×1080 screen (verify coordinates live):

```
Virtual coords (0,0) = game-window top-left + (20,40) inset

(10,  30) bg_panel   rect  w=450 h=260  fill="#aa000011" color="#446688"   // dark backing panel
(20,  45) procedure  text  size="large" color="yellow"                      // e.g. "ARRIVAL"
(20,  80) action     text  size="normal" color="#88ccff"                    // e.g. "scoop_refuel"
(20, 105) progress   text  size="normal" color="#aaffaa"                    // "step 3/7"
(20, 130) system     text  size="normal" color="#cccccc"                    // system name
(20, 155) starclass  text  size="normal" color="#ffaa44"                    // "Star: K (scoopable)"
(20, 180) fuel_label text  size="normal" color="#888888"                    // "Fuel:"
(20, 180) fuel_bg    rect  x=70 w=150 h=10 fill="#33333300"                // fuel bar bg
(70, 180) fuel_bar   rect  w=<dynamic> h=10 fill="#00cc44"                 // fuel bar fill
(20, 200) jumps      text  size="normal" color="#88ccff"                    // "Jumps: 42"
(20, 220) status     text  size="normal" color="#ffff44"                    // last event
```

Note: `fuel_bar` width changes require delete-then-recreate (§5.12, §6.5).

---

*Generated by ed-autojump research agent, 2026-06-07. Source commits examined: inorton/EDMCOverlay master (tag 1.0.6). Local install confirmed at `C:\Users\<user>\AppData\Local\EDMarketConnector\plugins\EDMCOverlay\EDMCOverlay.exe` v1.0.6.0.*
