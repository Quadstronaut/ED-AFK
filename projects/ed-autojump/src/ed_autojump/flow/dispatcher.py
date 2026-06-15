"""ed-autojump flow dispatcher -- thin shim, re-exports engine from ed-core.

The FlowRunner engine lives in ed_core.flow.dispatcher. Boot classifier rules
and event routes live in ed_autojump.flow.boot_routes (registered into core
via ed_autojump.activate()). This shim preserves the pre-reorg import surface
so tests/cli that do from ed_autojump.flow.dispatcher import FlowRunner keep
working unchanged.
"""
from ed_core.flow.dispatcher import FlowRunner, _TailHub, _CLEAR_JOIN_WINDOW_S  # noqa: F401

__all__ = ["FlowRunner", "_TailHub", "_CLEAR_JOIN_WINDOW_S"]
