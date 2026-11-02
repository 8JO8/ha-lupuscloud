"""Sensors for Lupus Cloud."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfElectricPotential, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import LupusConfigEntry
from .coordinator import LupusCloudCoordinator
from .entity import LupusAccountEntity, LupusDeviceEntity

BATTERY_STATES = {
    "ACTIVE": "ok",
    "WARNING": "critical",
    "ERROR": "empty",
    "NOT_AVAILABLE": "not_available",
}
SIGNAL_LEVELS = {
    "EXCELLENT": "excellent",
    "GOOD": "good",
    "WEAK": "weak",
    "UNKNOWN": "unknown",
}
DEVICE_STATUS = [
    "active",
    "warning",
    "error",
    "alarm",
    "muted",
    "decommissioned",
    "not_available",
    "other",
]
STATUS_REASONS = {
    "OK": "OK",
    "ACKNOWLEDGED": "Bestätigt",
    "ALARM": "Rauchalarm",
    "DECOMMISSIONED": "Stillgelegt",
    "ENVIRONMENT_OBSTACLES": "Umfelderkennung",
    "ERROR": "Fehler",
    "HIGH_TEMPERATURE_ALARM": "Temperaturalarm (hoch)",
    "HUMIDITY_ALARM": "Feuchtigkeitsalarm",
    "LOW_TEMPERATURE_ALARM": "Temperaturalarm (niedrig)",
    "MALFUNCTION": "Fehlfunktion",
    "MOTION_ALARM": "Bewegungsalarm",
    "MUTED": "Stummgeschaltet",
    "NOT_AVAILABLE": "N/A",
    "NOT_REPORTING": "Fehler Netzqualität",
    "PANIC_ALARM": "Panikalarm",
    "REPLACE": "Falls erforderlich ersetzen",
    "SMOKE_OPENINGS": "Raucheintrittsöffnung",
    "TAMPERED": "Demontage",
    "TEMPERATURE_ALARM": "Temperaturalarm",
    "WARNING": "Warnung",
}


def _ms_to_dt(value: Any) -> datetime | None:
    if not value:
        return None
    value = float(value)
    if value > 1e14:  # some endpoints return microseconds
        value /= 1000
    return dt_util.utc_from_timestamp(value / 1000)


def _temperature(device: dict[str, Any]) -> float | None:
    condition = device.get("condition") or {}
    value = condition.get("temperature", device.get("temperature"))
    return float(value) if value is not None else None


def _status(device: dict[str, Any]) -> str:
    status = str(device.get("status") or "").lower()
    if status in DEVICE_STATUS:
        return status
    if "alarm" in status:
        return "alarm"
    return "other"


@dataclass(frozen=True, kw_only=True)
class LupusSensorDescription(SensorEntityDescription):
    """Describes a device sensor."""

    value_fn: Callable[[dict[str, Any]], Any]
    exists_fn: Callable[[dict[str, Any]], bool] = lambda _: True


DEVICE_SENSORS: tuple[LupusSensorDescription, ...] = (
    LupusSensorDescription(
        key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=_temperature,
        exists_fn=lambda d: _temperature(d) is not None,
    ),
    LupusSensorDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("batteryLevel"),
        exists_fn=lambda d: d.get("batteryLevel") is not None,
    ),
    LupusSensorDescription(
        key="battery_state",
        translation_key="battery_state",
        device_class=SensorDeviceClass.ENUM,
        options=list(dict.fromkeys(BATTERY_STATES.values())),
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: BATTERY_STATES.get(
            str(d.get("batteryState") or "NOT_AVAILABLE").upper(), "not_available"
        ),
    ),
    LupusSensorDescription(
        key="signal_level",
        translation_key="signal_level",
        device_class=SensorDeviceClass.ENUM,
        options=list(SIGNAL_LEVELS.values()),
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: SIGNAL_LEVELS.get(
            str(d.get("signalLevel") or "UNKNOWN").upper(), "unknown"
        ),
    ),
    LupusSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=DEVICE_STATUS,
        value_fn=_status,
    ),
    LupusSensorDescription(
        key="status_reason",
        translation_key="status_reason",
        value_fn=lambda d: STATUS_REASONS.get(
            str(d.get("statusReason") or ""), d.get("statusReason")
        ),
    ),
    LupusSensorDescription(
        key="last_heartbeat",
        translation_key="last_heartbeat",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _ms_to_dt(d.get("lastHeartbeatDate")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LupusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    async_add_entities(
        [
            LupusUnreadNotifications(coordinator),
            LupusLastNotification(coordinator),
            LupusWebhookStatus(coordinator),
        ]
    )

    @callback
    def _add_new() -> None:
        entities: list[SensorEntity] = []
        for uuid, device in coordinator.data.devices.items():
            if uuid in known:
                continue
            known.add(uuid)
            entities.extend(
                LupusDeviceSensor(coordinator, uuid, description)
                for description in DEVICE_SENSORS
                if description.exists_fn(device)
            )
            entities.append(LupusLastEvent(coordinator, uuid))
        if entities:
            async_add_entities(entities)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class LupusDeviceSensor(LupusDeviceEntity, SensorEntity):
    """Generic device sensor."""

    entity_description: LupusSensorDescription

    @property
    def native_value(self) -> Any:
        """Return the state."""
        return self.entity_description.value_fn(self.device)


class LupusLastEvent(LupusDeviceEntity, SensorEntity):
    """Latest message (event log entry) of a device."""

    def __init__(self, coordinator: LupusCloudCoordinator, device_uuid: str) -> None:
        """Initialize."""
        super().__init__(
            coordinator,
            device_uuid,
            SensorEntityDescription(key="last_event", translation_key="last_event"),
        )

    def _events(self) -> list[dict[str, Any]]:
        return self.coordinator.data.history.get(self._uuid, [])

    @property
    def native_value(self) -> str | None:
        """Message of the latest event."""
        if not (events := self._events()):
            return None
        event = events[0]
        text = event.get("maintenanceStatus") or event.get("eventType") or ""
        return text[:255]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Details and the latest events."""
        events = self._events()
        if not events:
            return {}
        latest = events[0]
        return {
            "event_type": latest.get("eventType"),
            "status": latest.get("status"),
            "status_reason": latest.get("statusReason"),
            "timestamp": _ms_to_dt(latest.get("timestamp")),
            "temperature": latest.get("temperature"),
            "battery_voltage": latest.get("batteryLevel"),
            "recent_events": [
                {
                    "time": _ms_to_dt(e.get("timestamp")),
                    "event_type": e.get("eventType"),
                    "status_reason": e.get("statusReason"),
                    "message": e.get("maintenanceStatus"),
                }
                for e in events
            ],
        }


