from __future__ import annotations

import os
from typing import Optional

_TRUTHY = {"1", "true", "yes", "on"}


def is_truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in _TRUTHY


def env_is_truthy(name: str) -> bool:
    return is_truthy(os.environ.get(name))
