"""Base entity: every entity belongs to the player's House device."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, PORTAL_TOKENS_URL
from .coordinator import AshenmarchCoordinator


class AshenmarchEntity(CoordinatorEntity[AshenmarchCoordinator]):
    """One device per player, named after their House; ids carry the House slug."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AshenmarchCoordinator, key: str) -> None:
        """Attach the entity to the player's House device."""
        super().__init__(coordinator)
        self._attr_translation_key = key
        self._attr_unique_id = f"{coordinator.player_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.player_id)},
            name=coordinator.config_entry.title,
            manufacturer=MANUFACTURER,
            model="Realm",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=PORTAL_TOKENS_URL,
        )

    @property
    def available(self) -> bool:
        """Available once a summary has been read, even if the latest poll failed."""
        return bool(self.coordinator.data and self.coordinator.data.summary)
