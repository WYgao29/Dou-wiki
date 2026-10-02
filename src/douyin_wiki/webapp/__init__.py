"""Local-only Web v0.1.8 for capturing, browsing, and chatting with the Vault."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .app import create_app as create_app
    from .app import run_web as run_web

__all__ = ["create_app", "run_web"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from .app import create_app, run_web

    exports = {"create_app": create_app, "run_web": run_web}
    globals().update(exports)
    return exports[name]


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
