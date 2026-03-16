"""Runtime package."""

from prismpipe.revolutionary.computation.runtime.base import Runtime
from prismpipe.revolutionary.computation.runtime.python import PythonRuntime

__all__ = ["Runtime", "PythonRuntime"]
