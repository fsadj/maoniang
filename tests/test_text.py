from types import SimpleNamespace

from app import text


def test_parse_public_prompt_requires_newline_after_marker():
    assert text.parse_public_prompt("公共\n实际内容") == "实际内容"
    assert text.parse_public_prompt("  公共\r\n实际内容") == "实际内容"
    assert text.parse_public_prompt("公共 实际内容") is None
    assert text.parse_public_prompt("普通消息") is None
    assert text.parse_public_prompt("公共\n") == ""


def test_detect_private_commands_are_exact_and_personal_only():
    assert text.detect_private_command("清空", False) == "clear"
    assert text.detect_private_command("评分", False) == "rating"
    assert text.detect_private_command("清空一下", False) is None
    assert text.detect_private_command("评分 现在", False) is None
    assert text.detect_private_command("清空", True) is None
    assert text.detect_private_command("评分", True) is None


def test_sender_display_name_fallback_chain():
    assert text.sender_display_name("群名片", "QQ昵称") == "群名片"
    assert text.sender_display_name("", "QQ昵称") == "QQ昵称"
    assert text.sender_display_name(None, None) == "未知成员"


def test_format_public_body_prepends_display_name():
    assert text.format_public_body("群名片", "实际内容") == "[群名片]\n实际内容"


def _seg(seg_type: str, **data):
    return SimpleNamespace(type=seg_type, data=data)


def test_is_at_bot_matches_at_segment_for_self_id():
    msg = [_seg("text", text="hi"), _seg("at", qq="999"), _seg("at", qq="1000")]
    assert text.is_at_bot(msg, 1000) is True
    assert text.is_at_bot(msg, "1000") is True
    assert text.is_at_bot(msg, 4242) is False
    assert text.is_at_bot([], 1000) is False
