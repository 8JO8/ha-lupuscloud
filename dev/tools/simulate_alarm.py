"""Dev helper: POST a webhook payload in the exact Lupus format for a real
device to the local HA webhook (runs inside the dev HA container).

    docker exec -w /config ha-lupuscloud-dev python /config/tools/simulate_alarm.py <device_uuid> [EVENT]
"""

import asyncio
import json
import sys
import time

import aiohttp


async def main(device_uuid: str, event: str) -> None:
    with open("/config/.storage/core.config_entries", encoding="utf-8") as file:
        entry = next(e for e in json.load(file)["data"]["entries"] if e["domain"] == "lupuscloud")
    ack = event.endswith("_ACKNOWLEDGED")
    payload = {
        "event": event,
        "deviceId": "simulated",
        "resourceType": "DEVICE",
        "resourceUuid": device_uuid,
        "message": {
            "type": event,
            "timestamp": int(time.time() * 1000),
            "data": {
                "status": "ACTIVE" if ack else "ERROR",
                "status_reason": "ACKNOWLEDGED" if ack else "ALARM",
            },
        },
    }
    async with (
        aiohttp.ClientSession() as session,
        session.post(
            f"http://localhost:8123/api/webhook/{entry['data']['webhook_id']}",
            json=payload,
            headers={"Authorization": f"Bearer {entry['data']['webhook_secret']}"},
        ) as resp,
    ):
        print("HTTP", resp.status)


asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "FIRE_ALARM_DEVICE"))
