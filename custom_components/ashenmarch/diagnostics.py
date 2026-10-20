"""Diagnostics: the token is redacted, and no coordinates are included."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_TOKEN
from .coordinator import AshenmarchConfigEntry

TO_REDACT = {CONF_TOKEN}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AshenmarchConfigEntry
) -> dict[str, Any]:
    """What the integration knows about one player."""
    coordinator = entry.runtime_data
    area = coordinator.alert_area()
    data = coordinator.data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "alert_area": {
            "mode": area.mode,
            "zones": [z.entity_id for z in area.zones],
            "radius_km": round(area.radius_km, 2),
            "unit": area.unit,
        },
        "summary": data.summary if data else None,
        "open_sieges_nearby": data.nearby if data else None,
        "crest_bytes": len(data.crest) if data and data.crest else 0,
        "events_feed_available": coordinator.events_available,
        "crest_available": coordinator.crest_available,
        # Out-of-area events still advance the cursor; the latest are kept here.
        "skipped_out_of_area": list(coordinator.skipped),
        "rank_progress": (data.rank or {}).get("fraction") if data else None,
        "recent_events": len(data.recent) if data else 0,
        "holdings_listed": len(data.holdings) if data else 0,
        "holdings_list_available": coordinator.holdings_available,
        "update_interval_s": coordinator.update_interval.total_seconds()
        if coordinator.update_interval
        else None,
        "last_update_success": coordinator.last_update_success,
    }
