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


_CLEAR_ALIASES = frozenset({"清空", "清除", "重置", "重新开始", "reset", "清除上下文", "清空上下文"})
_HELP_ALIASES = frozenset({"帮助", "命令", "help", "?"})
_STATUS_ALIASES = frozenset({"状态", "status", "关于"})
_UNDO_ALIASES = frozenset({"撤销", "撤回", "undo"})


def detect_private_command(prompt: str, is_public: bool) -> str | None:
    """Recognize exact personal-only commands. Public mode never treats these as commands."""
    if is_public:
        return None
    if prompt in _CLEAR_ALIASES:
        return "clear"
    if prompt in _HELP_ALIASES:
        return "help"
    if prompt in _STATUS_ALIASES:
        return "status"
    if prompt in _UNDO_ALIASES:
        return "undo"
    return None


def sender_display_name(card: object, nickname: object) -> str:
    """Group card → nickname → placeholder, matching the reference bot's fallback."""
    card_s = str(card or "").strip()
    nickname_s = str(nickname or "").strip()
    return card_s or nickname_s or "未知成员"


# Brackets that can break out of the "[name]\n{body}" framing used by public messages.
_FRAMING_BRACKETS = set("[]{}()<>【】")
# Authority/role tokens that signal an impersonation attempt (e.g. a card set to "系统").
_AUTHORITY_TOKENS = ("系统", "管理员", "群主", "公告", "通知", "admin", "system", "管理")


def sanitize_display_name(name: object) -> str:
    """Make a group card/nickname safe to interpolate into a prompt.

    Strips control chars/newlines and framing brackets (which could close the auto-opened
    '[' and let a following line read as a system directive), neutralizes anything that
    impersonates an authority role, and caps length. Falls back to '未知成员' when empty
    or suspicious. This is the defense against the public-mode nickname-injection vector.
    """
    if not name:
        return "未知成员"
    cleaned = "".join(ch for ch in str(name) if ch >= " ")  # drop control chars incl. newlines
    cleaned = "".join(ch for ch in cleaned if ch not in _FRAMING_BRACKETS).strip()
    if not cleaned:
        return "未知成员"
    if any(tok in cleaned.lower() for tok in _AUTHORITY_TOKENS):
        return "未知成员"
    return cleaned[:16]



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
