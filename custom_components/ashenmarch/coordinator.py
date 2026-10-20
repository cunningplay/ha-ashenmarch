"""Coordinator: one per player. Polls the realm summary and the events feed every 5 minutes."""

from __future__ import annotations

import base64
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.translation import async_get_translations
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    AshenmarchApi,
    AshenmarchAuthError,
    AshenmarchError,
    AshenmarchNotAvailable,
    AshenmarchRateLimited,
)
from .const import (
    AREA_ALL,
    AREA_FILTERED_TYPES,
    AREA_NEAR,
    CONF_ALERT_AREA,
    CONF_RADIUS_KM,
    CONF_TOKEN,
    CONF_ZONES,
    CREST_MAX_AGE,
    DEFAULT_ZONE,
    DOMAIN,
    EVENT_ASHENMARCH,
    EVENTS_MAX_PAGES,
    EVENTS_PAGE_LIMIT,
    HOLDING_LOST,
    ISSUE_NO_ZONES,
    MAX_BACKOFF,
    NEAREST_ZONE_MAX_KM,
    SCAN_INTERVAL,
    SEEN_IDS_CAP,
    SIEGE_ENDED,
    SIEGE_STARTED,
    RECENT_CAP,
    SKIPPED_CAP,
    SLOW_INTERVAL,
    STORAGE_VERSION,
)
from .geo import Zone, ZoneMatch, default_radius, format_distance, from_km, to_km, zone_distances

_LOGGER = logging.getLogger(__name__)

type AshenmarchConfigEntry = ConfigEntry[AshenmarchCoordinator]


@dataclass
class AlertArea:
    """The alert area as it applies right now (zones that exist, radius in km)."""

    mode: str
    zones: list[Zone]
    radius_km: float
    unit: str  # "mi" or "km", from HA's unit system


@dataclass
class AshenmarchData:
    """Everything the entities read."""

    summary: dict[str, Any] = field(default_factory=dict)
    nearby: dict[str, dict[str, Any]] = field(default_factory=dict)  # territory id -> {name, zones}
    crest: bytes | None = None
    crest_updated: datetime | None = None
    rank: dict[str, Any] = field(default_factory=dict)  # /players/me/rank, hourly
    profile: dict[str, Any] = field(default_factory=dict)  # total_area_claimed_m2, first_claim_at (hourly)
    recent: list[dict[str, Any]] = field(default_factory=list)  # the last events, newest first


def display_unit(hass: HomeAssistant) -> str:
    """Miles or km, following HA's own unit system."""
    return "mi" if hass.config.units.length_unit == UnitOfLength.MILES else "km"


