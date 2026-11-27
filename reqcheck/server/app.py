"""FastAPI-приложение: /api/v1 (docs/04-integration.md, §4) и статический интерфейс на /."""

import hmac
import json
import re
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from reqcheck import __version__, pipeline, render
from reqcheck.config import criteria, models_config
from reqcheck.errors import InputError
from reqcheck.formats import MAX_INPUT_BYTES, detect_input, resolve_output
from reqcheck.schemas import Report
from reqcheck.server.jobs import TERMINAL, Job, JobStore, LLMFactory, new_workdir
from reqcheck.server.view import build_view
from reqcheck.settings import Settings

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MEDIA = {
    "json": "application/json",
    "md": "text/markdown; charset=utf-8",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def create_app(settings: Settings | None = None, llm_factory: LLMFactory | None = None) -> FastAPI:
    settings = settings or Settings()
    store = JobStore(settings, llm_factory)
    app = FastAPI(
        title="ReqCheck",
        version=__version__,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
    )

    def auth(request: Request) -> None:
        token = settings.api_token
        if token is None:
            return
        header = request.headers.get("authorization", "")
        scheme, _, given = header.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            given.encode(), token.get_secret_value().encode()
        ):
            raise HTTPException(401, "Нужен заголовок Authorization: Bearer <токен>")

    secured = [Depends(auth)]

    def job_or_404(job_id: str) -> Job:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "Проверка не найдена")
        return job

    def finished(job_id: str) -> Job:
        job = job_or_404(job_id)
        if job.status == "failed":
            raise HTTPException(409, job.error["message"] if job.error else "Проверка не удалась")
        if job.status != "done" or job.report is None:
            raise HTTPException(409, "Проверка ещё не завершена")
        return job

    @app.middleware("http")
    async def no_sniff(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    # ---- служебное ----------------------------------------------------------------------

    @app.get("/api/v1/healthz")
    def healthz() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/api/v1/models", dependencies=secured)
    def models() -> list[dict]:
        return [
            {
                "id": m.id,
                "tier": m.tier,
                "price_in": m.price_in,
                "price_out": m.price_out,
                "default": m.id == settings.llm_model,
            }
            for m in models_config().models
        ]

    @app.get("/api/v1/schema/report", dependencies=secured)
    def report_schema() -> dict:
        return Report.model_json_schema()

    # ---- проверки -----------------------------------------------------------------------

    @app.post("/api/v1/checks", status_code=202, dependencies=secured)
    async def create_check(
        file: Annotated[UploadFile, File()],
        model: Annotated[str | None, Form()] = None,
        max_cost_usd: Annotated[float | None, Form()] = None,
    ) -> dict:
        if llm_factory is None and settings.llm_api_key is None:
            raise HTTPException(503, "На сервере не задан ключ LLM (LLM_API_KEY)")
        if max_cost_usd is not None and max_cost_usd <= 0:
            raise HTTPException(400, "max_cost_usd должен быть больше нуля")
        data = await file.read(MAX_INPUT_BYTES + 1)
        if len(data) > MAX_INPUT_BYTES:
            raise HTTPException(413, f"Файл больше {MAX_INPUT_BYTES // (1024 * 1024)} МБ")

        # Имя попадёт в отчёт, поэтому файл лежит под ним; путь и управляющие символы убираем.
        filename = re.sub(r"[\x00-\x1f]", "_", Path(file.filename or "document").name) or "document"
        workdir = new_workdir()
        try:
            path = workdir / filename
            path.write_bytes(data)
            source = detect_input(path)
        except InputError as exc:
            shutil.rmtree(workdir, ignore_errors=True)
            raise HTTPException(400, exc.message) from exc
        except Exception:
            shutil.rmtree(workdir, ignore_errors=True)
            raise

        cost = min(max_cost_usd or settings.llm_max_cost_usd, settings.llm_max_cost_usd)
        options = pipeline.Options(model=model or settings.llm_model, max_cost_usd=cost)
        job = store.submit(workdir, source, filename, options)
        return {"id": job.id, "status": job.status}

    @app.get("/api/v1/checks/{job_id}", dependencies=secured)
    def check_status(job_id: str) -> dict:
        return job_or_404(job_id).public()

    @app.get("/api/v1/checks/{job_id}/events", dependencies=secured)
    def check_events(job_id: str) -> StreamingResponse:
        job = job_or_404(job_id)

        def stream() -> Iterator[str]:
            sent = 0
            while True:
                with job.cond:
                    while sent >= len(job.events) and job.status not in TERMINAL:
                        if not job.cond.wait(timeout=15):
                            break  # тишина 15 с: отправляем пинг, чтобы соединение не закрыли
                    batch = job.events[sent:]
                    sent += len(batch)
                    over = job.status in TERMINAL and sent >= len(job.events)
                if not batch:
                    yield ": ping\n\n"
                for event in batch:
                    data = json.dumps(event, ensure_ascii=False)
                    yield f"event: {event['type']}\ndata: {data}\n\n"
                if over:
                    return

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/checks/{job_id}/summary", dependencies=secured)
    def check_summary(job_id: str) -> dict:
        job = finished(job_id)
        assert job.report is not None
        view = build_view(job.report, criteria())
        # Какие фрагменты оригинала готовы (ADR-009): UI запрашивает их при раскрытии находки
        for fix in view["fixes"]:
            snip = job.snippets.get(fix["id"])
            fix["snippet"] = (
                None if snip is None else "image" if isinstance(snip, bytes) else "text"
            )
        view["annotated"] = job.annotated is not None
        return view

    @app.get("/api/v1/checks/{job_id}/report", dependencies=secured)
    def check_report(job_id: str, format: Annotated[str | None, Query()] = None) -> Response:
        job = finished(job_id)
        assert job.report is not None
        fmt = format or job.native_format or "json"
        if fmt not in MEDIA:
            raise HTTPException(400, "format: json, md, pdf или docx")
        if fmt == "json":
            body = job.report.model_dump_json(indent=2).encode()
        elif fmt == job.native_format and job.native is not None:
            body = job.native
        else:
            with tempfile.TemporaryDirectory(prefix="reqcheck-") as tmp:
                target = resolve_output(job.input_format, Path(tmp) / f"report.{fmt}")  # type: ignore[arg-type]
                render.write(job.report, target)
                body = target.path.read_bytes()
        stem = Path(job.filename).stem or "report"
        name = quote(f"{stem}-review.{fmt}")
        return Response(
            body,
            media_type=MEDIA[fmt],
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}"},
        )

    @app.get("/api/v1/checks/{job_id}/annotated", dependencies=secured)
    def check_annotated(job_id: str) -> Response:
        """Исходный PDF с пометками находок (только для PDF на входе)."""
        job = finished(job_id)
        if job.input_format != "pdf":
            raise HTTPException(404, "Исходник с пометками есть только для PDF")
        if job.annotated is None:
            raise HTTPException(404, "Исходник с пометками не подготовлен")
        name = quote(f"{Path(job.filename).stem or 'document'}-annotated.pdf")
        return Response(
            job.annotated,
            media_type=MEDIA["pdf"],
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}"},
        )

    @app.get("/api/v1/checks/{job_id}/findings/{finding_id}/snippet", dependencies=secured)
    def check_snippet(job_id: str, finding_id: str) -> Response:
        """PNG области страницы с цитатой (PDF) или JSON с абзацами (Markdown, TXT)."""
        job = finished(job_id)
        snip = job.snippets.get(finding_id)
        if snip is None:
            raise HTTPException(404, "Фрагмента для этой находки нет")
        if isinstance(snip, bytes):
            return Response(snip, media_type="image/png", headers={"Cache-Control": "private"})
        return JSONResponse(snip)

    @app.delete("/api/v1/checks/{job_id}", status_code=204, dependencies=secured)
    def delete_check(job_id: str) -> Response:
        if not store.delete(job_id):
            raise HTTPException(404, "Проверка не найдена")
        return Response(status_code=204)

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    # Интерфейс: те же файлы, что открываются с диска без сервера (docs/05-design.md, §9).
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app
