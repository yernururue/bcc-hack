"""OpenAI-совместимый клиент (OpenRouter, шлюз банка): строгий JSON, кэш, учёт расхода, бюджет.

Параметры маршрутизации проверены на живом OpenRouter 2026-11-25 (docs/02-trd.md, §4).
"""

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    OpenAI,
    PermissionDeniedError,
)
from pydantic import BaseModel, ValidationError

from reqcheck.config import ModelInfo, models_config
from reqcheck.errors import AuthError, BudgetExceededError, ReqCheckError
from reqcheck.llm.base import LLMResult, Usage
from reqcheck.schemas import LLMUsage

log = logging.getLogger(__name__)


class LLMCallError(ReqCheckError):
    """Вызов не удался (сеть, обрезанный ответ, невалидный JSON): этап помечается неудавшимся."""


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema для response_format strict: все поля обязательны, лишние запрещены."""
    schema = model.model_json_schema()

    def fix(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            node.pop("default", None)
            for value in node.values():
                fix(value)
        elif isinstance(node, list):
            for value in node:
                fix(value)

    fix(schema)
    return schema


class FileCache:
    """Ответы модели на диске: <root>/<2 символа>/<sha256>.json."""

    def __init__(self, root: Path | None) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        assert self.root is not None
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        if self.root is None:
            return None
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def put(self, key: str, data: dict[str, Any]) -> None:
        if self.root is None:
            return
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


class OpenAICompatClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        max_cost_usd: float,
        cache_dir: Path | None,
        use_cache: bool = True,
        openrouter: bool = True,
        max_tokens: int = 12000,
    ) -> None:
        self.model = model
        self.info: ModelInfo | None = models_config().get(model)
        self.max_cost_usd = max_cost_usd
        self.usage = LLMUsage()
        self._cache = FileCache(cache_dir)
        self._read_cache = use_cache
        self._openrouter = openrouter
        self._max_tokens = max_tokens
        self._lock = threading.Lock()
        # SDK сам повторяет 408/409/429/5xx и сетевые ошибки с учётом Retry-After.
        self._client = OpenAI(api_key=api_key, base_url=base_url, max_retries=5, timeout=300)

    # --- учёт -----------------------------------------------------------------

    def _cost(self, raw_usage: Any) -> float:
        extra = getattr(raw_usage, "model_extra", None) or {}
        if extra.get("cost") is not None:
            return float(extra["cost"])
        if self.info is None:
            return 0.0
        return (
            raw_usage.prompt_tokens * self.info.price_in
            + raw_usage.completion_tokens * self.info.price_out
        ) / 1e6

    def _check_budget(self) -> None:
        with self._lock:
            spent = self.usage.cost_usd
        if spent >= self.max_cost_usd:
            raise BudgetExceededError(
                f"достигнут лимит стоимости ${self.max_cost_usd:.2f} (потрачено ${spent:.4f})"
            )

    # --- вызов ----------------------------------------------------------------

    def _request(self, messages: list[dict[str, str]], name: str, schema: dict[str, Any]):
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            },
            "max_tokens": self._max_tokens,
        }
        if self.info is not None and self.info.temperature:
            kwargs["temperature"] = 0
        extra: dict[str, Any] = {}
        if self._openrouter:
            extra["provider"] = {"require_parameters": True, "sort": "price"}
        if self.info is not None and self.info.reasoning == "off":
            extra["reasoning"] = {"enabled": False}
        if extra:
            kwargs["extra_body"] = extra
        try:
            return self._client.chat.completions.create(**kwargs)
        except (AuthenticationError, PermissionDeniedError) as exc:
            raise AuthError(
                f"LLM отклонил ключ ({exc.status_code}): проверьте LLM_API_KEY"
            ) from exc
        except APIStatusError as exc:
            if exc.status_code == 402:
                raise BudgetExceededError(
                    "у провайдера LLM недостаточно средств (402): пополните баланс"
                ) from exc
            raise LLMCallError(f"ошибка LLM {exc.status_code}: {exc.message}") from exc
        except APIConnectionError as exc:
            raise LLMCallError(f"нет связи с LLM: {exc}") from exc

    def complete[M: BaseModel](
        self, *, system: str, user: str, schema: type[M], stage: str
    ) -> LLMResult[M]:
        schema_json = strict_schema(schema)
        key = hashlib.sha256(
            json.dumps(
                [self.model, system, user, schema_json], ensure_ascii=False, sort_keys=True
            ).encode()
        ).hexdigest()

        if self._read_cache and (hit := self._cache.get(key)):
            try:
                value = schema.model_validate_json(hit["content"])
            except (ValidationError, KeyError):
                value = None
            if value is not None:
                with self._lock:
                    self.usage.cached_calls += 1
                log.debug("%s: ответ из кэша", stage)
                return LLMResult(value, Usage(cached=True))

        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        prompt_tokens = completion_tokens = 0
        cost = 0.0
        last_error = ""
        for attempt in range(2):
            self._check_budget()
            resp = self._request(messages, schema.__name__, schema_json)
            if resp.usage is not None:
                call_cost = self._cost(resp.usage)
                prompt_tokens += resp.usage.prompt_tokens
                completion_tokens += resp.usage.completion_tokens
                cost += call_cost
                with self._lock:
                    self.usage.calls += 1
                    self.usage.prompt_tokens += resp.usage.prompt_tokens
                    self.usage.completion_tokens += resp.usage.completion_tokens
                    self.usage.cost_usd += call_cost
            choice = resp.choices[0]
            content = choice.message.content
            if choice.finish_reason == "length":
                raise LLMCallError(f"{stage}: ответ модели обрезан по max_tokens")
            if not content:
                raise LLMCallError(f"{stage}: пустой ответ модели")
            try:
                value = schema.model_validate_json(content)
            except ValidationError as exc:
                last_error = str(exc)[:2000]
                log.warning("%s: ответ не прошёл схему (попытка %d)", stage, attempt + 1)
                messages = [
                    *messages,
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": "Ответ не прошёл проверку схемы:\n"
                        f"{last_error}\nВерни исправленный JSON целиком.",
                    },
                ]
                continue
            self._cache.put(key, {"model": self.model, "stage": stage, "content": content})
            usage = Usage(prompt_tokens, completion_tokens, cost)
            return LLMResult(value, usage)
        raise LLMCallError(f"{stage}: ответ модели не соответствует схеме: {last_error[:300]}")
