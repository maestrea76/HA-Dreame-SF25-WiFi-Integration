"""Conservative checks for SF25 controls exposed through Home Assistant.

These are HA-side guards, not claims about the appliance's own interlocks.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from .const import PROP_PROGRAM, PROP_RUNNING, PROP_STATUS, PROP_TEMPERATURE

REMOTE_LID_MAX_TEMPERATURE_C = 40.0


def can_open_lid(data: Mapping[tuple[int, int], Any]) -> bool:
    """Allow the cloud lid command only for a confirmed, cool idle device."""
    try:
        program = int(data[PROP_PROGRAM])
        running = int(data[PROP_RUNNING])
        status = int(data[PROP_STATUS])
        temperature = float(data[PROP_TEMPERATURE])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False

    return (
        program == -1
        and running == -1
        and status == 2
        and math.isfinite(temperature)
        and temperature <= REMOTE_LID_MAX_TEMPERATURE_C
    )
