"""LLM-клиент без сети: строгая схема, кэш, повтор при невалидном JSON, бюджет, маршрутизация."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

from reqcheck.errors import BudgetExceededError
from reqcheck.llm.openai_compat import LLMCallError, OpenAICompatClient, strict_schema
from reqcheck.stages.check_each import JudgeResult


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: str
    note: str | None


def response(content: str | None, finish: str = "stop", cost: float = 0.001):
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20, model_extra={"cost": cost})
    message = SimpleNamespace(content=content)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish)], usage=usage
    )


def client(tmp_path: Path, replies: list, model: str = "deepseek/deepseek-v4.1-flash", **kw):
    c = OpenAICompatClient(
        api_key="test",
        base_url="https://openrouter.ai/api/v1",
        model=model,
        max_cost_usd=kw.pop("max_cost_usd", 1.0),
        cache_dir=tmp_path / "cache",
        **kw,
    )
    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        return replies.pop(0)

    c._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return c, calls


def ask(c: OpenAICompatClient):
    return c.complete(system="s", user="u", schema=Answer, stage="test")


def test_strict_schema_requires_all_fields() -> None:
    schema = strict_schema(JudgeResult)
    item = schema["$defs"]["JudgeFinding"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == set(item["properties"])
    assert "suggestion" in item["required"]  # nullable, но обязательное


def test_routing_and_reasoning_from_config(tmp_path: Path) -> None:
    c, calls = client(tmp_path, [response('{"verdict": "ok", "note": null}')])
    assert ask(c).value.verdict == "ok"
    kw = calls[0]
    assert kw["extra_body"]["provider"] == {"require_parameters": True, "sort": "price"}
    assert kw["extra_body"]["reasoning"] == {"enabled": False}
    assert kw["temperature"] == 0
    assert kw["response_format"]["json_schema"]["strict"] is True


def test_no_temperature_for_models_without_it(tmp_path: Path) -> None:
    c, calls = client(tmp_path, [response('{"verdict": "ok", "note": null}')], "openai/gpt-6-luna")
    ask(c)
    assert "temperature" not in calls[0]
    assert "reasoning" not in calls[0]["extra_body"]


def test_cache_hit_costs_nothing(tmp_path: Path) -> None:
    c, calls = client(tmp_path, [response('{"verdict": "ok", "note": null}')])
    ask(c)
    c2, calls2 = client(tmp_path, [])
    result = ask(c2)
    assert result.usage.cached and not calls2
    assert c2.usage.cached_calls == 1 and c2.usage.cost_usd == 0


def test_no_cache_flag_skips_reading(tmp_path: Path) -> None:
    c, _ = client(tmp_path, [response('{"verdict": "ok", "note": null}')])
    ask(c)
    c2, calls2 = client(tmp_path, [response('{"verdict": "new", "note": null}')], use_cache=False)
    assert ask(c2).value.verdict == "new" and len(calls2) == 1


def test_invalid_json_retried_once_with_error(tmp_path: Path) -> None:
    c, calls = client(
        tmp_path, [response('{"verdict": 1}'), response('{"verdict": "ok", "note": "x"}')]
    )
    assert ask(c).value.note == "x"
    assert len(calls) == 2
    assert "не прошёл проверку схемы" in calls[1]["messages"][-1]["content"]
    assert c.usage.calls == 2


def test_invalid_twice_fails_stage(tmp_path: Path) -> None:
    c, _ = client(tmp_path, [response("{}"), response("{}")])
    with pytest.raises(LLMCallError):
        ask(c)


def test_truncated_answer_is_error(tmp_path: Path) -> None:
    c, _ = client(tmp_path, [response(None, finish="length")])
    with pytest.raises(LLMCallError, match="обрезан"):
        ask(c)


def test_budget_stops_next_call(tmp_path: Path) -> None:
    c, calls = client(
        tmp_path,
        [response('{"verdict": "a", "note": null}', cost=0.06)],
        max_cost_usd=0.05,
    )
    ask(c)
    with pytest.raises(BudgetExceededError):
        c.complete(system="s", user="другой", schema=Answer, stage="test")
    assert len(calls) == 1


def _status_error(code: int):
    import httpx2 as httpx
    from openai import APIStatusError

    req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    return APIStatusError(
        "Insufficient credits", response=httpx.Response(code, request=req), body=None
    )


def test_payment_required_retried_once_then_succeeds(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("reqcheck.llm.openai_compat.PAYMENT_RETRY_DELAY", 0)
    c, calls = client(tmp_path, [])
    answers = [_status_error(402), response('{"verdict": "ok", "note": null}')]

    def create(**kwargs):
        calls.append(kwargs)
        item = answers.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    c._client.chat.completions.create = create
    assert ask(c).value.verdict == "ok" and len(calls) == 2


def test_payment_required_twice_reports_provider_reason(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("reqcheck.llm.openai_compat.PAYMENT_RETRY_DELAY", 0)
    c, _ = client(tmp_path, [])

    def create(**kwargs):
        raise _status_error(402)

    c._client.chat.completions.create = create
    with pytest.raises(BudgetExceededError, match="недостаточно средств.*Insufficient credits"):
        ask(c)
