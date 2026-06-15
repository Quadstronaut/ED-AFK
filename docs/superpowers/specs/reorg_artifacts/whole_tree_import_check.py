"""Authoritative whole-tree DAG check for the EXECUTED reorg.

Unlike A1 (reorg_import_graph.py), which walks ONE --src and applies a placement
MODEL with _splits, this walks the REAL post-move source of ALL FIVE packages and
checks the actual import edges between them. This is what verifies the executed
multi-package tree (A1, pointed at projects/ed-autojump only, never did).

Layering (rank): ed_vision=0 < ed_core=1 < {ed_autojump, ed_explore, ed_combat}=2.
Rules: a package may import only STRICTLY LOWER-ranked in-workspace packages.
  - ed_vision imports nothing in-workspace.
  - ed_core imports only ed_vision (NEVER a domain).
  - domains import ed_core + ed_vision, NEVER each other.
Deferred (function-local) imports COUNT, exactly as A1 counts them.
Exit 0 iff zero violations.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

PKGS = {"ed_vision": 0, "ed_core": 1,
        "ed_autojump": 2, "ed_explore": 2, "ed_combat": 2, "ed_trading": 2}
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    r"<repo-root>\ED-AFK\projects")

DIRS = {
    "ed_vision": ROOT / "ed-vision" / "src" / "ed_vision",
    "ed_core": ROOT / "ed-core" / "src" / "ed_core",
    "ed_autojump": ROOT / "ed-autojump" / "src" / "ed_autojump",
    "ed_explore": ROOT / "ed-explore" / "src" / "ed_explore",
    "ed_combat": ROOT / "ed-combat" / "src" / "ed_combat",
    "ed_trading": ROOT / "ed-trading" / "src" / "ed_trading",
}


def target_pkg(mod: str | None) -> str | None:
    if not mod:
        return None
    head = mod.split(".")[0]
    return head if head in PKGS else None


violations: list[str] = []
edges = 0
for pkg, base in DIRS.items():
    if not base.exists():
        continue
    for py in base.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError as e:
            violations.append(f"PARSE-ERROR {py}: {e}")
            continue
        for node in ast.walk(tree):
            tgts: list[str] = []
            if isinstance(node, ast.Import):
                tgts = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                # absolute only; relative (level>0) stays intra-package
                if node.level == 0 and node.module:
                    tgts = [node.module]
            for t in tgts:
                tp = target_pkg(t)
                if tp is None or tp == pkg:
                    continue
                edges += 1
                deferred = node.col_offset > 0
                tag = " (deferred)" if deferred else ""
                if PKGS[tp] >= PKGS[pkg]:
                    kind = ("SIDEWAYS" if PKGS[tp] == PKGS[pkg] else "UPWARD")
                    violations.append(
                        f"[{kind}] {pkg} -> {t}{tag}  ({py}:{node.lineno})")

print("=" * 64)
print("Whole-tree DAG check -- REAL post-move imports across 5 packages")
print("=" * 64)
print(f"cross-package edges: {edges}")
if violations:
    print(f"\nVIOLATIONS ({len(violations)}):")
    for v in violations:
        print("  " + v)
    print("\nRESULT: FAIL")
    sys.exit(1)
print("\n[PASS] every cross-package import points strictly DOWN the DAG")
print("RESULT: PASS")
sys.exit(0)
