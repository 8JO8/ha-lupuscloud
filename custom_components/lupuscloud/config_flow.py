"""Config flow for Lupus Cloud."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import re
import secrets
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    AreaSelector,
    BooleanSelector,
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

from .api import (
    CognitoAuth,
    LupusCloudApi,
    LupusCloudAuthError,
    LupusCloudConnectionError,
    LupusCloudError,
    LupusCloudMfaRequired,
    Tokens,
)
from .const import (
    ALARM_EVENT_REASONS,
    CONF_ACCOUNT_UUID,
    CONF_ALARM_EVENTS,
    CONF_AREAS,
    CONF_KNOWN_ROOMS,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_WEBHOOK_ENABLED,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    CONF_WEBHOOK_URL_OVERRIDE,
    DEFAULT_ALARM_EVENTS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import LupusNode, parse_structure

_LOGGER = logging.getLogger(__name__)

CONF_CODE = "code"


def rooms_with_devices(
    nodes: Mapping[str, LupusNode],
    devices: Mapping[str, dict[str, Any]],
    building_names: Mapping[str, str],
    extra: set[str] | None = None,
) -> list[LupusNode]:
    """Return the structure nodes (room, apartment, ...) that contain devices.

    ``extra`` adds nodes that should be shown anyway (e.g. already mapped ones).
    With several buildings the building name is prefixed to the label.
    """
    wanted = {d.get("nodeRef") for d in devices.values()} | (extra or set())
    multi = len(building_names) > 1
    rooms = [
        LupusNode(
            node.uuid,
            node.name,
            node.node_type,
            node.building_uuid,
            [building_names.get(node.building_uuid, ""), *node.path] if multi else node.path,
            node.is_room,
        )
        for node in nodes.values()
        if node.uuid in wanted
    ]
    rooms.sort(key=lambda n: n.label)
    return rooms


async def async_fetch_rooms(api: LupusCloudApi) -> list[LupusNode]:
    """Load structure and devices and return the nodes that contain devices."""
    tree = await api.get_location_tree()
    buildings = [
        (item, key == "privateHouses")
        for key, items in tree.items()
        if isinstance(items, list)
        for item in items
        if item.get("uuid")
    ]
    nodes: dict[str, LupusNode] = {}
    devices: dict[str, dict[str, Any]] = {}
    names: dict[str, str] = {}
    for building, personal in buildings:
        details = await api.get_building(building["uuid"])
        names[building["uuid"]] = building.get("name") or details.get("name") or ""
        nodes.update(parse_structure(details))
        for device in await api.get_devices(building["uuid"], personal):
            devices[device["uuid"]] = device
    return rooms_with_devices(nodes, devices, names)


def _normalize(name: str) -> str:
    return re.sub(r"[\s_\-]*\d+$", "", name).strip().casefold()


def rooms_schema(
    hass: HomeAssistant, rooms: list[LupusNode], current: Mapping[str, str]
) -> tuple[vol.Schema, dict[str, str]]:
    """Build a schema with one area selector per Lupus room.

    The room path is used as field key so it shows up as label in the UI.
    """
    areas = ar.async_get(hass)
    by_name = {_normalize(a.name): a.id for a in areas.async_list_areas()}
    schema: dict[Any, Any] = {}
    key_to_uuid: dict[str, str] = {}
    for room in rooms:
        key = room.label
        if key in key_to_uuid:
            key = f"{key} ({room.uuid[:8]})"
        key_to_uuid[key] = room.uuid
        suggested = current.get(room.uuid)
        if suggested is None and not current:
            suggested = by_name.get(_normalize(room.name))
        if suggested and areas.async_get_area(suggested) is None:
            suggested = None
        schema[vol.Optional(key, description={"suggested_value": suggested})] = AreaSelector()
    return vol.Schema(schema), key_to_uuid


class LupusCloudConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Lupus Cloud."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._username: str = ""
        self._password: str = ""
        self._code: str = ""
        self._data: dict[str, Any] = {}
        self._mfa: LupusCloudMfaRequired | None = None
        self._tokens: Tokens | None = None
        self._rooms: list[LupusNode] = []
        self._room_keys: dict[str, str] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return LupusCloudOptionsFlow()

    async def _login(self) -> dict[str, str]:
        errors: dict[str, str] = {}
        auth = CognitoAuth(async_get_clientsession(self.hass))
        try:
            if self._mfa is not None:
                self._tokens = await auth.respond_mfa(self._mfa, self._code)
            else:
                self._tokens = await auth.login(self._username, self._password)
        except LupusCloudMfaRequired as mfa:
            self._mfa = mfa
            errors["base"] = "mfa_required"
        except LupusCloudAuthError as err:
            _LOGGER.debug("Login failed: %s", err)
            errors["base"] = "invalid_code" if self._mfa else "invalid_auth"
        except LupusCloudConnectionError:
            errors["base"] = "cannot_connect"
        except LupusCloudError:
            _LOGGER.exception("Unexpected login error")
            errors["base"] = "unknown"
        return errors

    def _api(self) -> LupusCloudApi:
        assert self._tokens
        api = LupusCloudApi(
            async_get_clientsession(self.hass),
            self._username,
            self._password,
            self._tokens.refresh_token,
        )
        api.set_tokens(self._tokens)
        return api

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the Lupus Cloud credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._username = user_input[CONF_USERNAME].strip()
            self._password = user_input[CONF_PASSWORD]
            self._mfa = None
            errors = await self._login()
            if errors.get("base") == "mfa_required":
                return await self.async_step_mfa()
            if not errors:
                return await self._async_logged_in()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_USERNAME): TextSelector(
                            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
                        ),
                        vol.Required(CONF_PASSWORD): TextSelector(
                            TextSelectorConfig(
                                type=TextSelectorType.PASSWORD,
                                autocomplete="current-password",
                            )
                        ),
                    }
                ),
                {CONF_USERNAME: self._username},
            ),
            errors=errors,
        )

    async def async_step_mfa(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the 2FA code."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._code = user_input[CONF_CODE]
            errors = await self._login()
            if errors.get("base") == "invalid_code":
                # The Cognito session can only be used once -> start over
                self._mfa = None
                return self.async_show_form(
                    step_id="user",
                    data_schema=vol.Schema(
                        {
                            vol.Required(CONF_USERNAME, default=self._username): str,
                            vol.Required(CONF_PASSWORD): TextSelector(
                                TextSelectorConfig(type=TextSelectorType.PASSWORD)
                            ),
                        }
                    ),
                    errors=errors,
                )
            if not errors:
                return await self._async_logged_in()

        return self.async_show_form(
            step_id="mfa",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CODE): TextSelector(
                        TextSelectorConfig(autocomplete="one-time-code")
                    )
                }
            ),
            description_placeholders={
                "method": {
                    "SMS_MFA": "SMS",
                    "EMAIL_OTP": "E-Mail",
                }.get(self._mfa.challenge if self._mfa else "", "Authenticator-App")
            },
            errors=errors,
        )

    async def _async_logged_in(self) -> ConfigFlowResult:
        assert self._tokens
        api = self._api()
        account_uuid = api.account_uuid or self._username.lower()
        data = {
            CONF_USERNAME: self._username,
            CONF_PASSWORD: self._password,
            CONF_REFRESH_TOKEN: self._tokens.refresh_token,
            CONF_ACCOUNT_UUID: account_uuid,
        }

        await self.async_set_unique_id(account_uuid)
        if self.source in (SOURCE_REAUTH, SOURCE_RECONFIGURE):
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            entry = (
                self._get_reauth_entry()
                if self.source == SOURCE_REAUTH
                else self._get_reconfigure_entry()
            )
            return self.async_update_reload_and_abort(entry, data_updates=data)
        self._abort_if_unique_id_configured()

        self._data = {
            **data,
            CONF_WEBHOOK_ID: secrets.token_hex(32),
            CONF_WEBHOOK_SECRET: secrets.token_urlsafe(32),
        }
        try:
            self._rooms = await async_fetch_rooms(api)
        except LupusCloudError:
            _LOGGER.exception("Could not load building structure")
            self._rooms = []
        if not self._rooms:
            return self._create()
        return await self.async_step_areas()

    async def async_step_areas(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Assign Lupus rooms to Home Assistant areas (unassigned rooms are not included)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            areas = {
                self._room_keys[key]: area
                for key, area in user_input.items()
                if area and key in self._room_keys
            }
            if areas:
                return self._create(areas)
            errors["base"] = "no_rooms"
        schema, self._room_keys = rooms_schema(self.hass, self._rooms, {})
        return self.async_show_form(step_id="areas", data_schema=schema, errors=errors)

    def _create(self, areas: dict[str, str] | None = None) -> ConfigFlowResult:
        return self.async_create_entry(
            title=self._username,
            data=self._data,
            options={
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                CONF_WEBHOOK_ENABLED: True,
                CONF_ALARM_EVENTS: [e.lower() for e in DEFAULT_ALARM_EVENTS],
                CONF_AREAS: areas or {},
                CONF_KNOWN_ROOMS: [room.uuid for room in self._rooms],
            },
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauthentication."""
        self._username = entry_data[CONF_USERNAME]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again (login expired or password changed)."""
        return await self._async_password_step("reauth_confirm", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter a new password at any time (e.g. after changing it in the cloud)."""
        self._username = self._get_reconfigure_entry().data[CONF_USERNAME]
        return await self._async_password_step("reconfigure", user_input)

    async def _async_password_step(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._password = user_input[CONF_PASSWORD]
            self._mfa = None
            errors = await self._login()
            if errors.get("base") == "mfa_required":
                return await self.async_step_mfa()
            if not errors:
                return await self._async_logged_in()
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            description_placeholders={"username": self._username},
            errors=errors,
        )


