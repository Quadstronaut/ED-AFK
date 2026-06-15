"""ed-core flow registry -- the four registration surfaces + active-set registrar.

Surface #1  classifier rules  (boot-state -> procedure name or None)
Surface #2  event -> procedure routes
Surface #3  action -> step-fn table  (lives in step_registry.py)
Surface #4  TOML procedure directories

The active-set registrar (App / ActiveSet) lets the CLI host declare which
domain apps are active and call each app's activate() in turn before the
FlowRunner live loop starts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


# Surface #1: boot-state classifier rules
# (priority, name, fn) -- sorted ascending by priority; first non-None wins.
_CLASSIFIER_RULES: list[tuple[int, str, Callable]] = []


def register_classifier_rule(name: str, rule: Callable, *, priority: int) -> None:
    """Register a boot-state classifier. Rules run in ascending priority order.
    fn signature: (runner) -> Optional[str] -- returns procedure name or None.
    Raises ValueError on duplicate name."""
    if any(n == name for _, n, _ in _CLASSIFIER_RULES):
        raise ValueError(f"Duplicate classifier rule: {name!r}")
    _CLASSIFIER_RULES.append((priority, name, rule))
    _CLASSIFIER_RULES.sort(key=lambda x: x[0])


def run_classifiers(runner: Any) -> Optional[str]:
    """Run registered classifiers in ascending priority order.
    Returns the first non-None result, or None when no rule matches."""
    for _, _, rule in _CLASSIFIER_RULES:
        result = rule(runner)
        if result is not None:
            return result
    return None


# Surface #2: event -> procedure routes
# (priority, event_name, route_name, fn) -- sorted ascending by priority.
_EVENT_ROUTES: list[tuple[int, str, str, Callable]] = []


def register_event_route(
    event_name: str, route: Callable, *, name: str, priority: int = 100
) -> None:
    """Register an event route.
    fn signature: (runner, event) -> Optional[str].
    Raises ValueError on duplicate route name."""
    if any(n == name for _, _, n, _ in _EVENT_ROUTES):
        raise ValueError(f"Duplicate event route: {name!r}")
    _EVENT_ROUTES.append((priority, event_name, name, route))
    _EVENT_ROUTES.sort(key=lambda x: x[0])


def run_event_routes(runner: Any, ev: Any) -> Optional[str]:
    """Run registered event routes for this event.
    Returns the first non-None result from a matching handler, or None."""
    ev_name = getattr(ev, "event", None)
    for _, event_name, _, route in _EVENT_ROUTES:
        if event_name == ev_name or event_name == "*":
            result = route(runner, ev)
            if result is not None:
                return result
    return None


# Surface #4: TOML procedure directories
_PROC_DIRS: list[Path] = []


def register_procedure_dir(path: Path) -> None:
    """Register a directory of *.toml procedure files.
    Raises ValueError if the directory does not exist or is already registered."""
    path = Path(path).resolve()
    if not path.is_dir():
        raise ValueError(f"Procedure dir not found: {path}")
    if path in _PROC_DIRS:
        raise ValueError(f"Duplicate procedure dir: {path}")
    _PROC_DIRS.append(path)


def registered_proc_dirs() -> list[Path]:
    """Return a copy of the registered procedure directory list."""
    return list(_PROC_DIRS)


# Active-set registrar
@dataclass
class App:
    """A domain application that can be activated into the core registry."""
    name: str
    solo: bool = False
    activate: Callable[[], None] = field(default_factory=lambda: (lambda: None))


@dataclass
class ActiveSet:
    """Holds the registered domain apps and activates a named subset."""
    apps: list[App] = field(default_factory=list)

    def register(self, app: App) -> None:
        self.apps.append(app)

    def activate(self, *names: str) -> None:
        chosen = [a for a in self.apps if a.name in names]
        if any(a.solo for a in chosen) and len(chosen) > 1:
            raise ValueError("solo app cannot co-activate with others")
        for a in chosen:
            a.activate()
