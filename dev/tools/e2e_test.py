"""End-to-end checks against the dev HA instance (runs inside the container).

    docker exec -e HA_TOKEN=<access token> -w /config ha-lupuscloud-dev \
        python /config/tools/e2e_test.py

Requires a configured Lupus Cloud entry. Uses only the local webhook, nothing
is sent to the Lupus Cloud except normal API polling.
"""

import asyncio
import json
import os
import time

import aiohttp

BASE = "http://localhost:8123"
HEADERS = {"Authorization": f"Bearer {os.environ['HA_TOKEN']}"}
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, info: str = "") -> None:
    RESULTS.append((name, ok, info))
    print(("PASS " if ok else "FAIL ") + name + (f" ({info})" if info else ""))


def entry_data() -> dict:
    with open("/config/.storage/core.config_entries", encoding="utf-8") as file:
        return next(e for e in json.load(file)["data"]["entries"] if e["domain"] == "lupuscloud")


async def state(session: aiohttp.ClientSession, entity_id: str) -> str:
    async with session.get(f"{BASE}/api/states/{entity_id}", headers=HEADERS) as resp:
        return (await resp.json())["state"]


async def set_alarm_events(
    session: aiohttp.ClientSession,
    events: list[str],
    drop_room: str | None = None,
    add_areas: dict[str, str] | None = None,
) -> list[str]:
    entry = entry_data()
    async with session.post(
        f"{BASE}/api/config/config_entries/options/flow",
        headers=HEADERS,
        json={"handler": entry["entry_id"]},
    ) as resp:
        flow = await resp.json()
    opts = entry["options"]
    async with session.post(
        f"{BASE}/api/config/config_entries/options/flow/{flow['flow_id']}",
        headers=HEADERS,
        json={
            "alarm_events": events,
            "scan_interval": opts.get("scan_interval", 5),
            "webhook_enabled": opts.get("webhook_enabled", True),
        },
    ) as resp:
        step = await resp.json()
    room_keys = [field["name"] for field in step.get("data_schema", [])]
    keep = {
        field["name"]: field["description"]["suggested_value"]
        for field in step.get("data_schema", [])
        if (field.get("description") or {}).get("suggested_value")
        and not (drop_room and drop_room in field["name"])
    }
    keep.update(add_areas or {})
    async with session.post(
        f"{BASE}/api/config/config_entries/options/flow/{flow['flow_id']}",
        headers=HEADERS,
        json=keep,
    ) as resp:
        await resp.json()
    await asyncio.sleep(2)
    for _ in range(90):  # wait for the reload (the cloud API can be slow)
        async with session.get(f"{BASE}/api/config/config_entries/entry", headers=HEADERS) as resp:
            entries = await resp.json()
        if next(e for e in entries if e["domain"] == "lupuscloud")["state"] == "loaded":
            break
        await asyncio.sleep(1)
    return room_keys


async def post_webhook(
    session: aiohttp.ClientSession, payload: object, token: str | None, raw: bytes | None = None
) -> int:
    entry = entry_data()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with session.post(
        f"{BASE}/api/webhook/{entry['data']['webhook_id']}",
        data=raw if raw is not None else json.dumps(payload),
        headers={**headers, "Content-Type": "application/json"},
    ) as resp:
        return resp.status


def before_area(devices: dict[str, str | None]) -> str:
    """Area of the Arbeitszimmer device before the test."""
    return next(area for name, area in devices.items() if "Arbeitszimmer" in name) or ""


