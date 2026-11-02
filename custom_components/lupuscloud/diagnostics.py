"""Diagnostics support for Lupus Cloud."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import LupusConfigEntry
from .const import (
    CONF_ACCOUNT_UUID,
    CONF_CLOUDHOOK_URL,
    CONF_REFRESH_TOKEN,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    CONF_WEBHOOK_URL_OVERRIDE,
)

TO_REDACT = {
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_REFRESH_TOKEN,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    CONF_CLOUDHOOK_URL,
    CONF_WEBHOOK_URL_OVERRIDE,
    CONF_ACCOUNT_UUID,
    "accountUuid",
    "ownerUuid",
    "createdBy",
    "destination",
    "coordinate",
    "location",
    "email",
    "address",
    "streetAddress",
    "latitude",
    "longitude",
    "detailedMessage",
    "nodeName",
    "url",
    "bearer",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: LupusConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return async_redact_data(
        {
            "entry": {"data": dict(entry.data), "options": dict(entry.options)},
            "webhook_status": coordinator.webhook_status,
            "last_webhook": coordinator.last_webhook,
            "buildings": data.buildings,
            "nodes": {k: asdict(v) for k, v in data.nodes.items()},
            "devices": data.devices,
            "alarm_uuids": sorted(data.alarm_uuids),
            "history": data.history,
            "notifications": data.notifications,
        },
        TO_REDACT,
    )
