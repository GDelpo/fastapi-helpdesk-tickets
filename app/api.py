"""API endpoints for the Tickets Service."""

from __future__ import annotations

import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Path, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordRequestForm

from app.auth_service import IdentityServiceClient
from app.config import settings
from app.models import _build_meta
from app.dependencies import (
    AdminUser,
    CurrentUser,
    PaginationParams,
    get_notification_repository,
    get_pagination_params,
    get_queue_repository,
    get_ticket_filters,
    get_ticket_repository,
    get_ticket_service,
)
from app.exceptions import AuthenticationError, EntityNotFoundError
from app.logger import get_logger
from app.repository import NotificationRepository, QueueRepository, TicketRepository
from app.schemas import (
    AttachmentResponse,
    BulkStatusUpdate,
    FollowUpCreate,
    FollowUpResponse,
    IdentityUserSummary,
    LoginResponse,
    NotificationResponse,
    PaginatedResponse,
    PaginationMeta,
    QueueCreate,
    QueueResponse,
    QueueUpdate,
    StatsResponse,
    TicketCreate,
    TicketFilterParams,
    TicketRelationCreate,
    TicketRelationResponse,
    TicketResponse,
    TicketSummary,
    TicketUpdate,
)
from app.service import QueueService, TicketService
from app.notifications import NotificationDispatcher

router = APIRouter()
logger = get_logger(__name__)

identity_client = IdentityServiceClient(base_url=settings.identity_service_url)


async def _build_dispatcher(request: Request) -> NotificationDispatcher | None:
    """Build NotificationDispatcher for current request. Returns None on any failure."""
    try:
        client: httpx.AsyncClient = request.app.state.http_client
        svc_token = await identity_client.get_service_token(client)
        if not svc_token:
            return None
        return NotificationDispatcher(
            http_client=client,
            service_token=svc_token,
            users_cache=request.app.state.users_cache or [],
        )
    except Exception as e:
        logger.warning("Could not build NotificationDispatcher: %s", e)
        return None


# =============================================================================
# Auth
# =============================================================================

@router.post("/login", response_model=LoginResponse, tags=["Auth"])
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
):
    client: httpx.AsyncClient = request.app.state.http_client
    data = await identity_client.login(form_data.username, form_data.password, client)
    if not data:
        raise AuthenticationError("Invalid username or password")
    access_token = data.get("accessToken") or data.get("access_token")
    if not access_token:
        raise AuthenticationError("Invalid authentication response")
    return LoginResponse(
        access_token=access_token,
        token_type=data.get("tokenType") or data.get("token_type", "bearer"),
        expires_in=data.get("expiresIn") or data.get("expires_in", 1800),
    )


@router.get("/me", tags=["Auth"])
async def me(current_user: CurrentUser):
    return {
        "id": current_user.id,
        "userName": current_user.user_name,
        "mail": current_user.mail,
        "role": current_user.role,
    }


@router.get("/meta", tags=["Meta"])
async def get_meta():
    """Enum metadata — single source of truth for status and priority."""
    return _build_meta()


@router.get("/stats", response_model=StatsResponse, tags=["Stats"])
async def get_stats(
    _: AdminUser,
    ticket_repo: TicketRepository = Depends(get_ticket_repository),
):
    """Aggregate ticket counts by status — admin overview."""
    data = await ticket_repo.get_stats()
    return StatsResponse(**data)


@router.get("/my/stats", response_model=StatsResponse, tags=["Stats"])
async def get_my_stats(
    current_user: CurrentUser,
    ticket_repo: TicketRepository = Depends(get_ticket_repository),
):
    """Aggregate counts for tickets where the current user is submitter or watcher."""
    data = await ticket_repo.get_my_stats(current_user.user_name)
    return StatsResponse(**data)


# =============================================================================
# Identity proxy
# =============================================================================

@router.get("/identity/users", response_model=list[IdentityUserSummary], tags=["Identity"])
async def list_assignable_users(
    request: Request,
    current_user: AdminUser,
    role: Annotated[str, Query()] = "employee",
):
    client: httpx.AsyncClient = request.app.state.http_client
    users = await identity_client.list_users(client, role=role)
    return [
        IdentityUserSummary(
            id=str(u["id"]),
            user_name=u["userName"],
            mail=u.get("mail"),
            role=u["role"],
            is_active=u.get("isActive", True),
        )
        for u in users
    ]


# =============================================================================
# Queues
# =============================================================================

@router.get("/queues/", response_model=list[QueueResponse], tags=["Queues"])
async def list_queues(
    current_user: CurrentUser,
    queue_repo: QueueRepository = Depends(get_queue_repository),
):
    svc = QueueService(repo=queue_repo)
    queues = await svc.list_queues()
    return [QueueResponse.model_validate(q) for q in queues]


