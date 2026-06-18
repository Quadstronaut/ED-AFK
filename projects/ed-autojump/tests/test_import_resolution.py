"""Whole-tree import-RESOLUTION gate.

Sibling to the reorg's whole_tree_import_check.py, which only checks layering
DIRECTION (no upward/sideways cross-package edges). That gate is structurally
blind to the bug this one catches: an absolute import whose head package still
exists but whose submodule/symbol was moved or deleted by the reorg (e.g.
`from ed_autojump.config import load_config` after config.py moved to ed_core).
The head `ed_autojump` is a valid package, so layering passes -- but the import
fails at runtime with ModuleNotFoundError. Scripts under scripts/ aren't
exercised by any other test, so these rot silently until someone runs them.

This gate walks every workspace `src/` and `scripts/` tree, extracts each
absolute import that targets a workspace package, and verifies it RESOLVES:
the target module imports and each imported name exists. It does NOT execute
the scanned file -- the scripts press keys / grab frames / focus the game
window at module top level, so executing them here is unsafe. We import the
*target library modules* only (which the rest of the suite already imports).

Relative imports (level>0) are intra-package and skipped, matching the
layering gate. Non-workspace imports (stdlib / third-party) are skipped so an
optional dependency never fails the gate.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

# Workspace package roots. Head names whose imports we resolve; everything else
# (stdlib, third-party) is ignored.
WORKSPACE_PKGS = {
    "ed_vision", "ed_core", "ed_autojump", "ed_explore", "ed_combat", "ed_trading",
}

# Narrow exemption for deliberate forward-stubs that are guarded in live code
# and whose target module is not yet built. Add only after a council decision.
# Format: (file_suffix_relative_to_src, import_module)
_DEFERRED_EXEMPT: frozenset[tuple[str, str]] = frozenset({
    # D3 HUD matcher not yet built (ed_vision.hud_sc_indicators). The import
    # in flow/steps.py line 1471 is inside a function, guarded by an early
    # None-return + try/except, and cannot crash a flight. See comment at
    # steps.py:1465-1468. Remove this exemption once the D3 matcher lands.
    ("flow/steps.py", "ed_vision.hud_sc_indicators"),
})

# projects/ed-autojump/tests/this_file -> projects/
PROJECTS_ROOT = Path(__file__).resolve().parents[2]

# (package-dir, module-name) for each installed package's src tree.
_PKG_DIRS = {
    "ed_vision": "ed-vision",
    "ed_core": "ed-core",
    "ed_autojump": "ed-autojump",
    "ed_explore": "ed-explore",
    "ed_combat": "ed-combat",
    "ed_trading": "ed-trading",
}


def _scan_roots() -> list[Path]:
    """Directories to walk: every package's src/<module> and scripts/."""
    roots: list[Path] = []
    for mod, dirname in _PKG_DIRS.items():
        src = PROJECTS_ROOT / dirname / "src" / mod
        if src.is_dir():
            roots.append(src)
        scripts = PROJECTS_ROOT / dirname / "scripts"
        if scripts.is_dir():
            roots.append(scripts)
    return roots


def _resolves(module: str, name: str | None) -> bool:
    """True if `module` imports and (if given) exposes `name` -- either as an
    attribute or as an importable submodule (`module.name`)."""
    try:
        mod = importlib.import_module(module)
    except Exception:
        return False
    if name is None:
        return True
    if hasattr(mod, name):
        return True
    try:
        importlib.import_module(f"{module}.{name}")
        return True
    except Exception:
        return False


def _is_deferred_exempt(py: Path, module: str) -> bool:
    """True if (file, module) matches a known guarded forward-stub exemption."""
    # Normalise path separators so "flow/steps.py" matches on both OS.
    py_posix = py.as_posix()
    for suffix, exempt_module in _DEFERRED_EXEMPT:
        if py_posix.endswith(suffix.replace("\\", "/")) and module == exempt_module:
            return True
    return False


def _violations_in(py: Path) -> list[str]:
    """Return human-readable violation strings for one file (empty == clean)."""
    out: list[str] = []
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
    except SyntaxError as exc:
        return [f"{py}: PARSE-ERROR {exc}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in WORKSPACE_PKGS:
                    if not _resolves(alias.name, None):
                        out.append(f"{py}:{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0 or not node.module:
                continue  # relative import: intra-package, skip
            if node.module.split(".")[0] not in WORKSPACE_PKGS:
                continue
            if _is_deferred_exempt(py, node.module):
                continue  # guarded forward-stub, explicitly exempted above
            for alias in node.names:
                if alias.name == "*":
                    if not _resolves(node.module, None):
                        out.append(f"{py}:{node.lineno}: from {node.module} import *")
                elif not _resolves(node.module, alias.name):
                    out.append(
                        f"{py}:{node.lineno}: from {node.module} import {alias.name}"
                    )
    return out


def test_all_workspace_imports_resolve():
    violations: list[str] = []
    for root in _scan_roots():
        for py in sorted(root.rglob("*.py")):
            violations.extend(_violations_in(py))
    assert not violations, (
        f"{len(violations)} unresolved workspace import(s):\n  "
        + "\n  ".join(violations)
    )