class LupusUnreadNotifications(LupusAccountEntity, SensorEntity):
    """Number of unread notifications."""

    def __init__(self, coordinator: LupusCloudCoordinator) -> None:
        """Initialize."""
        super().__init__(
            coordinator,
            SensorEntityDescription(
                key="unread_notifications",
                translation_key="unread_notifications",
                state_class=SensorStateClass.MEASUREMENT,
            ),
        )

    @property
    def native_value(self) -> int:
        """Return the count."""
        return self.coordinator.data.unread_notifications


class LupusLastNotification(LupusAccountEntity, SensorEntity):
    """Latest notification (Meldung) of the account."""

    def __init__(self, coordinator: LupusCloudCoordinator) -> None:
        """Initialize."""
        super().__init__(
            coordinator,
            SensorEntityDescription(key="last_notification", translation_key="last_notification"),
        )

    @property
    def native_value(self) -> str | None:
        """Latest notification text."""
        if not (items := self.coordinator.data.notifications):
            return None
        return (items[0].get("detailedMessage") or items[0].get("eventType") or "")[:255]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Details of the latest notifications."""
        items = self.coordinator.data.notifications
        if not items:
            return {}
        latest = items[0]
        return {
            "event_type": latest.get("eventType"),
            "created": _ms_to_dt(latest.get("createdDate")),
            "read": latest.get("read"),
            "device_uuid": latest.get("resourceUuid"),
            "recent": [
                {
                    "created": _ms_to_dt(n.get("createdDate")),
                    "event_type": n.get("eventType"),
                    "message": n.get("detailedMessage"),
                    "read": n.get("read"),
                }
                for n in items[:10]
            ],
        }


class LupusWebhookStatus(LupusAccountEntity, SensorEntity):
    """Status of the push channel (webhook)."""

    def __init__(self, coordinator: LupusCloudCoordinator) -> None:
        """Initialize."""
        super().__init__(
            coordinator,
            SensorEntityDescription(
                key="webhook_status",
                translation_key="webhook_status",
                device_class=SensorDeviceClass.ENUM,
                options=["registered", "disabled", "no_external_url", "error"],
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
        )

    @property
    def native_value(self) -> str:
        """Return the status."""
        return self.coordinator.webhook_status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Last received push message."""
        return {
            "last_received": self.coordinator.last_webhook_time,
            "last_payload": self.coordinator.last_webhook,
        }
