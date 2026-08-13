"""Thin group-message matcher. Delegates all logic to the app/ service layer."""

from __future__ import annotations

import logging

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment, PrivateMessageEvent
from nonebot.rule import is_type

from app import text
from app.config import config
from app.history import PrivateScope, PublicScope
from app.runtime import get_service

logger = logging.getLogger(__name__)

auto_reply = on_message(rule=is_type(GroupMessageEvent), priority=20, block=False)


def _should_listen_to_user(event: GroupMessageEvent) -> bool:
    """Per-user filter plus optional groups that listen to everyone."""
    if str(event.user_id) == str(event.self_id):
        return False
    if event.group_id in config.all_users_group_ids:
        return True
    if event.user_id not in config.target_user_ids:
        return False
    return not config.target_group_ids or event.group_id in config.target_group_ids


def _instructions_for(is_public: bool, is_rating: bool) -> str:
    if is_rating:
        return config.rating_system_prompt
    return config.public_system_prompt if is_public else config.system_prompt


def _prefill_for(is_public: bool) -> tuple[str, str]:
    if is_public:
        return config.public_prefill_user, config.public_prefill_assistant
    return config.prefill_user, config.prefill_assistant


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

    service = get_service()
    if command == "clear":
        await service.clear(scope)
        await auto_reply.finish(MessageSegment.reply(event.message_id) + "上下文已清空。")
        return

    is_rating = command == "rating"
    prefill_user, prefill_assistant = _prefill_for(is_public)
    instructions = _instructions_for(is_public, is_rating)
    answer = await service.handle(
        scope,
        prompt,
        instructions=instructions,
        prefill_user=prefill_user,
        prefill_assistant=prefill_assistant,
        is_rating=is_rating,
    )
    await auto_reply.finish(MessageSegment.reply(event.message_id) + answer)


# ---- Private (1:1) chat ----
# Rule-level type filter (is_type) so this matcher never runs / never blocks for group events.
private_reply = on_message(rule=is_type(PrivateMessageEvent), priority=20, block=False)


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
    # DM history uses a (0, user_id) scope — deliberately separate from any group context.
    scope = PrivateScope(0, event.user_id)

    service = get_service()
    if command == "clear":
        await service.clear(scope)
        await private_reply.finish(MessageSegment.reply(event.message_id) + "上下文已清空。")
        return

    is_rating = command == "rating"
    prefill_user, prefill_assistant = _prefill_for(is_public=False)
    instructions = config.rating_system_prompt if is_rating else config.system_prompt
    answer = await service.handle(
        scope,
        prompt,
        instructions=instructions,
        prefill_user=prefill_user,
        prefill_assistant=prefill_assistant,
        is_rating=is_rating,
    )
    await private_reply.finish(MessageSegment.reply(event.message_id) + answer)
