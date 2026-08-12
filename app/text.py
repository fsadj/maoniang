"""Pure message helpers — no I/O, no state, no NoneBot. Unit-testable directly."""

from __future__ import annotations

from collections.abc import Iterable

_PUBLIC_MARKER = "公共"


def parse_public_prompt(text: str) -> str | None:
    """Return text after an exact `公共` + newline marker, else None.

    `公共\n` with an empty body returns "" (the caller decides whether to drop it).
    """
    text = text.lstrip(" \t")
    if not text.startswith(_PUBLIC_MARKER):
        return None
    remainder = text[len(_PUBLIC_MARKER):]
    if remainder.startswith("\r\n"):
        return remainder[2:].strip()
    if remainder.startswith("\n"):
        return remainder[1:].strip()
    return None


def detect_private_command(prompt: str, is_public: bool) -> str | None:
    """Recognize exact personal-only commands. Public mode never treats these as commands."""
    if is_public:
        return None
    if prompt == "清空":
        return "clear"
    if prompt == "评分":
        return "rating"
    return None


def sender_display_name(card: object, nickname: object) -> str:
    """Group card → nickname → placeholder, matching the reference bot's fallback."""
    card_s = str(card or "").strip()
    nickname_s = str(nickname or "").strip()
    return card_s or nickname_s or "未知成员"


def format_public_body(display_name: str, body: str) -> str:
    """Prefix a public message with the sender's display name."""
    return f"[{display_name}]\n{body}"


def is_at_bot(original_message: Iterable[object], self_id: object) -> bool:
    """True if the (unprocessed) message contains an explicit @ for this bot.

    `original_message` is walked (not `event.message`, which has the @ stripped when to_me).
    Each segment duck-types as `.type` + `.data` (mapping with a `qq` key).
    """
    bot_id = str(self_id)
    for segment in original_message:
        seg_type = getattr(segment, "type", None)
        data = getattr(segment, "data", None)
        if seg_type == "at" and data is not None and str(data.get("qq")) == bot_id:
            return True
    return False
