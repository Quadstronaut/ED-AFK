"""ed-core flow engine — the generic procedure machinery.

Holds the engine half of the flow package: the step context, the TOML loader,
the procedure model, the interpreter, and the merged step registry (surface #3).
The FlowRunner engine (dispatcher) and the jump/dock step impls live in their
own packages (ed-autojump.flow.dispatcher / .steps); the boot classifier +
routes live in ed-autojump.flow.boot_routes. Domains register their steps INTO
the core merged registry; the interpreter reads THAT, never a domain step module.
"""

from .context import StepContext
from .interpreter import ProcedureResult, run_procedure
from .loader import load_procedure, load_procedures, validate_procedure
from .model import OnRequiredFail, Procedure, Step
from .step_registry import (
    INPUT_EXCLUSIVE_ACTIONS,
    STEP_REGISTRY,
    input_exclusive_actions,
    merged_step_registry,
    register_step,
)

__all__ = [
    "StepContext", "ProcedureResult", "run_procedure",
    "load_procedure", "load_procedures", "validate_procedure",
    "OnRequiredFail", "Procedure", "Step",
    "STEP_REGISTRY", "INPUT_EXCLUSIVE_ACTIONS",
    "register_step", "merged_step_registry", "input_exclusive_actions",
]
