"""The Lupus Cloud integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import LupusCloudApi, LupusCloudError
from .const import (
    CONF_ACCOUNT_UUID,
    CONF_APPLIED_AREAS,
    CONF_AREAS,
    CONF_KNOWN_ROOMS,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)
from .coordinator import LupusCloudCoordinator
from .entity import building_device_info
from .webhook import async_remove_webhooks, async_setup_webhook, async_unload_webhook

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]

type LupusConfigEntry = ConfigEntry[LupusCloudCoordinator]


def create_api(hass: HomeAssistant, entry: ConfigEntry) -> LupusCloudApi:
    """Create an API client for a config entry."""
    api = LupusCloudApi(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data.get(CONF_PASSWORD),
        entry.data.get(CONF_REFRESH_TOKEN),
    )

    @callback
    def _store_refresh_token(token: str) -> None:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_REFRESH_TOKEN: token}
        )

    api.on_refresh_token_changed = _store_refresh_token
    return api


async def async_setup_entry(hass: HomeAssistant, entry: LupusConfigEntry) -> bool:
    """Set up Lupus Cloud from a config entry."""
    coordinator = LupusCloudCoordinator(hass, entry, create_api(hass, entry))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Buildings are parents ("via device") of the Lupus devices
    registry = dr.async_get(hass)
    for building_uuid in coordinator.data.buildings:
        building_device = registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            **building_device_info(coordinator, building_uuid),
        )
        coordinator.building_device_ids[building_uuid] = building_device.id

    remove_excluded_devices(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if CONF_KNOWN_ROOMS not in entry.options:
        # Entries created before this option existed: all current rooms are known
        options = {
            **entry.options,
            CONF_KNOWN_ROOMS: sorted(
                {d.get("nodeRef", "") for d in coordinator.data.all_devices.values()}
            ),
        }
        coordinator.options_snapshot = options
        hass.config_entries.async_update_entry(entry, options=options)

    @callback
    def _on_update() -> None:
        apply_area_mapping(hass, entry, coordinator)
        check_new_rooms(hass, entry, coordinator)

    # Force the mapping only for rooms whose area was changed in the options
    # since it was last applied; manual area changes in HA are kept otherwise.
    mapping: dict[str, str] = entry.options.get(CONF_AREAS, {})
    applied: dict[str, str] = entry.data.get(CONF_APPLIED_AREAS, mapping)
    changed = {room for room, area in mapping.items() if applied.get(room) != area}
    apply_area_mapping(hass, entry, coordinator, force_rooms=changed)
    if entry.data.get(CONF_APPLIED_AREAS) != mapping:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_APPLIED_AREAS: dict(mapping)}
        )
    check_new_rooms(hass, entry, coordinator)
    entry.async_on_unload(coordinator.async_add_listener(_on_update))

    await async_setup_webhook(hass, entry, coordinator)
    coordinator.async_update_listeners()
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: LupusConfigEntry) -> None:
    # Data updates (e.g. a rotated refresh token) must not trigger a reload
    if dict(entry.options) != entry.runtime_data.options_snapshot:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: LupusConfigEntry) -> bool:
    """Unload a config entry."""
    async_unload_webhook(hass, entry)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the webhook from the Lupus Cloud when the entry is deleted."""
    ir.async_delete_issue(hass, DOMAIN, f"new_rooms_{entry.entry_id}")
    try:
        await async_remove_webhooks(hass, entry, create_api(hass, entry))
    except LupusCloudError as err:
        _LOGGER.warning("Could not remove Lupus Cloud webhook: %s", err)


def _is_stale_device(coordinator: LupusCloudCoordinator, device: dr.DeviceEntry) -> bool:
    """True for Lupus devices that are no longer included (room not assigned / removed)."""
    keep = (
        set(coordinator.data.devices)
        | set(coordinator.data.buildings)
        | {coordinator.config_entry.data.get(CONF_ACCOUNT_UUID)}
    )
    return all(domain != DOMAIN or ident not in keep for domain, ident in device.identifiers)


@callback
def remove_excluded_devices(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: LupusCloudCoordinator
) -> None:
    """Remove devices of rooms that are not assigned to an area (anymore)."""
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if _is_stale_device(coordinator, device):
            registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: LupusConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow deleting devices in the UI that are no longer included."""
    return _is_stale_device(entry.runtime_data, device)


@callback
def check_new_rooms(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: LupusCloudCoordinator
) -> None:
    """Show a repair issue if devices appeared in rooms not offered for mapping yet."""
    if not coordinator.data:
        return
    known = set(entry.options.get(CONF_KNOWN_ROOMS, [])) | set(entry.options.get(CONF_AREAS, {}))
    new_rooms = {d.get("nodeRef", "") for d in coordinator.data.all_devices.values()} - known
    issue_id = f"new_rooms_{entry.entry_id}"
    if not new_rooms:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    labels = sorted(
        coordinator.data.nodes[uuid].label if uuid in coordinator.data.nodes else uuid
        for uuid in new_rooms
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="new_rooms",
        translation_placeholders={"rooms": ", ".join(labels)},
    )


@callback
def apply_area_mapping(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: LupusCloudCoordinator,
    force_rooms: set[str] | None = None,
) -> None:
    """Move devices into the HA area configured for their Lupus room.

    Devices without an area always get the mapped one. Devices that already
    have an area are only moved if their room is in ``force_rooms`` (its
    mapping was changed in the options), so manual changes are kept.
    """
    mapping: dict[str, str] = entry.options.get(CONF_AREAS, {})
    if not mapping or not coordinator.data:
        return
    registry = dr.async_get(hass)
    for uuid, device in coordinator.data.devices.items():
        area_id = mapping.get(device.get("nodeRef", ""))
        if not area_id:
            continue
        if hasattr(registry, "async_get_device_by_identifier"):
            entry_device = registry.async_get_device_by_identifier((DOMAIN, uuid), entry.entry_id)
        else:  # Home Assistant < 2026.8
            entry_device = registry.async_get_device(identifiers={(DOMAIN, uuid)})
        if entry_device is None or entry_device.area_id == area_id:
            continue
        if entry_device.area_id is None or device.get("nodeRef") in (force_rooms or set()):
            registry.async_update_device(entry_device.id, area_id=area_id)
