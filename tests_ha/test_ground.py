"""v0.5.0 (ASH-4514): per-block ground. The ground picture, the breach sensors, the block counts and the
street on siege_started, against the fake API, including legacy players and servers before ASH-4513."""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.ashenmarch.const import (
    CONF_GROUND_HOLDING,
    EVENT_ASHENMARCH,
    GROUND_AUTO,
)
from custom_components.ashenmarch.diagnostics import async_get_config_entry_diagnostics

from .conftest import (
    GROUND_PNG,
    HOME,
    PLAYER_A,
    TOKEN_A,
    FakeAshenmarch,
    Player,
    event,
    grounded,
    holding,
    make_entry,
    north_of,
    stretch,
    wrist,
)

pytestmark = pytest.mark.usefixtures("zones")

STREET = "2nd Ave & James St"
FALLS_AT = "2026-11-14T07:00:00Z"
GROUND_ENTITIES = ("image.house_testhold_ground", "sensor.house_testhold_breached_blocks", "sensor.house_testhold_worn_blocks")
V04_ENTITIES = (
    "sensor.house_testhold_holdings",
    "sensor.house_testhold_holdings_under_siege",
    "sensor.house_testhold_next_to_fade",
    "sensor.house_testhold_rank",
    "binary_sensor.house_testhold_under_siege",
    "binary_sensor.house_testhold_under_siege_nearby",
    "image.house_testhold_coat_of_arms",
)


