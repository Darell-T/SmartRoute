from __future__ import annotations

import math


def finite_float(value: object, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def nonnegative_int(value: object, *, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return max(0, int(default))
        return max(0, round(float(value)))
    except (TypeError, ValueError, OverflowError):
        return max(0, int(default))
