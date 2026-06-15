#!/usr/bin/env python3
"""Post-move dep-cycle lens for the Phase-1 ED-AFK workspace reorg.

READ-ONLY. Does NOT import any package, run pytest, or edit anything.

A1 (reorg_import_graph.py) validates the PLACEMENT MAP on the single, unmoved
`ed_autojump` tree — it cannot walk the SCATTERED post-move tree (it hardcodes
one src root + the `ed_autojump` top name). This script is the companion gate for
AFTER files physically move: it AST-walks ALL FIVE package src roots
(ed_core / ed_vision / ed_autojump / ed_explore / ed_combat), builds the REAL
cross-package import edge set (deferred / in-function imports counted, same as
A1), and asserts the SAME reorg invariants on the real on-disk tree:

  I3  ed-vision imports NOTHING in-workspace (true bottom leaf)
  I1  ed-core never imports a domain
  I2  no domain imports another domain
  I1  imports point DOWN only (domains -> {core,vision}; core -> vision)
  I1/I3  no import cycle of any length (module-level AND package-collapsed)

Exit 0 iff RESULT: PASS (zero violating edges, zero cycles).

Usage:
  python reorg_postmove_depcheck.py
  python reorg_postmove_depcheck.py --root <repo root>
"""
from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from pathlib import Path

# top import name -> package label
PKG_LABEL = {
    "ed_vision": "ed-vision",
    "ed_core": "ed-core",
    "ed_autojump": "ed-autojump",
    "ed_explore": "ed-explore",
    "ed_combat": "ed-combat",
}
TOP_NAMES = set(PKG_LABEL)

RANK = {
    "ed-vision": 0,
    "ed-core": 1,
    "ed-autojump": 2,
    "ed-explore": 2,
    "ed-combat": 2,
}
DOMAINS = {"ed-autojump", "ed-explore", "ed-combat"}

# package label -> src root, relative to repo root
SRC_DIRS = {
    "ed_vision": "projects/ed-vision/src/ed_vision",
    "ed_core": "projects/ed-core/src/ed_core",
    "ed_autojump": "projects/ed-autojump/src/ed_autojump",
    "ed_explore": "projects/ed-explore/src/ed_explore",
    "ed_combat": "projects/ed-combat/src/ed_combat",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def module_name(py: Path, src_parent: Path) -> str:
    rel = py.relative_to(src_parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def resolve_relative(level: int, mod: str | None, current: str) -> str | None:
    pkg_parts = current.split(".")[:-1]
    if level > len(pkg_parts) + 1:
        return None
    base = pkg_parts[: len(pkg_parts) - (level - 1)] if level - 1 else pkg_parts
    target = list(base)
    if mod:
        target += mod.split(".")
    return ".".join(target) if target else None


def collect_all(root: Path) -> dict[str, Path]:
    mods: dict[str, Path] = {}
    for top, rel in SRC_DIRS.items():
        src = root / rel
        if not src.exists():
            continue
        for p in sorted(src.rglob("*.py")):
            mods[module_name(p, src.parent)] = p
    return mods


def edges_for(py: Path, current: str, known: set[str]) -> set[str]:
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
                top = m.split(".")[0]
                if top in TOP_NAMES:
                    if m in known:
                        out.add(m)
                    for alias in node.names:
                        add_if_known(f"{m}.{alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in TOP_NAMES:
                    add_if_known(alias.name)
    out.discard(current)
    return out


def label_of(mod: str) -> str:
    return PKG_LABEL[mod.split(".")[0]]


def classify(pu: str, pv: str) -> str | None:
    ru, rv = RANK[pu], RANK[pv]
    if ru == rv:
        return "DOMAIN-DOMAIN" if pu in DOMAINS else None
    if ru < rv:  # importer lower than importee == upward
        if pu == "ed-vision":
            return "VISION-IMPORTS"
        if pu == "ed-core" and pv in DOMAINS:
            return "CORE-IMPORTS-DOMAIN"
        return "UPWARD-EDGE"
    return None  # legal downward edge


def find_cycles(nodes: list[str], adj: dict[str, set[str]]) -> list[list[str]]:
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=None)
    args = ap.parse_args()
    root = args.root or repo_root()

    mods = collect_all(root)
    known = set(mods)
    graph = {name: edges_for(py, name, known) for name, py in mods.items()}
    all_mods = sorted(graph)

    viol: dict[str, list[str]] = defaultdict(list)
    for u in all_mods:
        pu = label_of(u)
        for v in sorted(graph[u]):
            pv = label_of(v)
            if pu == pv:
                continue
            bucket = classify(pu, pv)
            if bucket:
                viol[bucket].append(f"{u} -> {v}   [{pu} -> {pv}]")

    cycles = find_cycles(all_mods, graph)

    pkg_adj: dict[str, set[str]] = defaultdict(set)
    for u in all_mods:
        pu = label_of(u)
        for v in graph[u]:
            pv = label_of(v)
            if pu != pv:
                pkg_adj[pu].add(pv)
    pkg_cycles = find_cycles(sorted(pkg_adj), pkg_adj)

    ok = True
    print("=" * 72)
    print("Post-move dep-cycle lens -- Phase-1 reorg (real scattered tree)")
    print("=" * 72)
    present = sorted({label_of(m) for m in all_mods})
    print(f"modules: {len(all_mods)}   edges: {sum(len(v) for v in graph.values())}"
          f"   packages present: {present}")
    print()

    def rule(name: str, bad: list[str]) -> None:
        nonlocal ok
        if bad:
            ok = False
        print(f"[{'PASS' if not bad else 'FAIL'}] {name}  ({len(bad)} violating)")
        for e in bad:
            print(f"        {e}")

    rule("I3  VISION-IMPORTS (ed-vision imports nothing in-workspace)",
         viol["VISION-IMPORTS"])
    rule("I1  CORE-IMPORTS-DOMAIN (ed-core must not import a domain)",
         viol["CORE-IMPORTS-DOMAIN"])
    rule("I2  DOMAIN-DOMAIN (no domain imports another domain)",
         viol["DOMAIN-DOMAIN"])
    rule("I1  UPWARD-EDGE (imports point down only)",
         viol["UPWARD-EDGE"])
    rule("I1/I3  MODULE CYCLES (no import cycle of any length)",
         ["  <->  ".join(c) for c in cycles])
    rule("I1  PACKAGE CYCLES (collapsed graph is a DAG)",
         ["  <->  ".join(c) for c in pkg_cycles])

    print()
    print(f"RESULT: {'PASS' if ok else 'FAIL'}")
    print("=" * 72)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
