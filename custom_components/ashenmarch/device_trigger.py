"""Device triggers: "Siege started", "Holding lost", ... on a House device, no YAML needed."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, EVENT_ASHENMARCH, EVENT_TYPES

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend({vol.Required(CONF_TYPE): vol.In(EVENT_TYPES)})


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> list[dict[str, Any]]:
    """One trigger per event type for a House device."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None or not any(i[0] == DOMAIN for i in device.identifiers):
        return []
    return [
        {CONF_PLATFORM: "device", CONF_DOMAIN: DOMAIN, CONF_DEVICE_ID: device_id, CONF_TYPE: t}
        for t in EVENT_TYPES
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Listen for ashenmarch_event of this type from this House."""
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: EVENT_ASHENMARCH,
            event_trigger.CONF_EVENT_DATA: {
                CONF_DEVICE_ID: config[CONF_DEVICE_ID],
                CONF_TYPE: config[CONF_TYPE],
            },
        }
    )
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )
