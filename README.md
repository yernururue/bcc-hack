# ReqCheck

ИИ-агент статического ревью документов требований по ISO/IEC/IEEE 29148:2018 (п. 5.2.5, 5.2.6) и IEEE 830-1998 (п. 4.3). Хакатон BCC-HUB x MNU FinTech.

Агент читает документ (PDF, Markdown, TXT), выделяет отдельные требования, проверяет каждое и весь набор, а затем пишет отчёт на русском: паспорт прогона, таблицу нарезки, проверки, сводку, вопросы автору и итог «можно ли начинать разработку и тестирование». Каждая находка опирается на цитату, которую код нашёл в тексте документа; находка без проверенной цитаты в отчёт не попадает. Вердикт и счётчики считает код, а не модель.

## Установка

Нужны Python 3.12+ и [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-extras
cp .env.example .env
```

Без uv: `python3.12 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`.

### Ключ LLM

1. Создайте ключ на [openrouter.ai/keys](https://openrouter.ai/keys) (нужен баланс в несколько долларов: один прогон документа на 10 страниц стоит центы).
2. Впишите его в `.env` в строку `LLM_API_KEY=`. Файл `.env` в git не попадает; пример без ключа лежит в `.env.example`.

Агент работает с любым OpenAI-совместимым API: для другого провайдера или внутреннего шлюза задайте `LLM_BASE_URL`, `LLM_API_KEY` и `LLM_MODEL` в `.env`.

### Модель

По умолчанию в `.env.example` указана `openai/gpt-6-luna`: лучшая по нашему eval среди дешёвых моделей ([сравнение](eval/results/2026-11-26-comparison.md), [решение](docs/08-decisions.md)). Другие модели и их цены перечислены в `config/models.yaml`; выбрать можно флагом `--model` или в интерфейсе.

## Запуск

Одна команда: путь к файлу требований и путь к файлу отчёта.

```bash
uv run python main.py путь/к/requirements.md out/report.md
uv run python main.py путь/к/requirements.pdf out/report.pdf
```

- Отчёт ляжет в указанный файл, рядом с ним всегда пишется машиночитаемый `<отчёт>.json` (например, `out/report.md.json`).
- Формат отчёта берётся из расширения. Если расширения нет, он совпадает с форматом входа (PDF → PDF, Markdown и TXT → Markdown).
- Перед прогоном печатается оценка стоимости. `--dry-run` только разбирает документ и показывает оценку, без вызовов LLM.
- `--max-cost USD` — предохранитель: при достижении лимита прогон останавливается, отчёт помечается неполным.
- Ответы модели кэшируются в `.cache/reqcheck`: повторный прогон того же документа не тратит деньги и даёт тот же отчёт. `--no-cache` не читает кэш, `REQCHECK_CACHE_DIR=off` отключает его.
- `--fail-on blocker` — код выхода 10, если есть блокирующие дефекты (для CI). Остальные коды: 0 — готово, 1 — ошибка, 2 — неверный вход, 3 — в PDF нет текстового слоя (скан), 4 — ключ отклонён или не задан, 5 — исчерпан лимит стоимости.

Полный список флагов: `uv run python main.py -h`.

## Веб-интерфейс и HTTP API

```bash
uv run python -m reqcheck.server        # http://127.0.0.1:8000
```

Один экран: перетащить файл, выбрать модель (рядом цена прогона), нажать «Проверить», получить вердикт, топ исправлений и отчёт для скачивания (PDF/MD и JSON). Кнопка «Попробовать на примере» подставляет учебный документ, остаётся нажать «Проверить». Демо без сервера: `python3 -m http.server 5173 --directory reqcheck/web` и адрес `?demo`.

HTTP API (`/api/v1`, спецификация на `/api/v1/docs`) описан в [docs/04-integration.md](docs/04-integration.md): загрузка, поток событий SSE, отчёт в JSON/MD/PDF. Токен `REQCHECK_API_TOKEN` защищает API; встроенный интерфейс в этом случае спросит токен и будет отправлять его во всех запросах.

## Docker

```bash
cp .env.example .env      # впишите LLM_API_KEY
docker compose up --build # http://127.0.0.1:8000
```

Образ (`Dockerfile`) собирает сервер с опциями `server` и `docx` и ставит Tesseract с русским языком для `--ocr`. Ключей в образе нет: `docker-compose.yml` передаёт переменные из `.env` при запуске, а `.dockerignore` не пускает `.env` в контекст сборки. Кэш ответов LLM лежит в томе `reqcheck-cache`. Порт открыт только на `127.0.0.1`; чтобы открыть его в сеть, задайте `REQCHECK_API_TOKEN`. CLI в контейнере: `docker compose run --rm reqcheck python main.py /data/in.pdf /data/out.pdf` (файлы смонтируйте в `/data`).

## Как это работает

`ingest` → `extract` (LLM) → `rules` (код) → `check_each` (LLM, батчи по 15) → `check_set` (LLM) → `confirm` (подтверждение противоречий) → `verify` (цитаты проверяет код) → фильтр шума → `questions` → `verdict` (код) → `render`. Подробности: [docs/02-trd.md](docs/02-trd.md), [docs/03-architecture.md](docs/03-architecture.md). Словари, пороги и критерии лежат в `config/*.yaml`, а не в коде; промпты — в `reqcheck/prompts/`.

## Разработка и проверка качества

```bash
uv run pytest -q          # тесты без сети
uv run ruff check .
uv run python -m reqcheck.eval --model openai/gpt-6-luna --dry-run   # оценка стоимости eval
uv run python -m reqcheck.eval --model openai/gpt-6-luna             # eval на фикстурах tests/fixtures
```

Eval сопоставляет находки с ожидаемыми дефектами (`tests/fixtures/expected.yaml`) и печатает recall, ложные находки на чистом документе, стоимость и время. Модели уровня `strong` в eval требуют флага `--allow-strong`.

Документация проекта: [docs/README.md](docs/README.md).
