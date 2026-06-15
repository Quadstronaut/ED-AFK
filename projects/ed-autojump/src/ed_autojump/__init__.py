"""ed-autojump -- autonomous exploration bot for Elite Dangerous: Odyssey."""

__version__ = "0.2.0"


def activate() -> None:
    """Register ed-autojump boot classifier, event routes, step impls, and
    procedure directory into the core registry surfaces. Called by the CLI host
    active-set registrar before constructing FlowRunner."""
    from . import flow as _flow  # noqa: F401 -- registers jump/dock steps (surface #3)
    from .flow import boot_routes as _br
    _br.activate()
    # Surface #4: procedure directory
    from pathlib import Path
    from ed_core.flow.registry import register_procedure_dir
    register_procedure_dir(Path(__file__).parent.parent.parent / "procedures")
