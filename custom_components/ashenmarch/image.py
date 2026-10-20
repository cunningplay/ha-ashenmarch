"""The House's coat of arms, downloaded with the token and served through HA's own image proxy."""

from __future__ import annotations

from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AshenmarchConfigEntry, AshenmarchCoordinator
from .entity import AshenmarchEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AshenmarchConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the coat-of-arms image for one player."""
    async_add_entities([CoatOfArms(hass, entry.runtime_data)])


class CoatOfArms(AshenmarchEntity, ImageEntity):
    """image.<house>_coat_of_arms. The browser never sees the API or the token."""

    _attr_content_type = "image/png"

    def __init__(self, hass: HomeAssistant, coordinator: AshenmarchCoordinator) -> None:
        """Set up the image."""
        AshenmarchEntity.__init__(self, coordinator, "coat_of_arms")
        ImageEntity.__init__(self, hass)
        self._attr_image_last_updated = coordinator.data.crest_updated if coordinator.data else None

    @property
    def available(self) -> bool:
        """Available once a crest has been downloaded."""
        return bool(self.coordinator.data and self.coordinator.data.crest)

    @callback
    def _handle_coordinator_update(self) -> None:
        """A new crest changes the image URL, so dashboards reload it."""
        updated = self.coordinator.data.crest_updated
        if updated != self._attr_image_last_updated:
            self._cached_image = None
            self._attr_image_last_updated = updated
        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The arms' field colour (for theming a House's page) and version."""
        s = self.coordinator.data.summary if self.coordinator.data else {}
        return {"tincture": s.get("tincture"), "crest_id": s.get("crest_id")}

    async def async_image(self) -> bytes | None:
        """The PNG as last downloaded."""
        return self.coordinator.data.crest if self.coordinator.data else None
