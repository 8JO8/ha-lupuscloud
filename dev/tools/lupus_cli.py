"""Small dev helper: call the Lupus Cloud API with the credentials of the
configured integration (runs inside the dev HA container).

    docker exec -w /config ha-lupuscloud-dev python /config/tools/lupus_cli.py webhooks
    docker exec -w /config ha-lupuscloud-dev python /config/tools/lupus_cli.py test-webhook
"""

import asyncio
import json
import sys

import aiohttp

sys.path.insert(0, "/config")
from custom_components.lupuscloud.api import LupusCloudApi  # noqa: E402


def _entry() -> dict:
    with open("/config/.storage/core.config_entries", encoding="utf-8") as file:
        entries = json.load(file)["data"]["entries"]
    return next(e for e in entries if e["domain"] == "lupuscloud")


async def main(cmd: str) -> None:
    entry = _entry()
    async with aiohttp.ClientSession() as session:
        api = LupusCloudApi(
            session,
            entry["data"]["username"],
            entry["data"].get("password"),
            entry["data"].get("refresh_token"),
        )
        tree = await api.get_location_tree()
        buildings = [b["uuid"] for items in tree.values() for b in items]
        if cmd == "webhooks":
            for uuid in buildings:
                raw = await api.request("GET", f"service/webhook/{uuid}")
                for key in ("bearer", "password"):
                    for hook in raw.get("webhooks", []) if isinstance(raw, dict) else []:
                        if hook.get(key):
                            hook[key] = "***"
                print(json.dumps(raw, indent=2))
        elif cmd == "test-webhook":
            for uuid in buildings:
                for hook in await api.get_webhooks(uuid):
                    if entry["data"]["webhook_id"] in hook.get("url", ""):
                        print("test", hook["uuid"], await api.test_webhook(hook["uuid"]))


asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "webhooks"))
