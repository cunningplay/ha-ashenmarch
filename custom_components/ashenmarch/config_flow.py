"""Config, re-auth and options flows for Ashenmarch (one entry per player)."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import house_title
from .api import AshenmarchApi, AshenmarchAuthError, AshenmarchError
from .const import (
    AREA_ALL,
    AREA_NEAR,
    CONF_ALERT_AREA,
    CONF_PLAYER_ID,
    CONF_RADIUS,
    CONF_RADIUS_KM,
    CONF_TOKEN,
    CONF_ZONES,
    DEFAULT_ZONE,
    DOMAIN,
    PORTAL_TOKENS_URL,
    RADIUS_MAX,
    RADIUS_MIN,
)
from .coordinator import display_unit
from .geo import default_radius, format_distance, from_km, to_km

_LOGGER = logging.getLogger(__name__)

TOKEN_SCHEMA = vol.Schema(
    {vol.Required(CONF_TOKEN): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
)


async def _validate(hass: HomeAssistant, token: str) -> tuple[dict[str, Any] | None, str | None]:
    """The player's profile for this token, or an error key. The token is never logged."""
    api = AshenmarchApi(async_get_clientsession(hass), token.strip())
    try:
        me = await api.get_me()
    except AshenmarchAuthError:
        return None, "invalid_auth"
    except AshenmarchError as err:
        _LOGGER.debug("Ashenmarch token check failed: %s", err)
        return None, "cannot_connect"
    if not isinstance(me, dict) or not me.get("id"):
        return None, "unknown"
    return me, None


class AshenmarchConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a player by pasting their personal access token."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the token; the player's id makes the entry unique."""
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            me, error = await _validate(self.hass, token)
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(str(me["id"]))
                self._abort_if_unique_id_configured()
                house = me.get("house_name")
                return self.async_create_entry(
                    title=house_title(self.hass.config.language, house),
                    data={CONF_TOKEN: token, CONF_PLAYER_ID: str(me["id"]), "house_name": house},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=TOKEN_SCHEMA,
            errors=errors,
            description_placeholders={"portal_url": PORTAL_TOKENS_URL},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The token was refused (revoked or expired): ask for a new one."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """A new token for the same player; other players are untouched."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            me, error = await _validate(self.hass, token)
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(str(me["id"]))
                self._abort_if_unique_id_mismatch(reason="wrong_player")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: token, "house_name": me.get("house_name")}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=TOKEN_SCHEMA,
            errors=errors,
            description_placeholders={"house": entry.title, "portal_url": PORTAL_TOKENS_URL},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> AshenmarchOptionsFlow:
        """Configure: where alerts come from."""
        return AshenmarchOptionsFlow()


class AshenmarchOptionsFlow(OptionsFlow):
    """Alert area: near the selected zones (default Home, 25 mi) or all territories."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """One screen: area, zones, radius in HA's unit."""
        options = self.config_entry.options
        unit = display_unit(self.hass)
        zones: list[str] = list(options.get(CONF_ZONES, [DEFAULT_ZONE]))
        custom_km: float | None = options.get(CONF_RADIUS_KM)
        shown = from_km(custom_km, unit) if custom_km is not None else default_radius(len(zones), unit)
        errors: dict[str, str] = {}

        if user_input is not None:
            area = user_input[CONF_ALERT_AREA]
            new_zones = list(user_input.get(CONF_ZONES) or [])
            radius = user_input.get(CONF_RADIUS)
            if area == AREA_NEAR and not new_zones:
                errors[CONF_ZONES] = "zones_required"
            else:
                # The default follows the zone count until the player types their own radius;
                # leaving the shown default (or clearing the field) keeps it a default.
                if radius is None or (custom_km is None and abs(float(radius) - shown) < 0.05):
                    new_custom = None
                else:
                    new_custom = round(to_km(float(radius), unit), 4)
                data: dict[str, Any] = {CONF_ALERT_AREA: area, CONF_ZONES: new_zones}
                if new_custom is not None:
                    data[CONF_RADIUS_KM] = new_custom
                return self.async_create_entry(data=data)

        default_now = default_radius(len(zones), unit)
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ALERT_AREA, default=options.get(CONF_ALERT_AREA, AREA_NEAR)
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[AREA_NEAR, AREA_ALL],
                        mode=SelectSelectorMode.LIST,
                        translation_key=CONF_ALERT_AREA,
                    )
                ),
                vol.Optional(CONF_ZONES, default=zones): EntitySelector(
                    EntitySelectorConfig(domain="zone", multiple=True)
                ),
                vol.Optional(
                    CONF_RADIUS, description={"suggested_value": round(shown, 1)}
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=RADIUS_MIN,
                        max=RADIUS_MAX,
                        step=0.1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement=unit,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "zone_count": str(len(zones)),
                "default": format_distance(default_now),
                "unit": unit,
                "one_zone": format_distance(default_radius(1, unit)),
                "many_zones": format_distance(default_radius(2, unit)),
            },
        )
