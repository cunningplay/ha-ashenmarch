"""select.<house>_ground_picture: which holding image.<house>_ground shows (ASH-4514 follow-up).

The same choice as Configure → Ground picture, as an entity, so a dashboard or an automation can switch the
picture (for example to the holding a player just tapped) and back to Automatic. Choosing writes the entry's
options, so Configure and this entity always agree, and the new picture is fetched right away.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_GROUND_HOLDING, DOMAIN, GROUND_AUTO
from .coordinator import AshenmarchConfigEntry, AshenmarchCoordinator
from .entity import AshenmarchEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AshenmarchConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the ground picture choice for one player."""
    async_add_entities([GroundPicture(entry.runtime_data)])


class GroundPicture(AshenmarchEntity, SelectEntity):
    """Automatic, or one holding that has ground (by name)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:map-search-outline"

    def __init__(self, coordinator: AshenmarchCoordinator) -> None:
        """Set up the select."""
        super().__init__(coordinator, "ground_picture")

    def _names(self) -> list[str]:
        holdings = self.coordinator.data.holdings if self.coordinator.data else []
        names = {h["name"] for h in holdings if h.get("name") and h.get("ground") is not None}
        return sorted(names, key=str.casefold)

    @property
    def available(self) -> bool:
        """Unavailable while no holding has ground (legacy player, older server)."""
        return super().available and bool(self._names())

    @property
    def options(self) -> list[str]:
        """Automatic, then every holding with ground."""
        return [GROUND_AUTO, *self._names()]

    @property
    def current_option(self) -> str:
        """The pinned holding while it's still held, else Automatic."""
        pinned = self.coordinator.config_entry.options.get(CONF_GROUND_HOLDING)
        return pinned if pinned in self._names() else GROUND_AUTO

    async def async_select_option(self, option: str) -> None:
        """Pin a holding (or go back to Automatic); the options listener fetches the new picture."""
        if option not in self.options:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="unknown_holding", translation_placeholders={"holding": option}
            )
        entry = self.coordinator.config_entry
        options = {k: v for k, v in entry.options.items() if k != CONF_GROUND_HOLDING}
        if option != GROUND_AUTO:
            options[CONF_GROUND_HOLDING] = option
        if options != dict(entry.options):
            self.hass.config_entries.async_update_entry(entry, options=options)
