from pathlib import Path

import pytest

from reqcheck.settings import DEFAULT_MODEL, OPENROUTER_BASE_URL, Settings

ENV_VARS = [
    "LLM_API_KEY",
    "OPENROUTER_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_MAX_COST_USD",
    "REQCHECK_CACHE_DIR",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def make() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]  # не читать локальный .env


def test_defaults_use_cheap_model_and_openrouter() -> None:
    s = make()
    assert s.llm_api_key is None
    assert s.llm_model == DEFAULT_MODEL
    assert s.llm_base_url == OPENROUTER_BASE_URL
    assert s.is_openrouter
    assert s.cache_path == Path(".cache/reqcheck")


def test_key_from_openrouter_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    s = make()
    assert s.llm_api_key is not None
    assert s.llm_api_key.get_secret_value() == "sk-test"
    assert "sk-test" not in repr(s)


def test_llm_api_key_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "primary")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fallback")
    assert make().llm_api_key.get_secret_value() == "primary"  # type: ignore[union-attr]


def test_custom_gateway_and_cache_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "https://llm-gateway.bank.local/v1")
    monkeypatch.setenv("REQCHECK_CACHE_DIR", "off")
    monkeypatch.setenv("LLM_MAX_COST_USD", "0.1")
    s = make()
    assert not s.is_openrouter
    assert s.cache_path is None
    assert s.llm_max_cost_usd == pytest.approx(0.1)


def test_env_file_is_read(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("LLM_MODEL=openai/gpt-6-luna\nLLM_API_KEY=from-file\n", encoding="utf-8")
    s = Settings(_env_file=env)  # type: ignore[call-arg]
    assert s.llm_model == "openai/gpt-6-luna"
    assert s.llm_api_key.get_secret_value() == "from-file"  # type: ignore[union-attr]
