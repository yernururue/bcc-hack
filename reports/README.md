# Итоговый отчёт по документу организаторов

Файлы `requirements-review.md` и `requirements-review.pdf` (JSON рядом) сформированы агентом по `docs/requirements.md` из репозитория ТЗ, модель `anthropic/claude-sonnet-5.5`, версия агента — в паспорте отчёта (раздел 1).

Команды:

```bash
uv run python main.py ../ai-requirements-validator-task/docs/requirements.md reports/requirements-review.md --model anthropic/claude-sonnet-5.5 --max-cost 0.60
uv run python main.py ../ai-requirements-validator-task/docs/requirements.md reports/requirements-review.pdf --model anthropic/claude-sonnet-5.5 --max-cost 0.60
```

Отчёты собраны из кэша ответов модели (`.cache/reqcheck`), записанных при реальных прогонах на этом документе, поэтому в паспорте стоит «0 вызовов (+8 из кэша), $0». Реально потрачено ≈ $0,35 за несколько прогонов (в том числе повтор после разового ответа 402 и после доработок этапа `confirm`). Чтобы повторить прогон с нуля, добавьте `--no-cache`: ответы модели могут отличаться, так как сильные модели не принимают `temperature`.
