"""Binary sensors for Lupus Cloud (alarm, problem, battery, tamper)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LupusConfigEntry
from .const import ALARM_REASONS, HEAT_TYPES, MOTION_TYPES, SMOKE_TYPES
from .coordinator import LupusCloudCoordinator
from .entity import LupusBuildingEntity, LupusDeviceEntity

PROBLEM_STATUS = {"ERROR", "WARNING", "MALFUNCTION", "NOT_REPORTING", "REPLACE"}
OK_REASONS = {"OK", "ACKNOWLEDGED", "ACTIVE", "", "NONE"}


@dataclass(frozen=True, kw_only=True)
class LupusBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a device binary sensor."""

    value_fn: Callable[[dict[str, Any]], bool | None]
    exists_fn: Callable[[dict[str, Any]], bool] = lambda _: True


def _problem(device: dict[str, Any]) -> bool:
    status = str(device.get("status") or "").upper()
    reason = str(device.get("statusReason") or "").upper()
    if reason in ALARM_REASONS or status in ALARM_REASONS:
        return False
    return status in PROBLEM_STATUS or reason not in OK_REASONS


DEVICE_SENSORS: tuple[LupusBinarySensorDescription, ...] = (
    LupusBinarySensorDescription(
        key="problem",
        translation_key="problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=_problem,
    ),
    LupusBinarySensorDescription(
        key="battery_low",
        translation_key="battery_low",
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: str(d.get("batteryState") or "").upper() in ("WARNING", "ERROR"),
        exists_fn=lambda d: d.get("batteryState") not in (None, "NOT_AVAILABLE"),
    ),
    LupusBinarySensorDescription(
        key="tamper",
        translation_key="tamper",
        device_class=BinarySensorDeviceClass.TAMPER,
        value_fn=lambda d: str(d.get("statusReason") or "").upper() == "TAMPERED",
    ),
)


def _alarm_class(device_type: str) -> BinarySensorDeviceClass:
    if device_type in SMOKE_TYPES:
        return BinarySensorDeviceClass.SMOKE
    if device_type in HEAT_TYPES:
        return BinarySensorDeviceClass.HEAT
    if device_type in MOTION_TYPES:
        return BinarySensorDeviceClass.MOTION
    return BinarySensorDeviceClass.SAFETY


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LupusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    coordinator = entry.runtime_data
    known_devices: set[str] = set()
    known_buildings: set[str] = set()

    @callback
    def _add_new() -> None:
        entities: list[BinarySensorEntity] = []
        for uuid in coordinator.data.buildings.keys() - known_buildings:
            known_buildings.add(uuid)
            entities.append(LupusBuildingAlarm(coordinator, uuid))
        for uuid, device in coordinator.data.devices.items():
            if uuid in known_devices:
                continue
            known_devices.add(uuid)
            entities.append(LupusAlarmSensor(coordinator, uuid))
            entities.extend(
                LupusDeviceBinarySensor(coordinator, uuid, description)
                for description in DEVICE_SENSORS
                if description.exists_fn(device)
            )
        if entities:
            async_add_entities(entities)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class LupusAlarmSensor(LupusDeviceEntity, BinarySensorEntity):
    """Alarm state (smoke, heat, motion, ...) of a device - push via webhook."""

    def __init__(self, coordinator: LupusCloudCoordinator, device_uuid: str) -> None:
        """Initialize."""
        device_type = coordinator.data.devices[device_uuid].get("deviceType", "")
        super().__init__(
            coordinator,
            device_uuid,
            BinarySensorEntityDescription(
                key="alarm",
                translation_key="alarm",
                device_class=_alarm_class(device_type),
            ),
        )

    @property
    def is_on(self) -> bool:
        """Return true if the device is in alarm."""
        return self.coordinator.is_alarm(self._uuid)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Raw status information."""
        device = self.device
        return {
            "status": device.get("status"),
            "status_reason": device.get("statusReason"),
            "location": device.get("nodeName"),
            "device_id": device.get("deviceId"),
        }


class LupusDeviceBinarySensor(LupusDeviceEntity, BinarySensorEntity):
    """Generic device binary sensor."""

    entity_description: LupusBinarySensorDescription

    @property
    def is_on(self) -> bool | None:
        """Return the state."""
        return self.entity_description.value_fn(self.device)


class LupusBuildingAlarm(LupusBuildingEntity, BinarySensorEntity):
    """On if any device of the building is in alarm."""

    def __init__(self, coordinator: LupusCloudCoordinator, building_uuid: str) -> None:
        """Initialize."""
        super().__init__(
            coordinator,
            building_uuid,
            BinarySensorEntityDescription(
                key="alarm",
                translation_key="building_alarm",
                device_class=BinarySensorDeviceClass.SAFETY,
            ),
        )

    def _alarm_devices(self) -> list[dict[str, Any]]:
        return [
            device
            for uuid, device in self.coordinator.data.devices.items()
            if device.get("buildingUuid") == self._building_uuid and self.coordinator.is_alarm(uuid)
        ]

    @property
    def is_on(self) -> bool:
        """Return true if a device (or the building itself, e.g. test alarm) is in alarm."""
        return bool(self._alarm_devices()) or self.coordinator.is_alarm(self._building_uuid)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """List the alarming devices."""
        return {
            "devices": [
                {
                    "name": self.coordinator.device_name(d),
                    "location": d.get("nodeName"),
                    "status_reason": d.get("statusReason"),
                }
                for d in self._alarm_devices()
            ]
        }
