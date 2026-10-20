"""Constants for the Ashenmarch integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "ashenmarch"
MANUFACTURER = "Ashenmarch"
VERSION = "0.4.0"

API_BASE = "https://api.ashenmarch.com/v1"
PORTAL_TOKENS_URL = "https://ashenmarch.com/portal/profile"
TOKEN_PREFIX = "am_pat_"

CONF_TOKEN = "token"
CONF_PLAYER_ID = "player_id"

# Options (per player entry)
CONF_ALERT_AREA = "alert_area"
CONF_ZONES = "zones"
CONF_RADIUS = "radius"  # shown in HA's unit in the form, stored as CONF_RADIUS_KM
CONF_RADIUS_KM = "radius_km"  # only present once the player typed a radius

AREA_NEAR = "near"
AREA_ALL = "all"
DEFAULT_ZONE = "zone.home"

# The radius default (geo.default_radius) follows the number of zones while the player
# hasn't set one: 25 mi / 40 km for one zone, 5 mi / 8 km for several (D-365).
RADIUS_MIN = 0.5
RADIUS_MAX = 100.0
# In "all territories" mode an event still names the nearest selected zone within this distance.
NEAREST_ZONE_MAX_KM = 160.934  # 100 mi

SCAN_INTERVAL = timedelta(minutes=5)
MAX_BACKOFF = timedelta(minutes=60)
CREST_MAX_AGE = timedelta(days=1)
SLOW_INTERVAL = timedelta(hours=1)  # profile (ever claimed) and rank progress
EVENTS_PAGE_LIMIT = 50
EVENTS_MAX_PAGES = 10  # per poll; the rest comes next poll

EVENT_ASHENMARCH = "ashenmarch_event"

# Event types (stable server ids, ASH-3746)
SIEGE_STARTED = "siege_started"
SIEGE_ENDED = "siege_ended"
HOLDING_LOST = "holding_lost"
HOLDING_WON = "holding_won"
FADING_SOON = "fading_soon"
MARCH_FILED = "march_filed"
EVENT_TYPES = [SIEGE_STARTED, SIEGE_ENDED, HOLDING_LOST, HOLDING_WON, FADING_SOON, MARCH_FILED]
# Filtered to the alert area; march_filed always fires (it's the player's own march).
AREA_FILTERED_TYPES = {SIEGE_STARTED, SIEGE_ENDED, HOLDING_LOST, HOLDING_WON, FADING_SOON}

STORAGE_VERSION = 1
SEEN_IDS_CAP = 500
SKIPPED_CAP = 25  # out-of-area events kept for the diagnostics
RECENT_CAP = 20  # events kept per House for its details page (recent attribute)

ISSUE_NO_ZONES = "no_zones"
