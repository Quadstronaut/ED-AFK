"""Operator-extensible nav-panel type-icon REGISTRY.

The route-complete destination router asks ONE question of the nav panel: is the
locked (highlighted) row a body we DOCK at, or one we PARK near? The answer is the
row's type-icon glyph. This module is the manifest that maps each glyph template
to a body KIND and the ACTION ("park" | "dock") the router takes on a match.

The headline operator ask (Q3): a non-expert extends the bot's dock vocabulary by
(1) dropping a cropped template PNG into assets/navpanel_icons/ and (2) adding one
``[[icon]]`` row to registry.toml. NO code change. See that dir's README.md.

FAIL-LOUD on a malformed registry (unknown action, missing template file, missing
field): a bad manifest is a BUILD error surfaced at the import-time registry test,
NOT a silent park on the live ship. The matcher (navpanel_icons.classify_icon_kind)
is the thing that fails CLOSED at runtime (abstain-as-park on a low-confidence
read); the LOADER fails LOUD so a typo never ships.

Pure-stdlib (tomllib + pathlib); no cv2/numpy here, so it imports without the
[vision] extra. The template PIXELS are loaded lazily by the classifier, not here
-- this module only validates that each referenced file EXISTS.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Optional

# The ONLY two actions. Validated at load; anything else is a ValueError.
DOCK_ACTIONS = frozenset({"dock"})
PARK_ACTIONS = frozenset({"park"})
_VALID_ACTIONS = DOCK_ACTIONS | PARK_ACTIONS

# Default manifest location: alongside the templates it references.
_ASSETS_DIR = Path(__file__).parent / "assets" / "navpanel_icons"
_DEFAULT_MANIFEST = _ASSETS_DIR / "registry.toml"


@dataclass(frozen=True)
class IconKind:
    """One registry row: a template glyph -> (kind, action)."""
    template: str          # PNG filename in assets/navpanel_icons/
    kind: str              # human label: "star", "station-coriolis", ...
    action: str            # "park" | "dock" (validated at load)
    notes: str = ""

    @property
    def is_dock(self) -> bool:
        return self.action in DOCK_ACTIONS

    @property
    def is_park(self) -> bool:
        return self.action in PARK_ACTIONS


# Cache: parsed once per (resolved) manifest path. The default path is the hot
# path; an explicit path (tests) is cached under its own resolved key.
_CACHE: dict[Path, tuple[IconKind, ...]] = {}


def _manifest_dir(path: Path) -> Path:
    """The directory holding the manifest == where its templates live."""
    return path.parent


def load_registry(path: Optional[Path] = None) -> tuple[IconKind, ...]:
    """Parse registry.toml -> the validated IconKind tuple. Cached.

    FAIL-LOUD (ValueError) on a bad row: unknown action, missing template file,
    or a missing required field. A malformed registry is a build error, NOT a
    silent park. Raises FileNotFoundError if the manifest itself is absent.
    """
    manifest = (path or _DEFAULT_MANIFEST).resolve()
    cached = _CACHE.get(manifest)
    if cached is not None:
        return cached

    if not manifest.is_file():
        raise FileNotFoundError(f"nav-panel icon registry not found: {manifest}")

    with manifest.open("rb") as fh:
        data = tomllib.load(fh)

    rows = data.get("icon")
    if not isinstance(rows, list) or not rows:
        raise ValueError(
            f"registry {manifest} has no [[icon]] rows (expected at least one)")

    tdir = _manifest_dir(manifest)
    out: list[IconKind] = []
    seen_templates: set[str] = set()
    for i, row in enumerate(rows):
        where = f"registry {manifest} [[icon]] #{i + 1}"
        if not isinstance(row, dict):
            raise ValueError(f"{where}: not a table")
        template = row.get("template")
        kind = row.get("kind")
        action = row.get("action")
        notes = row.get("notes", "")
        # Required-field presence (loud).
        for field, val in (("template", template), ("kind", kind),
                           ("action", action)):
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"{where}: missing/blank required field '{field}'")
        # Action vocabulary (loud).
        if action not in _VALID_ACTIONS:
            raise ValueError(
                f"{where}: action {action!r} is not one of "
                f"{sorted(_VALID_ACTIONS)} (template={template!r})")
        # Path-traversal guard (council 2026-06-22 security lens): a template is a
        # BARE filename living in the assets dir. Reject separators / .. / absolute
        # / drive so an operator-dropped row (the no-code extension path) can never
        # point load outside the package. Belt-and-suspenders: also verify the
        # resolved path stays under tdir.
        if ("/" in template or "\\" in template or ".." in template
                or PurePath(template).is_absolute() or PurePath(template).drive):
            raise ValueError(
                f"{where}: template {template!r} must be a bare filename in {tdir} "
                f"(no path separators, '..', or drive)")
        resolved = (tdir / template).resolve()
        if tdir.resolve() not in resolved.parents:
            raise ValueError(
                f"{where}: template {template!r} resolves outside {tdir}")
        # Template file must EXIST (loud) -- a manifest pointing at a missing PNG
        # would silently never match -> a kind that can never dock. Catch it now.
        if not resolved.is_file():
            raise ValueError(
                f"{where}: template file {template!r} not found in {tdir}")
        if template in seen_templates:
            # A duplicated template (same glyph mapped twice) is almost certainly
            # a copy-paste error; the argmax correlation would only ever pick one.
            raise ValueError(f"{where}: template {template!r} listed more than once")
        seen_templates.add(template)
        out.append(IconKind(template=template, kind=kind, action=action,
                            notes=notes if isinstance(notes, str) else ""))

    result = tuple(out)
    _CACHE[manifest] = result
    return result


def clear_cache() -> None:
    """Drop the parse cache (tests that write a temp manifest call this)."""
    _CACHE.clear()
