"""Alert-area math: distances to a territory, zone matching, radius defaults, formatting."""

import pytest

from conftest import ashenmarch_geo as geo

KM_PER_DEG_LAT = 111.195  # one degree of latitude, in km

HOME = geo.Zone("zone.home", "Home", 39.7392, -104.9903)
WORK = geo.Zone("zone.work", "Work", 39.7392 + 30 * 1.609344 / KM_PER_DEG_LAT, -104.9903)


def territory_at(zone: geo.Zone, km_north: float, half_km: float = 0.0) -> dict:
    """A square territory whose centre is km_north of the zone."""
    lat = zone.latitude + km_north / KM_PER_DEG_LAT
    d = half_km / KM_PER_DEG_LAT
    return {
        "id": "t1",
        "name": "Ashford Mill",
        "center": [lat, zone.longitude],
        "bbox": [lat - d, zone.longitude - d, lat + d, zone.longitude + d],
    }


def test_haversine_one_degree_latitude():
    assert geo.haversine_km(0, 0, 1, 0) == pytest.approx(111.19, abs=0.01)


def test_inside_bbox_is_zero():
    t = territory_at(HOME, 0, half_km=1)
    assert geo.distance_to_territory_km(HOME.latitude, HOME.longitude, t) == 0


def test_distance_to_nearest_edge_not_centre():
    # Centre 10 km away, but the territory reaches 3 km towards home.
    t = territory_at(HOME, 10, half_km=3)
    assert geo.distance_to_territory_km(HOME.latitude, HOME.longitude, t) == pytest.approx(7, abs=0.05)


def test_centre_only_territory():
    t = territory_at(HOME, 10)
    del t["bbox"]
    assert geo.distance_to_territory_km(HOME.latitude, HOME.longitude, t) == pytest.approx(10, abs=0.05)


def test_no_geometry_matches_nothing():
    assert geo.zones_within({"id": "t"}, [HOME], 100) == []
    assert geo.zones_within(None, [HOME], 100) == []


def test_default_25_mi_commute():
    radius_km = geo.to_km(geo.default_radius(1, "mi"), "mi")
    near = territory_at(HOME, 12 * geo.KM_PER_MI)
    far = territory_at(HOME, 60 * geo.KM_PER_MI)
    assert [m.zone.name for m in geo.zones_within(near, [HOME], radius_km)] == ["Home"]
    assert geo.zones_within(far, [HOME], radius_km) == []


def test_two_zones_5_mi_matches_work_only():
    radius_km = geo.to_km(geo.default_radius(2, "mi"), "mi")
    t = territory_at(WORK, 3 * geo.KM_PER_MI)  # 3 mi from Work, 33 mi from Home
    matches = geo.zones_within(t, [HOME, WORK], radius_km)
    assert [m.zone.name for m in matches] == ["Work"]
    assert geo.from_km(matches[0].distance_km, "mi") == pytest.approx(3, abs=0.05)


def test_matches_sorted_nearest_first():
    t = territory_at(HOME, 20)
    matches = geo.zone_distances(t, [WORK, HOME])
    assert [m.zone.name for m in matches] == ["Home", "Work"]


@pytest.mark.parametrize(
    ("count", "unit", "expected"),
    [(0, "mi", 25), (1, "mi", 25), (2, "mi", 5), (3, "mi", 5), (1, "km", 40), (2, "km", 8)],
)
def test_default_radius(count, unit, expected):
    assert geo.default_radius(count, unit) == expected


def test_unit_round_trip():
    assert geo.from_km(geo.to_km(25, "mi"), "mi") == pytest.approx(25)
    assert geo.to_km(40, "km") == 40


@pytest.mark.parametrize(("value", "text"), [(3.24, "3.2"), (2.96, "3"), (12.0, "12"), (0.5, "0.5")])
def test_format_distance(value, text):
    assert geo.format_distance(value) == text
