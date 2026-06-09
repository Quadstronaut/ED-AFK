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

  --mode step      FULL procedures run against real state with read-only vision
                   built from config. NullSender presses nothing, so vision
                   STEER steps (orient_*) fail-close — expected, itself auditable.

VISIBILITY (operator ask 2026-06-09: "I want the conditions and flags too — what
it is thinking, not just that it got stuck on item 9"): every loop this prints a
[STATE] line whenever ANY watched flag/condition changes (and on a heartbeat),
showing the full set of inputs the dispatch/gates read — supercruise, docked,
FSD charging/cooldown/jump, GUI focus, pips, destination, route, next hop,
witchspace, smacked, jump age, FSS body count, FSD target + star class. The
[DECISION] stream carries every ctx.log/recorder outcome (Step, gate results,
HoldAlignmentDone reason, ProcedureAborted, …). So a "stuck on step 9" shows the
flags it was staring at, not just the step name.

SCOPE / LIMIT (state honestly): keys are OFF, so this harness audits ROUTING and
GATE branches. It does NOT reproduce a keys-ON run's exact event-consumption
TIMING. For that use `ed-autojump run --record --engage-keys`, and for an offline
deterministic replay of a captured journal use `scripts/replay_driver.py`.

Usage (PowerShell, from projects/ed-autojump):
    .venv\Scripts\python scripts\gatewalk.py --mode routing
    .venv\Scripts\python scripts\gatewalk.py --mode step --duration 3600
"""

from __future__ import annotations

import argparse
import threading
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
    t = datetime.now(timezone.utc)
    return t.strftime("%H:%M:%S.") + f"{t.microsecond // 1000:03d}"


def _line(tag: str, msg: str) -> None:
    # One terminal, one stream. Tags are fixed-width so columns line up.
    print(f"{_now()}  {tag:<11} {msg}", flush=True)


# ── state snapshot (real FlowRunner attributes — read, never compute) ───────
def _snapshot(runner: FlowRunner) -> dict:
    """Photograph every input the real dispatch/gate code reads. Uses the bot's
    OWN cached `_latest_status` (set by run_live's `_poll_status`) so we show the
    exact view the gates see and don't race a second reader. Recomputes nothing."""
    st = getattr(runner, "_latest_status", None)
    nr = None
    try:
        nr = runner._navroute_state()
    except Exception:  # noqa: BLE001 — a snapshot must never abort the walk
        pass
    route = getattr(nr, "route", None) if nr is not None else None
    dest = getattr(st, "destination", None) if st is not None else None
    tgt = getattr(runner, "_latest_fsd_target", None)

    def g(obj, attr):
        return getattr(obj, attr, None) if obj is not None else None

    snap = {
        "system": getattr(runner, "_current_system", None),
        "in_supercruise": g(st, "in_supercruise"),
        "docked": g(st, "docked"),
        "fsd_charging": g(st, "fsd_charging"),
        "fsd_cooldown": g(st, "fsd_cooldown"),
        "fsd_jump": g(st, "fsd_jump"),
        "fsd_mass_locked": g(st, "fsd_mass_locked"),
        "low_fuel": g(st, "low_fuel"),
        "gui_focus": g(st, "gui_focus"),
        "pips": g(st, "pips"),
        "destination": g(dest, "name"),
        "route_len": (len(route) if route else 0),
        "next_hop": (g(route[0], "star_system") if route else None),
        "in_witchspace": getattr(runner, "_in_witchspace", None),
        "smacked": getattr(runner, "_smacked", None),
        "fss_body_count": getattr(runner, "_fss_body_count", None),
        "fss_discovered": getattr(runner, "_fss_discovered", None),
        "arrival_star_class": getattr(runner, "_arrival_star_class", None),
        "fsd_target": g(tgt, "name"),
        "fsd_target_class": g(tgt, "star_class"),
        "caught_up": getattr(runner, "_caught_up", None),
        "running_proc": getattr(runner, "_running_proc", None),
    }
    try:
        snap["jump_age_s"] = runner._jump_age()
    except Exception:  # noqa: BLE001
        snap["jump_age_s"] = None
    return snap


def _fmt_state(snap: dict) -> str:
    sc = "DOCKED" if snap.get("docked") else ("SC" if snap.get("in_supercruise") else "normal")
    bits = [f"sys={snap.get('system')}", sc]
    fsd = []
    if snap.get("fsd_charging"):
        fsd.append("CHARGING")
    if snap.get("fsd_cooldown"):
        fsd.append("COOLDOWN")
    if snap.get("fsd_jump"):
        fsd.append("JUMPbit")
    if snap.get("fsd_mass_locked"):
        fsd.append("MASSLOCK")
    if snap.get("in_witchspace"):
        fsd.append("WITCHSPACE")
    if snap.get("smacked"):
        fsd.append("SMACKED")
    bits.append(f"fsd=[{','.join(fsd) or '-'}]")
    bits.append(f"gui={snap.get('gui_focus')}")
    if snap.get("pips") is not None:
        bits.append(f"pips={snap.get('pips')}")
    bits.append(f"route={snap.get('route_len')}")
    if snap.get("next_hop"):
        bits.append(f"next={snap['next_hop']}")
    if snap.get("destination"):
        bits.append(f"dest={snap['destination']}")
    if snap.get("fsd_target"):
        bits.append(f"target={snap['fsd_target']}({snap.get('fsd_target_class')})")
    if snap.get("fss_body_count"):
        bits.append(f"fss={snap['fss_body_count']}")
    if snap.get("arrival_star_class"):
        bits.append(f"arrstar={snap['arrival_star_class']}")
    if snap.get("jump_age_s") is not None:
        bits.append(f"jump_age={snap['jump_age_s']:.0f}s")
    bits.append(f"caught_up={snap.get('caught_up')}")
    if snap.get("running_proc"):
        bits.append(f"RUNNING={snap['running_proc']}")
    return "  ".join(bits)


# ── state monitor (the "what it's thinking" stream) ─────────────────────────
def _state_monitor_loop(runner: FlowRunner, stop: threading.Event,
                        heartbeat_s: float) -> None:
    """Print a [STATE] line whenever any watched flag changes, plus a heartbeat
    every `heartbeat_s` so a WEDGED/STUCK state keeps showing its conditions
    (not silence). Reads only — never steers."""
    prev = None
    last_beat = 0.0
    import time
    while not stop.is_set():
        try:
            snap = _snapshot(runner)
            now = time.monotonic()
            changed = snap != prev
            beat = (now - last_beat) >= heartbeat_s
            if changed or beat:
                _line("[STATE]", _fmt_state(snap))
                prev = snap
                last_beat = now
        except Exception as exc:  # noqa: BLE001 — display must never crash the run
            _line("[STATE-ERR]", repr(exc))
        stop.wait(0.4)


# ── raw game-truth tail (independent of the bot — cross-check) ───────────────
def _raw_tail_loop(journal_dir: Path, stop: threading.Event) -> None:
    """Tail Journal.*.log + Status.json + NavRoute.json with the REAL parsers,
    printing each GAME-TRUTH transition so we can confirm the bot saw reality."""
    jt = JournalTail(journal_dir)
    sr = StatusReader(journal_dir / "Status.json")
    nr = NavRouteReader(journal_dir / "NavRoute.json")
    jt.step()  # drain backlog once; only LIVE transitions on screen
    try:
        sr.poll()
        nr.poll()
    except Exception:  # noqa: BLE001
        pass
    _salient = ("StarSystem", "Body", "BodyType", "JumpType", "StarClass",
                "Name", "Docked", "Scooped", "RemainingJumpsInRoute")
    while not stop.is_set():
        try:
            for ev in jt.step():
                name = getattr(ev, "event", "?")
                fields = []
                dump = ev.model_dump(by_alias=True) if hasattr(ev, "model_dump") else {}
                for k in _salient:
                    v = getattr(ev, k.lower(), None)
                    if v is None:
                        v = dump.get(k)
                    if v is not None:
                        fields.append(f"{k}={v}")
                _line("[JOURNAL]", f"{name}  " + " ".join(fields))
            st = sr.poll()
            if st is not None:
                sc = "DOCKED" if getattr(st, "docked", False) else (
                    "SC" if getattr(st, "in_supercruise", False) else "normal")
                d = getattr(st, "destination", None)
                _line("[STATUS]",
                      f"{sc} charging={getattr(st, 'fsd_charging', None)} "
                      f"cooldown={getattr(st, 'fsd_cooldown', None)} "
                      f"jumpbit={getattr(st, 'fsd_jump', None)} "
                      f"masslock={getattr(st, 'fsd_mass_locked', None)} "
                      f"gui={getattr(st, 'gui_focus', None)} "
                      f"pips={getattr(st, 'pips', None)} "
                      f"dest={getattr(d, 'name', None) if d else None}")
            route = nr.poll()
            if route is not None:
                r = getattr(route, "route", None) or []
                nxt = r[0].star_system if r else None
                last = r[-1].star_system if r else None
                _line("[ROUTE]", f"len={len(r)} next={nxt} final={last}")
        except Exception as exc:  # noqa: BLE001
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
    ap.add_argument("--heartbeat", type=float, default=5.0,
                    help="seconds between [STATE] heartbeats even when unchanged (default 5)")
    ap.add_argument("--sessions-dir", type=Path, default=None,
                    help="where to write the session jsonl (default ~/ed-afk-sessions)")
    ap.add_argument("--no-raw-tail", action="store_true",
                    help="suppress the Journal/Status/NavRoute ground-truth tail")
    args = ap.parse_args(argv)

    cfg_path = ROOT / "config.toml"
    cfg = load_config(cfg_path if cfg_path.is_file() else None)
    journal_dir = cfg.paths.journal_dir_expanded()

    base = args.sessions_dir or (Path.home() / "ed-afk-sessions")
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
    session_path = base / f"gatewalk_{args.mode}_{stamp}.jsonl"
    recorder = Recorder(session_path)

    # record Tee: every ctx.log()/dispatcher.record() lands in the jsonl AND
    # prints live. ctx.log -> ctx.record -> this.
    def record(outcome_type: str, payload) -> None:
        recorder.record_outcome(outcome_type, payload)
        _line("[DECISION]", f"{outcome_type}  {payload}")

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

    if args.mode == "routing":
        def _trace_dispatch(name: str) -> None:
            snap = _snapshot(runner)
            record("GatewalkDispatch", {"procedure": name, "state": snap})
            _line("DISPATCH>", f"==> {name.upper():<16} | {_fmt_state(snap)}")
        runner._run = _trace_dispatch  # type: ignore[method-assign]

    print("=" * 80)
    print(f"  GATE/PATH WALK  ·  mode={args.mode}  ·  keys=OFF (NullSender)")
    print(f"  journal : {journal_dir}")
    print(f"  session : {session_path}")
    print("  [STATE] = every flag/condition the gates read (changes + heartbeat).")
    print("  [DECISION] = every ctx.log/recorder outcome.  DISPATCH> = chosen procedure.")
    print("  [JOURNAL]/[STATUS]/[ROUTE] = game ground-truth.  Ctrl+C to stop.")
    print("=" * 80)

    stop = threading.Event()
    threads = []
    threads.append(threading.Thread(
        target=_state_monitor_loop, args=(runner, stop, args.heartbeat), daemon=True))
    if not args.no_raw_tail:
        threads.append(threading.Thread(
            target=_raw_tail_loop, args=(journal_dir, stop), daemon=True))
    for t in threads:
        t.start()

    try:
        runner.run_live(duration_s=args.duration)
    except KeyboardInterrupt:
        print("\ninterrupted — stopping walk")
        panic.trip()
        runner.request_stop()
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=2.0)
        recorder.close()
        print(f"\nsession written -> {session_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
