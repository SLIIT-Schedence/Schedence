"""Event-loop helper for running async database code from scripts.

psycopg's async mode cannot use Windows' default ``ProactorEventLoop``; it
needs a ``SelectorEventLoop``. On Linux/macOS (and therefore in containers)
the default loop already works.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


def run(main: Coroutine[Any, Any, T]) -> T:
    """Like :func:`asyncio.run`, but on a selector event loop on Windows."""
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            return runner.run(main)
    return asyncio.run(main)