class LupusCloudOptionsFlow(OptionsFlow):
    """Options: polling interval, webhook and room/area mapping."""

    def __init__(self) -> None:
        """Initialize."""
        self._options: dict[str, Any] = {}
        self._rooms: list[LupusNode] = []
        self._room_keys: dict[str, str] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """General options."""
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        errors: dict[str, str] = {}
        if user_input is not None:
            url = (user_input.get(CONF_WEBHOOK_URL_OVERRIDE) or "").strip()
            parsed = urlparse(url)
            if url and (parsed.scheme not in ("http", "https") or not parsed.netloc):
                errors[CONF_WEBHOOK_URL_OVERRIDE] = "invalid_url"
            if not user_input.get(CONF_ALARM_EVENTS):
                errors[CONF_ALARM_EVENTS] = "no_alarm_events"
            if not errors:
                self._options = {**self.config_entry.options, **user_input}
                self._options[CONF_SCAN_INTERVAL] = int(user_input[CONF_SCAN_INTERVAL])
                if url:
                    self._options[CONF_WEBHOOK_URL_OVERRIDE] = url
                else:
                    self._options.pop(CONF_WEBHOOK_URL_OVERRIDE, None)
                return await self.async_step_areas()

        coordinator = self.config_entry.runtime_data
        schema = vol.Schema(
            {
                vol.Required(CONF_ALARM_EVENTS): SelectSelector(
                    SelectSelectorConfig(
                        options=[event.lower() for event in ALARM_EVENT_REASONS],
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                        translation_key=CONF_ALARM_EVENTS,
                    )
                ),
                vol.Required(CONF_SCAN_INTERVAL): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),
                vol.Required(CONF_WEBHOOK_ENABLED): BooleanSelector(),
                vol.Optional(CONF_WEBHOOK_URL_OVERRIDE): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.URL)
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema,
                user_input
                or {
                    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                    CONF_WEBHOOK_ENABLED: True,
                    CONF_ALARM_EVENTS: [e.lower() for e in DEFAULT_ALARM_EVENTS],
                    **self.config_entry.options,
                },
            ),
            description_placeholders={
                "webhook_status": coordinator.webhook_status,
            },
            errors=errors,
        )

    async def async_step_areas(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Room -> area mapping."""
        if user_input is not None:
            self._options[CONF_AREAS] = {
                self._room_keys[key]: area
                for key, area in user_input.items()
                if area and key in self._room_keys
            }
            self._options[CONF_KNOWN_ROOMS] = sorted(
                set(self.config_entry.options.get(CONF_KNOWN_ROOMS, []))
                | {room.uuid for room in self._rooms}
            )
            return self.async_create_entry(data=self._options)

        # Fetch fresh data so devices added in the meantime show up
        coordinator = self.config_entry.runtime_data
        await coordinator.async_refresh()
        current: dict[str, str] = self.config_entry.options.get(CONF_AREAS, {})
        if coordinator.data:
            self._rooms = rooms_with_devices(
                coordinator.data.nodes,
                coordinator.data.all_devices,
                {u: b.get("name", "") for u, b in coordinator.data.buildings.items()},
                extra=set(current),
            )
        if not self._rooms:
            return self.async_create_entry(data=self._options)
        schema, self._room_keys = rooms_schema(self.hass, self._rooms, current)
        return self.async_show_form(step_id="areas", data_schema=schema)
