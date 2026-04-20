"""Admin dashboard routes — /dashboard/*"""
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.logger import get_logger
from app.models import _build_meta

logger = get_logger(__name__)

_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _DIR / "static"
templates = Jinja2Templates(directory=str(_DIR / "templates"))

router = APIRouter(tags=["UI"], include_in_schema=False)

_MIME = {
    ".js": "application/javascript",
    ".css": "text/css",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
}

_ADMIN_NAV = [
    {"href_suffix": "/dashboard/", "icon": "layout-dashboard", "label": "Overview", "key": "overview"},
    {"href_suffix": "/dashboard/tickets/", "icon": "ticket", "label": "All tickets", "key": "tickets"},
    {"href_suffix": "/dashboard/queues/", "icon": "layers", "label": "Categories", "key": "queues"},
]


@router.get("/static/{path:path}")
async def admin_static(path: str):
    file = _STATIC_DIR / path
    if not file.is_file() or not file.resolve().is_relative_to(_STATIC_DIR.resolve()):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(file, media_type=_MIME.get(file.suffix, "application/octet-stream"))


def _base(request: Request) -> str:
    return (request.scope.get("root_path") or "").rstrip("/")


def _admin_ctx(request: Request, *, page_title: str, active_key: str, **extra) -> dict:
    base = _base(request)
    nav_items = [
        {**item, "href": base + item["href_suffix"], "active": item["key"] == active_key}
        for item in _ADMIN_NAV
    ]
    return {
        "request": request, "base": base,
        "service_name": settings.service_name,
        "service_icon": "ticket",
        "company_name": settings.company_name,
        "company_website": settings.company_website,
        "company_logo_url": settings.company_logo_url,
        "company_favicon_url": settings.company_favicon_url,
        "support_email": settings.support_email,
        "nav_items": nav_items,
        "page_title": page_title,
        "layout": "admin",
        "meta": _build_meta(),
        **extra,
    }


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    base = _base(request)
    return templates.TemplateResponse("admin/login.html", {
        "request": request, "base": base,
        "service_name": settings.service_name,
        "service_icon": "ticket",
        "company_name": settings.company_name,
        "company_website": settings.company_website,
        "company_logo_url": settings.company_logo_url,
        "company_favicon_url": settings.company_favicon_url,
        "support_email": settings.support_email,
        "identity_me_url": base + "/api/v1/me",
        "meta": _build_meta(),
    })


@router.get("/", response_class=HTMLResponse)
async def dashboard_overview(request: Request):
    return templates.TemplateResponse(
        "admin/overview.html",
        _admin_ctx(request, page_title="Overview", active_key="overview"),
    )


@router.get("/tickets/", response_class=HTMLResponse)
async def dashboard_tickets(request: Request):
    return templates.TemplateResponse(
        "admin/tickets.html",
        _admin_ctx(request, page_title="All tickets", active_key="tickets"),
    )


@router.get("/tickets/{ticket_id}", response_class=HTMLResponse)
async def dashboard_ticket_detail(request: Request, ticket_id: str):
    return templates.TemplateResponse(
        "admin/ticket_detail.html",
        _admin_ctx(request, page_title="Ticket", active_key="tickets", ticket_id=ticket_id),
    )


@router.get("/queues/", response_class=HTMLResponse)
async def dashboard_queues(request: Request):
    return templates.TemplateResponse(
        "admin/queues.html",
        _admin_ctx(request, page_title="Categories", active_key="queues"),
    )
