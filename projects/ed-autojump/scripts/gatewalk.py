r"""Gate/Path Walk harness — the Interactive 1-by-1 testing session.

Plan: docs/superpowers/plans/2026-06-08-future-test-gate-path-walk.md
Operator framing: "iteratively, interactively, 1 by 1 test every condition,
gate, path of the code ... Exact code extractions must be made, nothing new
generated in the test that doesn't exist in the code ... monitor through script
and active log monitors every action, at every distance."

INVIOLABLE PRINCIPLE (plan §1): this harness drives the REAL code — the real
`FlowRunner`, the real `_maybe_startup`/`dispatch` branches, the real
`STEP_REGISTRY` gate functions — fed real game state. Nothing here re-implements
a gate. Operator drives the ship; the bot OBSERVES and logs which branch the real
code takes. Keys are OFF (NullSender) so the bot never fights the operator.

Two modes:

  --mode routing   Pure ROUTING trace. `runner._run` is replaced by a tracer
                   that LOGS the dispatch decision (which procedure the real
                   `_maybe_startup`/`dispatch` chose, plus a full state snapshot)
                   and returns WITHOUT executing the procedure's motor steps.
                   This is the precise instrument for plan §5.2 "confusion at
                   different starting locations" — walk every `_maybe_startup`
                   branch and every `dispatch(event)` branch with real states.
                   The decision code is 100% real; only the motor execution is
                   skipped (Operator flies the motor manually).

  --mode step      FULL procedures run against real state with read-only vision
                   built from config (compass / widget / nav-panel readers).
                   NullSender still presses nothing, so vision STEER steps
                   (orient_*) cannot succeed and will fail-closed — that is
                   expected and itself auditable. Use this to walk the per-step
                   gates (Status preconditions, danger filter, scoop gates,
                   nav-panel reads) against live frames.

SCOPE / LIMIT (state honestly): keys are OFF, so this harness audits ROUTING
and GATE branches. It does NOT reproduce a keys-ON run's exact event-consumption
TIMING (a procedure blocking ~70s on a live jump while its in-procedure waiter
drains the journal). For a timing-path reproduction use the real engine with
keys: `ed-autojump run --record --engage-keys`. See the checklist for which
rows each tool covers.

Usage (PowerShell, from projects/ed-autojump):
    .venv\Scripts\python scripts\gatewalk.py --mode routing
    .venv\Scripts\python scripts\gatewalk.py --mode step --duration 3600
"""

from __future__ import annotations

import argparse
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ed_autojump.config import load_config
from ed_autojump.flow import FlowRunner, load_procedures
from ed_autojump.flow.loader import validate_procedure
from ed_autojump.flow.steps import STEP_REGISTRY
from ed_autojump.journal.tail import JournalTail
from ed_autojump.keys import LoggingSender, NullSender
from ed_autojump.panic import PanicSwitch
from ed_autojump.recorder import Recorder
from ed_autojump.status.navroute import NavRouteReader
from ed_autojump.status.status import StatusReader

ROOT = Path(__file__).resolve().parents[1]


# ── pretty-printing ────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S.") + \
        f"{datetime.now(timezone.utc).microsecond // 1000:03d}"


def _line(tag: str, msg: str) -> None:
    # One terminal, one stream. Tags are fixed-width so columns line up.
    print(f"{_now()}  {tag:<10} {msg}", flush=True)


# ── state snapshot (real FlowRunner attributes — read, never compute) ───────
def _snapshot(runner: FlowRunner) -> dict:
    """Pull the same state the real dispatch branches read, for the record.

    Every value comes straight off the live FlowRunner / real readers — this
    does NOT recompute any gate, it just photographs the inputs a gate sees."""
    st = None
    try:
        st = runner._fresh_status()
    except Exception:  # noqa: BLE001 — a snapshot must never abort the walk
        pass
    nr = None
    try:
        nr = runner._navroute_state()
    except Exception:  # noqa: BLE001
        pass
    route = getattr(nr, "route", None) if nr is not None else None
    dest = getattr(st, "destination", None) if st is not None else None
    snap = {
        "system": getattr(runner, "_current_system", None),
        "in_supercruise": getattr(st, "in_supercruise", None) if st else None,
        "docked": getattr(st, "docked", None) if st else None,
        "fsd_charging": getattr(st, "fsd_charging", None) if st else None,
        "fsd_cooldown": getattr(st, "fsd_cooldown", None) if st else None,
        "fsd_jump": getattr(st, "fsd_jump", None) if st else None,
        "gui_focus": getattr(st, "gui_focus", None) if st else None,
        "destination": (getattr(dest, "name", None) if dest else None),
        "route_len": (len(route) if route else 0),
        "next_hop": (getattr(route[0], "star_system", None)
                     if route else None),
        "in_witchspace": getattr(runner, "_in_witchspace", None),
        "smacked": getattr(runner, "_smacked", None),
    }
    try:
        snap["jump_age_s"] = runner._jump_age()
    except Exception:  # noqa: BLE001
        snap["jump_age_s"] = None
    return snap


