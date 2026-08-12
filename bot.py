"""NoneBot application entry point. Wires the long-lived Responses client lifecycle."""

from __future__ import annotations

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

from app.runtime import get_client

nonebot.init()
driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)


@driver.on_startup
async def _start_responses_client() -> None:
    await get_client().start()


@driver.on_shutdown
async def _stop_responses_client() -> None:
    await get_client().close()


nonebot.load_plugin("plugins.auto_reply")
nonebot.load_plugin("plugins.status_notify")


if __name__ == "__main__":
    nonebot.run()
