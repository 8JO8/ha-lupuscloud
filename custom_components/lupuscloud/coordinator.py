"""Data update coordinator for Lupus Cloud."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import LupusCloudApi, LupusCloudAuthError, LupusCloudError, LupusCloudMfaRequired
from .const import (
    ALARM_EVENT_REASONS,
    CONF_ALARM_EVENTS,
    CONF_AREAS,
    CONF_SCAN_INTERVAL,
    DEFAULT_ALARM_EVENTS,
    DEFAULT_SCAN_INTERVAL,
    DEVICE_TYPE_NAMES,
    DOMAIN,
    EVENT_ALARM,
    EVENT_NOTIFICATION,
    EVENT_WEBHOOK,
    HISTORY_INTERVAL,
    WEBHOOK_ALARM_HOLD,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)

# Keys removed from webhook payloads before they are stored in states / events
_PRIVATE_KEYS = {"longitude", "latitude", "location", "accountUuid", "coordinate"}


@dataclass
class LupusNode:
    """A node of the building structure (floor, apartment, room, ...)."""

    uuid: str
    name: str
    node_type: str
    building_uuid: str
    path: list[str]
    is_room: bool

    @property
    def label(self) -> str:
        return " / ".join(self.path)


@dataclass
class LupusData:
    """All data fetched from the cloud."""

    buildings: dict[str, dict[str, Any]] = field(default_factory=dict)
    nodes: dict[str, LupusNode] = field(default_factory=dict)
    # Devices in rooms the user assigned to an area (these get entities)
    devices: dict[str, dict[str, Any]] = field(default_factory=dict)
    # All devices of the account (used to offer rooms in the options)
    all_devices: dict[str, dict[str, Any]] = field(default_factory=dict)
    alarm_uuids: set[str] = field(default_factory=set)
    history: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    notifications: list[dict[str, Any]] = field(default_factory=list)
    unread_notifications: int = 0


def parse_structure(building: dict[str, Any]) -> dict[str, LupusNode]:
    """Flatten the nested building structure."""
    building_uuid = building["uuid"]
    nodes: dict[str, LupusNode] = {}

    def walk(items: list[dict[str, Any]], path: list[str]) -> None:
        for item in items or []:
            uuid = item.get("uuid")
            if not uuid:
                continue
            name = item.get("name") or item.get("nodeName") or uuid
            children = item.get("children") or []
            node_path = [*path, name]
            nodes[uuid] = LupusNode(
                uuid=uuid,
                name=name,
                node_type=item.get("templateNodeType") or "",
                building_uuid=building_uuid,
                path=node_path,
                is_room=bool(item.get("leaf")) or not children,
            )
            walk(children, node_path)

    walk(building.get("buildingStructure") or building.get("floors") or [], [])
    return nodes


def sanitize_payload(payload: Any) -> Any:
    """Strip location data from a webhook payload (top level and message.data)."""
    if not isinstance(payload, dict):
        return {"raw": str(payload)[:1000]}
    clean = {k: v for k, v in payload.items() if k not in _PRIVATE_KEYS}
    message = clean.get("message")
    if isinstance(message, dict) and isinstance(message.get("data"), dict):
        clean["message"] = {
            **message,
            "data": {k: v for k, v in message["data"].items() if k not in _PRIVATE_KEYS},
        }
    return clean


class LupusCloudCoordinator(DataUpdateCoordinator[LupusData]):
    """Fetch all Lupus Cloud data periodically and on webhook push."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: LupusCloudApi) -> None:
        """Initialize."""
        minutes = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=minutes),
        )
        self.api = api
        self.options_snapshot = dict(entry.options)
        # Which Lupus events count as alarm (configurable in the options)
        self.alarm_events: set[str] = {
            event.upper()
            for event in entry.options.get(CONF_ALARM_EVENTS, DEFAULT_ALARM_EVENTS)
            if event.upper() in ALARM_EVENT_REASONS
        }
        self.alarm_reasons: frozenset[str] = frozenset().union(
            *(ALARM_EVENT_REASONS[event] for event in self.alarm_events)
        )
        # Only devices in rooms assigned to an area are included
        # (None = no mapping configured at all -> include everything)
        self.included_rooms: set[str] | None = (
            set(entry.options[CONF_AREAS]) if CONF_AREAS in entry.options else None
        )
        self.webhook_url: str | None = None
        self.building_device_ids: dict[str, str] = {}
        self.webhook_alarms: dict[str, datetime] = {}
        self.last_webhook: dict[str, Any] | None = None
        self.last_webhook_time: datetime | None = None
        self.webhook_status: str = "disabled"
        self._history_fetched: dict[str, tuple[Any, datetime]] = {}
        self._seen_notifications: set[str] | None = None
        self._previous_alarms: set[str] = set()
        self._pending_refreshes: list[Any] = []

    # ------------------------------------------------------------------ helpers

    def device_name(self, device: dict[str, Any]) -> str:
        """Human readable device name."""
        type_name = DEVICE_TYPE_NAMES.get(
            device.get("deviceType", ""), device.get("deviceType") or "Gerät"
        )
        room = self.room_of(device)
        room_name = room.name if room else (device.get("nodeName") or "").split(" > ")[-1]
        return f"{type_name} {room_name}".strip()

    def room_of(self, device: dict[str, Any]) -> LupusNode | None:
        if self.data is None:
            return None
        return self.data.nodes.get(device.get("nodeRef", ""))

    def device_in_alarm(self, device: dict[str, Any]) -> bool:
        """Return True if the device data reports one of the selected alarms."""
        return (
            str(device.get("status") or "").upper() in self.alarm_reasons
            or str(device.get("statusReason") or "").upper() in self.alarm_reasons
        )

    def event_is_alarm(self, event_type: str, status_reason: str = "") -> bool:
        """Return True if a Lupus event is one of the selected alarms."""
        if event_type in ALARM_EVENT_REASONS:
            return event_type in self.alarm_events
        return status_reason in self.alarm_reasons

    def is_alarm(self, uuid: str) -> bool:
        """Alarm state of a device (or building) from API data or a recent webhook."""
        if self.data and (
            uuid in self.data.alarm_uuids or self.device_in_alarm(self.data.devices.get(uuid, {}))
        ):
            return True
        held = self.webhook_alarms.get(uuid)
        return held is not None and dt_util.utcnow() - held < WEBHOOK_ALARM_HOLD

    # ------------------------------------------------------------------ polling

    async def _async_update_data(self) -> LupusData:
        try:
            return await self._fetch()
        except (LupusCloudAuthError, LupusCloudMfaRequired) as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except LupusCloudError as err:
            raise UpdateFailed(str(err)) from err

    async def _fetch(self) -> LupusData:
        api = self.api
        data = LupusData()
        tree = await api.get_location_tree()
        for key, items in tree.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if item.get("type", "BUILDING") != "BUILDING" or not item.get("uuid"):
                    continue
                data.buildings[item["uuid"]] = {**item, "personal": key == "privateHouses"}

        async def load_building(uuid: str, personal: bool) -> None:
            building, devices = await asyncio.gather(
                api.get_building(uuid), api.get_devices(uuid, personal)
            )
            data.buildings[uuid].update(
                {k: v for k, v in building.items() if k != "buildingStructure"}
            )
            data.nodes.update(parse_structure(building))
            for device in devices:
                device["buildingUuid"] = uuid
                data.all_devices[device["uuid"]] = device

        await asyncio.gather(*(load_building(u, b["personal"]) for u, b in data.buildings.items()))
        data.devices = {
            uuid: device
            for uuid, device in data.all_devices.items()
            if self.included_rooms is None or device.get("nodeRef") in self.included_rooms
        }

        alarm_devices, notifications, unread = await asyncio.gather(
            api.get_alarm_devices(),
            api.get_notifications(),
            api.get_unread_count(),
            return_exceptions=True,
        )
        if isinstance(alarm_devices, LupusCloudAuthError):
            raise alarm_devices
        if isinstance(alarm_devices, list):
            # Only devices whose alarm type is selected
            data.alarm_uuids = {
                d["uuid"]
                for d in alarm_devices
                if d.get("uuid") in data.devices and self.device_in_alarm(d)
            }
        if isinstance(notifications, list):
            data.notifications = notifications
        if isinstance(unread, int):
            data.unread_notifications = unread

        await self._fetch_history(data)
        self._fire_events(data)
        return data

    async def _fetch_history(self, data: LupusData) -> None:
        """Fetch the event log of devices that changed (or periodically)."""
        now = dt_util.utcnow()
        previous = self.data.history if self.data else {}
        todo: list[str] = []
        for uuid, device in data.devices.items():
            marker = (device.get("lastModifiedDate"), device.get("lastHeartbeatDate"))
            fetched = self._history_fetched.get(uuid)
            if (
                fetched is None
                or fetched[0] != marker
                or now - fetched[1] > HISTORY_INTERVAL
                or uuid in data.alarm_uuids
            ):
                todo.append(uuid)
            else:
                data.history[uuid] = previous.get(uuid, [])

        sem = asyncio.Semaphore(4)

        async def load(uuid: str) -> None:
            async with sem:
                try:
                    data.history[uuid] = await self.api.get_device_history(uuid)
                except LupusCloudError as err:
                    _LOGGER.debug("History for %s failed: %s", uuid, err)
                    data.history[uuid] = previous.get(uuid, [])
                    return
            device = data.devices[uuid]
            self._history_fetched[uuid] = (
                (device.get("lastModifiedDate"), device.get("lastHeartbeatDate")),
                now,
            )

        await asyncio.gather(*(load(u) for u in todo))

    @callback
    def _fire_events(self, data: LupusData) -> None:
        # Alarm transitions
        alarms = {u for u, d in data.devices.items() if self.device_in_alarm(d)}
        alarms |= data.alarm_uuids
        for uuid in alarms - self._previous_alarms:
            device = data.devices.get(uuid, {})
            self.hass.bus.async_fire(
                EVENT_ALARM,
                {
                    "device_uuid": uuid,
                    "device_id": device.get("deviceId"),
                    "status": device.get("status"),
                    "status_reason": device.get("statusReason"),
                    "location": device.get("nodeName"),
                    "source": "poll",
                },
            )
        self._previous_alarms = alarms

        # New notifications
        uuids = {n.get("uuid") for n in data.notifications}
        if self._seen_notifications is None:
            self._seen_notifications = uuids
            return
        now_ms = dt_util.utcnow().timestamp() * 1000
        for notification in reversed(data.notifications):
            if notification.get("uuid") in self._seen_notifications:
                continue
            event_type = str(notification.get("eventType") or "")
            self.hass.bus.async_fire(
                EVENT_NOTIFICATION,
                {
                    "uuid": notification.get("uuid"),
                    "event_type": event_type,
                    "message": notification.get("detailedMessage"),
                    "device_uuid": notification.get("resourceUuid"),
                    "created": notification.get("createdDate"),
                    "alarm": event_type in self.alarm_events,
                },
            )
            device_uuid = notification.get("resourceUuid")
            if (
                event_type in self.alarm_events
                and device_uuid
                and now_ms - (notification.get("createdDate") or 0)
                < WEBHOOK_ALARM_HOLD.total_seconds() * 1000
            ):
                self.webhook_alarms[device_uuid] = dt_util.utcnow()
        self._seen_notifications |= uuids

    # ------------------------------------------------------------------ webhook

    @callback
    def handle_webhook(self, payload: Any) -> None:
        """Process a push message from the Lupus Cloud.

        Format sent by the Lupus Cloud:
        {"event": "FIRE_ALARM_DEVICE", "deviceId": "...", "resourceUuid": "<device uuid>",
         "resourceType": "DEVICE", "room": "...", "parentUuids": "<building>/...",
         "message": {"type": "...", "timestamp": ..., "data": {"status": "ERROR",
                     "status_reason": "ALARM", "location": {...}}}}
        """
        now = dt_util.utcnow()
        clean = sanitize_payload(payload)
        self.last_webhook = clean
        self.last_webhook_time = now

        event_type, status_reason = "", ""
        matched: set[str] = set()
        buildings: set[str] = set()
        if isinstance(payload, dict):
            message = payload.get("message")
            message = message if isinstance(message, dict) else {}
            msg_data = message.get("data")
            msg_data = msg_data if isinstance(msg_data, dict) else {}
            event_type = str(payload.get("event") or message.get("type") or "").upper()
            status_reason = str(msg_data.get("status_reason") or "").upper()
            matched = self._match_devices(payload)
            # Devices of rooms that are not included are ignored completely
            known_elsewhere = bool(self.data) and (
                str(payload.get("resourceUuid") or "") in self.data.all_devices
            )
            if self.data and not matched and not known_elsewhere:
                buildings = {
                    part
                    for part in str(payload.get("parentUuids") or "").split("/")
                    if part in self.data.buildings
                }

        is_clear = event_type.endswith("_ACKNOWLEDGED") or status_reason in ("OK", "ACKNOWLEDGED")
        is_alarm = not is_clear and self.event_is_alarm(event_type, status_reason)

        if is_alarm:
            # Alarms of unknown devices (e.g. the "Testalarm" of the web app)
            # are attributed to the building listed in parentUuids.
            for uuid in matched or buildings:
                self.webhook_alarms[uuid] = now
        elif is_clear:
            for uuid in matched | buildings:
                self.webhook_alarms.pop(uuid, None)

        self.hass.bus.async_fire(
            EVENT_WEBHOOK,
            {
                "event_type": event_type or None,
                "status_reason": status_reason or None,
                "device_uuids": sorted(matched),
                "alarm": is_alarm,
                "payload": clean,
            },
        )
        if is_alarm:
            for uuid in matched:
                device = self.data.devices.get(uuid, {}) if self.data else {}
                self.hass.bus.async_fire(
                    EVENT_ALARM,
                    {
                        "device_uuid": uuid,
                        "device_id": device.get("deviceId"),
                        "event_type": event_type or None,
                        "status_reason": status_reason or None,
                        "location": device.get("nodeName"),
                        "source": "webhook",
                    },
                )
                self._previous_alarms.add(uuid)

        # Update entities right away, then fetch fresh data
        self.async_update_listeners()
        self.hass.async_create_task(self.async_request_refresh())
        for cancel in self._pending_refreshes:
            cancel()
        self._pending_refreshes = [
            async_call_later(self.hass, delay, self._delayed_refresh) for delay in (15, 45)
        ]
        # Re-evaluate entity states once the webhook alarm hold expires
        self._pending_refreshes.append(
            async_call_later(self.hass, WEBHOOK_ALARM_HOLD.total_seconds() + 1, self._hold_expired)
        )

    async def _delayed_refresh(self, _now: datetime) -> None:
        await self.async_refresh()

    @callback
    def _hold_expired(self, _now: datetime) -> None:
        self.async_update_listeners()

    def _match_devices(self, payload: dict[str, Any]) -> set[str]:
        """Find the device a webhook refers to (resourceUuid / deviceId)."""
        if not self.data:
            return set()
        resource = str(payload.get("resourceUuid") or "")
        if resource in self.data.devices:
            return {resource}
        device_id = str(payload.get("deviceId") or "")
        return {
            uuid
            for uuid, device in self.data.devices.items()
            if device_id and str(device.get("deviceId")) == device_id
        }

    async def async_shutdown(self) -> None:
        """Cancel pending timers."""
        for cancel in self._pending_refreshes:
            cancel()
        self._pending_refreshes.clear()
        await super().async_shutdown()