@router.post("/queues/", response_model=QueueResponse, status_code=status.HTTP_201_CREATED, tags=["Queues"])
async def create_queue(
    data: QueueCreate,
    current_user: AdminUser,
    queue_repo: QueueRepository = Depends(get_queue_repository),
):
    svc = QueueService(repo=queue_repo)
    q = await svc.create_queue(data, current_user)
    q.__dict__["ticket_count"] = 0
    return QueueResponse.model_validate(q)


@router.get("/queues/{queue_id}", response_model=QueueResponse, tags=["Queues"])
async def get_queue(
    queue_id: Annotated[int, Path(...)],
    current_user: CurrentUser,
    queue_repo: QueueRepository = Depends(get_queue_repository),
):
    svc = QueueService(repo=queue_repo)
    q = await svc.get_queue(queue_id)
    return QueueResponse.model_validate(q)


@router.patch("/queues/{queue_id}", response_model=QueueResponse, tags=["Queues"])
async def update_queue(
    queue_id: Annotated[int, Path(...)],
    data: QueueUpdate,
    current_user: AdminUser,
    queue_repo: QueueRepository = Depends(get_queue_repository),
):
    svc = QueueService(repo=queue_repo)
    q = await svc.update_queue(queue_id, data, current_user)
    return QueueResponse.model_validate(q)


@router.delete("/queues/{queue_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["Queues"])
async def delete_queue(
    queue_id: Annotated[int, Path(...)],
    current_user: AdminUser,
    queue_repo: QueueRepository = Depends(get_queue_repository),
):
    svc = QueueService(repo=queue_repo)
    await svc.delete_queue(queue_id, current_user)


# =============================================================================
# Tickets (staff)
# =============================================================================

@router.get("/tickets/", response_model=PaginatedResponse[TicketSummary], tags=["Tickets"])
async def list_tickets(
    current_user: CurrentUser,
    pagination: PaginationParams = Depends(get_pagination_params),
    filters: TicketFilterParams = Depends(get_ticket_filters),
    service: TicketService = Depends(get_ticket_service),
):
    tickets, total = await service.list_tickets(
        filters=filters, skip=pagination.skip, limit=pagination.limit,
        current_user=current_user,
    )
    meta = PaginationMeta.from_params(total, pagination.skip, pagination.limit)
    return PaginatedResponse(data=[TicketSummary.model_validate(t) for t in tickets], meta=meta)


@router.post("/tickets/", response_model=TicketResponse, status_code=status.HTTP_201_CREATED, tags=["Tickets"])
async def create_ticket(
    data: TicketCreate,
    request: Request,
    current_user: CurrentUser,
    service: TicketService = Depends(get_ticket_service),
):
    dispatcher = await _build_dispatcher(request)
    ticket = await service.create_ticket(data, current_user, dispatcher=dispatcher)
    return TicketResponse.model_validate(ticket)


@router.get("/tickets/{ticket_id}", response_model=TicketResponse, tags=["Tickets"])
async def get_ticket(
    ticket_id: Annotated[uuid.UUID, Path(...)],
    current_user: CurrentUser,
    service: TicketService = Depends(get_ticket_service),
):
    ticket = await service.get_ticket(ticket_id, current_user)
    return TicketResponse.model_validate(ticket)


@router.patch("/tickets/bulk", status_code=200, tags=["Tickets"])
async def bulk_update_tickets(
    payload: BulkStatusUpdate,
    current_user: AdminUser,
    request: Request,
    service: TicketService = Depends(get_ticket_service),
):
    """Bulk-update status for multiple tickets. Admin only. Creates audit FollowUps."""
    dispatcher = await _build_dispatcher(request)
    updated = 0
    for ticket_id in payload.ids:
        try:
            await service.update_ticket(
                ticket_id,
                TicketUpdate(status=payload.status),
                current_user,
                dispatcher=dispatcher,
            )
            updated += 1
        except Exception:
            pass
    return {"updated": updated}


@router.post("/admin/reminders/run", tags=["Admin"])
async def run_reminders_now(
    _: AdminUser,
    request: Request,
):
    """Manually trigger the reminder check. Admin only. Returns counts sent."""
    from app.database import async_session_maker
    from app.reminders import check_and_send_reminders
    client: httpx.AsyncClient = request.app.state.http_client
    svc_token = await identity_client.get_service_token(client)
    dispatcher = NotificationDispatcher(
        http_client=client,
        service_token=svc_token or '',
        users_cache=request.app.state.users_cache or [],
    )
    async with async_session_maker() as session:
        result = await check_and_send_reminders(session, dispatcher)
    return result


@router.patch("/tickets/{ticket_id}", response_model=TicketResponse, tags=["Tickets"])
async def update_ticket(
    ticket_id: Annotated[uuid.UUID, Path(...)],
    data: TicketUpdate,
    request: Request,
    current_user: CurrentUser,
    service: TicketService = Depends(get_ticket_service),
):
    dispatcher = await _build_dispatcher(request)
    ticket = await service.update_ticket(ticket_id, data, current_user, dispatcher=dispatcher)
    return TicketResponse.model_validate(ticket)


@router.delete("/tickets/{ticket_id}", response_model=TicketResponse, tags=["Tickets"])
async def close_ticket(
    ticket_id: Annotated[uuid.UUID, Path(...)],
    request: Request,
    current_user: AdminUser,
    service: TicketService = Depends(get_ticket_service),
):
    dispatcher = await _build_dispatcher(request)
    ticket = await service.close_ticket(ticket_id, current_user, dispatcher=dispatcher)
    return TicketResponse.model_validate(ticket)


# =============================================================================
# FollowUps
# =============================================================================

@router.post(
    "/tickets/{ticket_id}/followups/",
    response_model=FollowUpResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["FollowUps"],
)
async def add_followup(
    ticket_id: Annotated[uuid.UUID, Path(...)],
    data: FollowUpCreate,
    request: Request,
    current_user: CurrentUser,
    service: TicketService = Depends(get_ticket_service),
):
    dispatcher = await _build_dispatcher(request)
    followup = await service.add_followup(ticket_id, data, current_user, dispatcher=dispatcher)
    return FollowUpResponse.model_validate(followup)


# =============================================================================
# Ticket Relations
# =============================================================================

@router.post(
    "/tickets/{ticket_id}/relations/",
    response_model=TicketRelationResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Relations"],
)
async def add_relation(
    ticket_id: Annotated[uuid.UUID, Path(...)],
    data: TicketRelationCreate,
    current_user: CurrentUser,
    service: TicketService = Depends(get_ticket_service),
):
    relation = await service.add_relation(ticket_id, data, current_user)
    return TicketRelationResponse.model_validate(relation)


@router.delete(
    "/tickets/{ticket_id}/relations/{relation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Relations"],
)
async def remove_relation(
    ticket_id: Annotated[uuid.UUID, Path(...)],
    relation_id: Annotated[uuid.UUID, Path(...)],
    current_user: CurrentUser,
    service: TicketService = Depends(get_ticket_service),
):
    await service.remove_relation(ticket_id, relation_id, current_user)


