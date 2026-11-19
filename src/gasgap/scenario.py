"""Reservation arithmetic. Volumes are taken from the snapshot; this file only applies the shares."""

from __future__ import annotations

RESERVE_SHARE = 0.20
DEMAND_BUFFER = 0.10


def reserve(volume: float, share: float = RESERVE_SHARE) -> float:
    if volume < 0:
        raise ValueError("volume cannot be negative")
    return volume * share


def cover(reserved: float, gap: float) -> dict[str, float]:
    """How much of a positive gap the reserved volume covers, and what is left over.

    A negative gap is spare supply. Nothing is drawn from the reserve, and the
    whole reserve is left over.
    """
    if reserved < 0:
        raise ValueError("reserved cannot be negative")
    unmet = max(gap, 0.0)
    covered = min(reserved, unmet)
    return {
        "gap": gap,
        "unmet": unmet,
        "covered": covered,
        "left_over": reserved - covered,
        "still_short": unmet - covered,
    }


def demand_calibrated(
    forecast_demand: float,
    export_volume: float,
    buffer: float = DEMAND_BUFFER,
    cap: float = RESERVE_SHARE,
) -> dict[str, float | bool]:
    """Obligation = min(forecast demand × (1 + buffer), cap × exports)."""
    if forecast_demand < 0 or export_volume < 0:
        raise ValueError("volumes cannot be negative")
    asked = forecast_demand * (1.0 + buffer)
    capped = export_volume * cap
    return {
        "asked": asked,
        "cap": capped,
        "obligation": min(asked, capped),
        "cap_binds": asked > capped,
    }
