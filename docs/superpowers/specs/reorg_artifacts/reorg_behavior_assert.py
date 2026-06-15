#!/usr/bin/env python3
"""A2 -- behavior-preservation lens for the Phase-1 ED-AFK FlowRunner split.

READ-ONLY. Parses SOURCE; does NOT import the package, run flight logic, or
run pytest. Edits nothing.

It static-asserts the byte-identical invariants the reorg must preserve. After
the operator executes the plan, re-run with --src pointed at the relocated
ed-core / ed-autojump roots to confirm the constants survived verbatim.

Seven asserts:
  B1  classifier FRESH_ARRIVAL_WINDOW_S == 30.0   (dispatcher, _maybe_startup)
  B2  steps._FRESH_ARRIVAL_WINDOW_S == 120.0      (DISTINCT from B1 -- G7 trap)
  B3  _CLEAR_JOIN_WINDOW_S == 60.0                 (route-complete join window)
  B4  _maybe_startup classifier ladder order, source-recovered:
        docked -> in_supercruise{parked_terminal, p1_indeterminate <
        p2_local_star < p3_fresh_arrival} -> smacked+cooldown ->
        empty-route guard -> startup
  B5  parallel_tracks=["honk"] in all 7 domain procedures
  B6  body_tour wired in EXACTLY arrival.toml
  B7  the shared/honk/body step impls all present in steps.py

Exit 0 iff all seven pass.

Usage:
  python reorg_behavior_assert.py
  python reorg_behavior_assert.py --src <package src root> --proc <procedures dir>
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

PKG = "ed_autojump"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def default_src() -> Path:
    return repo_root() / "projects" / "ed-autojump" / "src" / PKG


def default_proc() -> Path:
    return repo_root() / "projects" / "ed-autojump" / "procedures"


# --------------------------------------------------------------------------
# constant extractors
# --------------------------------------------------------------------------
def assign_value_in_function(src: str, func: str, name: str):
    """Return the literal float assigned to `name` inside `def func`, or None."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if isinstance(t, ast.Name) and t.id == name:
                            if isinstance(sub.value, ast.Constant):
                                return sub.value.value
    return None


def module_constant(src: str, name: str):
    """Return the module-level literal assigned to `name`, or None."""
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    if isinstance(node.value, ast.Constant):
                        return node.value.value
    return None


# --------------------------------------------------------------------------
# B4 -- ladder order recovery
# --------------------------------------------------------------------------
def recover_ladder(src: str, fn_name: str = "_maybe_startup") -> list[str]:
    """Walk fn_name in source order and emit a token per decision branch.

    We key off the distinctive guards/markers so the recovered list is the
    SAME ORDER the live classifier evaluates. Reordering any branch in a future
    edit changes this list -> B4 fails.
    """
    tree = ast.parse(src)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            fn = node
            break
    if fn is None:
        return []
    body_src = ast.get_source_segment(src, fn) or ""
    markers = [
        ("docked", re.compile(r'getattr\(st,\s*"docked"')),
        ("in_supercruise", re.compile(r'getattr\(st,\s*"in_supercruise"')),
        ("parked_terminal", re.compile(r"_is_parked_terminal\((?:runner,\s*)?st\)")),
        ("p1_indeterminate", re.compile(r"near_star is None or dest is None")),
        ("p2_local_star", re.compile(r"near_star is True")),
        ("p3_fresh_arrival",
         re.compile(r"jump_age is None or jump_age <= FRESH_ARRIVAL_WINDOW_S")),
        ("smacked_cooldown",
         re.compile(r'(?:self|runner)\._smacked and getattr\(st,\s*"fsd_cooldown"')),
        ("empty_route_guard", re.compile(r"if not route:")),
        ("startup", re.compile(r'(?:self|runner)\._run\("startup"\)')),
    ]
    found: list[tuple[int, str]] = []
    for tok, pat in markers:
        m = pat.search(body_src)
        if m:
            found.append((m.start(), tok))
    found.sort()
    return [tok for _, tok in found]


EXPECTED_LADDER = [
    "docked",
    "in_supercruise",
    "parked_terminal",
    "p1_indeterminate",
    "p2_local_star",
    "p3_fresh_arrival",
    "smacked_cooldown",
    "empty_route_guard",
    "startup",
]


# --------------------------------------------------------------------------
# B5/B6 -- procedure TOML checks (regex, no toml dep needed)
# --------------------------------------------------------------------------
DOMAIN_PROCS = [
    "arrival", "dock", "dock_resume", "route_complete_park",
    "sc_resume", "smack_recovery", "startup",
]


def has_honk_track(text: str) -> bool:
    m = re.search(r"parallel_tracks\s*=\s*\[([^\]]*)\]", text)
    return bool(m and '"honk"' in m.group(1))


def references_body_tour(text: str) -> bool:
    return bool(re.search(r'action\s*=\s*"body_tour"', text))


