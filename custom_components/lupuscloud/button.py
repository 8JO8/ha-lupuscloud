"""Buttons for Lupus Cloud (acknowledge alarm / device)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LupusConfigEntry
from .api import LupusCloudApi, LupusCloudError
from .entity import LupusDeviceEntity


@dataclass(frozen=True, kw_only=True)
class LupusButtonDescription(ButtonEntityDescription):
    """Describes a button."""

    press_fn: Callable[[LupusCloudApi, str], Awaitable[Any]]


BUTTONS: tuple[LupusButtonDescription, ...] = (
    LupusButtonDescription(
        key="acknowledge_alarm",
        translation_key="acknowledge_alarm",
        press_fn=lambda api, uuid: api.acknowledge_alarm(uuid),
    ),
    LupusButtonDescription(
        key="acknowledge",
        translation_key="acknowledge",
        entity_registry_enabled_default=False,
        press_fn=lambda api, uuid: api.acknowledge_device(uuid),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LupusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up buttons."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = coordinator.data.devices.keys() - known
        known.update(new)
        if new:
            async_add_entities(
                LupusButton(coordinator, uuid, description)
                for uuid in new
                for description in BUTTONS
            )

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class LupusButton(LupusDeviceEntity, ButtonEntity):
    """Acknowledge button."""

    entity_description: LupusButtonDescription

    async def async_press(self) -> None:
        """Send the acknowledgement."""
        try:
            await self.entity_description.press_fn(self.coordinator.api, self._uuid)
        except LupusCloudError as err:
            raise HomeAssistantError(str(err)) from err
        self.coordinator.webhook_alarms.pop(self._uuid, None)
        await self.coordinator.async_request_refresh()
