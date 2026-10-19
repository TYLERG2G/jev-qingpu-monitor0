"""設定（.env）與決策模型 provider 檢查；不連網。"""

from jev.config import decision_settings, load_env, mask, parse_env, report


def test_parse_env_quotes_comments_and_export():
    v = parse_env('# 註解\nA=1\nexport B="x y"\nC=\'z\'\nD=val # 行尾註解\nE=\nbad line\n')
    assert v == {"A": "1", "B": "x y", "C": "z", "D": "val", "E": ""}


def test_environment_wins_over_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text("JEV_DB=data/x.db\nJEV_API_KEY=from-file\n", encoding="utf-8")
    env = {"JEV_API_KEY": "from-env"}
    assert load_env(f, env) == ["JEV_API_KEY", "JEV_DB"]
    assert env == {"JEV_API_KEY": "from-env", "JEV_DB": "data/x.db"}


def test_default_is_off_and_sends_nothing():
    s = decision_settings({})
    assert s.provider is None and not s.enabled
    s = decision_settings({"JEV_DECISION_PROVIDER": "openai", "OPENAI_API_KEY": "sk-test-123456"})
    assert s.provider.name == "openai" and not s.enabled  # 沒有允許文字或圖片外送


def test_openai_and_typesafe_and_openrouter():
    s = decision_settings({"JEV_DECISION_PROVIDER": "openai", "OPENAI_API_KEY": "sk-test-123456", "JEV_DECISION_ALLOW_IMAGES": "true"})
    assert s.enabled and s.provider.endpoint == "https://api.openai.com/v1/decisions" and s.provider.model == "gpt-6-luna"
    t = decision_settings({"JEV_DECISION_PROVIDER": "typesafe", "TYPESAFE_API_KEY": "ts-123456789", "JEV_DECISION_ALLOW_IMAGES": "1"})
    assert not t.enabled and any("不支援圖片" in p for p in t.problems)
    t = decision_settings({"JEV_DECISION_PROVIDER": "typesafe", "TYPESAFE_API_KEY": "ts-123456789", "JEV_DECISION_ALLOW_TEXT": "yes"})
    assert t.enabled and t.provider.endpoint == "https://api.typesafe.ai/v1/systemone" and t.provider.model == "jev-latest"
    o = decision_settings({"JEV_DECISION_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "or-123456789", "JEV_DECISION_ALLOW_TEXT": "true"})
    assert not o.enabled and "OPENROUTER_DECISION_MODEL 未設定" in o.problems
    o = decision_settings({"JEV_DECISION_PROVIDER": "OpenRouter", "OPENROUTER_API_KEY": "or-123456789", "JEV_DECISION_ALLOW_TEXT": "true",
                           "OPENROUTER_DECISION_MODEL": "some/model", "OPENROUTER_WIRE_FORMAT": "decisions"})
    assert o.enabled and o.provider.endpoint == "https://openrouter.ai/api/alpha/decisions"


def test_bad_values_are_reported():
    s = decision_settings({"JEV_DECISION_PROVIDER": "gemini"})
    assert s.provider is None and s.problems
    s = decision_settings({"JEV_DECISION_PROVIDER": "openai", "OPENAI_API_KEY": "k" * 20, "OPENAI_BASE_URL": "http://evil.example",
                           "JEV_DECISION_ALLOW_TEXT": "true"})
    assert not s.enabled and any("https://" in p for p in s.problems)
    s = decision_settings({"JEV_DECISION_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "k" * 20, "OPENROUTER_DECISION_MODEL": "typesafe/jev-1.14",
                           "JEV_DECISION_ALLOW_TEXT": "true"})
    assert s.enabled and s.provider.endpoint == "https://openrouter.ai/api/v1/systemone"


def test_report_never_prints_full_key():
    key = "sk-proj-SECRETSECRETSECRET99"
    out = report({"OPENAI_API_KEY": key, "JEV_DECISION_PROVIDER": "openai"}, env_keys=[])
    assert key not in out and mask(key) in out
