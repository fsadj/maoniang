from app import config


def _env(**overrides):
    base = {
        "TARGET_USER_IDS": "111, 222 ,333",
        "TARGET_USER_IDS_BAD": "ignored",  # not used directly here
        "API_BASE_URL": "https://example.com/v1/",
        "API_KEY": "sk-test",
        "API_MODEL": "gpt-4o-mini",
    }
    base.update(overrides)
    return base


def test_csv_ints_is_lenient_about_malformed_values():
    env = {"IDS": "1, two ,3,,"}
    assert config.csv_ints("IDS", env) == frozenset({1, 3})
    assert config.csv_ints("MISSING", env) == frozenset()


def test_load_config_parses_sets_and_rstrips_base_url():
    cfg = config.load_config(_env())
    assert cfg.target_user_ids == frozenset({111, 222, 333})
    assert cfg.api_base_url == "https://example.com/v1"  # trailing slash stripped
    assert cfg.api_key == "sk-test"


def test_public_system_prompt_falls_back_to_system_prompt():
    cfg = config.load_config(_env(SYSTEM_PROMPT="我是助手", PUBLIC_SYSTEM_PROMPT=""))
    assert cfg.public_system_prompt == "我是助手"
    cfg2 = config.load_config(_env(SYSTEM_PROMPT="我是助手", PUBLIC_SYSTEM_PROMPT="公共人设"))
    assert cfg2.public_system_prompt == "公共人设"


def test_personal_history_max_is_double_turns():
    cfg = config.load_config(_env(MAX_CONVERSATION_TURNS="20"))
    assert cfg.personal_history_max == 40
    cfg2 = config.load_config(_env(MAX_CONVERSATION_TURNS="1"))
    assert cfg2.personal_history_max == 2  # clamped to at least 2


def test_numeric_parsing_falls_back_on_garbage():
    cfg = config.load_config(_env(MAX_CONVERSATION_TURNS="not-a-number"))
    assert cfg.max_conversation_turns == 20  # default


def test_budget_and_retry_defaults():
    cfg = config.load_config(_env())
    assert cfg.budget_daily_calls == 0  # disabled by default
    assert cfg.max_retries == 3
