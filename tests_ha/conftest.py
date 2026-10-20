"""Home Assistant integration tests against a fake Ashenmarch API. Needs pytest-homeassistant-custom-component:
  pytest tests_ha -o asyncio_mode=auto   (README: Development)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ashenmarch.const import API_BASE, DOMAIN

pytest_plugins = ["pytest_homeassistant_custom_component"]

# A test location (Denver), never the real house.
HOME = (39.7392, -104.9903)
KM_PER_MI = 1.609344
KM_PER_DEG_LAT = 111.195
WORK_MI_NORTH = 30.0

TOKEN_A = "am_pat_testtokenAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
TOKEN_B = "am_pat_testtokenBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
PLAYER_A = "11111111-1111-4111-8111-111111111111"
PLAYER_B = "22222222-2222-4222-8222-222222222222"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def north_of(point: tuple[float, float], miles: float) -> tuple[float, float]:
    return (point[0] + miles * KM_PER_MI / KM_PER_DEG_LAT, point[1])


WORK = north_of(HOME, WORK_MI_NORTH)


def territory(at: tuple[float, float], tid: str = "t-1", name: str = "Ashford Mill") -> dict[str, Any]:
    """A tiny territory (about 20 m across) centred at a point."""
    d = 0.0001
    return {"id": tid, "name": name, "center": [at[0], at[1]], "bbox": [at[0] - d, at[1] - d, at[0] + d, at[1] + d]}


def event(eid: str, etype: str, at: tuple[float, float] | None, player: str = PLAYER_A, house: str = "Testhold", **data) -> dict[str, Any]:
    ev = {
        "id": eid,
        "type": etype,
        "at": "2026-10-20T12:00:00Z",
        "player_id": player,
        "house": house,
        "data": data,
        "message": f"House Rival lays siege to Ashford Mill ({eid})",
    }
    if at is not None:
        ev["territory"] = territory(at, tid=f"t-{eid}")
    return ev


def wrist(**overrides) -> dict[str, Any]:
    s = {
        "under_siege": 0,
        "holdings": 12,
        "area_m2": 184000,
        "next_fade_at": "2026-10-22T08:00:00Z",
        "rank": 4,
        "rank_region": "Test City",
        "rank_title": "Baron",
        "week_distance_m": 21400,
        "tincture": "#7A1F2B",
        "crest_id": "crest-v1",
        "as_of": "2026-10-20T12:00:00Z",
    }
    s.update(overrides)
    return s


@dataclass
class Player:
    """What the fake server answers for one token."""

    token: str
    player_id: str
    house: str
    summary: dict[str, Any] = field(default_factory=wrist)
    events: list[dict[str, Any]] = field(default_factory=list)
    status: int = 200  # 401 to revoke the token


class FakeAshenmarch:
    """A fake api.ashenmarch.com that answers per token, with a real cursor."""

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        self.mocker = mocker
        self.players: dict[str, Player] = {}
        self.wrist_status = 200
        self.events_status = 200
        self.crest_status = 200
        self.calls: list[tuple[str, dict[str, str], dict[str, Any]]] = []
        for path in ("/players/me", "/players/me/wrist-summary", "/players/me/events", "/players/me/coat-of-arms.png", "/players/me/rank"):
            mocker.get(f"{API_BASE}{path}", side_effect=self._handle)

    def add(self, player: Player) -> Player:
        self.players[player.token] = player
        return player

    async def _handle(self, method, url, data):
        from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

        headers = self.mocker.mock_calls[-1][3] or {}
        token = headers.get("Authorization", "").removeprefix("Bearer ")
        params = dict(url.query)
        path = url.path.removeprefix("/v1")
        self.calls.append((path, dict(headers), params))
        p = self.players.get(token)
        if p is None or p.status == 401:
            return AiohttpClientMockResponse(method, url, status=401, json={"error": "unauthorized", "message": "x"})
        if path == "/players/me":
            return AiohttpClientMockResponse(method, url, json={"id": p.player_id, "house_name": p.house, "locale": "en", "rank": "knight", "email": "player@example.com", "created_at": "x", "updated_at": "x",
                                                                "total_area_claimed_m2": 5100000, "first_claim_at": "2026-06-01T10:00:00Z"})
        if path == "/players/me/rank":
            return AiohttpClientMockResponse(method, url, json={"rank": "knight", "next_rank": "baron", "points": 1240.5, "tier_floor": 900, "next_threshold": 1450,
                                                                "fraction": 0.62, "area_claimed_m2": 5100000, "next_area_m2": 8000000, "current_index": 3,
                                                                "ladder": [{"key": "knight", "name": "Knight", "milestone_gated": False, "achieved": True},
                                                                           {"key": "baron", "name": "Baron", "milestone_gated": True, "achieved": False}]})
        if path == "/players/me/wrist-summary":
            if self.wrist_status != 200:
                return AiohttpClientMockResponse(method, url, status=self.wrist_status)
            etag = f'W/"{hash(str(sorted(p.summary.items())))}"'
            if headers.get("If-None-Match") == etag:
                return AiohttpClientMockResponse(method, url, status=304, headers={"ETag": etag})
            return AiohttpClientMockResponse(method, url, json=p.summary, headers={"ETag": etag})
        if path == "/players/me/events":
            if self.events_status != 200:
                return AiohttpClientMockResponse(method, url, status=self.events_status)
            since = int(params["since"]) if params.get("since") else None
            limit = int(params.get("limit", 50))
            if since is None:
                start = 0  # "the last 24 hours" = everything the fake holds
            else:
                start = since
            page = p.events[start : start + limit]
            return AiohttpClientMockResponse(method, url, json={"events": page, "next": str(start + len(page))})
        if path == "/players/me/coat-of-arms.png":
            if self.crest_status != 200:
                return AiohttpClientMockResponse(method, url, status=self.crest_status)
            etag = f'"{p.summary.get("crest_id")}"'
            if headers.get("If-None-Match") == etag:
                return AiohttpClientMockResponse(method, url, status=304, headers={"ETag": etag})
            return AiohttpClientMockResponse(method, url, response=PNG, headers={"ETag": etag, "Content-Type": "image/png"})
        return AiohttpClientMockResponse(method, url, status=404)


@pytest.fixture
def server(aioclient_mock: AiohttpClientMocker) -> FakeAshenmarch:
    return FakeAshenmarch(aioclient_mock)


@pytest.fixture
async def zones(hass: HomeAssistant) -> None:
    """Home at the test location (US units, so miles), plus a Work zone 30 mi north."""
    await hass.config.async_update(latitude=HOME[0], longitude=HOME[1])
    hass.config.units = US_CUSTOMARY_SYSTEM
    assert await async_setup_component(
        hass,
        "zone",
        {"zone": [{"name": "Work", "latitude": WORK[0], "longitude": WORK[1], "radius": 100}]},
    )
    await hass.async_block_till_done()


def make_entry(token: str = TOKEN_A, player_id: str = PLAYER_A, house: str = "Testhold", options: dict | None = None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"House {house}",
        unique_id=player_id,
        data={"token": token, "player_id": player_id, "house_name": house},
        options=options or {},
    )
