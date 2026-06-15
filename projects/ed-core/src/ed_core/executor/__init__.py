"""ed-core executor — the shared closed-loop maneuver controllers.

Holds align.py, the compass closed-loop align controller backing the shared
step_orient_compass primitive (it imports only ed_vision, downward). The
jump/dock executors (jump.py, navpanel.py) are jump-domain and live in
ed_autojump.executor.
"""
