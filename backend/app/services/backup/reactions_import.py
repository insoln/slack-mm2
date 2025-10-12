"""Legacy stub: reactions now imported inside messages_import single-pass pipeline.

This file intentionally retains a minimal public function so older code paths/tests
that might still import parse_reactions_from_export won't break. The unified importer
increments reaction counters and creates relations atomically with messages.
"""

from typing import Any

__all__ = ["parse_reactions_from_export"]


async def parse_reactions_from_export(*_args: Any, **_kwargs: Any) -> int:  # pragma: no cover
    """No-op stub retained for backwards compatibility.

    Returns 0 to signal that this separate stage does no work anymore.
    """
    return 0
