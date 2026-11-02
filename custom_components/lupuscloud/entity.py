"""Base entities for Lupus Cloud."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ACCOUNT_UUID, DEVICE_TYPE_NAMES, DOMAIN, MANUFACTURER
from .coordinator import LupusCloudCoordinator

VIA_DEVICE_ID_SUPPORTED = "via_device_id" in DeviceInfo.__annotations__


class LupusDeviceEntity(CoordinatorEntity[LupusCloudCoordinator]):
    """Entity belonging to a Lupus device (smoke detector, ...)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LupusCloudCoordinator,
        device_uuid: str,
        description: EntityDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self.entity_description = description
        self._uuid = device_uuid
        self._attr_unique_id = f"{device_uuid}_{description.key}"
        device = self.device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_uuid)},
            name=coordinator.device_name(device),
            manufacturer=MANUFACTURER,
            model=DEVICE_TYPE_NAMES.get(device.get("deviceType", ""), device.get("deviceType")),
            model_id=device.get("deviceType"),
            serial_number=device.get("deviceId"),
            sw_version=device.get("firmwareVersion"),
        )
        building_device_id = coordinator.building_device_ids.get(device["buildingUuid"])
        if VIA_DEVICE_ID_SUPPORTED and building_device_id:
            self._attr_device_info["via_device_id"] = building_device_id
        else:  # Home Assistant < 2026.8
            self._attr_device_info["via_device"] = (DOMAIN, device["buildingUuid"])

    @property
    def device(self) -> dict[str, Any]:
        """Current raw device data."""
        return self.coordinator.data.devices.get(self._uuid, {})

    @property
    def available(self) -> bool:
        """Device must still exist in the cloud."""
        return super().available and self._uuid in self.coordinator.data.devices


class LupusBuildingEntity(CoordinatorEntity[LupusCloudCoordinator]):
    """Entity belonging to a building."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LupusCloudCoordinator,
        building_uuid: str,
        description: EntityDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self.entity_description = description
        self._building_uuid = building_uuid
        self._attr_unique_id = f"{building_uuid}_{description.key}"
        self._attr_device_info = building_device_info(coordinator, building_uuid)

    @property
    def available(self) -> bool:
        """Building must still exist."""
        return super().available and self._building_uuid in self.coordinator.data.buildings


def building_device_info(coordinator: LupusCloudCoordinator, building_uuid: str) -> DeviceInfo:
    """Device info for a building."""
    building = coordinator.data.buildings.get(building_uuid, {})
    address = building.get("address") or {}
    street = " ".join(filter(None, [address.get("streetAddress"), address.get("number")]))
    return DeviceInfo(
        identifiers={(DOMAIN, building_uuid)},
        name=building.get("name") or street or "Gebäude",
        manufacturer=MANUFACTURER,
        model="Gebäude",
        configuration_url="https://lupuscloud.com/dashboard/private",
    )


class LupusAccountEntity(CoordinatorEntity[LupusCloudCoordinator]):
    """Entity belonging to the cloud account."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: LupusCloudCoordinator, description: EntityDescription) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self.entity_description = description
        account = coordinator.config_entry.data[CONF_ACCOUNT_UUID]
        self._attr_unique_id = f"{account}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, account)},
            name="Lupus Cloud",
            manufacturer=MANUFACTURER,
            model="Cloud-Konto",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://lupuscloud.com",
        )