async def setup(hass: HomeAssistant, entry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def poll(hass: HomeAssistant, entry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


def owl(**ground) -> dict:
    return grounded(holding("Owl Flight", north_of(HOME, 2), "2026-10-22T08:00:00Z"), **ground)


def pitchfork(**ground) -> dict:
    return grounded(holding("Pitchfork", north_of(HOME, -40), "2026-10-30T19:26:50Z"), **ground)


def breached_player() -> Player:
    """Owl Flight has a breached street; Pitchfork is only worn."""
    return Player(
        TOKEN_A,
        PLAYER_A,
        "Testhold",
        summary=wrist(siege_place=STREET, siege_falls_at=FALLS_AT, siege_overdue=False, ground_holding="Owl Flight",
                      ground_fortified=12, ground_held=30, ground_worn=4, ground_breached=1),
        holdings=[
            owl(fortified=12, held=30, worn=4, breached=1, needs_walking=[
                stretch(STREET, "breached", 1, falls_at=FALLS_AT), stretch("Pike St", "worn", 3)]),
            pitchfork(held=8, worn=6, needs_walking=[stretch("Elm St", "unwalked_days", 2, days=9)]),
        ],
    )


def worn_player() -> Player:
    """Nothing breached; Pitchfork is the most worn, so a glance shows it."""
    return Player(
        TOKEN_A,
        PLAYER_A,
        "Testhold",
        summary=wrist(siege_place=None, siege_falls_at=None, siege_overdue=False, ground_holding="Pitchfork",
                      ground_fortified=0, ground_held=8, ground_worn=6, ground_breached=0),
        holdings=[owl(fortified=12, held=30, worn=2), pitchfork(held=8, worn=6, needs_walking=[stretch("Pike St", "worn", 6)])],
    )


def legacy_player() -> Player:
    """A holding-wide (legacy) player on the new server: the fields are there, all null."""
    p = Player(TOKEN_A, PLAYER_A, "Testhold",
               summary=wrist(siege_place=None, siege_falls_at=None, siege_overdue=False, ground_holding=None,
                             ground_fortified=None, ground_held=None, ground_worn=None, ground_breached=None))
    for h in p.holdings:
        h["ground"] = None
        h["needs_walking"] = None
    return p


def ground_calls(server: FakeAshenmarch) -> list:
    return [c for c in server.calls if c[0] == "/players/me/ground.png"]


def integration_logs(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name.startswith("custom_components.ashenmarch") and r.levelno > logging.DEBUG]


# ---------------- a breached player ----------------


async def test_breached_player(hass: HomeAssistant, server: FakeAshenmarch, hass_client) -> None:
    server.add(breached_player())
    entry = make_entry()
    await setup(hass, entry)

    breached = hass.states.get("binary_sensor.house_testhold_breached")
    assert breached.state == "on"
    assert breached.attributes["place"] == STREET
    assert breached.attributes["falls_at"] == FALLS_AT
    assert breached.attributes["overdue"] is False
    assert breached.attributes["holding"] == "Owl Flight"

    falls = hass.states.get("sensor.house_testhold_breach_falls")
    assert dt_util.parse_datetime(falls.state) == dt_util.parse_datetime(FALLS_AT)
    assert falls.attributes["place"] == STREET

    assert hass.states.get("sensor.house_testhold_breached_blocks").state == "1"
    assert hass.states.get("sensor.house_testhold_worn_blocks").state == "10"  # 4 + 6, summed over every holding

    image = hass.states.get("image.house_testhold_ground")
    assert image.state != "unavailable"
    assert image.attributes["holding"] == "Owl Flight"
    assert [image.attributes[w] for w in ("fortified", "held", "worn", "breached")] == [12, 30, 4, 1]
    assert image.attributes["needs_walking"] == [
        {"place": STREET, "reason": "breached", "block_count": 1, "falls_at": FALLS_AT},
        {"place": "Pike St", "reason": "worn", "block_count": 3},
    ]
    call = ground_calls(server)[-1]
    assert call[2] == {"holding": "h-Owl Flight", "size": "512"}

    # Served by HA itself: the browser fetches HA's proxy URL, never the API.
    client = await hass_client()
    resp = await client.get(image.attributes["entity_picture"])
    assert resp.status == 200
    assert await resp.read() == GROUND_PNG + b"h-Owl Flight"
    assert "ashenmarch.com" not in image.attributes["entity_picture"]

    lst = hass.states.get("sensor.house_testhold_holdings").attributes["list"]
    assert lst[0]["ground"] == {"fortified": 12, "held": 30, "worn": 4, "breached": 1}
    assert lst[1]["needs_walking"] == [{"place": "Elm St", "reason": "unwalked_days", "block_count": 2, "days": 9}]
    # No coordinates anywhere in the attributes.
    for entity_id in ("sensor.house_testhold_holdings", "image.house_testhold_ground"):
        attrs = str(hass.states.get(entity_id).attributes)
        assert "center" not in attrs and "-104.99" not in attrs and "bbox" not in attrs


async def test_diagnostics_redact_streets(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    server.add(breached_player())
    entry = make_entry(options={CONF_GROUND_HOLDING: "Pitchfork"})
    await setup(hass, entry)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = str(diag)
    assert STREET not in text and "Pike St" not in text and "Elm St" not in text
    assert "Pitchfork" not in text  # the pinned holding's name is redacted in the options
    assert "-104.99" not in text and TOKEN_A not in text
    assert diag["ground_picture_available"] is True and diag["ground_picture_pinned"] is True
    assert diag["holdings_with_ground"] == 2


async def test_overdue_breach(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    p = server.add(breached_player())
    p.summary["siege_overdue"] = True
    entry = make_entry()
    await setup(hass, entry)
    assert hass.states.get("binary_sensor.house_testhold_breached").attributes["overdue"] is True


# ---------------- a worn-only player ----------------


async def test_worn_only_player(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    server.add(worn_player())
    entry = make_entry()
    await setup(hass, entry)
    breached = hass.states.get("binary_sensor.house_testhold_breached")
    assert breached.state == "off"
    assert breached.attributes["place"] is None and breached.attributes["holding"] is None
    assert hass.states.get("sensor.house_testhold_breach_falls").state == "unknown"
    assert hass.states.get("sensor.house_testhold_breached_blocks").state == "0"
    assert hass.states.get("sensor.house_testhold_worn_blocks").state == "8"
    image = hass.states.get("image.house_testhold_ground")
    assert image.attributes["holding"] == "Pitchfork"
    assert image.attributes["worn"] == 6 and image.attributes["breached"] == 0
    assert ground_calls(server)[-1][2]["holding"] == "h-Pitchfork"


# ---------------- legacy player and an older server ----------------


async def test_legacy_player(hass: HomeAssistant, server: FakeAshenmarch, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    server.add(legacy_player())
    entry = make_entry()
    await setup(hass, entry)
    await poll(hass, entry)
    for entity_id in GROUND_ENTITIES:
        assert hass.states.get(entity_id).state == "unavailable", entity_id
    # The siege line still works on the holding-wide model; nothing is breached here.
    assert hass.states.get("binary_sensor.house_testhold_breached").state == "off"
    for entity_id in V04_ENTITIES:
        assert hass.states.get(entity_id).state != "unavailable", entity_id
    assert hass.states.get("sensor.house_testhold_holdings").state == "12"
    lst = hass.states.get("sensor.house_testhold_holdings").attributes["list"]
    assert lst[0]["ground"] is None and lst[0]["needs_walking"] is None
    assert ground_calls(server) == []  # no holding has ground: nothing to draw, nothing asked
    assert integration_logs(caplog) == []


async def test_legacy_breach_names_the_holding(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    """On the holding-wide model the siege line names the holding itself."""
    p = server.add(legacy_player())
    p.summary |= {"siege_place": "Owl Flight", "siege_falls_at": FALLS_AT}
    entry = make_entry()
    await setup(hass, entry)
    breached = hass.states.get("binary_sensor.house_testhold_breached")
    assert breached.state == "on" and breached.attributes["holding"] == "Owl Flight"
    assert hass.states.get("image.house_testhold_ground").state == "unavailable"


async def test_old_server(hass: HomeAssistant, server: FakeAshenmarch, caplog) -> None:
    """A server before ASH-4513: no ground fields, no siege line, and ground.png is a 404."""
    caplog.set_level(logging.DEBUG)
    server.add(Player(TOKEN_A, PLAYER_A, "Testhold"))
    server.ground_status = 404
    entry = make_entry()
    await setup(hass, entry)
    await poll(hass, entry)
    for entity_id in (*GROUND_ENTITIES, "binary_sensor.house_testhold_breached", "sensor.house_testhold_breach_falls"):
        assert hass.states.get(entity_id).state == "unavailable", entity_id
    for entity_id in V04_ENTITIES:
        assert hass.states.get(entity_id).state != "unavailable", entity_id
    assert integration_logs(caplog) == []


async def test_png_404_is_not_now(hass: HomeAssistant, server: FakeAshenmarch, caplog, freezer) -> None:
    """Holdings have ground but the picture route answers 404: unavailable, debug only, tried again hourly."""
    caplog.set_level(logging.DEBUG)
    server.add(breached_player())
    server.ground_status = 404
    entry = make_entry()
    await setup(hass, entry)
    assert hass.states.get("image.house_testhold_ground").state == "unavailable"
    assert hass.states.get("sensor.house_testhold_breached_blocks").state == "1"  # the counts don't need the picture
    assert len(ground_calls(server)) == 1
    await poll(hass, entry)
    assert len(ground_calls(server)) == 1  # not every poll
    server.ground_status = 200
    freezer.tick(timedelta(hours=1, minutes=1))
    async_fire_time_changed(hass)
    await poll(hass, entry)
    assert len(ground_calls(server)) == 2
    assert hass.states.get("image.house_testhold_ground").state != "unavailable"
    assert integration_logs(caplog) == []


async def test_token_scope_on_png_is_not_now(hass: HomeAssistant, server: FakeAshenmarch, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    server.add(breached_player())
    server.ground_status = 403
    entry = make_entry()
    await setup(hass, entry)
    assert hass.states.get("image.house_testhold_ground").state == "unavailable"
    assert hass.states.get("sensor.house_testhold_holdings").state == "12"
    assert integration_logs(caplog) == []


# ---------------- ETags: download only when something changed ----------------


async def test_png_downloaded_only_on_change_and_304_keeps_it(hass: HomeAssistant, server: FakeAshenmarch, freezer) -> None:
    p = server.add(breached_player())
    entry = make_entry()
    await setup(hass, entry)
    picture = hass.states.get("image.house_testhold_ground").attributes["entity_picture"]
    assert len(ground_calls(server)) == 1

    # Holdings unchanged (a 304) and the same glance holding: no request at all.
    await poll(hass, entry)
    await poll(hass, entry)
    assert len(ground_calls(server)) == 1

    # The holdings changed, the picture didn't: a conditional request, a 304, the same image.
    p.holdings[1]["decay_pct"] = 0.5
    await poll(hass, entry)
    calls = ground_calls(server)
    assert len(calls) == 2 and calls[-1][1].get("If-None-Match") == 'W/"h-Owl Flight-g1"'
    image = hass.states.get("image.house_testhold_ground")
    assert image.state != "unavailable" and image.attributes["entity_picture"] == picture

    # The ground moved: the holdings ETag moves and the picture is downloaded again.
    p.holdings[0]["ground"]["breached"] = 2
    server.ground_version = "g2"
    freezer.tick(timedelta(seconds=5))
    await poll(hass, entry)
    assert len(ground_calls(server)) == 3
    assert hass.states.get("image.house_testhold_ground").attributes["breached"] == 2

    # A day later it is checked again even though nothing changed.
    await poll(hass, entry)
    assert len(ground_calls(server)) == 3
    freezer.tick(timedelta(days=1, minutes=1))
    await poll(hass, entry)
    assert len(ground_calls(server)) == 4


async def test_glance_holding_change_downloads_the_other(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    p = server.add(breached_player())
    entry = make_entry()
    await setup(hass, entry)
    p.summary["ground_holding"] = "Pitchfork"
    await poll(hass, entry)
    assert ground_calls(server)[-1][2]["holding"] == "h-Pitchfork"
    assert "If-None-Match" not in ground_calls(server)[-1][1]
    assert hass.states.get("image.house_testhold_ground").attributes["holding"] == "Pitchfork"


async def test_picture_survives_restart_without_download(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    server.add(breached_player())
    entry = make_entry()
    await setup(hass, entry)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert len(ground_calls(server)) == 1  # the stored picture and ETags are reused
    assert hass.states.get("image.house_testhold_ground").state != "unavailable"


# ---------------- pinning a holding ----------------


async def test_pinned_holding_then_lost(hass: HomeAssistant, server: FakeAshenmarch, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    p = server.add(breached_player())
    entry = make_entry(options={CONF_GROUND_HOLDING: "Pitchfork"})
    await setup(hass, entry)
    assert hass.states.get("image.house_testhold_ground").attributes["holding"] == "Pitchfork"
    assert ground_calls(server)[-1][2]["holding"] == "h-Pitchfork"

    p.holdings = [p.holdings[0]]  # Pitchfork is lost
    await poll(hass, entry)
    await poll(hass, entry)
    assert hass.states.get("image.house_testhold_ground").attributes["holding"] == "Owl Flight"
    lost = [r for r in integration_logs(caplog) if "pinned" in r.getMessage()]
    assert len(lost) == 1 and lost[0].levelno == logging.INFO
    assert "Pitchfork" not in lost[0].getMessage()


async def test_options_offer_holdings_with_ground(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    server.add(breached_player())
    entry = make_entry()
    await setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    field = next(k for k in result["data_schema"].schema if k == CONF_GROUND_HOLDING)
    selector = result["data_schema"].schema[field]
    assert selector.config["options"] == [GROUND_AUTO, "Owl Flight", "Pitchfork"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"alert_area": "near", "zones": ["zone.home"], CONF_GROUND_HOLDING: "Pitchfork"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_GROUND_HOLDING] == "Pitchfork"
    await hass.async_block_till_done()
    assert hass.states.get("image.house_testhold_ground").attributes["holding"] == "Pitchfork"

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"alert_area": "near", "zones": ["zone.home"], CONF_GROUND_HOLDING: GROUND_AUTO}
    )
    assert CONF_GROUND_HOLDING not in entry.options


# ---------------- siege_started names the street ----------------


async def test_siege_started_with_place(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    p = server.add(breached_player())
    entry = make_entry()
    await setup(hass, entry)
    events = async_capture_events(hass, EVENT_ASHENMARCH)
    p.events.append(event("e1", "siege_started", north_of(HOME, 2), attacker_house="Rival",
                          place=STREET, blocks_breached=3, falls_at=FALLS_AT))
    await poll(hass, entry)
    assert len(events) == 1
    data = events[0].data
    assert data["place"] == STREET and data["blocks_breached"] == 3 and data["falls_at"] == FALLS_AT
    assert data["data"]["place"] == STREET
    recent = hass.states.get("sensor.house_testhold_last_event").attributes["recent"][0]
    assert recent["place"] == STREET and recent["falls_at"] == FALLS_AT


async def test_siege_started_without_place(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    """The holding-wide model (or an older server): no street fields, and nothing else changes."""
    p = server.add(legacy_player())
    entry = make_entry()
    await setup(hass, entry)
    events = async_capture_events(hass, EVENT_ASHENMARCH)
    p.events.append(event("e1", "siege_started", north_of(HOME, 2), attacker_house="Rival"))
    await poll(hass, entry)
    assert len(events) == 1
    data = events[0].data
    assert not {"place", "blocks_breached", "falls_at"} & set(data)
    assert data["data"] == {"attacker_house": "Rival"}
    assert data["zone"] == "zone.home"


async def test_upgrade_from_v04_reads_ground(hass: HomeAssistant, server: FakeAshenmarch, hass_storage) -> None:
    """An entry stored by v0.4 (holdings without ground, an ETag the server still matches) gets ground on the first poll."""
    p = server.add(breached_player())
    entry = make_entry()
    old = [{"name": h["name"], "state": "active"} for h in p.holdings]
    hass_storage[f"ashenmarch.{entry.entry_id}"] = {
        "version": 1, "key": f"ashenmarch.{entry.entry_id}",
        "data": {"holdings": old, "holdings_etag": f'W/"{hash(str(p.holdings))}"'},
    }
    await setup(hass, entry)
    assert hass.states.get("image.house_testhold_ground").state != "unavailable"
    assert hass.states.get("sensor.house_testhold_breached_blocks").state == "1"
    assert hass.states.get("sensor.house_testhold_holdings").attributes["list"][0]["ground"]["breached"] == 1


# ---------------- v0.6.0: select.<house>_ground_picture ----------------


async def test_ground_picture_select(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    server.add(breached_player())
    entry = make_entry()
    await setup(hass, entry)
    sel = hass.states.get("select.house_testhold_ground_picture")
    assert sel.state == GROUND_AUTO
    assert sel.attributes["options"] == [GROUND_AUTO, "Owl Flight", "Pitchfork"]

    await hass.services.async_call("select", "select_option", {"entity_id": sel.entity_id, "option": "Pitchfork"}, blocking=True)
    await hass.async_block_till_done()
    assert entry.options[CONF_GROUND_HOLDING] == "Pitchfork"
    assert hass.states.get("select.house_testhold_ground_picture").state == "Pitchfork"
    assert hass.states.get("image.house_testhold_ground").attributes["holding"] == "Pitchfork"
    assert ground_calls(server)[-1][2]["holding"] == "h-Pitchfork"

    await hass.services.async_call("select", "select_option", {"entity_id": sel.entity_id, "option": GROUND_AUTO}, blocking=True)
    await hass.async_block_till_done()
    assert CONF_GROUND_HOLDING not in entry.options
    assert hass.states.get("image.house_testhold_ground").attributes["holding"] == "Owl Flight"


async def test_ground_picture_select_rejects_unknown(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    from homeassistant.exceptions import ServiceValidationError

    server.add(breached_player())
    entry = make_entry()
    await setup(hass, entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call("select", "select_option",
                                       {"entity_id": "select.house_testhold_ground_picture", "option": "Nowhere"}, blocking=True)


async def test_ground_picture_select_legacy_unavailable(hass: HomeAssistant, server: FakeAshenmarch) -> None:
    server.add(legacy_player())
    entry = make_entry()
    await setup(hass, entry)
    assert hass.states.get("select.house_testhold_ground_picture").state == "unavailable"
