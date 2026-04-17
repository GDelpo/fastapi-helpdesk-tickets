"""Employee portal routes — /portal/*"""
from datetime import date
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

portal_router = APIRouter(tags=["Portal"], include_in_schema=False)

_MIME = {
    ".js": "application/javascript",
    ".css": "text/css",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
}

_PORTAL_NAV_LEFT = [
    {"href_suffix": "/portal/", "label": "Inicio", "key": "inicio", "icon": "house", "fa_icon": "fa-solid fa-house"},
    {"href_suffix": "/portal/categories/", "label": "Categorías", "key": "categories", "icon": "layout-grid", "fa_icon": "fa-solid fa-table-cells-large"},
]
_PORTAL_NAV_CTA = {"href_suffix": "/portal/new", "label": "Abrir Ticket", "key": "new", "icon": "plus-circle", "fa_icon": "fa-solid fa-circle-plus"}
_PORTAL_NAV_RIGHT = [
    {"href_suffix": "/portal/tickets/", "label": "Mis Tickets", "key": "my", "icon": "ticket", "fa_icon": "fa-solid fa-list-check"},
]


@portal_router.get("/static/{path:path}")
async def portal_static(path: str):
    file = _STATIC_DIR / path
    if not file.is_file() or not file.resolve().is_relative_to(_STATIC_DIR.resolve()):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(file, media_type=_MIME.get(file.suffix, "application/octet-stream"))


def _base(request: Request) -> str:
    return (request.scope.get("root_path") or "").rstrip("/")


def _portal_ctx(request: Request, *, page_title: str, active_key: str, **extra) -> dict:
    base = _base(request)
    nav_left = [
        {**item, "href": base + item["href_suffix"], "active": item["key"] == active_key}
        for item in _PORTAL_NAV_LEFT
    ]
    nav_right = [
        {**item, "href": base + item["href_suffix"], "active": item["key"] == active_key}
        for item in _PORTAL_NAV_RIGHT
    ]
    nav_cta = {
        **_PORTAL_NAV_CTA,
        "href": base + _PORTAL_NAV_CTA["href_suffix"],
        "active": _PORTAL_NAV_CTA["key"] == active_key,
    }
    return {
        "request": request, "base": base,
        "service_name": settings.service_name,
        "portal_navbar_title": settings.portal_navbar_title,
        "company_name": settings.company_name,
        "company_website": settings.company_website,
        "company_logo_url": settings.company_logo_url,
        "company_favicon_url": settings.company_favicon_url,
        "support_email": settings.support_email,
        "company_legal_tagline": settings.company_legal_tagline,
        "current_year": date.today().year,
        "nav_left": nav_left,
        "nav_cta": nav_cta,
        "nav_right": nav_right,
        "active_key": active_key,
        "page_title": page_title,
        "layout": "portal",
        "meta": _build_meta(),
        **extra,
    }


@portal_router.get("/login", response_class=HTMLResponse)
async def portal_login(request: Request):
    base = _base(request)
    return templates.TemplateResponse("portal/login.html", {
        "request": request, "base": base,
        "service_name": settings.service_name,
        "portal_navbar_title": settings.portal_navbar_title,
        "company_name": settings.company_name,
        "company_website": settings.company_website,
        "company_logo_url": settings.company_logo_url,
        "company_favicon_url": settings.company_favicon_url,
        "support_email": settings.support_email,
        "identity_me_url": base + "/api/v1/me",
        "meta": _build_meta(),
    })


@portal_router.get("/", response_class=HTMLResponse)
async def portal_home(request: Request):
    return templates.TemplateResponse(
        "portal/inicio.html",
        _portal_ctx(request, page_title="Inicio", active_key="inicio"),
    )


@portal_router.get("/categories/", response_class=HTMLResponse)
async def portal_categories(request: Request):
    return templates.TemplateResponse(
        "portal/categories.html",
        _portal_ctx(request, page_title="Categorías", active_key="categories"),
    )


@portal_router.get("/categories/{queue_id}/", response_class=HTMLResponse)
async def portal_category_tickets(request: Request, queue_id: int):
    return templates.TemplateResponse(
        "portal/category_tickets.html",
        _portal_ctx(request, page_title="Categoría", active_key="categories", queue_id=queue_id),
    )


@portal_router.get("/tickets/", response_class=HTMLResponse)
async def portal_tickets(request: Request):
    return templates.TemplateResponse(
        "portal/my_tickets.html",
        _portal_ctx(request, page_title="Mis Tickets", active_key="my"),
    )


@portal_router.get("/new", response_class=HTMLResponse)
async def portal_new_ticket(request: Request):
    return templates.TemplateResponse(
        "portal/new_ticket.html",
        _portal_ctx(
            request,
            page_title="Abrir Ticket",
            active_key="new",
            today=date.today().isoformat(),
        ),
    )


@portal_router.get("/tickets/{ticket_id}", response_class=HTMLResponse)
async def portal_ticket_detail(request: Request, ticket_id: str):
    return templates.TemplateResponse(
        "portal/ticket_detail.html",
        _portal_ctx(request, page_title="Ticket", active_key="my", ticket_id=ticket_id),
    )
