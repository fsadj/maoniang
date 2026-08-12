"""Send configured group notifications when the bot starts and stops.

The signal-handler machinery is genuine process state and stays module-global
(wrapping it in a service would be over-abstraction). Only the config plumbing
was repointed at app.config.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from types import FrameType
from typing import Any

from nonebot import get_driver
from nonebot.adapters.onebot.v11 import Bot

from app.config import config

logger = logging.getLogger(__name__)
driver = get_driver()

STATUS_GROUP_IDS = config.status_group_ids
ONLINE_MESSAGE = config.online_message
OFFLINE_MESSAGE = config.offline_message
SEND_TIMEOUT_SECONDS = 5.0

_connected_bots: dict[str, Bot] = {}
_online_announced: set[str] = set()
_offline_attempted = False
_previous_signal_handlers: dict[int, Any] = {}
_signal_handlers_installed = False
_shutdown_task: asyncio.Task[None] | None = None


async def _send_status_message(bot: Bot, message: str, label: str) -> int:
    """Send one status message to every configured group independently."""
    if not STATUS_GROUP_IDS or not message:
        return 0

    sent = 0
    for group_id in sorted(STATUS_GROUP_IDS):
        try:
            await asyncio.wait_for(
                bot.send_group_msg(group_id=group_id, message=message),
                timeout=SEND_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.warning(
                "Could not send %s notification to group %s (%s): %s",
                label, group_id, type(exc).__name__, exc,
            )
        else:
            sent += 1
    return sent


@driver.on_bot_connect
async def _handle_bot_connect(bot: Bot) -> None:
    bot_id = str(bot.self_id)
    _connected_bots[bot_id] = bot
    if bot_id in _online_announced:
        return
    _online_announced.add(bot_id)
    await _send_status_message(bot, ONLINE_MESSAGE, "online")


@driver.on_bot_disconnect
async def _handle_bot_disconnect(bot: Bot) -> None:
    bot_id = str(bot.self_id)
    if _connected_bots.get(bot_id) is bot:
        _connected_bots.pop(bot_id, None)


async def _send_offline_once() -> None:
    global _offline_attempted
    if _offline_attempted:
        return
    _offline_attempted = True
    for bot in list(_connected_bots.values()):
        await _send_status_message(bot, OFFLINE_MESSAGE, "offline")


async def _notify_then_exit(sig: int, frame: FrameType | None, previous_handler: Any) -> None:
    try:
        await _send_offline_once()
    finally:
        if callable(previous_handler):
            previous_handler(sig, frame)


def _defer_exit_signal(sig: int, frame: FrameType | None) -> None:
    """Delay Uvicorn shutdown until the offline notification is attempted."""
    global _shutdown_task
    previous_handler = _previous_signal_handlers.get(sig)

    if _shutdown_task is not None and not _shutdown_task.done():
        if callable(previous_handler):
            previous_handler(sig, frame)
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        if callable(previous_handler):
            previous_handler(sig, frame)
        return

    _shutdown_task = loop.create_task(_notify_then_exit(sig, frame, previous_handler))


@driver.on_startup
async def _install_signal_handlers() -> None:
    """Wrap Uvicorn's handlers after it installs them during startup."""
    global _signal_handlers_installed
    if _signal_handlers_installed:
        return

    handled_signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        handled_signals.append(signal.SIGBREAK)

    installed_any = False
    for handled_signal in handled_signals:
        previous_handler = signal.getsignal(handled_signal)
        if not callable(previous_handler):
            continue
        try:
            signal.signal(handled_signal, _defer_exit_signal)
        except (OSError, ValueError):
            continue
        _previous_signal_handlers[int(handled_signal)] = previous_handler
        installed_any = True

    _signal_handlers_installed = installed_any


@driver.on_shutdown
async def _handle_shutdown() -> None:
    """Best-effort fallback for graceful shutdowns not initiated by a signal."""
    await _send_offline_once()
