"""Ashenmarch in Home Assistant: config flow, sensors, events near zones, restart, re-auth, households."""

from __future__ import annotations

from datetime import timedelta
import logging

import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_capture_events, async_fire_time_changed

from custom_components.ashenmarch.const import (
    AREA_ALL,
    CONF_ALERT_AREA,
    CONF_RADIUS_KM,
    CONF_ZONES,
    DOMAIN,
    EVENT_ASHENMARCH,
    MAX_BACKOFF,
    SCAN_INTERVAL,
)
from custom_components.ashenmarch.diagnostics import async_get_config_entry_diagnostics

from .conftest import (
    HOME,
    PLAYER_A,
    PLAYER_B,
    PNG,
    TOKEN_A,
    TOKEN_B,
    WORK,
    FakeAshenmarch,
    Player,
    event,
    make_entry,
    north_of,
    wrist,
)

pytestmark = pytest.mark.usefixtures("zones")


async def setup(hass: HomeAssistant, entry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def poll(hass: HomeAssistant, entry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


# ---------------- config flow ----------------


async def test_user_flow_creates_house_entry(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"token": f"  {TOKEN_A} "})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "House Testhold"
    assert result["result"].unique_id == PLAYER_A
    assert result["data"]["token"] == TOKEN_A


async def test_user_flow_bad_token(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"token": "am_pat_nope"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_same_player_twice_is_refused(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    await setup(hass, make_entry())
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"token": TOKEN_A})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_token_never_logged(hass: HomeAssistant, server: FakeAshenmarch, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    await setup(hass, make_entry())
    await poll(hass, hass.config_entries.async_entries(DOMAIN)[0])
    assert TOKEN_A not in caplog.text


# ---------------- sensors ----------------


async def test_sensors_have_values_after_one_poll(hass: HomeAssistant, server: FakeAshenmarch, hass_client) -> None:
    server.add(Player(TOKEN_A, PLAYER_A, "Testhold", summary=wrist(under_siege=2)))
    await setup(hass, make_entry())
    assert hass.states.get("sensor.house_testhold_holdings").state == "12"
    assert hass.states.get("sensor.house_testhold_holdings").attributes["area_m2"] == 184000
    assert hass.states.get("sensor.house_testhold_holdings_under_siege").state == "2"
    assert hass.states.get("sensor.house_testhold_next_to_fade").state == "2026-10-22T08:00:00+00:00"
    rank = hass.states.get("sensor.house_testhold_rank")
    assert rank.state == "4" and rank.attributes["region"] == "Test City" and rank.attributes["title"] == "Baron"
    # 21.4 km, shown in miles on a US-customary HA
    assert float(hass.states.get("sensor.house_testhold_distance_this_week").state) == pytest.approx(13.3, abs=0.05)
    assert hass.states.get("binary_sensor.house_testhold_under_siege").state == "on"
    assert hass.states.get("binary_sensor.house_testhold_under_siege_nearby").state == "off"

    # The crest is served by HA's own proxy; the sensors never point at the API.
    image = hass.states.get("image.house_testhold_coat_of_arms")
    picture = image.attributes["entity_picture"]
    assert picture.startswith("/api/image_proxy/") and "ashenmarch.com" not in picture
    assert hass.states.get("sensor.house_testhold_holdings").attributes["entity_picture"] == picture
    resp = await (await hass_client()).get(picture)
    assert resp.status == 200 and await resp.read() == PNG


async def test_unchanged_summary_and_crest_use_etags(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    entry = make_entry()
    await setup(hass, entry)
    server.calls.clear()
    await poll(hass, entry)
    wrist_calls = [c for c in server.calls if c[0] == "/players/me/wrist-summary"]
    assert wrist_calls and "If-None-Match" in wrist_calls[0][1]
    # The crest is fresh (same crest_id, under a day old): not fetched again.
    assert not [c for c in server.calls if c[0] == "/players/me/coat-of-arms.png"]
    assert hass.states.get("sensor.house_testhold_holdings").state == "12"


# ---------------- events near zones ----------------


async def test_first_poll_does_not_replay_old_events(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    p = server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    p.events.append(event("e1", "siege_started", north_of(HOME, 1)))
    fired = async_capture_events(hass, EVENT_ASHENMARCH)
    await setup(hass, make_entry())
    assert fired == []


async def test_siege_12_mi_from_home_fires_once(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    p = server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    entry = make_entry()
    await setup(hass, entry)
    fired = async_capture_events(hass, EVENT_ASHENMARCH)

    p.summary = wrist(under_siege=1)
    p.events.append(event("e1", "siege_started", north_of(HOME, 12)))
    await poll(hass, entry)
    await poll(hass, entry)  # nothing new: no second event

    assert len(fired) == 1
    data = fired[0].data
    assert data["type"] == "siege_started"
    assert data["player_id"] == PLAYER_A and data["house"] == "Testhold"
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, PLAYER_A), hass.config_entries.async_entries(DOMAIN)[0].entry_id)
    assert data["device_id"] == device.id
    assert data["zone"] == "zone.home" and data["zone_name"] == "test home"
    assert data["distance"] == pytest.approx(12, abs=0.1) and data["distance_unit"] == "mi"
    assert data["distance_km"] == pytest.approx(19.3, abs=0.1)
    assert data["message_with_zone"].endswith("— near test home (12 mi)")
    assert [z["zone"] for z in data["zones_matched"]] == ["zone.home"]
    assert hass.states.get("binary_sensor.house_testhold_under_siege").state == "on"
    nearby = hass.states.get("binary_sensor.house_testhold_under_siege_nearby")
    assert nearby.state == "on" and nearby.attributes["zones"] == ["test home"]

    p.summary = wrist(under_siege=0)
    p.events.append(event("e2", "siege_ended", north_of(HOME, 12), outcome="held"))
    p.events[-1]["territory"]["id"] = "t-e1"
    await poll(hass, entry)
    assert hass.states.get("binary_sensor.house_testhold_under_siege_nearby").state == "off"


async def test_siege_60_mi_away_is_skipped_until_all_territories(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    p = server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    entry = make_entry()
    await setup(hass, entry)
    fired = async_capture_events(hass, EVENT_ASHENMARCH)

    p.summary = wrist(under_siege=1)
    p.events.append(event("far1", "siege_started", north_of(HOME, -60)))
    await poll(hass, entry)
    assert fired == []
    assert hass.states.get("binary_sensor.house_testhold_under_siege").state == "on"
    assert hass.states.get("binary_sensor.house_testhold_under_siege_nearby").state == "off"
    assert [s["id"] for s in entry.runtime_data.skipped] == ["far1"]

    hass.config_entries.async_update_entry(entry, options={CONF_ALERT_AREA: AREA_ALL})
    p.events.append(event("far2", "siege_started", north_of(HOME, -60)))
    await poll(hass, entry)
    assert [e.data["event_id"] for e in fired] == ["far2"]  # applies from now on, no replay
    # Nearest selected zone within 100 mi still named, but nothing inside the radius.
    assert fired[0].data["zone"] == "zone.home" and fired[0].data["zones_matched"] == []
    assert fired[0].data["distance"] == pytest.approx(60, abs=0.1)


async def test_all_territories_far_away_has_no_zone_fields(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    p = server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    entry = make_entry(options={CONF_ALERT_AREA: AREA_ALL})
    await setup(hass, entry)
    fired = async_capture_events(hass, EVENT_ASHENMARCH)
    p.events.append(event("x", "holding_lost", north_of(HOME, -150)))
    await poll(hass, entry)
    assert len(fired) == 1 and "zone" not in fired[0].data
    assert fired[0].data["message_with_zone"] == fired[0].data["message"]


async def test_home_and_work_default_5_mi_names_work(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    p = server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    entry = make_entry(options={CONF_ZONES: ["zone.home", "zone.work"]})
    await setup(hass, entry)
    fired = async_capture_events(hass, EVENT_ASHENMARCH)
    p.summary = wrist(under_siege=1)
    p.events.append(event("w1", "siege_started", north_of(WORK, 3)))  # 3 mi from Work, 33 from Home
    p.events.append(event("w2", "siege_started", north_of(WORK, 8)))  # outside 5 mi of either
    await poll(hass, entry)
    assert [e.data["event_id"] for e in fired] == ["w1"]
    assert fired[0].data["zone_name"] == "Work"
    assert fired[0].data["message_with_zone"].endswith("near Work (3 mi)")
    assert hass.states.get("binary_sensor.house_testhold_under_siege_nearby").attributes["zones"] == ["Work"]


async def test_march_filed_always_fires(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    p = server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    entry = make_entry()
    await setup(hass, entry)
    fired = async_capture_events(hass, EVENT_ASHENMARCH)
    p.events.append(event("m1", "march_filed", north_of(HOME, -500), distance_m=5000))
    p.events.append(event("m2", "march_filed", None, distance_m=3000))
    await poll(hass, entry)
    assert [e.data["event_id"] for e in fired] == ["m1", "m2"]


async def test_restart_neither_replays_nor_loses(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    p = server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    entry = make_entry()
    await setup(hass, entry)
    fired = async_capture_events(hass, EVENT_ASHENMARCH)
    p.events.append(event("r1", "siege_started", north_of(HOME, 2)))
    await poll(hass, entry)
    assert len(fired) == 1

    server.calls.clear()
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    # The cursor survived the restart: polling resumes after r1 instead of re-reading the last 24 h.
    first = next(c for c in server.calls if c[0] == "/players/me/events")
    assert first[2].get("since") == "1"
    p.events.append(event("r2", "holding_lost", north_of(HOME, 2)))
    await poll(hass, entry)
    assert [e.data["event_id"] for e in fired] == ["r1", "r2"]


# ---------------- auth, households ----------------


async def test_revoked_token_asks_for_reauth_for_that_player_only(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    a = server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    server.add(Player(TOKEN_B, PLAYER_B, "Otherhold"))
    entry_a = make_entry()
    entry_b = make_entry(TOKEN_B, PLAYER_B, "Otherhold")
    await setup(hass, entry_a)
    await setup(hass, entry_b)

    a.status = 401
    await poll(hass, entry_a)
    await poll(hass, entry_b)
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]
    assert flows[0]["context"]["entry_id"] == entry_a.entry_id
    assert entry_b.runtime_data.last_update_success

    # New token for the same player: accepted, others untouched.
    new_token = "am_pat_newtokenAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    server.add(Player(new_token, PLAYER_A, "Testhold"))
    result = await hass.config_entries.flow.async_configure(flows[0]["flow_id"], {"token": new_token})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful"
    assert entry_a.data["token"] == new_token


async def test_reauth_with_another_players_token_is_refused(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    a = server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    server.add(Player(TOKEN_B, PLAYER_B, "Otherhold"))
    entry = make_entry()
    await setup(hass, entry)
    a.status = 401
    await poll(hass, entry)
    flow = hass.config_entries.flow.async_progress()[0]
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {"token": TOKEN_B})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "wrong_player"


async def test_two_houses_are_independent(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    a = server.add(Player(TOKEN_A, PLAYER_A, "Testhold", summary=wrist(holdings=12)))
    b = server.add(Player(TOKEN_B, PLAYER_B, "Otherhold", summary=wrist(holdings=3)))
    entry_a, entry_b = make_entry(), make_entry(TOKEN_B, PLAYER_B, "Otherhold")
    await setup(hass, entry_a)
    await setup(hass, entry_b)
    assert hass.states.get("sensor.house_testhold_holdings").state == "12"
    assert hass.states.get("sensor.house_otherhold_holdings").state == "3"
    devices = dr.async_get(hass)
    dev_a = devices.async_get_device_by_identifier((DOMAIN, PLAYER_A), entry_a.entry_id)
    dev_b = devices.async_get_device_by_identifier((DOMAIN, PLAYER_B), entry_b.entry_id)
    assert dev_a.name == "House Testhold" and dev_b.name == "House Otherhold"

    fired = async_capture_events(hass, EVENT_ASHENMARCH)
    a.events.append(event("a1", "siege_started", north_of(HOME, 1)))
    b.events.append(event("b1", "siege_started", north_of(HOME, 1), player=PLAYER_B, house="Otherhold"))
    await poll(hass, entry_a)
    await poll(hass, entry_b)
    by_id = {e.data["event_id"]: e.data for e in fired}
    assert by_id["a1"]["player_id"] == PLAYER_A and by_id["a1"]["device_id"] == dev_a.id
    assert by_id["b1"]["house"] == "Otherhold" and by_id["b1"]["device_id"] == dev_b.id


# ---------------- options ----------------


async def test_options_radius_default_follows_zone_count(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    entry = make_entry()
    await setup(hass, entry)

    # Untouched 25 mi + a second zone -> stays a default (5 mi applies).
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["description_placeholders"]["default"] == "25"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ALERT_AREA: "near", CONF_ZONES: ["zone.home", "zone.work"], "radius": 25}
    )
    assert CONF_RADIUS_KM not in entry.options
    assert entry.runtime_data.alert_area().radius_km == pytest.approx(5 * 1.609344)

    # A typed radius is kept, whatever the zone count.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["description_placeholders"]["default"] == "5"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ALERT_AREA: "near", CONF_ZONES: ["zone.home", "zone.work"], "radius": 10}
    )
    assert entry.options[CONF_RADIUS_KM] == pytest.approx(16.0934, abs=0.001)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ALERT_AREA: "near", CONF_ZONES: ["zone.home"], "radius": 10}
    )
    assert entry.options[CONF_RADIUS_KM] == pytest.approx(16.0934, abs=0.001)

    # Clearing the field goes back to the default.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ALERT_AREA: "near", CONF_ZONES: ["zone.home"]}
    )
    assert CONF_RADIUS_KM not in entry.options


async def test_options_need_a_zone_when_near(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    entry = make_entry()
    await setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ALERT_AREA: "near", CONF_ZONES: []}
    )
    assert result["errors"] == {CONF_ZONES: "zones_required"}


async def test_deleted_zone_falls_back_to_all_with_repair(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    p = server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    entry = make_entry(options={CONF_ZONES: ["zone.gone"]})
    await setup(hass, entry)
    fired = async_capture_events(hass, EVENT_ASHENMARCH)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"no_zones_{entry.entry_id}")
    assert issue is not None
    p.events.append(event("f1", "holding_lost", north_of(HOME, -300)))
    await poll(hass, entry)
    assert len(fired) == 1

    hass.config_entries.async_update_entry(entry, options={CONF_ZONES: ["zone.home"]})
    await poll(hass, entry)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"no_zones_{entry.entry_id}") is None


# ---------------- resilience, diagnostics ----------------


async def test_rate_limit_backs_off_then_recovers(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    entry = make_entry()
    await setup(hass, entry)
    server.wrist_status = 429
    await poll(hass, entry)
    assert entry.runtime_data.update_interval == SCAN_INTERVAL * 2
    await poll(hass, entry)
    await poll(hass, entry)
    await poll(hass, entry)
    assert entry.runtime_data.update_interval == MAX_BACKOFF
    assert hass.states.get("sensor.house_testhold_holdings").state == "12"  # last values stay
    server.wrist_status = 200
    await poll(hass, entry)
    assert entry.runtime_data.update_interval == SCAN_INTERVAL


async def test_missing_events_feed_keeps_sensors(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    server.events_status = 404
    server.crest_status = 404
    entry = make_entry()
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.house_testhold_holdings").state == "12"
    assert hass.states.get("image.house_testhold_coat_of_arms").state == "unavailable"


async def test_diagnostics_redact_token(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    entry = make_entry()
    await setup(hass, entry)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["token"] == "**REDACTED**"
    assert TOKEN_A not in str(diag)
    assert str(HOME[0]) not in str(diag)


async def test_device_triggers(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    from custom_components.ashenmarch.device_trigger import async_get_triggers

    server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    await setup(hass, make_entry())
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, PLAYER_A), hass.config_entries.async_entries(DOMAIN)[0].entry_id)
    types = {t["type"] for t in await async_get_triggers(hass, device.id)}
    assert types == {"siege_started", "siege_ended", "holding_lost", "holding_won", "fading_soon", "march_filed"}


async def test_polls_every_5_minutes(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    p = server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    await setup(hass, make_entry())
    p.summary = wrist(holdings=13)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=5, seconds=1))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.house_testhold_holdings").state == "13"


# ---------------- v0.2.0: rank progress, ever claimed, recent events ----------------


async def test_rank_progress_and_ever_claimed_hourly(hass: HomeAssistant, server: FakeAshenmarch, freezer) -> None:
    server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    entry = make_entry()
    await setup(hass, entry)
    rp = hass.states.get("sensor.house_testhold_rank_progress")
    assert rp.state == "62" and rp.attributes["next_rank_name"] == "Baron" and rp.attributes["rank_name"] == "Knight"
    ever = hass.states.get("sensor.house_testhold_area_ever_claimed")
    assert float(ever.state) == pytest.approx(1.97, abs=0.01)  # 5.1 km² shown in mi² on a US-customary HA
    assert ever.attributes["first_claim_at"] == "2026-06-01T10:00:00Z"

    server.calls.clear()
    await poll(hass, entry)
    assert not [c for c in server.calls if c[0] == "/players/me/rank"]  # not again within the hour
    freezer.tick(timedelta(minutes=61))
    await poll(hass, entry)
    assert [c for c in server.calls if c[0] == "/players/me/rank"]


async def test_recent_events_newest_first_and_survive_restart(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    p = server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    entry = make_entry()
    await setup(hass, entry)
    p.events.append(event("n1", "holding_won", north_of(HOME, 1)))
    p.events.append(event("f1", "holding_won", north_of(HOME, -400)))  # outside the alert area: kept anyway
    p.events.append(event("m1", "march_filed", None, distance_m=4200, new_holding=True))
    await poll(hass, entry)
    last = hass.states.get("sensor.house_testhold_last_event")
    recent = last.attributes["recent"]
    assert [e["id"] for e in recent] == ["m1", "f1", "n1"]
    assert last.attributes["type"] == "march_filed" and recent[0]["distance_m"] == 4200
    assert recent[1]["territory"] == "Ashford Mill" and "center" not in str(recent)

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert [e["id"] for e in hass.states.get("sensor.house_testhold_last_event").attributes["recent"]] == ["m1", "f1", "n1"]


async def test_recent_keeps_twenty(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    p = server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    entry = make_entry()
    await setup(hass, entry)
    for k in range(25):
        p.events.append(event(f"e{k}", "march_filed", None))
    await poll(hass, entry)
    recent = hass.states.get("sensor.house_testhold_last_event").attributes["recent"]
    assert len(recent) == 20 and recent[0]["id"] == "e24"


async def test_rank_failure_keeps_the_realm_running(hass: HomeAssistant, server: FakeAshenmarch, aioclient_mock) -> None:
    p = server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    entry = make_entry()
    await setup(hass, entry)
    entry.runtime_data._slow_at = None  # due again
    orig = server._handle

    async def flaky(method, url, data):
        if url.path.endswith("/players/me/rank"):
            from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse
            return AiohttpClientMockResponse(method, url, status=503)
        return await orig(method, url, data)

    for m in aioclient_mock._mocks:
        m.side_effect = flaky
    p.summary = wrist(holdings=14)
    await poll(hass, entry)
    assert entry.runtime_data.last_update_success
    assert hass.states.get("sensor.house_testhold_holdings").state == "14"
    assert hass.states.get("sensor.house_testhold_rank_progress").state == "62"  # previous value kept


async def test_empty_recent_is_seeded_once_without_firing(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    # An entry from v0.1.0: a stored cursor but no recent list. The last day's events fill it, and nothing fires again.
    p = server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    p.events.extend([event("old1", "holding_won", north_of(HOME, 1)), event("old2", "siege_started", north_of(HOME, 1))])
    entry = make_entry()
    await setup(hass, entry)
    coord = entry.runtime_data
    coord._recent.clear()
    fired = async_capture_events(hass, EVENT_ASHENMARCH)
    await poll(hass, entry)
    assert [e["id"] for e in hass.states.get("sensor.house_testhold_last_event").attributes["recent"]] == ["old2", "old1"]
    assert fired == []
    assert [c for c in server.calls if c[0] == "/players/me/events"][-1][2].get("since") == "2"  # the cursor didn't move
