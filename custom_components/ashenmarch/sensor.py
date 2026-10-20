"""Realm sensors from the wrist summary (ASH-3709)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, Platform, UnitOfArea, UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import AshenmarchConfigEntry, AshenmarchCoordinator
from .entity import AshenmarchEntity


def _dt(value: Any) -> datetime | None:
    return dt_util.parse_datetime(value) if isinstance(value, str) else None


def _holdings_attrs(s: dict[str, Any]) -> dict[str, Any]:
    area = s.get("area_m2")
    if area is None:
        return {}
    return {
        "area_m2": area,
        "area_km2": round(area / 1e6, 3),
        "area_mi2": round(area / 2_589_988.11, 3),
    }


@dataclass(frozen=True, kw_only=True)
class AshenmarchSensorDescription(SensorEntityDescription):
    """How one sensor reads the summary."""

    value: Callable[[Any], Any]
    attrs: Callable[[Any], dict[str, Any]] | None = None
    crest_picture: bool = False
    source: str = "summary"  # which part of the data the value reads: summary, rank, profile or recent (a list)
    unrecorded: frozenset[str] = frozenset()


SENSORS: tuple[AshenmarchSensorDescription, ...] = (
    AshenmarchSensorDescription(
        key="holdings",
        icon="mdi:castle",
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: s.get("holdings"),
        attrs=_holdings_attrs,
        crest_picture=True,
    ),
    AshenmarchSensorDescription(
        key="under_siege_count",
        icon="mdi:sword-cross",
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: s.get("under_siege"),
    ),
    AshenmarchSensorDescription(
        key="next_fade",
        icon="mdi:timer-sand",
        device_class=SensorDeviceClass.TIMESTAMP,
        value=lambda s: _dt(s.get("next_fade_at")),
    ),
    AshenmarchSensorDescription(
        key="rank",
        icon="mdi:trophy",
        value=lambda s: s.get("rank"),
        attrs=lambda s: {"region": s.get("rank_region"), "title": s.get("rank_title")},
    ),
    AshenmarchSensorDescription(
        key="week_distance",
        icon="mdi:shoe-print",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        suggested_display_precision=1,
        value=lambda s: round(s["week_distance_m"] / 1000, 3)
        if s.get("week_distance_m") is not None
        else None,
    ),
    AshenmarchSensorDescription(
        key="rank_progress",
        icon="mdi:chevron-triple-up",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        source="rank",
        value=lambda r: round(r["fraction"] * 100) if isinstance(r.get("fraction"), (int, float)) else None,
        attrs=lambda r: {k: r.get(k) for k in ("rank", "rank_name", "next_rank", "next_rank_name", "points", "next_threshold", "next_area_m2")},
    ),
    AshenmarchSensorDescription(
        key="area_ever_claimed",
        icon="mdi:map-marker-radius",
        device_class=SensorDeviceClass.AREA,
        native_unit_of_measurement=UnitOfArea.SQUARE_KILOMETERS,
        suggested_display_precision=2,
        state_class=SensorStateClass.TOTAL_INCREASING,
        source="profile",
        value=lambda p: round(p["total_area_claimed_m2"] / 1e6, 4) if isinstance(p.get("total_area_claimed_m2"), (int, float)) else None,
        attrs=lambda p: {"first_claim_at": p.get("first_claim_at")},
    ),
    AshenmarchSensorDescription(
        key="last_event",
        icon="mdi:script-text-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        source="recent",
        value=lambda r: _dt(r[0].get("at")) if r else None,
        # The last events, newest first, for the House's details page: type, at, message (the Herald line), territory.
        attrs=lambda r: {"type": r[0].get("type") if r else None, "recent": r},
        unrecorded=frozenset({"recent"}),
    ),
    AshenmarchSensorDescription(
        key="last_updated",
        icon="mdi:clock-check-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s: _dt(s.get("as_of")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AshenmarchConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the realm sensors for one player."""
    async_add_entities(AshenmarchSensor(entry.runtime_data, d) for d in SENSORS)


class AshenmarchSensor(AshenmarchEntity, SensorEntity):
    """One number from the realm summary."""

    entity_description: AshenmarchSensorDescription

    def __init__(
        self, coordinator: AshenmarchCoordinator, description: AshenmarchSensorDescription
    ) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._unrecorded_attributes = description.unrecorded

    def _source(self) -> Any:
        return getattr(self.coordinator.data, self.entity_description.source)

    @property
    def native_value(self) -> Any:
        """The value from the latest data."""
        return self.entity_description.value(self._source())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Extra detail (area, region, rank title, rank progress, recent events)."""
        if self.entity_description.attrs is None:
            return None
        return self.entity_description.attrs(self._source())

    @property
    def entity_picture(self) -> str | None:
        """Holdings shows the House's coat of arms, through HA's own image proxy (never the API URL)."""
        if not self.entity_description.crest_picture:
            return None
        image_id = er.async_get(self.hass).async_get_entity_id(
            Platform.IMAGE, DOMAIN, f"{self.coordinator.player_id}_coat_of_arms"
        )
        state = self.hass.states.get(image_id) if image_id else None
        return state.attributes.get("entity_picture") if state else None