def _fmt_snapshot(snap: dict) -> str:
    sc = "SC" if snap.get("in_supercruise") else "normal"
    if snap.get("docked"):
        sc = "DOCKED"
    bits = [
        f"sys={snap.get('system')}",
        sc,
        f"route={snap.get('route_len')}",
    ]
    if snap.get("next_hop"):
        bits.append(f"next={snap['next_hop']}")
    if snap.get("destination"):
        bits.append(f"dest={snap['destination']}")
    if snap.get("in_witchspace"):
        bits.append("WITCHSPACE")
    if snap.get("smacked"):
        bits.append("SMACKED")
    if snap.get("jump_age_s") is not None:
        bits.append(f"jump_age={snap['jump_age_s']:.0f}s")
    return "  ".join(bits)


# ── raw game-truth tail (independent of the bot — cross-check) ───────────────
def _raw_tail_loop(journal_dir: Path, stop: threading.Event) -> None:
    """Tail Journal.*.log + Status.json + NavRoute.json with the REAL parsers,
    printing each GAME-TRUTH transition so we can confirm the bot saw reality.

    StatusReader/NavRouteReader.poll() return None when the file is unchanged
    (mtime-deduped), so a non-None poll IS a transition — no manual diffing."""
    jt = JournalTail(journal_dir)
    sr = StatusReader(journal_dir / "Status.json")
    nr = NavRouteReader(journal_dir / "NavRoute.json")
    jt.step()  # drain backlog once; we only want LIVE transitions on screen
    # Prime the readers so we don't dump the whole current state as "changes".
    try:
        sr.poll()
        nr.poll()
    except Exception:  # noqa: BLE001
        pass
    _salient = ("StarSystem", "Body", "BodyType", "JumpType", "StarClass",
                "Name", "Docked", "Scooped")
    while not stop.is_set():
        try:
            for ev in jt.step():
                name = getattr(ev, "event", "?")
                fields = []
                for k in _salient:
                    v = getattr(ev, k.lower(), None)
                    if v is None and hasattr(ev, "model_dump"):
                        v = ev.model_dump(by_alias=True).get(k)
                    if v is not None:
                        fields.append(f"{k}={v}")
                _line("[JOURNAL]", f"{name}  " + " ".join(fields))
            st = sr.poll()
            if st is not None:
                sc = "SC" if getattr(st, "in_supercruise", False) else "normal"
                if getattr(st, "docked", False):
                    sc = "DOCKED"
                d = getattr(st, "destination", None)
                _line("[STATUS]",
                      f"{sc} charging={getattr(st, 'fsd_charging', None)} "
                      f"cooldown={getattr(st, 'fsd_cooldown', None)} "
                      f"gui={getattr(st, 'gui_focus', None)} "
                      f"dest={getattr(d, 'name', None) if d else None}")
            route = nr.poll()
            if route is not None:
                r = getattr(route, "route", None) or []
                nxt = r[0].star_system if r else None
                last = r[-1].star_system if r else None
                _line("[ROUTE]", f"len={len(r)} next={nxt} final={last}")
        except Exception as exc:  # noqa: BLE001 — display must never crash the run
            _line("[TAIL-ERR]", repr(exc))
        stop.wait(0.5)


