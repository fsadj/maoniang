"""Thin group/private message matcher. Delegates all logic to the app/ service layer."""

from __future__ import annotations

import asyncio
import logging
import random

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment, PrivateMessageEvent
from nonebot.rule import is_type

from app import text
from app.config import config
from app.history import PrivateScope, PublicScope
from app.runtime import get_service, status_info

logger = logging.getLogger(__name__)

auto_reply = on_message(rule=is_type(GroupMessageEvent), priority=20, block=False)
private_reply = on_message(rule=is_type(PrivateMessageEvent), priority=20, block=False)

_HELP_TEXT = (
    "指令(私聊直接发 / 群里 @我 + 指令):\n"
    "清空 / 清除 / 重置 / reset —— 清掉当前对话上下文\n"
    "撤销 / undo —— 删掉上一轮\n"
    "状态 / status —— 运行时长、今日调用、预算\n"
    "帮助 / ? —— 这条\n"
    "(仅群)公共 + 换行 + 内容 —— 进入本群共享对话"
)


def _should_listen_to_user(event: GroupMessageEvent) -> bool:
    """Per-user filter plus optional groups that listen to everyone."""
    if str(event.user_id) == str(event.self_id):
        return False
    if event.group_id in config.all_users_group_ids:
        return True
    if event.user_id not in config.target_user_ids:
        return False
    return not config.target_group_ids or event.group_id in config.target_group_ids


def _instructions_for(is_public: bool) -> str:
    return config.public_system_prompt if is_public else config.system_prompt


def _prefill_for(is_public: bool) -> tuple[str, str]:
    if is_public:
        return config.public_prefill_user, config.public_prefill_assistant
    return config.prefill_user, config.prefill_assistant


def _fmt_uptime(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h{(s % 3600) // 60}m"


def _status_text() -> str:
    info = status_info()
    if info["budget_limit"] > 0:
        budget = f"剩余 {max(0, info['budget_limit'] - info['calls_today'])}/{info['budget_limit']}"
    else:
        budget = "无上限"
    return (
        f"⏱ 运行 {_fmt_uptime(info['uptime_s'])}\n"
        f"📊 今日调用 {info['calls_today']} 次\n"
        f"💰 预算 {budget}"
    )


def _reply_msg(event, text: str):
    """Build the outgoing message: quoted (引用) if REPLY_QUOTE, else plain text."""
    if config.reply_quote:
        return MessageSegment.reply(event.message_id) + text
    return text


async def _command_reply(command: str | None, scope) -> str | None:
    """Handle a meta command (no API call). Returns reply text, or None if not a command."""
    if command == "clear":
        await get_service().clear(scope)
        return "上下文已清空。"
    if command == "undo":
        removed = await get_service().undo(scope)
        return "已撤销上一轮。" if removed else "没有可撤销的了。"
    if command == "help":
        return _HELP_TEXT
    if command == "status":
        return _status_text()
    return None


@auto_reply.handle()
async def handle_group_message(event: GroupMessageEvent) -> None:
    if not _should_listen_to_user(event):
        return
    if not text.is_at_bot(event.original_message, event.self_id):
        return

    raw = event.get_plaintext()
    public_prompt = text.parse_public_prompt(raw)
    is_public = public_prompt is not None
    prompt = public_prompt if is_public else raw.strip()
    if not prompt:
        return

    command = text.detect_private_command(prompt, is_public)
    prompt = prompt[: config.max_message_length]
    if is_public:
        display = text.sanitize_display_name(text.sender_display_name(event.sender.card, event.sender.nickname))
        prompt = text.format_public_body(display, prompt)

    scope: PrivateScope | PublicScope = (
        PublicScope(event.group_id) if is_public else PrivateScope(event.group_id, event.user_id)
    )

    reply = await _command_reply(command, scope)
    if reply is not None:
        await auto_reply.finish(_reply_msg(event, reply))
        return

    # Anti-detection: probabilistic reply (checked BEFORE the API call so a dropped
    # turn costs nothing). Meta commands above are exempt — only normal chat can be skipped.
    if config.reply_probability < 1.0 and random.random() >= config.reply_probability:
        return
    prefill_user, prefill_assistant = _prefill_for(is_public)
    answer = await get_service().handle(
        scope,
        prompt,
        instructions=_instructions_for(is_public),
        prefill_user=prefill_user,
        prefill_assistant=prefill_assistant,
    )
    if config.reply_delay_max > 0:
        await asyncio.sleep(random.uniform(config.reply_delay_min, config.reply_delay_max))
    await auto_reply.finish(_reply_msg(event, answer))


@private_reply.handle()
async def handle_private_message(event: PrivateMessageEvent) -> None:
    """DM is opt-in via TARGET_USER_IDS only (higher abuse risk than group @)."""
    if str(event.user_id) == str(event.self_id):
        return
    if event.user_id not in config.target_user_ids:
        return

    raw = event.get_plaintext().strip()
    if not raw:
        return

    command = text.detect_private_command(raw, is_public=False)
    prompt = raw[: config.max_message_length]
    scope = PrivateScope(0, event.user_id)

    reply = await _command_reply(command, scope)
    if reply is not None:
        await private_reply.finish(_reply_msg(event, reply))
        return

    if config.reply_probability < 1.0 and random.random() >= config.reply_probability:
        return
    answer = await get_service().handle(
        scope,
        prompt,
        instructions=config.system_prompt,
        prefill_user=config.prefill_user,
        prefill_assistant=config.prefill_assistant,
    )
    if config.reply_delay_max > 0:
        await asyncio.sleep(random.uniform(config.reply_delay_min, config.reply_delay_max))
    await private_reply.finish(_reply_msg(event, answer))
