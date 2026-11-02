"""Constants for the Lupus Cloud integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "lupuscloud"
MANUFACTURER: Final = "Lupus Electronics"

# Values taken from the lupuscloud.com web app (environment config).
COGNITO_REGION: Final = "eu-central-1"
COGNITO_POOL_ID: Final = "eu-central-1_QObZU1E4n"
COGNITO_CLIENT_ID: Final = "3ip8eelf4l2fatq7ficlbk5spq"
COGNITO_URL: Final = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/"
API_BASE_URL: Final = "https://api.lupusiot.de/api/v1"

CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_ACCOUNT_UUID: Final = "account_uuid"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_WEBHOOK_ENABLED: Final = "webhook_enabled"
CONF_WEBHOOK_URL_OVERRIDE: Final = "webhook_url_override"
CONF_WEBHOOK_ID: Final = "webhook_id"
CONF_WEBHOOK_SECRET: Final = "webhook_secret"
CONF_CLOUDHOOK_URL: Final = "cloudhook_url"
CONF_AREAS: Final = "areas"
# Rooms that were already offered in the area step (to detect new ones)
CONF_KNOWN_ROOMS: Final = "known_rooms"

DEFAULT_SCAN_INTERVAL: Final = 5  # minutes
MIN_SCAN_INTERVAL: Final = 1
MAX_SCAN_INTERVAL: Final = 24 * 60
HISTORY_INTERVAL: Final = timedelta(minutes=15)

# How long an alarm reported by a webhook is held "on" even if the
# REST API does not (yet) report the device in alarm.
WEBHOOK_ALARM_HOLD: Final = timedelta(minutes=2)

EVENT_WEBHOOK: Final = f"{DOMAIN}_webhook"
EVENT_NOTIFICATION: Final = f"{DOMAIN}_notification"
EVENT_ALARM: Final = f"{DOMAIN}_alarm"

# Selectable alarm events (Lupus notification / webhook event types) and the
# device status / statusReason values that belong to them. The user chooses
# in the options which of these turn the alarm sensors on.
ALARM_EVENT_REASONS: Final[dict[str, frozenset[str]]] = {
    "FIRE_ALARM_DEVICE": frozenset({"ALARM", "FIRE_ALARM", "SMOKE_ALARM"}),
    "HEAT_ALARM_DEVICE": frozenset({"HEAT_ALARM"}),
    "HIGH_TEMPERATURE_ALARM_DEVICE": frozenset({"HIGH_TEMPERATURE_ALARM", "TEMPERATURE_ALARM"}),
    "LOW_TEMPERATURE_ALARM_DEVICE": frozenset({"LOW_TEMPERATURE_ALARM"}),
    "RISING_TEMPERATURE_ALARM_DEVICE": frozenset({"RISING_TEMPERATURE_ALARM"}),
    "MOTION_ALARM_DEVICE": frozenset({"MOTION_ALARM"}),
    "PANIC_ALARM_DEVICE": frozenset({"PANIC_ALARM"}),
    "OTHER_ALARM_DEVICE": frozenset({"GAS_ALARM", "HUMIDITY_ALARM", "MEDICAL_ALARM"}),
    "ALERT_DEVICE": frozenset({"TAMPERED"}),
    "WARNING_DEVICE": frozenset(
        {"WARNING", "MALFUNCTION", "SMOKE_OPENINGS", "ENVIRONMENT_OBSTACLES", "REPLACE"}
    ),
}
DEFAULT_ALARM_EVENTS: Final = [
    "FIRE_ALARM_DEVICE",
    "HEAT_ALARM_DEVICE",
    "HIGH_TEMPERATURE_ALARM_DEVICE",
    "LOW_TEMPERATURE_ALARM_DEVICE",
    "RISING_TEMPERATURE_ALARM_DEVICE",
    "MOTION_ALARM_DEVICE",
    "PANIC_ALARM_DEVICE",
    "OTHER_ALARM_DEVICE",
]
# All status values that mean "alarm" regardless of the selection
# (used to not report them as "problem")
ALARM_REASONS: Final = frozenset().union(
    *(ALARM_EVENT_REASONS[event] for event in DEFAULT_ALARM_EVENTS)
)

CONF_ALARM_EVENTS: Final = "alarm_events"

# Upper bound for webhook request bodies
MAX_WEBHOOK_BODY: Final = 64 * 1024

DEVICE_TYPE_NAMES: Final = {
    "RWM": "Rauchwarnmelder",
    "SMOKE_DETECTOR": "Rauchmelder",
    "HEAT_DETECTOR": "Hitzemelder",
    "MOTION_DETECTOR": "Bewegungsmelder",
    "ALARM_BUTTON": "Alarmbutton",
    "ALARM_BUTTON_AND_PANIC_BUTTON": "Alarmbutton/Notruftaster",
    "PANIC_BUTTON": "Notruftaster",
    "SIREN": "Sirene",
    "SWAN_GATEWAY": "Swan Gateway",
    "TEMPERATURE_HUMIDITY_SENSOR": "Temperatur-/Feuchtigkeitssensor",
    "HEAT_COST_ALLOCATOR": "Heizkostenverteiler",
    "HEAT_METER": "Wärmemengenzähler",
    "WATER_METER_COLD": "Wasserzähler (kalt)",
    "WATER_METER_HOT": "Wasserzähler (warm)",
    "POWER_METER": "Stromzähler",
    "GAS_METER": "Gaszähler",
    "OMS": "OMS-Gerät",
    "MIOTY": "MIOTY-Gerät",
    "NBIOT": "NB-IoT-Gerät",
}

SMOKE_TYPES: Final = {"RWM", "SMOKE_DETECTOR"}
HEAT_TYPES: Final = {"HEAT_DETECTOR"}
MOTION_TYPES: Final = {"MOTION_DETECTOR"}
