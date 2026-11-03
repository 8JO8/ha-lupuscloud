"""Check that manual area changes survive restarts (runs inside the dev container).

    docker exec -e HA_TOKEN=<token> -w /config ha-lupuscloud-dev \
        python /config/tools/area_test.py <step>

Steps: ``show``, ``move <device name part> <area_id>``, ``remap <room name part> <area_id>``.
"""

import asyncio
import json
import os
import sys

import aiohttp

BASE = "http://localhost:8123"
TOKEN = os.environ["HA_TOKEN"]
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


async def ws_call(session: aiohttp.ClientSession, payload: dict) -> object:
    async with session.ws_connect(f"{BASE}/api/websocket") as ws:
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": TOKEN})
        await ws.receive_json()
        await ws.send_json({"id": 1, **payload})
        return (await ws.receive_json())["result"]


async def devices(session: aiohttp.ClientSession) -> list[dict]:
    result = await ws_call(session, {"type": "config/device_registry/list"})
    return [d for d in result if any(i[0] == "lupuscloud" for i in d["identifiers"])]


async def wait_loaded(session: aiohttp.ClientSession) -> None:
    for _ in range(90):
        async with session.get(f"{BASE}/api/config/config_entries/entry", headers=HEADERS) as resp:
            entries = await resp.json()
        if next(e for e in entries if e["domain"] == "lupuscloud")["state"] == "loaded":
            return
        await asyncio.sleep(1)


async def main(step: str, args: list[str]) -> None:
    async with aiohttp.ClientSession() as session:
        await wait_loaded(session)
        if step == "move":
            device = next(d for d in await devices(session) if args[0] in d["name"])
            await ws_call(
                session,
                {
                    "type": "config/device_registry/update",
                    "device_id": device["id"],
                    "area_id": args[1],
                },
            )
        elif step == "remap":
            with open("/config/.storage/core.config_entries", encoding="utf-8") as file:
                entry = next(
                    e for e in json.load(file)["data"]["entries"] if e["domain"] == "lupuscloud"
                )
            opts = entry["options"]
            async with session.post(
                f"{BASE}/api/config/config_entries/options/flow",
                headers=HEADERS,
                json={"handler": entry["entry_id"]},
            ) as resp:
                flow = await resp.json()
            async with session.post(
                f"{BASE}/api/config/config_entries/options/flow/{flow['flow_id']}",
                headers=HEADERS,
                json={
                    "alarm_events": opts["alarm_events"],
                    "scan_interval": opts["scan_interval"],
                    "webhook_enabled": opts["webhook_enabled"],
                },
            ) as resp:
                step_form = await resp.json()
            areas = {
                f["name"]: f["description"]["suggested_value"]
                for f in step_form["data_schema"]
                if (f.get("description") or {}).get("suggested_value")
            }
            key = next(f["name"] for f in step_form["data_schema"] if args[0] in f["name"])
            areas[key] = args[1]
            async with session.post(
                f"{BASE}/api/config/config_entries/options/flow/{flow['flow_id']}",
                headers=HEADERS,
                json=areas,
            ) as resp:
                await resp.json()
            await asyncio.sleep(2)
            await wait_loaded(session)
            await asyncio.sleep(1)
        for device in await devices(session):
            print(f"{device['name']}: {device['area_id']}")


asyncio.run(main(sys.argv[1], sys.argv[2:]))