# =============================================================================
# Attachments
# =============================================================================

@router.post(
    "/tickets/{ticket_id}/attachments/",
    response_model=list[AttachmentResponse],
    status_code=status.HTTP_201_CREATED,
    tags=["Attachments"],
)
async def upload_attachments(
    ticket_id: Annotated[uuid.UUID, Path(...)],
    files: list[UploadFile],
    current_user: CurrentUser,
    followup_id: Annotated[uuid.UUID | None, Query()] = None,
    service: TicketService = Depends(get_ticket_service),
):
    """Upload files to a ticket. Optionally link to a followup."""
    from app.attachment_storage import save_attachments
    ticket = await service.get_ticket(ticket_id, current_user)
    saved = await save_attachments(
        ticket_id=ticket.id,
        files=files,
        uploaded_by=current_user.user_name,
        followup_id=followup_id,
        session=service.ticket_repo.session,
    )
    return [AttachmentResponse.model_validate(a) for a in saved]


@router.delete(
    "/tickets/{ticket_id}/attachments/{attachment_id}",
    status_code=204,
    tags=["Attachments"],
)
async def delete_attachment(
    ticket_id: Annotated[uuid.UUID, Path(...)],
    attachment_id: Annotated[uuid.UUID, Path(...)],
    current_user: CurrentUser,
    service: TicketService = Depends(get_ticket_service),
):
    """Delete an attachment. Admin: any. Non-admin: own uploads within 24h."""
    await service.delete_attachment(ticket_id, attachment_id, current_user)


