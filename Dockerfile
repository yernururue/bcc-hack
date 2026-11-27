# ReqCheck: HTTP API + веб-интерфейс (docs/04-integration.md). Секретов в образе нет:
# ключ LLM и токен API передаются переменными окружения при запуске (см. docker-compose.yml).
FROM python:3.12-slim

# Tesseract с русским и английским — для `--ocr` (ADR-014); без него OCR даёт понятную ошибку.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# Сначала зависимости (слой кэшируется), потом код. config/ читается из корня проекта.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra server --extra docx --no-install-project
COPY reqcheck ./reqcheck
COPY config ./config
COPY main.py ./
RUN uv sync --frozen --no-dev --extra server --extra docx

RUN useradd --create-home --uid 10001 reqcheck && mkdir -p /data/cache && chown -R reqcheck /data
USER reqcheck
ENV REQCHECK_CACHE_DIR=/data/cache

EXPOSE 8000
CMD ["python", "-m", "reqcheck.server", "--host", "0.0.0.0", "--port", "8000"]
