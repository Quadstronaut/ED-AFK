"""ed-autojump flow — the jump/dock domain half of the flow package.

The generic engine (context/loader/model/interpreter + the merged step registry)
lives in ed_core.flow. This package holds the jump/dock step impls (steps) and
the FlowRunner engine instance (dispatcher). Re-exports the same public surface
the rest of ed-autojump (cli) imported pre-reorg, sourcing the engine names from
ed_core.flow so `from ed_autojump.flow import FlowRunner, load_procedures, ...`
keeps working unchanged.

NOTE: importing this module imports .steps, which registers the jump/dock steps
into the core merged registry as a side effect (registration surface #3).
"""

from ed_core.flow.context import StepContext
from ed_core.flow.interpreter import ProcedureResult, run_procedure
from ed_core.flow.loader import load_procedure, load_procedures, validate_procedure
from ed_core.flow.model import OnRequiredFail, Procedure, Step
from ed_core.flow.step_registry import STEP_REGISTRY

from .dispatcher import FlowRunner
from . import steps as _steps  # noqa: F401 — import-for-side-effect: registers jump/dock steps

__all__ = [
    "StepContext", "FlowRunner", "ProcedureResult", "run_procedure",
    "load_procedure", "load_procedures", "validate_procedure",
    "OnRequiredFail", "Procedure", "Step", "STEP_REGISTRY",
]
