"""SSA-015 read-only dashboard: protected queue, history and case evidence."""

import secrets
from pathlib import Path
from uuid import UUID
from typing import Literal

import psycopg
from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from backend.database.dashboard import DashboardRepository
from backend.services.dashboard_preview import PreviewDashboardRepository
from backend.services.dashboard_display import business_time, case_view, order_items, percentage, reason_text
from backend.services.dashboard_review import ReviewService, ReviewConflict, ReviewNotFound
from backend.connectors.orders import OrderExecutionError

basic = HTTPBasic(auto_error=False)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
templates.env.filters.update(business_time=business_time, percentage=percentage,
                             reason_text=reason_text, order_items=order_items)


def require_dashboard_password(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(basic),
) -> None:
    expected = request.app.state.settings.dashboard_password
    if not expected:
        raise HTTPException(503, "Dashboard password is not configured.")
    if credentials is None or not secrets.compare_digest(
        credentials.password.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            401, "Dashboard password required.",
            headers={"WWW-Authenticate": 'Basic realm="Staff dashboard"'},
        )


router = APIRouter(
    prefix="/dashboard", dependencies=[Depends(require_dashboard_password)],
    include_in_schema=False,
)


def get_repository(request: Request):
    settings = request.app.state.settings
    if settings.dashboard_preview:
        return PreviewDashboardRepository()
    return DashboardRepository(settings, getattr(request.app.state, "connection_factory", None))


def get_review_service(request: Request):
    return ReviewService(request.app.state.settings, getattr(request.app.state, "connection_factory", None))


class DecisionInput(BaseModel):
    decision: Literal["approve", "deny"]
    note: str = Field(default="", max_length=2000)


def render(request, name, context, status_code=200):
    context = {
        "preview": request.app.state.settings.dashboard_preview,
        "providers": {"chat": "Not recorded", "retrieval": "See case", "jev": "See case"},
        **context,
    }
    return templates.TemplateResponse(
        request=request, name=name, context=context, status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, repository=Depends(get_repository)):
    try:
        rows = repository.queue()
        recent = repository.recent()
    except psycopg.Error:
        return render(request, "dashboard.html", {
            "rows": [], "calls": [], "orders": [],
            "error": "Database unavailable. Reviews could not be loaded. Check the database connection and migrations.",
        }, 503)
    return render(request, "dashboard.html", {"rows": rows, **recent, "error": None})


@router.get("/queue", response_class=HTMLResponse)
def queue(request: Request, repository=Depends(get_repository)):
    try:
        rows = repository.queue()
    except psycopg.Error:
        return render(request, "queue.html", {
            "rows": [], "error": "Queue unavailable. Retrying automatically; do not assume there are no pending reviews.",
        }, 503)
    return render(request, "queue.html", {"rows": rows, "error": None})


@router.get("/cases/{action_id}", response_class=HTMLResponse)
def case_detail(request: Request, action_id: UUID, repository=Depends(get_repository)):
    try:
        case = repository.case(action_id)
    except psycopg.Error:
        return render(request, "error.html", {
            "message": "Database unavailable. Case evidence could not be loaded.",
        }, 503)
    if case is None:
        return render(request, "error.html", {"message": "Case not found."}, 404)
    case = case_view(case)
    return render(request, "case.html", {"case": case, "providers": case["providers"]})


@router.post("/cases/{action_id}/decision")
def decide(request: Request, action_id: UUID, body: DecisionInput,
           service=Depends(get_review_service)):
    if request.app.state.settings.dashboard_preview:
        raise HTTPException(409, "Preview is read only. No decision was saved.")
    try:
        service.decide(action_id, body.decision, body.note)
    except ReviewNotFound:
        raise HTTPException(404, "Review not found.")
    except ReviewConflict:
        raise HTTPException(409, "This review has changed or was already decided. Reload the case.")
    except OrderExecutionError:
        raise HTTPException(409, "The action cannot be executed. Reload the case to check its state.")
    except psycopg.Error:
        raise HTTPException(503, "The result could not be confirmed. Reload the case before trying again.")
    return {"redirect": f"/dashboard/cases/{action_id}"}