@router.get(
    "/tickets/{ticket_id}/attachments/{attachment_id}/download",
    tags=["Attachments"],
)
async def download_attachment(
    ticket_id: Annotated[uuid.UUID, Path(...)],
    attachment_id: Annotated[uuid.UUID, Path(...)],
    current_user: CurrentUser,
    service: TicketService = Depends(get_ticket_service),
):
    """Download an attachment file."""
    from app.attachment_storage import get_attachment_path
    # Validates access
    await service.get_ticket(ticket_id, current_user)
    attachment = await service.ticket_repo.get_attachment(attachment_id)
    if not attachment or attachment.ticket_id != ticket_id:
        raise EntityNotFoundError("Attachment", attachment_id)
    file_path = get_attachment_path(attachment.storage_name)
    return FileResponse(
        path=str(file_path),
        filename=attachment.filename,
        media_type=attachment.mime_type or "application/octet-stream",
    )


# =============================================================================
# Portal — Mis Tickets
# =============================================================================

@router.get("/my/tickets/", response_model=PaginatedResponse[TicketSummary], tags=["Portal"])
async def my_tickets(
    current_user: CurrentUser,
    pagination: PaginationParams = Depends(get_pagination_params),
    filters: TicketFilterParams = Depends(get_ticket_filters),
    service: TicketService = Depends(get_ticket_service),
):
    """Employee's tickets — where they are watcher or submitter."""
    my_filters = TicketFilterParams(
        queue_id=filters.queue_id,
        status=filters.status,
        search=filters.search,
        include_closed=filters.include_closed,
    )
    filter_kwargs = {k: v for k, v in my_filters.model_dump().items()
                    if v is not None and k != "include_closed"}
    tickets = await service.ticket_repo.list_tickets(
        skip=pagination.skip,
        limit=pagination.limit,
        submitter_username=current_user.user_name,
        watcher_user_id=current_user.user_name,
        include_closed=my_filters.include_closed,
        **filter_kwargs,
    )
    total = await service.ticket_repo.count_tickets(
        submitter_username=current_user.user_name,
        watcher_user_id=current_user.user_name,
        include_closed=my_filters.include_closed,
        **filter_kwargs,
    )
    meta = PaginationMeta.from_params(total, pagination.skip, pagination.limit)
    return PaginatedResponse(data=[TicketSummary.model_validate(t) for t in tickets], meta=meta)


@router.post("/my/tickets/", response_model=TicketResponse, status_code=status.HTTP_201_CREATED, tags=["Portal"])
async def create_my_ticket(
    data: TicketCreate,
    request: Request,
    current_user: CurrentUser,
    service: TicketService = Depends(get_ticket_service),
):
    """Create a ticket from the employee portal."""
    dispatcher = await _build_dispatcher(request)
    ticket = await service.create_ticket(data, current_user, dispatcher=dispatcher)
    return TicketResponse.model_validate(ticket)


# =============================================================================
# Portal — Notifications
# =============================================================================

@router.get("/my/notifications/", tags=["Portal"])
async def my_notifications(
    current_user: CurrentUser,
    unread: Annotated[bool, Query(description="Only unread")] = False,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    offset: Annotated[int, Query(ge=0)] = 0,
    notif_repo: NotificationRepository = Depends(get_notification_repository),
):
    notifs = await notif_repo.list_for_user(
        current_user.user_name, unread_only=unread, limit=limit, offset=offset,
    )
    unread_count = await notif_repo.count_unread(current_user.user_name)
    return {
        "data": [NotificationResponse.model_validate(n) for n in notifs],
        "unreadCount": unread_count,
    }


@router.post("/my/notifications/read-all", tags=["Portal"])
async def read_all_notifications(
    current_user: CurrentUser,
    notif_repo: NotificationRepository = Depends(get_notification_repository),
):
    await notif_repo.mark_all_read(current_user.user_name)
    return {"ok": True}


@router.patch("/my/notifications/{notif_id}", tags=["Portal"])
async def read_notification(
    notif_id: Annotated[uuid.UUID, Path(...)],
    current_user: CurrentUser,
    notif_repo: NotificationRepository = Depends(get_notification_repository),
):
    notif = await notif_repo.mark_one_read(notif_id, current_user.user_name)
    if not notif:
        raise EntityNotFoundError("Notification", notif_id)
    return NotificationResponse.model_validate(notif)


# =============================================================================
# Portal — User suggestions (proxy to identidad)
# =============================================================================

@router.get("/users/search", tags=["Portal"])
async def search_users(
    request: Request,
    current_user: CurrentUser,
    q: Annotated[str, Query(min_length=0, max_length=100, description="Search query — empty returns all employees (for client-side cache)")] = "",
) -> list[dict]:
    """Search users for @mention autocomplete.
    Now always served from in-memory cache loaded at startup with service token.
    """
    users = request.app.state.users_cache or []
    if not q:
        return users[:200]
    lq = q.lower()
    return [u for u in users if lq in u["userName"].lower() or (u.get("mail") and lq in u["mail"].lower())][:10]
