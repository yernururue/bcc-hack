# 04. Интеграция в системы банка

Цель: встроить ReqCheck в существующий процесс за час, не меняя код ядра.

## 1. Способы встраивания

| Способ | Когда подходит | Что нужно |
|---|---|---|
| Python-библиотека | Свой сервис на Python | `pip install reqcheck` (или из git) |
| CLI | Скрипты, GitLab CI, cron | Python 3.12+ или Docker |
| HTTP API | Любой язык, Confluence/Jira-плагины, BPM | Запущенный сервер (`reqcheck.server`) |
| Docker (P1) | Изолированный запуск в контуре банка | Образ + переменные окружения |

## 2. Библиотека

```python
from reqcheck import validate, Options

report = validate(
    data=open("spec.pdf", "rb").read(),
    filename="spec.pdf",
    options=Options(model="openai/gpt-6-luna", max_cost_usd=0.5),
)
print(report.verdict.can_start_development)   # bool
print(report.summary.blockers)                 # int
report.save("out/report.pdf")                  # формат по расширению
report.save("out/report.json")
```

Прогресс можно получать колбэком: `validate(..., on_event=lambda e: print(e.stage, e.progress))`.

## 3. CLI в CI

GitLab CI (пример):

```yaml
requirements-review:
  image: python:3.12-slim
  script:
    - pip install -r requirements.txt
    - python main.py docs/spec.pdf out/review.pdf
  artifacts:
    paths: [out/]
  allow_failure: true    # код выхода сообщает о проблемах, но не блокирует пайплайн
```

Коды выхода описаны в [02-trd.md](02-trd.md#3-cli). Чтобы пайплайн падал на блокирующих дефектах, есть флаг `--fail-on blocker`: тогда при `blocker ≥ 1` код выхода 10.

## 4. HTTP API

База: `/api/v1`. OpenAPI-спецификация генерируется автоматически: `/api/v1/openapi.json`, документация — `/api/v1/docs`.

| Метод | Путь | Назначение |
|---|---|---|
| `POST` | `/checks` | multipart: `file`, `model?`, `max_cost_usd?` → `202 {"id": "...", "status": "queued"}` |
| `GET` | `/checks/{id}` | Статус: `queued`, `running`, `done`, `failed`; текущий этап; доля выполнения |
| `GET` | `/checks/{id}/events` | SSE-поток событий `stage`, `progress`, `done`, `error` |
| `GET` | `/checks/{id}/report?format=json\|md\|pdf` | Отчёт; по умолчанию формат входа |
| `GET` | `/checks/{id}/annotated` | P1: исходный PDF с пометками (выделения + комментарии) |
| `GET` | `/checks/{id}/findings/{finding_id}/snippet` | P1: PNG фрагмента страницы с подсвеченной цитатой (PDF) или JSON с абзацами (Markdown) |
| `DELETE` | `/checks/{id}` | Удалить задание и файлы |
| `GET` | `/models` | Список моделей из `config/models.yaml` с ценами |
| `GET` | `/healthz` | Проверка живости |

Пример:

```bash
curl -s -F file=@spec.pdf -F model=openai/gpt-6-luna http://localhost:8000/api/v1/checks
curl -s http://localhost:8000/api/v1/checks/<id>
curl -s -o review.pdf "http://localhost:8000/api/v1/checks/<id>/report?format=pdf"
```

Авторизация: если задан `REQCHECK_API_TOKEN`, все запросы требуют `Authorization: Bearer <token>`. Для SSO банка сервер ставится за reverse-proxy, который делает аутентификацию.

## 5. JSON-контракт

- Схема: `GET /api/v1/schema/report` и файл `docs/report.schema.json` (генерируется из Pydantic).
- Поле `schema_version` (semver). Новые поля — минорная версия, удаление или переименование — мажорная.
- Ключевые поля для автоматизации:
  - `verdict.can_start_development`, `verdict.can_start_testing` — bool;
  - `summary.requirements_total`, `summary.violations_total`, `summary.blockers`;
  - `findings[].severity`, `findings[].anchor`, `findings[].quote`, `findings[].suggestion`;
  - `questions[]` — готовый список для комментария в Jira/Confluence.

Полная структура: [06-report-spec.md](06-report-spec.md).

## 6. LLM в контуре банка

- Любой OpenAI-совместимый эндпоинт: внутренний LLM-шлюз, vLLM, Ollama, Azure OpenAI с совместимым адресом.
- Настройка: `LLM_BASE_URL=https://llm-gateway.bank.local/v1`, `LLM_API_KEY=...`, `LLM_MODEL=...`.
- Если шлюз не поддерживает `response_format: json_schema`, клиент переключается на `json_object` + валидацию Pydantic (флаг `LLM_STRUCTURED_MODE=json_object`).
- Специфика OpenRouter (`provider.require_parameters`, поле `usage.cost`) включается только если `LLM_BASE_URL` указывает на openrouter.ai.

## 7. Требования к окружению и безопасности

- Исходящие соединения: только `LLM_BASE_URL`. Других внешних вызовов нет (шрифты UI и PDF лежат в пакете).
- Данные на диске: временные загрузки (удаляются после прогона), кэш LLM (`REQCHECK_CACHE_DIR`, можно отключить `--no-cache` или `REQCHECK_CACHE_DIR=off`).
- Логи без содержимого документа на уровне INFO.
- Ресурсы: ~300 МБ RAM на прогон, CPU без GPU.

## 8. Точки расширения

| Задача | Что сделать |
|---|---|
| Новый формат входа (DOCX, Confluence) | Класс `Ingestor` + запись в реестре |
| Новый формат отчёта | Класс `Renderer` + шаблон |
| Свои критерии или словари банка | Править `config/*.yaml`, код не трогать |
| Свои пороги «можно/нельзя» | `config/thresholds.yaml` |
| Другая модель для отдельного этапа | `config/models.yaml`: секция `stage_overrides` |
