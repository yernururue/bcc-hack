"""HTTP API на фейковом LLM: загрузка → этапы → отчёт, ошибки входа, токен. Сеть не нужна."""

import json
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_pipeline import DOC, FakeLLM

from reqcheck import pipeline
from reqcheck.schemas import Report
from reqcheck.server.app import create_app
from reqcheck.settings import Settings


def make_client(token: str | None = None, key: bool = True, **kwargs) -> TestClient:
    values: dict = {"LLM_MAX_COST_USD": 0.5}
    if token:
        values["REQCHECK_API_TOKEN"] = token
    settings = Settings(_env_file=None, **values)  # type: ignore[call-arg]
    factory = (lambda _opts: FakeLLM()) if key else None
    return TestClient(create_app(settings, llm_factory=factory, **kwargs))


def post(client: TestClient, name: str = "req.md", data: bytes | None = None, **form):
    payload = DOC.encode() if data is None else data
    return client.post("/api/v1/checks", files={"file": (name, payload)}, data=form)


def wait_done(client: TestClient, job_id: str, timeout: float = 20) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        info = client.get(f"/api/v1/checks/{job_id}").json()
        if info["status"] in ("done", "failed"):
            return info
        time.sleep(0.05)
    raise AssertionError("проверка не завершилась")


def test_healthz_models_and_schema() -> None:
    c = make_client()
    assert c.get("/api/v1/healthz").json()["status"] == "ok"
    models = c.get("/api/v1/models").json()
    assert {"id", "tier", "price_in", "price_out", "default"} <= set(models[0])
    assert sum(m["default"] for m in models) <= 1
    assert "verdict" in c.get("/api/v1/schema/report").json()["properties"]
    assert c.get("/api/v1/openapi.json").status_code == 200


def test_full_run_report_formats_and_summary() -> None:
    c = make_client()
    r = post(c, model="fake/model")
    assert r.status_code == 202
    job_id = r.json()["id"]
    info = wait_done(c, job_id)
    assert info["status"] == "done" and info["progress"] == 1.0
    assert info["stages"] == list(pipeline.STAGES)

    rep = c.get(f"/api/v1/checks/{job_id}/report?format=json")
    assert rep.status_code == 200
    report = Report.model_validate(rep.json())
    assert report.run.document.name == "req.md"  # имя из загрузки, а не безопасное имя на диске

    md = c.get(f"/api/v1/checks/{job_id}/report")  # по умолчанию формат входа
    assert md.headers["content-type"].startswith("text/markdown")
    assert "## 7. Итог" in md.text
    assert "filename*=UTF-8''req-review.md" in md.headers["content-disposition"]

    pdf = c.get(f"/api/v1/checks/{job_id}/report?format=pdf")  # другой формат — рендер по запросу
    assert pdf.content.startswith(b"%PDF-")

    view = c.get(f"/api/v1/checks/{job_id}/summary").json()
    assert view["headline"].startswith("Нельзя начинать: ")
    assert view["stats"]["requirements"] == 3
    assert view["fixes"] and view["fixes"][0]["quotes"]
    assert view["fixes"][0]["id"] == report.verdict.top_fixes[0]

    assert c.delete(f"/api/v1/checks/{job_id}").status_code == 204
    assert c.get(f"/api/v1/checks/{job_id}").status_code == 404


def test_events_stream_stages_in_order_then_done() -> None:
    c = make_client()
    job_id = post(c).json()["id"]
    wait_done(c, job_id)
    with c.stream("GET", f"/api/v1/checks/{job_id}/events") as resp:
        body = "".join(resp.iter_text())
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    stages = [e for e in events if e["type"] == "stage"]
    assert [e["stage"] for e in stages if e["state"] == "done"] == list(pipeline.STAGES)
    assert all(e["index"] == pipeline.STAGES.index(e["stage"]) for e in stages)
    assert events[-1] == {"type": "done"}
    assert "event: stage" in body and "event: done" in body


def test_input_errors() -> None:
    c = make_client()
    bad_ext = post(c, name="a.exe", data=b"x")
    assert bad_ext.status_code == 400 and "Неподдерживаемый формат" in bad_ext.json()["detail"]
    fake_pdf = post(c, name="a.pdf", data=b"not a pdf")
    assert fake_pdf.status_code == 400 and "не является PDF" in fake_pdf.json()["detail"]
    empty = post(c, name="a.md", data=b"")
    assert empty.status_code == 400
    assert post(c, max_cost_usd="0").status_code == 400
    assert c.get("/api/v1/checks/nope").status_code == 404
    assert c.get("/api/v1/checks/nope/events").status_code == 404


def test_report_before_done_and_failed_job() -> None:
    # Документ без текста: конвейер падает на ingest с понятной ошибкой
    c = make_client()
    job_id = post(c, name="only.md", data=b"# \n").json()["id"]
    info = wait_done(c, job_id)
    assert info["status"] == "failed" and info["error"]["message"]
    with c.stream("GET", f"/api/v1/checks/{job_id}/events") as resp:
        body = "".join(resp.iter_text())
    assert '"stage": "ingest", "index": 0, "state": "error"' in body  # этап помечен ошибкой
    assert "event: error" in body
    assert c.get(f"/api/v1/checks/{job_id}/report").status_code == 409


def test_cost_is_capped_by_server_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[pipeline.Options] = []
    c = make_client()
    original = pipeline.run

    def spy(source, target, options, *args, **kwargs):
        seen.append(options)
        return original(source, target, options, *args, **kwargs)

    monkeypatch.setattr(pipeline, "run", spy)
    wait_done(c, post(c, max_cost_usd="99").json()["id"])
    assert seen[0].max_cost_usd == 0.5  # потолок из настроек сервера


def test_no_llm_key_gives_503() -> None:
    c = make_client(key=False)
    r = post(c)
    assert r.status_code == 503 and "LLM_API_KEY" in r.json()["detail"]


def test_bearer_token_required_for_api_but_not_ui() -> None:
    c = make_client(token="s3cret")
    assert c.get("/api/v1/models").status_code == 401
    assert c.get("/api/v1/models", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = c.get("/api/v1/models", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
    assert c.get("/api/v1/healthz").status_code == 200  # живость без токена
    assert c.get("/").status_code == 200


def test_uploads_are_removed_after_run(monkeypatch: pytest.MonkeyPatch) -> None:
    import tempfile

    created: list[Path] = []
    original = tempfile.mkdtemp

    def spy(*a, **kw):
        path = original(*a, **kw)
        created.append(Path(path))
        return path

    monkeypatch.setattr(tempfile, "mkdtemp", spy)
    c = make_client()
    wait_done(c, post(c).json()["id"])
    time.sleep(0.1)
    assert created and not any(p.exists() for p in created)


def test_ui_is_served_with_local_assets_only() -> None:
    c = make_client()
    html = c.get("/").text
    assert "<title>ReqCheck" in html
    assert not re.search(r'(?:src|href)="https?://', html)  # внешних загрузок нет
    assert c.get("/app.js").status_code == 200
    assert c.get("/fonts.css").status_code == 200


def test_upload_name_cannot_escape_workdir() -> None:
    c = make_client()
    job_id = post(c, name="../../evil.md").json()["id"]
    info = wait_done(c, job_id)
    assert info["status"] == "done" and info["filename"] == "evil.md"