class AshenmarchCoordinator(DataUpdateCoordinator[AshenmarchData]):
    """Polls one player's realm and fires ashenmarch_event for what happened since the last poll."""

    config_entry: AshenmarchConfigEntry

    def __init__(self, hass: HomeAssistant, entry: AshenmarchConfigEntry) -> None:
        """Set up the coordinator for one player."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=SCAN_INTERVAL,
        )
        self.api = AshenmarchApi(async_get_clientsession(hass), entry.data[CONF_TOKEN])
        self.player_id: str = entry.unique_id or ""
        self.house: str = entry.data.get("house_name") or ""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._cursor: str | None = None
        self._seen: deque[str] = deque(maxlen=SEEN_IDS_CAP)
        self._summary: dict[str, Any] = {}
        self._summary_etag: str | None = None
        self._nearby: dict[str, dict[str, Any]] = {}
        self._crest: bytes | None = None
        self._crest_etag: str | None = None
        self._crest_id: str | None = None
        self._crest_fetched: datetime | None = None
        self.skipped: deque[dict[str, Any]] = deque(maxlen=SKIPPED_CAP)
        self._recent: deque[dict[str, Any]] = deque(maxlen=RECENT_CAP)  # newest first
        self._rank: dict[str, Any] = {}
        self._profile: dict[str, Any] = {}
        self._slow_at: datetime | None = None  # when profile + rank were last read
        self.events_available = True
        self.crest_available = True
        self._device_id: str | None = None

    # ---- persistence ----

    async def async_load_store(self) -> None:
        """Restore the cursor, the open nearby sieges and the last summary and crest."""
        stored = await self._store.async_load() or {}
        self._cursor = stored.get("cursor")
        self._seen.extend(stored.get("seen") or [])
        self._summary = stored.get("summary") or {}
        self._summary_etag = stored.get("summary_etag")
        self._nearby = stored.get("nearby") or {}
        if crest := stored.get("crest"):
            try:
                self._crest = base64.b64decode(crest)
            except ValueError:
                self._crest = None
        self._crest_etag = stored.get("crest_etag")
        self._crest_id = stored.get("crest_id")
        self._crest_fetched = dt_util.parse_datetime(stored.get("crest_fetched") or "")
        self.skipped.extend(stored.get("skipped") or [])
        self._recent.extend(stored.get("recent") or [])
        self._rank = stored.get("rank") or {}
        self._profile = stored.get("profile") or {}
        self._slow_at = dt_util.parse_datetime(stored.get("slow_at") or "")

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "cursor": self._cursor,
                "seen": list(self._seen),
                "summary": self._summary,
                "summary_etag": self._summary_etag,
                "nearby": self._nearby,
                "crest": base64.b64encode(self._crest).decode() if self._crest else None,
                "crest_etag": self._crest_etag,
                "crest_id": self._crest_id,
                "crest_fetched": self._crest_fetched.isoformat() if self._crest_fetched else None,
                "skipped": list(self.skipped),
                "recent": list(self._recent),
                "rank": self._rank,
                "profile": self._profile,
                "slow_at": self._slow_at.isoformat() if self._slow_at else None,
            }
        )

    async def async_remove_store(self) -> None:
        """Forget everything stored for this player."""
        await self._store.async_remove()

    # ---- polling ----

    async def _async_update_data(self) -> AshenmarchData:
        try:
            await self._async_poll_summary()
            await self._async_poll_events()
            await self._async_poll_crest()
            await self._async_poll_slow()
        except AshenmarchAuthError as err:
            raise ConfigEntryAuthFailed(f"Ashenmarch refused the token ({err})") from err
        except AshenmarchRateLimited as err:
            # Back off: double the interval each time, up to an hour; a good poll resets it.
            current = self.update_interval or SCAN_INTERVAL
            self.update_interval = min(current * 2, MAX_BACKOFF)
            raise UpdateFailed(f"Ashenmarch is busy ({err}); next try in {self.update_interval}") from err
        except AshenmarchError as err:
            raise UpdateFailed(f"Ashenmarch unavailable: {err}") from err
        self.update_interval = SCAN_INTERVAL
        await self._async_save()
        return self._snapshot()

    def _snapshot(self) -> AshenmarchData:
        return AshenmarchData(
            summary=dict(self._summary),
            nearby={k: dict(v) for k, v in self._nearby.items()},
            crest=self._crest,
            crest_updated=self._crest_fetched,
            rank=dict(self._rank),
            profile=dict(self._profile),
            recent=list(self._recent),
        )

    async def _async_poll_summary(self) -> None:
        fetched = await self.api.get_wrist_summary(self._summary_etag)
        self._summary_etag = fetched.etag
        if not fetched.not_modified and isinstance(fetched.data, dict):
            self._summary = fetched.data
        # No siege anywhere in the realm: none can be open nearby either.
        if self._summary and not self._summary.get("under_siege"):
            self._nearby.clear()

    async def _async_poll_events(self) -> None:
        if not self.events_available:
            return
        if not self._recent and self._cursor is not None:
            await self._async_seed_recent()
        first_run = self._cursor is None
        area = self.alert_area()
        strings = await self._async_strings()
        try:
            for _ in range(EVENTS_MAX_PAGES):
                page = await self.api.get_events(self._cursor, EVENTS_PAGE_LIMIT)
                events = page.get("events") or []
                for event in events:
                    self._handle_event(event, area, strings, fire=not first_run)
                next_cursor = page.get("next") or self._cursor
                done = len(events) < EVENTS_PAGE_LIMIT or next_cursor == self._cursor
                self._cursor = next_cursor
                if done:
                    break
        except AshenmarchNotAvailable:
            # The server doesn't have the feed yet: sensors still work, alerts wait.
            _LOGGER.warning("The Ashenmarch events feed isn't available yet; alerts are off")
            self.events_available = False

    async def _async_seed_recent(self) -> None:
        """Fill an empty recent list from the feed's last 24 hours (an entry set up before v0.2.0 kept none).
        Nothing fires and the cursor doesn't move."""
        try:
            page = await self.api.get_events(None, EVENTS_PAGE_LIMIT)
        except AshenmarchNotAvailable:
            return
        for event in page.get("events") or []:  # oldest first
            # Only events already handled; a new one is left to the normal pass, which also fires it.
            if event.get("id") and str(event["id"]) in self._seen:
                self._recent.appendleft(_recent_entry(event))

    async def _async_poll_crest(self) -> None:
        if not self.crest_available:
            return
        crest_id = self._summary.get("crest_id")
        now = dt_util.utcnow()
        fresh = (
            self._crest is not None
            and crest_id == self._crest_id
            and self._crest_fetched is not None
            and now - self._crest_fetched < CREST_MAX_AGE
        )
        if fresh:
            return
        try:
            fetched = await self.api.get_coat_of_arms(self._crest_etag if self._crest else None)
        except AshenmarchNotAvailable:
            _LOGGER.warning("The Ashenmarch coat-of-arms image isn't available yet")
            self.crest_available = False
            return
        self._crest_etag = fetched.etag
        self._crest_id = crest_id
        self._crest_fetched = now
        if not fetched.not_modified and fetched.data:
            self._crest = bytes(fetched.data)

    async def _async_poll_slow(self) -> None:
        """Profile and rank progress, at most once an hour: they move slowly. A failure here keeps the old values
        and is retried on the next poll; it never fails the realm summary (only a refused token does)."""
        now = dt_util.utcnow()
        if self._slow_at is not None and now - self._slow_at < SLOW_INTERVAL:
            return
        try:
            me = await self.api.get_me()
            rank = await self.api.get_rank()
        except AshenmarchAuthError:
            raise
        except AshenmarchError as err:
            _LOGGER.debug("Ashenmarch profile/rank not read this time: %s", err)
            return
        if isinstance(me, dict):
            self._profile = {k: me.get(k) for k in ("total_area_claimed_m2", "first_claim_at", "house_name")}
        if isinstance(rank, dict):
            names = {r.get("key"): r.get("name") for r in rank.get("ladder") or [] if isinstance(r, dict)}
            self._rank = {
                "rank": rank.get("rank"),
                "rank_name": names.get(rank.get("rank")),
                "next_rank": rank.get("next_rank"),
                "next_rank_name": names.get(rank.get("next_rank")),
                "fraction": rank.get("fraction"),
                "points": rank.get("points"),
                "next_threshold": rank.get("next_threshold"),
                "next_area_m2": rank.get("next_area_m2"),
            }
        self._slow_at = now

    # ---- alert area ----

    def alert_area(self) -> AlertArea:
        """The alert area from the options, with only the zones that still exist."""
        options = self.config_entry.options
        unit = display_unit(self.hass)
        mode = options.get(CONF_ALERT_AREA, AREA_NEAR)
        wanted = options.get(CONF_ZONES, [DEFAULT_ZONE])
        zones = []
        for entity_id in wanted:
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            lat, lon = state.attributes.get("latitude"), state.attributes.get("longitude")
            if lat is None or lon is None:
                continue
            zones.append(Zone(entity_id, state.name, float(lat), float(lon)))
        radius_km = options.get(CONF_RADIUS_KM)
        if radius_km is None:
            radius_km = to_km(default_radius(len(wanted), unit), unit)

        issue_id = f"{ISSUE_NO_ZONES}_{self.config_entry.entry_id}"
        if mode == AREA_NEAR and not zones:
            # No zone left to measure from: alert on everything rather than nothing.
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_NO_ZONES,
                translation_placeholders={"house": self.config_entry.title},
            )
            mode = AREA_ALL
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        return AlertArea(mode, zones, float(radius_km), unit)

    async def _async_strings(self) -> dict[str, str]:
        translations = await async_get_translations(
            self.hass, self.hass.config.language, "common", {DOMAIN}
        )
        prefix = f"component.{DOMAIN}.common."
        return {k[len(prefix) :]: v for k, v in translations.items() if k.startswith(prefix)}

    # ---- events ----

    def _handle_event(
        self, event: dict[str, Any], area: AlertArea, strings: dict[str, str], *, fire: bool
    ) -> None:
        event_id = str(event.get("id", ""))
        if not event_id or event_id in self._seen:
            return
        self._seen.append(event_id)
        etype = event.get("type")
        territory = event.get("territory") or None
        self._recent.appendleft(_recent_entry(event))
        distances = zone_distances(territory, area.zones)
        within = [m for m in distances if m.distance_km <= area.radius_km]

        # Nearby sieges are tracked whatever the mode: "nearby" means within the radius.
        tid = str((territory or {}).get("id", ""))
        if tid:
            if etype == SIEGE_STARTED and within:
                self._nearby[tid] = {
                    "name": (territory or {}).get("name"),
                    "zones": [m.zone.name for m in within],
                    "zone_ids": [m.zone.entity_id for m in within],
                }
            elif etype in (SIEGE_ENDED, HOLDING_LOST):
                self._nearby.pop(tid, None)

        if etype in AREA_FILTERED_TYPES and area.mode == AREA_NEAR and not within:
            self.skipped.append(
                {
                    "id": event_id,
                    "type": etype,
                    "at": event.get("at"),
                    "nearest_km": round(distances[0].distance_km, 1) if distances else None,
                }
            )
            return
        if not fire:
            return

        if within:
            nearest: ZoneMatch | None = within[0]
        else:
            nearest = next((m for m in distances if m.distance_km <= NEAREST_ZONE_MAX_KM), None)
        payload = self._payload(event, area, strings, nearest, within)
        self.hass.bus.async_fire(EVENT_ASHENMARCH, payload)

    def _payload(
        self,
        event: dict[str, Any],
        area: AlertArea,
        strings: dict[str, str],
        nearest: ZoneMatch | None,
        within: list[ZoneMatch],
    ) -> dict[str, Any]:
        message = event.get("message") or ""
        payload: dict[str, Any] = {
            "type": event.get("type"),
            "message": message,
            "message_with_zone": message,
            "territory": event.get("territory"),
            "data": event.get("data") or {},
            "at": event.get("at"),
            "event_id": event.get("id"),
            "player_id": event.get("player_id") or self.player_id,
            "house": event.get("house") or self.house,
            "device_id": self.device_id,
            "config_entry_id": self.config_entry.entry_id,
            "zones_matched": [self._zone_dict(m, area.unit) for m in within],
        }
        if nearest is not None:
            distance = round(from_km(nearest.distance_km, area.unit), 1)
            payload |= {
                "zone": nearest.zone.entity_id,
                "zone_name": nearest.zone.name,
                "distance": distance,
                "distance_unit": area.unit,
                "distance_km": round(nearest.distance_km, 2),
            }
            template = strings.get("near_zone", "near {zone} ({distance} {unit})")
            suffix = template.format(
                zone=nearest.zone.name, distance=format_distance(distance), unit=area.unit
            )
            payload["message_with_zone"] = f"{message} — {suffix}" if message else suffix
        return payload

    @staticmethod
    def _zone_dict(match: ZoneMatch, unit: str) -> dict[str, Any]:
        return {
            "zone": match.zone.entity_id,
            "zone_name": match.zone.name,
            "distance": round(from_km(match.distance_km, unit), 1),
            "distance_km": round(match.distance_km, 2),
        }

    @property
    def device_id(self) -> str | None:
        """This player's device in HA, for automations that target one House."""
        if self._device_id is None:
            device = dr.async_get(self.hass).async_get_device_by_identifier(
                (DOMAIN, self.player_id), self.config_entry.entry_id
            )
            self._device_id = device.id if device else None
        return self._device_id


def _recent_entry(event: dict[str, Any]) -> dict[str, Any]:
    """One event for a House's details page: what happened and the Herald's line, never coordinates."""
    data = event.get("data") or {}
    return {
        "id": str(event.get("id", "")),
        "type": event.get("type"),
        "at": event.get("at"),
        "message": event.get("message"),
        "territory": (event.get("territory") or {}).get("name"),
        **{k: data[k] for k in ("outcome", "distance_m", "new_holding", "area_new_m2") if k in data},
    }
