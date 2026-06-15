#!/usr/bin/env python3
"""A1 -- dep-cycle lens for the Phase-1 ED-AFK workspace reorg.

READ-ONLY. Does NOT import the package, does NOT run pytest, edits nothing.

What it does:
  1. AST-walks projects/ed-autojump/src/ed_autojump and builds the REAL
     intra-package import edge set. DEFERRED (in-function) imports are counted
     as edges by design -- layering must not depend on import-time ordering.
  2. Takes a candidate placement.json (module-dotted-path -> package label) and
     overlays it on the graph.
  3. Checks the reorg invariants I1/I2/I3 + totality:
       - domains never import each other (I2)
       - ed-core never imports a domain (I1)
       - ed-vision imports NOTHING in-workspace (I1/I3)
       - imports point DOWN only (domains -> {core,vision}; core -> vision) (I1)
       - no import cycle of any length, module-level AND package-collapsed (I1/I3)
       - placement.json labels EVERY module (totality / AC1)
  4. Prints per-rule PASS/FAIL with the explicit list of violating edges.

Exit 0 iff RESULT: PASS (zero violations, zero cycles, zero unlabeled).

Usage:
  python reorg_import_graph.py --dump
  python reorg_import_graph.py --placement placement.json
  python reorg_import_graph.py --src <root> --placement placement.json
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import defaultdict
from pathlib import Path

PKG = "ed_autojump"  # top import name of the (pre-move) single package

# Package layering rank. Lower rank may be imported by higher; never reverse.
# Domains share rank 2 and may NOT import one another.
RANK = {
    "ed-vision": 0,
    "ed-core": 1,
    "ed-autojump": 2,
    "ed-explore": 2,
    "ed-combat": 2,
}
DOMAINS = {"ed-autojump", "ed-explore", "ed-combat"}


def default_src() -> Path:
    here = Path(__file__).resolve()
    root = here.parents[4]  # .../reorg_artifacts -> repo root (4 up)
    return root / "projects" / "ed-autojump" / "src" / PKG


def module_name(py: Path, src_root: Path) -> str:
    rel = py.relative_to(src_root.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def resolve_relative(level: int, mod: str | None, current: str) -> str | None:
    """Resolve `from .x import y` to an absolute module, Python semantics.

    level 1 == current package (one component shorter than the module name).
    """
    pkg_parts = current.split(".")[:-1]
    if level > len(pkg_parts) + 1:
        return None
    base = pkg_parts[: len(pkg_parts) - (level - 1)] if level - 1 else pkg_parts
    target = list(base)
    if mod:
        target += mod.split(".")
    return ".".join(target) if target else None


def collect_modules(src_root: Path) -> dict[str, Path]:
    return {module_name(p, src_root): p for p in sorted(src_root.rglob("*.py"))}


def edges_for(py: Path, current: str, known: set[str]) -> set[str]:
    """All intra-package import targets (deferred imports included)."""
    src = py.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src, filename=str(py))
    out: set[str] = set()

    def add_if_known(name: str | None) -> None:
        if not name:
            return
        if name in known:
            out.add(name)
            return
        parent = ".".join(name.split(".")[:-1])
        if parent in known:
            out.add(parent)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = resolve_relative(node.level, node.module, current)
                if base is None:
                    continue
                if base in known:
                    out.add(base)
                for alias in node.names:
                    cand = f"{base}.{alias.name}" if base else alias.name
                    add_if_known(cand)
            else:
                m = node.module or ""
                if m == PKG or m.startswith(PKG + "."):
                    if m in known:
                        out.add(m)
                    for alias in node.names:
                        add_if_known(f"{m}.{alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == PKG or alias.name.startswith(PKG + "."):
                    add_if_known(alias.name)
    out.discard(current)
    return out


def build_graph(src_root: Path) -> tuple[dict[str, Path], dict[str, set[str]]]:
    mods = collect_modules(src_root)
    known = set(mods)
    graph = {name: edges_for(py, name, known) for name, py in mods.items()}
    return mods, graph


def find_cycles(nodes: list[str], adj: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan SCCs > 1 (cycles) plus self-loops."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    stack: list[str] = []
    counter = [0]
    sccs: list[list[str]] = []
    sys.setrecursionlimit(10000)

    def strong(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack[v] = True
        for w in adj.get(v, ()):
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif on_stack.get(w):
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack[w] = False
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                sccs.append(sorted(comp))

    for v in nodes:
        if v not in index:
            strong(v)
    for v in nodes:
        if v in adj.get(v, ()):
            sccs.append([v])
    return sccs


def classify(pu: str, pv: str) -> str | None:
    """Return a violation bucket for a cross-package edge pu -> pv, or None."""
    ru, rv = RANK.get(pu), RANK.get(pv)
    if ru is None or rv is None:
        return "UNKNOWN-PACKAGE"
    if ru == rv:
        return "DOMAIN-DOMAIN" if pu in DOMAINS else "PEER-CORE"
    if ru < rv:  # importer LOWER than importee == upward edge
        if pu == "ed-vision":
            return "VISION-IMPORTS"
        if pu == "ed-core" and pv in DOMAINS:
            return "CORE-IMPORTS-DOMAIN"
        return "UPWARD-EDGE"
    return None  # legal downward edge


def apply_splits(graph: dict[str, set[str]], placement: dict[str, str],
                 splits: dict) -> tuple[dict[str, set[str]], list[str]]:
    """Model planned module SPLITS faithfully on the unmoved tree.

    A split says: "module M is broken in two; the listed edges actually belong
    to a NEW module N (placed in package P)." We rewrite the graph so those
    edges originate from N instead of M, register N as a labeled node, and add
    the M->N edge ONLY if the plan declares the halves still reference each
    other. This lets A1 verify the POST-SPLIT layering without moving any file.

    splits schema (in placement.json under key "_splits"):
      {
        "<source module>": {
          "new_module": "<synthetic module name>",
          "package": "<label for the new module>",
          "move_edges": ["<target module>", ...],     # OUTGOING edges -> new_module
          "redirect_incoming": ["<reader module>", ...],# readers now point at new_module
          "back_edge": false                            # M imports N? (usually no)
        }
      }
    Returns (rewritten_graph, notes[]).
    """
    notes: list[str] = []
    g = {k: set(v) for k, v in graph.items()}
    for src_mod, spec in splits.items():
        if src_mod not in g:
            notes.append(f"split source not found: {src_mod}")
            continue
        new_mod = spec["new_module"]
        moved = set(spec.get("move_edges", []))
        readers = set(spec.get("redirect_incoming", []))
        g.setdefault(new_mod, set())
        placement[new_mod] = spec["package"]
        # relocate named OUTGOING edges from src_mod onto new_mod
        relocated = g[src_mod] & moved
        g[src_mod] -= relocated
        g[new_mod] |= relocated
        # redirect named INCOMING edges (reader -> src_mod) to (reader -> new_mod)
        redirected = []
        for r in readers:
            if r in g and src_mod in g[r]:
                g[r].discard(src_mod)
                g[r].add(new_mod)
                redirected.append(r)
        if spec.get("back_edge"):
            g[src_mod].add(new_mod)
        notes.append(
            f"split {src_mod} -> +{new_mod} [{spec['package']}]; "
            f"moved {sorted(relocated)}; redirected-readers {sorted(redirected)}")
    return g, notes


def check(src_root: Path, placement: dict[str, str]) -> int:
    placement = dict(placement)  # local copy; splits inject synthetic modules
    splits = placement.pop("_splits", {})
    placement.pop("_comment", None)
    mods, graph = build_graph(src_root)
    graph, split_notes = apply_splits(graph, placement, splits)
    all_mods = sorted(graph)

    unlabeled = [m for m in all_mods if m not in placement]
    extra = [m for m in placement if m not in graph]

    viol: dict[str, list[str]] = defaultdict(list)
    for u in all_mods:
        pu = placement.get(u)
        for v in sorted(graph[u]):
            pv = placement.get(v)
            if pu is None or pv is None or pu == pv:
                continue
            bucket = classify(pu, pv)
            if bucket:
                viol[bucket].append(f"{u} -> {v}   [{pu} -> {pv}]")

    cycles = find_cycles(all_mods, graph)

    pkg_adj: dict[str, set[str]] = defaultdict(set)
    for u in all_mods:
        pu = placement.get(u)
        for v in graph[u]:
            pv = placement.get(v)
            if pu and pv and pu != pv:
                pkg_adj[pu].add(pv)
    pkg_cycles = find_cycles(sorted(pkg_adj), pkg_adj)

    ok = True
    print("=" * 72)
    print("A1 dep-cycle lens -- Phase-1 reorg")
    print("=" * 72)
    print(f"modules: {len(all_mods)}   edges: {sum(len(v) for v in graph.values())}")
    for n in split_notes:
        print(f"  split: {n}")
    print()

    def rule(name: str, bad: list[str]) -> None:
        nonlocal ok
        if bad:
            ok = False
        print(f"[{'PASS' if not bad else 'FAIL'}] {name}  ({len(bad)} violating)")
        for e in bad:
            print(f"        {e}")

    rule("TOTALITY: every module labeled", unlabeled)
    if extra:
        print(f"[WARN] placement labels {len(extra)} unknown modules: {extra[:5]}")
    rule("I3  VISION-IMPORTS (ed-vision imports nothing in-workspace)",
         viol["VISION-IMPORTS"])
    rule("I1  CORE-IMPORTS-DOMAIN (ed-core must not import a domain)",
         viol["CORE-IMPORTS-DOMAIN"])
    rule("I2  DOMAIN-DOMAIN (no domain imports another domain)",
         viol["DOMAIN-DOMAIN"])
    rule("I1  UPWARD-EDGE (imports point down only)",
         viol["UPWARD-EDGE"] + viol["PEER-CORE"] + viol["UNKNOWN-PACKAGE"])
    rule("I1/I3  MODULE CYCLES (no import cycle of any length)",
         ["  <->  ".join(c) for c in cycles])
    rule("I1  PACKAGE CYCLES (collapsed graph is a DAG)",
         ["  <->  ".join(c) for c in pkg_cycles])

    print()
    print(f"RESULT: {'PASS' if ok else 'FAIL'}")
    print("=" * 72)
    return 0 if ok else 1


def dump(src_root: Path) -> int:
    mods, graph = build_graph(src_root)
    for u in sorted(mods):
        targets = sorted(graph[u])
        if targets:
            for v in targets:
                print(f"{u} -> {v}")
        else:
            print(f"{u} -> (leaf)")
    print(f"# {len(mods)} modules, {sum(len(v) for v in graph.values())} edges",
          file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=None)
    ap.add_argument("--placement", type=Path, default=None)
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()
    src_root = args.src or default_src()
    if not src_root.exists():
        print(f"ERROR: src root not found: {src_root}", file=sys.stderr)
        return 2
    if args.dump:
        return dump(src_root)
    if not args.placement:
        print("ERROR: --placement required (or --dump)", file=sys.stderr)
        return 2
    placement = json.loads(args.placement.read_text(encoding="utf-8"))
    return check(src_root, placement)


if __name__ == "__main__":
    raise SystemExit(main())