# --------------------------------------------------------------------------
# B7 -- step impls present
# --------------------------------------------------------------------------
REQUIRED_STEPS = [
    # shared flight primitives (-> ed-core)
    "step_orient_compass", "step_orient_widget_ring",
    "step_hold_alignment", "step_pitch_compass",
    # honk track (-> ed-core)
    "step_ensure_analysis_mode", "step_hold_until_event",
    # explore (-> ed-explore)
    "step_body_tour",
]


def defined_funcs(src: str) -> set[str]:
    return {n.name for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.FunctionDef)}


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=None)
    ap.add_argument("--proc", type=Path, default=None)
    ap.add_argument("--boot", type=Path, default=None,
                    help="Path to boot_routes.py (post-split); enables classify_startup checks")
    ap.add_argument("--engine", type=Path, default=None,
                    help="Path to ed-core dispatcher.py (post-split); B3 reads from here")
    ap.add_argument("--explore", type=Path, default=None,
                    help="Path to ed-explore steps_body_tour.py (Step 5+); B7 includes it")
    args = ap.parse_args()
    src_root = args.src or default_src()
    proc_dir = args.proc or default_proc()

    dispatcher = (src_root / "flow" / "dispatcher.py").read_text(encoding="utf-8")
    steps = (src_root / "flow" / "steps.py").read_text(encoding="utf-8")
    boot_src = args.boot.read_text(encoding="utf-8") if args.boot else None
    engine_src = args.engine.read_text(encoding="utf-8") if args.engine else None
    explore_src = args.explore.read_text(encoding="utf-8") if args.explore else None

    results: list[tuple[str, bool, str]] = []

    # B1
    if boot_src is not None:
        b1 = assign_value_in_function(boot_src, "classify_startup",
                                      "FRESH_ARRIVAL_WINDOW_S")
    else:
        b1 = assign_value_in_function(dispatcher, "_maybe_startup",
                                      "FRESH_ARRIVAL_WINDOW_S")
    results.append(("B1 classifier FRESH_ARRIVAL_WINDOW_S == 30.0",
                    b1 == 30.0, f"got {b1!r}"))

    # B2 (distinct from B1)
    b2 = module_constant(steps, "_FRESH_ARRIVAL_WINDOW_S")
    results.append(("B2 steps._FRESH_ARRIVAL_WINDOW_S == 120.0 (!= B1)",
                    b2 == 120.0 and b2 != b1, f"got {b2!r}"))

    # B3
    b3 = module_constant(engine_src if engine_src is not None else dispatcher,
                         "_CLEAR_JOIN_WINDOW_S")
    results.append(("B3 _CLEAR_JOIN_WINDOW_S == 60.0", b3 == 60.0, f"got {b3!r}"))

    # B4
    if boot_src is not None:
        ladder = recover_ladder(boot_src, "classify_startup")
    else:
        ladder = recover_ladder(dispatcher)
    results.append(("B4 _maybe_startup ladder order",
                    ladder == EXPECTED_LADDER,
                    f"got {ladder}"))

    # B5
    missing_honk = []
    for p in DOMAIN_PROCS:
        f = proc_dir / f"{p}.toml"
        if not (f.exists() and has_honk_track(f.read_text(encoding="utf-8"))):
            missing_honk.append(p)
    results.append(('B5 parallel_tracks=["honk"] in all 7 domain procs',
                    not missing_honk, f"missing: {missing_honk}"))

    # B6
    body_procs = [f.stem for f in sorted(proc_dir.glob("*.toml"))
                  if references_body_tour(f.read_text(encoding="utf-8"))]
    results.append(("B6 body_tour wired in EXACTLY arrival.toml",
                    body_procs == ["arrival"], f"got {body_procs}"))

    # B7
    have = defined_funcs(steps)
    if boot_src is not None:
        # Post-split: shared steps are in ed-core steps_shared.py
        # Compute path: boot_routes.py -> ed-autojump/src/ed_autojump/flow/
        # ed-core/src/ed_core/flow/steps_shared.py is 3 dirs up then down to ed-core
        ed_autojump_proj = args.boot.parent.parent.parent.parent  # .../ed-autojump
        steps_shared_p = (ed_autojump_proj.parent / "ed-core" / "src"
                          / "ed_core" / "flow" / "steps_shared.py")
        if steps_shared_p.exists():
            have |= defined_funcs(steps_shared_p.read_text(encoding="utf-8"))
    if explore_src is not None:
        # Step 5+: step_body_tour relocated to ed-explore/steps_body_tour.py
        have |= defined_funcs(explore_src)
    missing_steps = [s for s in REQUIRED_STEPS if s not in have]
    results.append(("B7 shared/honk/body step impls present",
                    not missing_steps, f"missing: {missing_steps}"))

    print("=" * 72)
    print("A2 behavior-preservation lens -- Phase-1 FlowRunner split")
    print("=" * 72)
    ok = True
    for name, passed, detail in results:
        if not passed:
            ok = False
        print(f"[{'PASS' if passed else 'FAIL'}] {name}"
              + ("" if passed else f"   <- {detail}"))
    print()
    print(f"RESULT: {'PASS' if ok else 'FAIL'}")
    print("=" * 72)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