def fire_payload(device_uuid: str, event: str = "FIRE_ALARM_DEVICE") -> dict:
    ack = event.endswith("_ACKNOWLEDGED")
    return {
        "event": event,
        "resourceType": "DEVICE",
        "resourceUuid": device_uuid,
        "message": {
            "type": event,
            "timestamp": int(time.time() * 1000),
            "data": {"status_reason": "ACKNOWLEDGED" if ack else "ALARM"},
        },
    }


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE}/api/states", headers=HEADERS) as resp:
            states = await resp.json()
        lupus = [s for s in states if "rauchwarnmelder" in s["entity_id"]]
        check("entities exist", len(lupus) > 10, f"{len(lupus)} entities")
        alarm_entities = [s["entity_id"] for s in lupus if s["entity_id"].endswith("_alarm")]
        target_entity = alarm_entities[0]
        # find device uuid of the first detector via entity registry file
        with open("/config/.storage/core.entity_registry", encoding="utf-8") as file:
            reg = json.load(file)["data"]["entities"]
        uid = next(e["unique_id"] for e in reg if e["entity_id"] == target_entity)
        device_uuid = uid.rsplit("_", 1)[0]

        secret = entry_data()["data"]["webhook_secret"]

        # --- security
        check("webhook without token -> 401", await post_webhook(session, {}, None) == 401)
        check("webhook wrong token -> 401", await post_webhook(session, {}, "x" * 43) == 401)
        big = b'{"a":"' + b"x" * (70 * 1024) + b'"}'
        check("oversized body -> 413", await post_webhook(session, None, secret, big) == 413)

        # --- alarm event selection: without fire alarm
        rooms = await set_alarm_events(session, ["heat_alarm_device", "panic_alarm_device"])
        # rooms with devices plus rooms that are already mapped
        check("area step lists rooms with devices", len(rooms) >= 3, ", ".join(rooms))
        status = await post_webhook(session, fire_payload(device_uuid), secret)
        await asyncio.sleep(1)
        st = await state(session, target_entity)
        check("fire alarm ignored when not selected", status == 200 and st == "off", st)

        # --- with fire alarm selected
        await set_alarm_events(session, ["fire_alarm_device", "heat_alarm_device"])
        await post_webhook(session, fire_payload(device_uuid), secret)
        await asyncio.sleep(1)
        st = await state(session, target_entity)
        check("fire alarm triggers when selected", st == "on", st)
        await post_webhook(session, fire_payload(device_uuid, "SMOKE_ALARM_ACKNOWLEDGED"), secret)
        await asyncio.sleep(1)
        st = await state(session, target_entity)
        check("acknowledge clears alarm", st == "off", st)

        # --- reconfigure flow is offered
        async with session.post(
            f"{BASE}/api/config/config_entries/flow",
            headers=HEADERS,
            json={
                "handler": "lupuscloud",
                "entry_id": entry_data()["entry_id"],
                "source": "reconfigure",
            },
        ) as resp:
            flow = await resp.json()
        check(
            "reconfigure (change password) form",
            flow.get("step_id") == "reconfigure",
            str(flow.get("step_id") or flow),
        )
        if flow.get("flow_id"):
            await session.delete(
                f"{BASE}/api/config/config_entries/flow/{flow['flow_id']}", headers=HEADERS
            )

        # --- unassigned room -> device removed, re-assigned -> device back
        async def lupus_devices() -> dict[str, str | None]:
            # live registry via websocket (the .storage file is written delayed)
            async with session.ws_connect(f"{BASE}/api/websocket") as ws:
                await ws.receive_json()
                await ws.send_json({"type": "auth", "access_token": os.environ["HA_TOKEN"]})
                await ws.receive_json()
                await ws.send_json({"id": 1, "type": "config/device_registry/list"})
                devs = (await ws.receive_json())["result"]
            return {
                d["name"]: d["area_id"]
                for d in devs
                if any(i[0] == "lupuscloud" for i in d["identifiers"])
            }

        before = await lupus_devices()
        room_key = next(k for k in rooms if "Arbeitszimmer" in k)
        await set_alarm_events(session, ["fire_alarm_device"], drop_room="Arbeitszimmer")
        await asyncio.sleep(2)
        after = await lupus_devices()
        removed = set(before) - set(after)
        check(
            "unassigned room removes its device",
            any("Arbeitszimmer" in name for name in removed) and len(after) == len(before) - 1,
            ", ".join(sorted(removed)) or "nothing removed",
        )
        await set_alarm_events(
            session, ["fire_alarm_device"], add_areas={room_key: before_area(before)}
        )
        await asyncio.sleep(2)
        restored = await lupus_devices()
        check("re-assigned room adds device again", restored == before, str(restored))

        # restore defaults
        await set_alarm_events(
            session,
            [
                "fire_alarm_device",
                "heat_alarm_device",
                "high_temperature_alarm_device",
                "low_temperature_alarm_device",
                "rising_temperature_alarm_device",
                "motion_alarm_device",
                "panic_alarm_device",
                "other_alarm_device",
            ],
        )

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")


asyncio.run(main())
