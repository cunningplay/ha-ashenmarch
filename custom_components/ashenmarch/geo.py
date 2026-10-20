"""Alert-area math: which of the player's zones a territory is near (D-365).

Pure Python (no Home Assistant), so it is tested on its own in tests/.
Distances are measured from a zone's centre to the nearest point of the
territory's bounding box, so a large territory that reaches into the area counts.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import math
from typing import Any

EARTH_RADIUS_KM = 6371.0088
KM_PER_MI = 1.609344


@dataclass(frozen=True)
class Zone:
    """A Home Assistant zone, by entity id (names change, ids don't)."""

    entity_id: str
    name: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class ZoneMatch:
    """One zone and how far the territory is from it."""

    zone: Zone
    distance_km: float


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def distance_to_territory_km(lat: float, lon: float, territory: dict[str, Any]) -> float | None:
    """Distance from a point to a territory's bbox (0 inside it); its centre when there is no bbox."""
    bbox = territory.get("bbox")
    if isinstance(bbox, Sequence) and len(bbox) == 4:
        min_lat, min_lon, max_lat, max_lon = (float(v) for v in bbox)
        return haversine_km(
            lat, lon, min(max(lat, min_lat), max_lat), min(max(lon, min_lon), max_lon)
        )
    center = territory.get("center")
    if isinstance(center, Sequence) and len(center) == 2:
        return haversine_km(lat, lon, float(center[0]), float(center[1]))
    return None


def zone_distances(territory: dict[str, Any] | None, zones: Sequence[Zone]) -> list[ZoneMatch]:
    """Every zone with its distance to the territory, nearest first."""
    if not territory:
        return []
    out = []
    for zone in zones:
        d = distance_to_territory_km(zone.latitude, zone.longitude, territory)
        if d is not None:
            out.append(ZoneMatch(zone, d))
    return sorted(out, key=lambda m: m.distance_km)


def zones_within(
    territory: dict[str, Any] | None, zones: Sequence[Zone], radius_km: float
) -> list[ZoneMatch]:
    """The zones the territory is within radius_km of, nearest first."""
    return [m for m in zone_distances(territory, zones) if m.distance_km <= radius_km]


def default_radius(zone_count: int, unit: str) -> float:
    """Default radius in the display unit: wide for one zone, tight for several."""
    if zone_count >= 2:
        return 5.0 if unit == "mi" else 8.0
    return 25.0 if unit == "mi" else 40.0


def to_km(value: float, unit: str) -> float:
    """A distance in the display unit, in km."""
    return value * KM_PER_MI if unit == "mi" else value


def from_km(value_km: float, unit: str) -> float:
    """A distance in km, in the display unit."""
    return value_km / KM_PER_MI if unit == "mi" else value_km


def format_distance(value: float) -> str:
    """One decimal, without a trailing .0: 3.24 -> "3.2", 2.96 -> "3"."""
    rounded = round(value, 1)
    return str(int(rounded)) if rounded == int(rounded) else f"{rounded:.1f}"
