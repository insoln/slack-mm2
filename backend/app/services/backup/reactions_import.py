"""Deprecated reactions import module.

All reaction handling occurs during message import (single unified pass).
This module remains as a backwards-compatible stub only.
"""

from typing import Any


async def parse_reactions_from_export(
    *_args: Any, **_kwargs: Any
) -> int:  # pragma: no cover
    return 0
