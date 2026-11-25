# ReqCheck

ИИ-агент статического ревью документов требований по ISO/IEC/IEEE 29148:2018 (п. 5.2.5, 5.2.6) и IEEE 830-1998 (п. 4.3). Хакатон BCC-HUB x MNU FinTech.

> Статус: каркас. Конвейер проверки ещё не реализован, команда запуска проверяет входной файл и настройки. План работ: [docs/07-plan.md](docs/07-plan.md).

## Установка

Нужны Python 3.12+ и [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-extras
cp .env.example .env
```

Ключ LLM: создайте ключ на [openrouter.ai/keys](https://openrouter.ai/keys) и впишите его в `.env` в `LLM_API_KEY`. Файл `.env` в git не попадает.

Без uv: `pip install -r requirements.txt`.

## Запуск

```bash
uv run python main.py <файл_требований> <файл_отчёта>
```

Формат отчёта совпадает с форматом входа (PDF → PDF, Markdown → Markdown), рядом пишется `<отчёт>.json`. Флаги: `--model`, `--max-cost`, `--no-cache`, `--dry-run`; полный список — `python main.py -h`.

## Разработка

```bash
uv run pytest -q
uv run ruff check .
```

Макет интерфейса на демо-данных: `python3 -m http.server 5173 --directory reqcheck/web`.

Документация проекта: [docs/README.md](docs/README.md).
