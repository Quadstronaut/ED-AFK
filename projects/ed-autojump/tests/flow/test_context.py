from ed_autojump.flow.context import StepContext


def test_context_minimal_construction():
    ctx = StepContext(sender=object())
    # safe no-op defaults so steps can call them unconditionally
    assert ctx.status_supplier() is None
    assert ctx.event_time("drop") is None
    assert ctx.compass_reader is None
    assert ctx.frame_grabber is None
    assert ctx.compass_samples == 7
