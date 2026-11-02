"""Receive push messages (alarms) from the Lupus Cloud via a HA webhook.

The integration registers a Home Assistant webhook and then creates a
matching webhook entry in the Lupus Cloud for every building (the same
thing you can do manually under "Integrationen > Webhook" in the web app).
The Lupus Cloud then POSTs every event to Home Assistant immediately.
"""

from __future__ import annotations

import hmac
import json
import logging
import secrets
from typing import TYPE_CHECKING, Any

from aiohttp import web

from homeassistant.components import cloud, webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.network import NoURLAvailableError

from .api import LupusCloudApi, LupusCloudError
from .const import (
    CONF_CLOUDHOOK_URL,
    CONF_WEBHOOK_ENABLED,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    CONF_WEBHOOK_URL_OVERRIDE,
    DOMAIN,
    MAX_WEBHOOK_BODY,
)
from .coordinator import sanitize_payload

if TYPE_CHECKING:
    from .coordinator import LupusCloudCoordinator

_LOGGER = logging.getLogger(__name__)


def _token_matches(header: str, secret: str) -> bool:
    """Check an Authorization header ("Bearer <token>" or the bare token)."""
    parts = header.split()
    token = parts[-1] if parts else ""
    return hmac.compare_digest(token.encode(), secret.encode())


def _issue_id(entry: ConfigEntry) -> str:
    return f"webhook_{entry.entry_id}"


async def _async_webhook_url(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Return the public URL the Lupus Cloud should call."""
    webhook_id = entry.data[CONF_WEBHOOK_ID]
    override = (entry.options.get(CONF_WEBHOOK_URL_OVERRIDE) or "").strip()
    if override:
        return f"{override.rstrip('/')}/api/webhook/{webhook_id}"

    if "cloud" in hass.config.components and cloud.async_active_subscription(hass):
        if not (url := entry.data.get(CONF_CLOUDHOOK_URL)):
            url = await cloud.async_create_cloudhook(hass, webhook_id)
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_CLOUDHOOK_URL: url}
            )
        return url

    return webhook.async_generate_url(hass, webhook_id, allow_internal=False, prefer_external=True)


async def async_setup_webhook(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: LupusCloudCoordinator
) -> None:
    """Register the HA webhook and announce it to the Lupus Cloud."""
    if not entry.options.get(CONF_WEBHOOK_ENABLED, True):
        coordinator.webhook_status = "disabled"
        ir.async_delete_issue(hass, DOMAIN, _issue_id(entry))
        return

    if CONF_WEBHOOK_ID not in entry.data:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_WEBHOOK_ID: secrets.token_hex(32),
                CONF_WEBHOOK_SECRET: secrets.token_urlsafe(32),
            },
        )
    webhook_id = entry.data[CONF_WEBHOOK_ID]

    async def _handle(hass: HomeAssistant, webhook_id: str, request: web.Request) -> web.Response:
        # The Lupus Cloud sends the bearer token we registered. Requests
        # without the correct token are rejected (constant time compare).
        secret = str(entry.data.get(CONF_WEBHOOK_SECRET) or "")
        auth = request.headers.get("Authorization", "")
        if not secret or not _token_matches(auth, secret):
            _LOGGER.warning("Rejected Lupus Cloud webhook request without valid token")
            return web.Response(status=401)
        if (request.content_length or 0) > MAX_WEBHOOK_BODY:
            return web.Response(status=413)
        body = await request.content.read(MAX_WEBHOOK_BODY + 1)
        if len(body) > MAX_WEBHOOK_BODY:
            return web.Response(status=413)
        payload: Any
        try:
            payload = json.loads(body) if body else {}
        except ValueError:
            payload = body.decode(errors="replace")
        _LOGGER.debug("Lupus Cloud webhook received: %s", sanitize_payload(payload))
        coordinator.handle_webhook(payload)
        return web.Response(status=200)

    webhook.async_register(
        hass,
        DOMAIN,
        f"Lupus Cloud ({entry.title})",
        webhook_id,
        _handle,
        local_only=False,
        allowed_methods=["POST", "PUT"],
    )

    try:
        url = await _async_webhook_url(hass, entry)
    except (NoURLAvailableError, cloud.CloudNotAvailable, cloud.CloudNotConnected):
        coordinator.webhook_status = "no_external_url"
        ir.async_create_issue(
            hass,
            DOMAIN,
            _issue_id(entry),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="no_external_url",
        )
        _LOGGER.warning(
            "No external URL available - Lupus Cloud alarms are only polled. "
            "Configure an external URL, Home Assistant Cloud or the webhook "
            "URL option of the integration"
        )
        return

    coordinator.webhook_url = url
    try:
        await _async_register_in_cloud(coordinator, entry, url)
    except LupusCloudError as err:
        coordinator.webhook_status = "error"
        ir.async_create_issue(
            hass,
            DOMAIN,
            _issue_id(entry),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="webhook_registration_failed",
            translation_placeholders={"error": str(err)},
        )
        _LOGGER.warning("Could not register webhook in Lupus Cloud: %s", err)
        return

    coordinator.webhook_status = "registered"
    ir.async_delete_issue(hass, DOMAIN, _issue_id(entry))


async def _async_register_in_cloud(
    coordinator: LupusCloudCoordinator, entry: ConfigEntry, url: str
) -> None:
    api = coordinator.api
    webhook_id = entry.data[CONF_WEBHOOK_ID]
    bearer = entry.data[CONF_WEBHOOK_SECRET]
    for building_uuid in coordinator.data.buildings:
        existing = await api.get_webhooks(building_uuid)
        ours = [hook for hook in existing if webhook_id in str(hook.get("url", ""))]
        if not ours:
            await api.add_webhook(building_uuid, url, bearer)
            _LOGGER.info("Registered webhook for building %s", building_uuid)
            continue
        hook = ours[0]
        if (
            hook.get("url") != url
            or not hook.get("enabled", True)
            or hook.get("filter")
            or hook.get("bearer") not in (None, bearer)
        ):
            await api.update_webhook(hook["uuid"], url, bearer)
        for duplicate in ours[1:]:
            await api.delete_webhook(duplicate["uuid"])


@callback
def async_unload_webhook(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Unregister the HA webhook (the cloud side is kept)."""
    if webhook_id := entry.data.get(CONF_WEBHOOK_ID):
        webhook.async_unregister(hass, webhook_id)


async def async_remove_webhooks(
    hass: HomeAssistant, entry: ConfigEntry, api: LupusCloudApi
) -> None:
    """Delete our webhooks from the Lupus Cloud and the cloudhook."""
    ir.async_delete_issue(hass, DOMAIN, _issue_id(entry))
    webhook_id = entry.data.get(CONF_WEBHOOK_ID)
    if not webhook_id:
        return
    if entry.data.get(CONF_CLOUDHOOK_URL) and "cloud" in hass.config.components:
        try:
            await cloud.async_delete_cloudhook(hass, webhook_id)
        except (cloud.CloudNotAvailable, ValueError):
            pass
    tree = await api.get_location_tree()
    for items in tree.values():
        if not isinstance(items, list):
            continue
        for item in items:
            if not item.get("uuid"):
                continue
            for hook in await api.get_webhooks(item["uuid"]):
                if webhook_id in str(hook.get("url", "")):
                    await api.delete_webhook(hook["uuid"])
