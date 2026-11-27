"""Задания проверки в памяти процесса: очередь, события этапов, результат.

На диске остаётся только временная загрузка, и она удаляется сразу после прогона
(docs/02-trd.md, §8). Отчёт хранится в памяти до DELETE или до истечения срока.
"""

import logging
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from reqcheck import pipeline
from reqcheck.errors import ReqCheckError
from reqcheck.formats import InputFile, resolve_output
from reqcheck.llm.base import LLMClient
from reqcheck.schemas import Report
from reqcheck.settings import Settings

log = logging.getLogger(__name__)

Status = Literal["queued", "running", "done", "failed"]
TERMINAL: tuple[Status, ...] = ("done", "failed")
MAX_WORKERS = 2
JOB_TTL_SECONDS = 3600


@dataclass
class Job:
    id: str
    filename: str
    input_format: str
    model: str
    created: float = field(default_factory=time.time)
    status: Status = "queued"
    stage: str | None = None
    stage_index: int | None = None
    stage_state: str | None = None
    progress: float = 0.0
    events: list[dict] = field(default_factory=list)
    error: dict | None = None
    report: Report | None = None
    native: bytes | None = None  # отчёт в формате входа, как его записал конвейер
    native_format: str | None = None
    # Готовятся в конце прогона, пока загрузка ещё на диске (потом её удаляют):
    annotated: bytes | None = None  # исходный PDF с пометками
    snippets: dict[str, bytes | dict] = field(default_factory=dict)  # F-ID → PNG или абзацы
    cond: threading.Condition = field(default_factory=threading.Condition)
    workdir: Path | None = None

    def emit(self, event: dict) -> None:
        with self.cond:
            self.events.append(event)
            self.cond.notify_all()

    def finish(self, status: Status, event: dict) -> None:
        with self.cond:
            self.status = status
            self.events.append(event)
            self.cond.notify_all()

    def public(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "filename": self.filename,
            "model": self.model,
            "stage": self.stage,
            "stage_index": self.stage_index,
            "stages": list(pipeline.STAGES),
            "progress": round(self.progress, 3),
            "error": self.error,
        }


LLMFactory = Callable[[pipeline.Options], LLMClient]


class JobStore:
    def __init__(self, settings: Settings, llm_factory: LLMFactory | None = None) -> None:
        self._settings = settings
        self._llm_factory = llm_factory
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="check")

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def delete(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if job is None:
            return False
        if job.workdir is not None:
            shutil.rmtree(job.workdir, ignore_errors=True)
        return True

    def purge(self, ttl: float = JOB_TTL_SECONDS) -> None:
        now = time.time()
        with self._lock:
            old = [
                j.id for j in self._jobs.values() if j.status in TERMINAL and now - j.created > ttl
            ]
        for job_id in old:
            self.delete(job_id)

    def submit(
        self, workdir: Path, source: InputFile, filename: str, options: pipeline.Options
    ) -> Job:
        self.purge()
        job = Job(
            id=uuid.uuid4().hex[:16],
            filename=filename,
            input_format=source.format,
            model=options.model,
            workdir=workdir,
        )
        with self._lock:
            self._jobs[job.id] = job
        self._pool.submit(self._run, job, source, options)
        return job

    def _run(self, job: Job, source: InputFile, options: pipeline.Options) -> None:
        workdir = job.workdir
        assert workdir is not None
        job.status = "running"

        def on_stage(stage: str, state: str, note: str | None) -> None:
            index = pipeline.STAGES.index(stage)
            job.stage, job.stage_index, job.stage_state = stage, index, state
            if state in ("done", "error"):
                job.progress = (index + 1) / len(pipeline.STAGES)
            job.emit(
                {"type": "stage", "stage": stage, "index": index, "state": state, "note": note}
            )

        try:
            target = resolve_output(source.format, workdir / f"report.{_native_ext(source.format)}")
            llm = self._llm_factory(options) if self._llm_factory else None
            outcome = pipeline.run(
                source, target, options, self._settings, on_stage=on_stage, llm=llm
            )
            assert outcome.report is not None
            job.report = outcome.report
            job.native = target.path.read_bytes()
            job.native_format = target.format
            _prepare_source_views(job, source, outcome.report)
            job.progress = 1.0
            job.finish("done", {"type": "done"})
        except ReqCheckError as exc:
            _mark_stage_failed(job, on_stage, exc.message)
            job.error = {"code": int(exc.exit_code), "message": exc.message}
            job.finish("failed", {"type": "error", **job.error})
        except Exception:  # непредвиденное: подробности в лог, пользователю общий текст
            log.exception("check %s failed", job.id)
            _mark_stage_failed(job, on_stage, "внутренняя ошибка")
            job.error = {"code": 1, "message": "Внутренняя ошибка сервера. Подробности в логе."}
            job.finish("failed", {"type": "error", **job.error})
        finally:
            # Загрузка и промежуточные файлы больше не нужны: отчёт уже в памяти.
            shutil.rmtree(workdir, ignore_errors=True)
            job.workdir = None


def _prepare_source_views(job: Job, source: InputFile, report: Report) -> None:
    """Исходник с пометками и фрагменты оригинала (ADR-009). Ошибка здесь не портит отчёт."""
    from reqcheck.config import thresholds
    from reqcheck.ingest import ingest
    from reqcheck.render.annotate import annotate_pdf
    from reqcheck.render.snippet import pdf_snippets, text_snippets

    try:
        if source.format == "pdf":
            assert job.workdir is not None
            target = job.workdir / "annotated.pdf"
            annotate_pdf(source.path, report, target)
            job.annotated = target.read_bytes()
            job.snippets = dict(pdf_snippets(source.path, report.findings))
        else:
            doc = ingest(source)
            job.snippets = dict(
                text_snippets(doc, report.findings, thresholds().quote_min_similarity)
            )
    except Exception:
        log.warning("check %s: не удалось подготовить фрагменты оригинала", job.id, exc_info=True)


def _mark_stage_failed(job: Job, on_stage, message: str) -> None:
    """Этап, на котором оборвался прогон, не должен остаться «активным»."""
    if job.stage is not None and job.stage_state == "active":
        on_stage(job.stage, "error", message)


def _native_ext(fmt: str) -> str:
    return {"pdf": "pdf", "md": "md", "txt": "md", "docx": "docx"}[fmt]


def new_workdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="reqcheck-"))
