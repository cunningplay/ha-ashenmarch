"""Ashenmarch: a player's realm (holdings, sieges, coat of arms) and alerts for territory near their zones."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store

from .const import DOMAIN, ISSUE_NO_ZONES, STORAGE_VERSION
from .coordinator import AshenmarchConfigEntry, AshenmarchCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.IMAGE, Platform.SENSOR]

# The House prefix is the client's to add, localized (the API returns the bare name, D-261),
# with the same words the Ashenmarch app uses.
HOUSE_PREFIX = {"en": "House", "es": "Casa", "fr": "Maison", "pt": "Casa", "de": "Haus", "it": "Casa"}


def house_title(language: str, house_name: str | None) -> str:
    """'House Cunningham', 'Casa Cunningham', ... in HA's language."""
    if not house_name:
        return "Ashenmarch"
    prefix = HOUSE_PREFIX.get(language.split("-")[0].lower(), HOUSE_PREFIX["en"])
    return f"{prefix} {house_name}"


async def async_setup_entry(hass: HomeAssistant, entry: AshenmarchConfigEntry) -> bool:
    """Set up one player."""
    coordinator = AshenmarchCoordinator(hass, entry)
    await coordinator.async_load_store()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AshenmarchConfigEntry) -> bool:
    """Unload one player; the others keep running."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Forget a removed player's cursor, crest and open sieges."""
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()
    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_NO_ZONES}_{entry.entry_id}")
