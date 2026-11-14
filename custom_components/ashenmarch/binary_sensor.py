"""Under siege: the whole realm (from the summary) and near the player's zones (from the events); breached (summary)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AshenmarchConfigEntry, AshenmarchCoordinator
from .entity import AshenmarchEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AshenmarchConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the siege binary sensors for one player."""
    coordinator = entry.runtime_data
    async_add_entities([UnderSiege(coordinator), UnderSiegeNearby(coordinator), Breached(coordinator)])


class UnderSiege(AshenmarchEntity, BinarySensorEntity):
    """On while any holding in the realm is contested."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:sword-cross"

    def __init__(self, coordinator: AshenmarchCoordinator) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, "under_siege")

    @property
    def is_on(self) -> bool:
        """Whether any holding is contested."""
        return bool(self.coordinator.data.summary.get("under_siege"))


class UnderSiegeNearby(AshenmarchEntity, BinarySensorEntity):
    """On while a siege is open within the radius of one of the selected zones."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:shield-alert"

    def __init__(self, coordinator: AshenmarchCoordinator) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, "under_siege_nearby")

    @property
    def is_on(self) -> bool:
        """Whether a siege is open near a selected zone."""
        return bool(self.coordinator.data.nearby)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Which zones have an open siege nearby, and which territories."""
        nearby = self.coordinator.data.nearby.values()
        zones: list[str] = []
        for siege in nearby:
            zones.extend(z for z in siege.get("zones", []) if z not in zones)
        return {
            "zones": zones,
            "territories": [s.get("name") for s in nearby if s.get("name")],
        }


class Breached(AshenmarchEntity, BinarySensorEntity):
    """On while any of the player's blocks is breached (ASH-4514): the wrist summary's siege line."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:wall-fire"

    def __init__(self, coordinator: AshenmarchCoordinator) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, "breached")

    @property
    def available(self) -> bool:
        """Unavailable on a server that doesn't send the siege line."""
        return super().available and "siege_place" in self.coordinator.data.summary

    @property
    def is_on(self) -> bool:
        """Whether something of the player's is breached."""
        return self.coordinator.data.summary.get("siege_place") is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The street to walk, its deadline, whether it's past, and the holding."""
        s = self.coordinator.data.summary
        place = s.get("siege_place")
        if place is None:
            return {"place": None, "falls_at": None, "overdue": False, "holding": None}
        # On the holding-wide model the siege line names the holding itself and ground_holding is null.
        return {
            "place": place,
            "falls_at": s.get("siege_falls_at"),
            "overdue": bool(s.get("siege_overdue")),
            "holding": s.get("ground_holding") or place,
        }
