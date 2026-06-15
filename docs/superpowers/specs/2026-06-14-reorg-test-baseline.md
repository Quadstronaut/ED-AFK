# Reorg test baseline (pinned pre-move)

**Captured:** 2026-06-15, on `master` before the Phase-1 workspace reorg.
**Command:** `python -m pytest --tb=no -q` in `projects/ed-autojump`
(config: `-m 'not requires_game'`, `pythonpath=["src"]`).

**Totals:** 1597 collected · 16 failing · **1581 green**.

## The invariant for the reorg
After the reorg, **exactly these 16 tests fail and nothing else.** Any 17th failure =
the move broke something = revert/fix before commit. The 16 are pre-existing red (toml↔test
drift + in-session WIP: the `cv_debug` default flip, the navpanel-icon work) and are OUT OF
SCOPE for the behavior-preserving reorg (spec §8). They get fixed in Phase 2 alongside the
flows they test.

## The 16 pinned reds
```
tests/flow/test_nav_panel_bounded.py::test_route_complete_park_locks_and_orbits_close_star
tests/flow/test_nav_panel_bounded.py::test_route_complete_park_nav_target_is_required_no_skip
tests/flow/test_route_complete_park.py::test_step_order_is_arrival_front_half_only
tests/flow/test_route_complete_park.py::test_nav_panel_target_is_required_orbit_is_best_effort
tests/flow/test_route_complete_park.py::test_retry_anchor_is_the_lock_bounded
tests/flow/test_route_complete_park.py::test_runs_to_completion_firing_lock_and_orbit_only
tests/flow/test_route_complete_park.py::test_required_lock_failure_aborts_after_bounded_retries_no_jump
tests/flow/test_smack_recovery_flow.py::test_v7_step_order
tests/flow/test_smack_recovery_flow.py::test_retry_split_real_space_vs_supercruise
tests/flow/test_smack_recovery_flow.py::test_escape_vector_segment_charge_orient_hold
tests/flow/test_smack_recovery_flow.py::test_first_throttle_is_zero_then_full_burn_before_the_pitch
tests/flow/test_smack_recovery_flow.py::test_toml_carries_supercruise_retry_key_at_the_anchor
tests/flow/test_smack_recovery_flow.py::test_scene_pre_anchor_orient_fail_in_real_space_resumes_at_throttle
tests/test_config_overrides.py::test_defaults_when_nothing_present
tests/vision/test_navpanel_icons.py::test_detect_row_icon_full_frame_star_then_system
tests/vision/test_navpanel_icons.py::test_scan_navpanel_rows_labels_and_boxes
```
