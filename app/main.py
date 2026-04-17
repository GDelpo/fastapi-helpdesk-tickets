"""Tickets Service — Main application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import select

from app.api import router as api_router
from app.config import settings
from app.ui.routes import portal_router, router as dashboard_router
from app.database import async_session_maker
from app.exceptions import (
    ServiceException,
    general_exception_handler,
    service_exception_handler,
    sqlalchemy_exception_handler,
    validation_exception_handler,
)
from app.logger import configure_logging, get_logger
from app.middleware import ProxyHeadersMiddleware, RequestLoggingMiddleware
from app.schemas import DependencyStatus, HealthResponse, ServiceStatus

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app_instance: FastAPI) -> AsyncIterator[None]:
    logger.info(
        "Starting Tickets Service",
        extra={"extra_fields": {"version": settings.project_version, "environment": settings.environment}},
    )

    # Shared HTTP client para llamadas a identidad y mailsender
    app_instance.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(10.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        follow_redirects=True,
    )
    logger.info("HTTP client initialized")

    # Preload users cache for mentions/autocomplete
    from app.auth_service import IdentityServiceClient
    identity_client = IdentityServiceClient(base_url=settings.identity_service_url)
    async def load_users_cache():
        client = app_instance.state.http_client
        service_token = await identity_client.get_service_token(client)
        if not service_token:
            logger.error("No service token for preload users cache")
            return []
        try:
            params = {"role": "employee", "is_active": "true", "sort_by": "user_name:asc", "limit": "100"}
            resp = await client.get(
                settings.identity_users_url,
                params=params,
                headers={"Authorization": f"Bearer {service_token}"},
                timeout=5.0,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                logger.info(f"Preloaded {len(data)} users for mention cache")
                return [
                    {"userName": u["userName"], "mail": u.get("mail"), "displayName": u["userName"]}
                    for u in data if u.get("role") != "service"
                ]
            logger.warning(f"Identity preload users failed: {resp.status_code} {resp.text[:200]}")
            return []
        except Exception as e:
            logger.error(f"Identity preload users error: {e}")
            return []


    import asyncio
    app_instance.state.users_cache = await load_users_cache()
    if app_instance.state.users_cache:
        logger.info(f"Users cache loaded: {len(app_instance.state.users_cache)} users")
    else:
        logger.warning("Users cache is EMPTY after preload")

    async def users_cache_refresher():
        while True:
            await asyncio.sleep(5 * 60 * 60)  # 5 horas
            logger.info("Refreshing users cache from Identidad...")
            app_instance.state.users_cache = await load_users_cache()
            logger.info(f"Users cache refreshed: {len(app_instance.state.users_cache)} users")

    app_instance.state._users_cache_task = asyncio.create_task(users_cache_refresher())

    async def reminder_task():
        while True:
            await asyncio.sleep(settings.reminder_check_interval_hours * 3600)
            if not settings.reminder_enabled:
                continue
            try:
                from app.database import async_session_maker
                from app.reminders import check_and_send_reminders
                from app.notifications import NotificationDispatcher
                svc_token = await identity_client.get_service_token(app_instance.state.http_client)
                dispatcher = NotificationDispatcher(
                    http_client=app_instance.state.http_client,
                    service_token=svc_token or '',
                    users_cache=app_instance.state.users_cache or [],
                )
                async with async_session_maker() as session:
                    result = await check_and_send_reminders(session, dispatcher)
                logger.info("Reminders sent", extra={"extra_fields": result})
            except Exception as e:
                logger.error("Reminder task error: %s", e)

    app_instance.state._reminder_task = asyncio.create_task(reminder_task())

    yield

    logger.info("Shutting down Tickets Service")
    app_instance.state._users_cache_task.cancel()
    app_instance.state._reminder_task.cancel()
    await app_instance.state.http_client.aclose()


# =============================================================================
# Application Factory
# =============================================================================

app = FastAPI(
    title=settings.project_name,
    version=settings.project_version,
    description=settings.project_description,
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/openapi.json",
    debug=settings.debug,
    lifespan=lifespan,
)

# =============================================================================
# Middleware
# =============================================================================

app.add_middleware(ProxyHeadersMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# =============================================================================
# Exception Handlers
# =============================================================================

app.add_exception_handler(ServiceException, service_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(PydanticValidationError, validation_exception_handler)
app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

# =============================================================================
# Routers
# =============================================================================

app.include_router(api_router, prefix=settings.api_prefix)
app.include_router(dashboard_router, prefix="/dashboard")
app.include_router(portal_router, prefix="/portal")


# =============================================================================
# Health Check
# =============================================================================

@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check(request: Request) -> HealthResponse:
    from fastapi.responses import JSONResponse

    dependencies: dict[str, DependencyStatus] = {}

    # Check PostgreSQL (CRITICAL)
    try:
        async with async_session_maker() as session:
            await session.execute(select(1))
        dependencies["postgresql"] = DependencyStatus(status=ServiceStatus.HEALTHY, latency_ms=0)
    except Exception as e:
        dependencies["postgresql"] = DependencyStatus(status=ServiceStatus.UNHEALTHY, error=str(e))

    # Check identidad reachability (OPTIONAL)
    try:
        client: httpx.AsyncClient = request.app.state.http_client
        resp = await client.get(f"{settings.identity_service_url.replace('/api/v1', '')}/health", timeout=3.0)
        if resp.status_code == 200:
            dependencies["identity"] = DependencyStatus(status=ServiceStatus.HEALTHY, latency_ms=0)
        else:
            dependencies["identity"] = DependencyStatus(status=ServiceStatus.DEGRADED, error=f"HTTP {resp.status_code}")
    except Exception as e:
        dependencies["identity"] = DependencyStatus(status=ServiceStatus.DEGRADED, error=str(e))

    critical = [dependencies["postgresql"].status]
    if ServiceStatus.UNHEALTHY in critical:
        overall = ServiceStatus.UNHEALTHY
    elif any(d.status == ServiceStatus.DEGRADED for d in dependencies.values()):
        overall = ServiceStatus.DEGRADED
    else:
        overall = ServiceStatus.HEALTHY

    response_data = HealthResponse(
        status=overall,
        version=settings.project_version,
        environment=settings.environment,
        dependencies=dependencies,
    )

    if overall == ServiceStatus.UNHEALTHY:
        return JSONResponse(status_code=503, content=response_data.model_dump(by_alias=True))

    return response_data


@app.get("/", tags=["System"])
async def root():
    return {
        "service": settings.project_name,
        "version": settings.project_version,
        "docs": "/docs",
        "health": "/health",
    }


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=settings.debug)