# ── main ─────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["routing", "step"], default="routing",
                    help="routing: trace dispatch only (default). "
                         "step: run full procedures with read-only vision.")
    ap.add_argument("--duration", type=float, default=3600.0,
                    help="seconds to run the walk (default 3600)")
    ap.add_argument("--sessions-dir", type=Path, default=None,
                    help="where to write the session jsonl (default ~/ed-afk-sessions)")
    ap.add_argument("--no-raw-tail", action="store_true",
                    help="suppress the Journal/Status/NavRoute ground-truth tail")
    args = ap.parse_args(argv)

    cfg_path = ROOT / "config.toml"
    cfg = load_config(cfg_path if cfg_path.is_file() else None)
    journal_dir = cfg.paths.journal_dir_expanded()

    # Session recorder — the canonical audit trail (journal NOT included; the
    # raw tail shows that). Distinct gatewalk_ prefix so these never mix with
    # real flight sessions in tests/test_recorded_sessions.py.
    base = args.sessions_dir or (Path.home() / "ed-afk-sessions")
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
    session_path = base / f"gatewalk_{args.mode}_{stamp}.jsonl"
    recorder = Recorder(session_path)

    # record Tee: every ctx.log()/dispatcher.record() lands in the jsonl AND
    # prints live. ctx.log -> ctx.record -> this. So Step/HoldAlignmentDone/
    # ProcedureAborted/ArrivalOnRestart/RouteComplete all stream here.
    def record(outcome_type: str, payload) -> None:
        recorder.record_outcome(outcome_type, payload)
        _line("[DECISION]", f"{outcome_type}  {payload}")

    # NullSender = keys OFF. LoggingSender wraps it so the (empty) keypress log
    # is still recorded, exactly like the real `run --record` path.
    sender = LoggingSender(NullSender(), recorder)

    status_reader = StatusReader(journal_dir / "Status.json")
    navroute_reader = NavRouteReader(journal_dir / "NavRoute.json")

    procedures = load_procedures(ROOT / "procedures")
    problems = []
    for proc in procedures.values():
        problems += validate_procedure(proc, known_actions=STEP_REGISTRY.keys())
    if problems:
        for p in problems:
            print(f"procedure error: {p}")
        return 2

    # Vision: OFF in routing mode (procedures never run). READ-ONLY in step
    # mode — the same builders the real run uses, gated by config calibration.
    compass_reader = frame_grabber = None
    widget_ring_reader = widget_frame_grabber = None
    nav_panel_reader = nav_panel_grabber = None
    if args.mode == "step":
        from ed_autojump.vision.capture import (build_navpanel_vision,
                                                build_vision,
                                                build_widget_vision)
        compass_reader, frame_grabber = build_vision(cfg)
        nav_panel_reader, nav_panel_grabber = build_navpanel_vision(cfg)
        if cfg.vision.widget_ring_alignment:
            widget_ring_reader, widget_frame_grabber = build_widget_vision(cfg)
        _line("[SETUP]",
              f"vision read-only: compass={compass_reader is not None} "
              f"navpanel={nav_panel_reader is not None} "
              f"widget={widget_ring_reader is not None}")

    align_kwargs = dict(
        align_tol=cfg.vision.align_tol, deadzone=cfg.vision.deadzone,
        gain=cfg.vision.gain, min_press=cfg.vision.min_press_s,
        max_press=cfg.vision.max_press_s, search_press=cfg.vision.search_press_s,
        settle_s=cfg.vision.settle_s, max_iters=cfg.vision.max_iters,
        timeout_s=cfg.vision.timeout_s,
    )

    panic = PanicSwitch()
    runner = FlowRunner(
        procedures=procedures,
        sender=sender,
        status_reader=status_reader,
        navroute_reader=navroute_reader,
        compass_reader=compass_reader,
        frame_grabber=frame_grabber,
        align_kwargs=align_kwargs,
        compass_samples=cfg.vision.align_samples,
        widget_ring_enabled=cfg.vision.widget_ring_alignment and args.mode == "step",
        widget_ring_reader=widget_ring_reader,
        widget_frame_grabber=widget_frame_grabber,
        widget_ring_on_miss=cfg.vision.widget_ring_on_miss,
        body_tour_enabled=cfg.exploration.body_tour_enabled,
        body_tour_dwell_s=cfg.exploration.body_tour_dwell_s,
        body_tour_max_bodies=cfg.exploration.body_tour_max_bodies,
        body_tour_max_rows=cfg.exploration.body_tour_max_rows,
        body_tour_orbit_timeout_s=cfg.exploration.body_tour_orbit_timeout_s,
        body_tour_min_bodies=cfg.exploration.body_tour_min_bodies,
        nav_panel_reader=nav_panel_reader,
        nav_panel_grabber=nav_panel_grabber,
        overlay=None,
        record=record,
        tail=JournalTail(journal_dir),
        panic_switch=panic,
    )

    # ROUTING mode: replace the motor (_run) with a decision tracer. run_live's
    # real _maybe_startup / dispatch / _caught_up / witchspace logic is left
    # untouched — only the procedure EXECUTION is skipped, so the trace is the
    # pure routing decision for each real state Operator drives the ship into.
    if args.mode == "routing":
        def _trace_dispatch(name: str) -> None:
            snap = _snapshot(runner)
            record("GatewalkDispatch", {"procedure": name, "state": snap})
            _line("DISPATCH>", f"--> {name.upper():<16} | {_fmt_snapshot(snap)}")
        runner._run = _trace_dispatch  # type: ignore[method-assign]

    # ── banner ──
    print("=" * 78)
    print(f"  GATE/PATH WALK  ·  mode={args.mode}  ·  keys=OFF (NullSender)")
    print(f"  journal : {journal_dir}")
    print(f"  session : {session_path}")
    if args.mode == "routing":
        print("  Operator drives the ship into each state; each line marked")
        print("  'DISPATCH>' is the REAL procedure the live code would run.")
        print("  Watch a full jump: normal+route -> SC -> hyperspace -> arrival.")
    else:
        print("  Full procedures run vs live state; orient_* fail-close (no keys).")
    print("  Ctrl+C to stop.")
    print("=" * 78)

    stop = threading.Event()
    tail_thread = None
    if not args.no_raw_tail:
        tail_thread = threading.Thread(
            target=_raw_tail_loop, args=(journal_dir, stop), daemon=True)
        tail_thread.start()

    try:
        runner.run_live(duration_s=args.duration)
    except KeyboardInterrupt:
        print("\ninterrupted — stopping walk")
        panic.trip()
        runner.request_stop()
    finally:
        stop.set()
        if tail_thread is not None:
            tail_thread.join(timeout=2.0)
        recorder.close()
        print(f"\nsession written -> {session_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
