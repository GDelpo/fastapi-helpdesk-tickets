"""FastAPI dependencies for authentication, pagination, and repositories."""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import Depends, Query, Request
from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings
from app.database import get_session
from app.exceptions import AuthenticationError, AuthorizationError, ExternalServiceError
from app.logger import get_logger
from app.repository import NotificationRepository, QueueRepository, TicketRepository
from app.schemas import TicketFilterParams, TokenData
from app.service import TicketService

logger = get_logger(__name__)

from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=settings.identity_login_url)


# =============================================================================
# HTTP client
# =============================================================================

def get_http_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client


# =============================================================================
# Authentication
# =============================================================================

async def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
) -> TokenData:
    """Validate JWT with identidad /me, return user data."""
    client: httpx.AsyncClient = request.app.state.http_client
    try:
        response = await client.get(
            settings.identity_me_url,
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 200:
            data = response.json()
            return TokenData(
                id=str(data["id"]),
                user_name=data["userName"],
                mail=data.get("mail"),
                role=data["role"],
            )
        raise AuthenticationError("Invalid or expired token")
    except httpx.ConnectTimeout:
        raise ExternalServiceError("Identity", "Connection timeout")
    except httpx.ConnectError:
        raise ExternalServiceError("Identity", "Service unavailable")
    except AuthenticationError:
        raise
    except Exception as e:
        logger.error("Identity service error: %s", e)
        raise AuthenticationError(str(e))


async def admin_required(
    current_user: TokenData = Depends(get_current_user),
) -> TokenData:
    """Require admin or service role."""
    if current_user.role not in ("admin", "service"):
        raise AuthorizationError("Administrator permissions required")
    return current_user


CurrentUser = Annotated[TokenData, Depends(get_current_user)]
AdminUser = Annotated[TokenData, Depends(admin_required)]


# =============================================================================
# Repositories and Services
# =============================================================================

async def get_ticket_repository(
    session: AsyncSession = Depends(get_session),
) -> TicketRepository:
    return TicketRepository(session=session)


async def get_queue_repository(
    session: AsyncSession = Depends(get_session),
) -> QueueRepository:
    return QueueRepository(session=session)


async def get_notification_repository(
    session: AsyncSession = Depends(get_session),
) -> NotificationRepository:
    return NotificationRepository(session=session)


async def get_ticket_service(
    ticket_repo: TicketRepository = Depends(get_ticket_repository),
    queue_repo: QueueRepository = Depends(get_queue_repository),
    notif_repo: NotificationRepository = Depends(get_notification_repository),
) -> TicketService:
    return TicketService(ticket_repo=ticket_repo, queue_repo=queue_repo, notif_repo=notif_repo)


# =============================================================================
# Pagination
# =============================================================================

class PaginationParams(BaseModel):
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=200)

    @property
    def page(self) -> int:
        return (self.skip // self.limit) + 1 if self.limit > 0 else 1


def get_pagination_params(
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> PaginationParams:
    return PaginationParams(skip=skip, limit=limit)


# =============================================================================
# Ticket filter params
# =============================================================================

def get_ticket_filters(
    queue_id: Annotated[int | None, Query(description="Filter by category")] = None,
    status: Annotated[str | None, Query(description="open|in_progress|pending|reopened|resolved|closed")] = None,
    priority: Annotated[int | None, Query(ge=1, le=5, description="1=Critical … 5=Very low")] = None,
    assigned_to: Annotated[str | None, Query(description="Username of the assigned agent")] = None,
    search: Annotated[str | None, Query(description="Search in title or by UUID prefix")] = None,
    include_closed: Annotated[bool, Query(description="Include closed tickets")] = False,
) -> TicketFilterParams:
    return TicketFilterParams(
        queue_id=queue_id,
        status=status,
        priority=priority,
        assigned_to=assigned_to,
        search=search.strip() if search else None,
        include_closed=include_closed,
    )
