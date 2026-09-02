from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from visa_agent.config import Settings
from visa_agent.domain.models import Case
from visa_agent.domain.policy import load_policy
from visa_agent.domain.rules import evaluate_gate
from visa_agent.review_ui import render_empty_page, render_page
from visa_agent.storage.sqlite import SQLiteStore

settings = Settings.from_env()
policy = load_policy(settings.policy_path)


app = FastAPI(title="UK Visa Agent Review Console")


def load_cases() -> list[Case]:
    request_store = SQLiteStore(settings.database_path)
    try:
        return request_store.list_cases()
    finally:
        request_store.close()


def load_case(case_id: str) -> Case | None:
    request_store = SQLiteStore(settings.database_path)
    try:
        return request_store.get_case(case_id)
    finally:
        request_store.close()


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    cases = load_cases()
    if not cases:
        return HTMLResponse(render_empty_page())
    case = cases[0]
    gate = evaluate_gate(case, policy, date.today())
    return HTMLResponse(render_page(case, gate))


@app.get("/api/cases")
def list_cases() -> list[dict[str, object]]:
    return [case.model_dump(mode="json") for case in load_cases()]


@app.get("/api/cases/{case_id}")
def get_case(case_id: str) -> dict[str, object]:
    case = load_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case.model_dump(mode="json")


@app.get("/api/cases/{case_id}/pack")
def get_pack(case_id: str) -> FileResponse:
    case = load_case(case_id)
    if case is None or case.delivery_path is None:
        raise HTTPException(status_code=404, detail="Review pack is not available")
    path = Path(case.delivery_path).resolve()
    allowed_root = settings.output_dir.resolve()
    if allowed_root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Review pack is not available")
    return FileResponse(path, filename=path.name, media_type="application/zip")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "policy_version": policy.version}
