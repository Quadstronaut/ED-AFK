from ed_autojump.flow.model import Step, OnRequiredFail, Procedure


def test_step_holds_action_and_params():
    s = Step(action="press", params={"bind": "X", "hold_s": 6.0}, required=True)
    assert s.action == "press"
    assert s.params["bind"] == "X"
    assert s.required is True


def test_procedure_defaults():
    proc = Procedure(name="arrival", steps=(Step(action="wait", params={"s": 1.0}),))
    assert proc.parallel is False
    assert proc.stop_on_event is None
    assert proc.parallel_tracks == ()
    assert proc.on_required_fail == OnRequiredFail()


def test_index_of_action_finds_first_match():
    proc = Procedure(
        name="p",
        steps=(
            Step(action="target_ahead"),
            Step(action="orient_compass", required=True),
            Step(action="orient_compass"),
        ),
    )
    assert proc.index_of_action("orient_compass") == 1
    assert proc.index_of_action("missing") is None
