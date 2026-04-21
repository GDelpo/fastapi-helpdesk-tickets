"""Pydantic schemas (DTOs) for API requests and responses."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import RelationType, TicketPriority, TicketStatus


# =============================================================================
# Base with camelCase
# =============================================================================

def to_camel(string: str) -> str:
    components = string.split("_")
    return components[0] + "".join(word.capitalize() for word in components[1:])


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


# =============================================================================
# Health / Error
# =============================================================================

class ServiceStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class DependencyStatus(CamelModel):
    status: ServiceStatus
    latency_ms: float | None = None
    error: str | None = None


class HealthResponse(CamelModel):
    status: ServiceStatus = ServiceStatus.HEALTHY
    version: str
    environment: str
    dependencies: dict[str, DependencyStatus] = {}


class ErrorResponse(CamelModel):
    success: bool = False
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# Pagination
# =============================================================================

class PaginationMeta(CamelModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_previous: bool

    @classmethod
    def from_params(cls, total: int, skip: int, limit: int) -> PaginationMeta:
        page = (skip // limit) + 1 if limit > 0 else 1
        total_pages = (total + limit - 1) // limit if limit > 0 else 1
        return cls(
            total=total, page=page, page_size=limit, total_pages=total_pages,
            has_next=skip + limit < total, has_previous=skip > 0,
        )


class PaginatedResponse[T](CamelModel):
    data: list[T]
    meta: PaginationMeta


class StatsResponse(CamelModel):
    total: int
    open: int
    in_progress: int
    pending: int
    reopened: int
    resolved: int
    closed: int


# =============================================================================
# Auth
# =============================================================================

class LoginResponse(CamelModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class TokenData(CamelModel):
    id: str
    user_name: str
    mail: str | None = None
    role: str


# =============================================================================
# Queue (category)
# =============================================================================

class QueueCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=100, pattern="^[a-z0-9-]+$")
    description: str | None = None
    email: str | None = None
    parent_id: int | None = None
    sort_order: int = 0
    icon: str | None = None
    color: str | None = None


class QueueUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    slug: str | None = Field(None, min_length=1, max_length=100, pattern="^[a-z0-9-]+$")
    description: str | None = None
    email: str | None = None
    assigned_to_id: str | None = None
    is_active: bool | None = None
    parent_id: int | None = None
    sort_order: int | None = None
    icon: str | None = None
    color: str | None = None


class QueueResponse(CamelModel):
    id: int
    parent_id: int | None = None
    name: str
    slug: str
    description: str | None = None
    email: str | None = None
    assigned_to_id: str | None = None
    is_active: bool
    sort_order: int = 0
    icon: str | None = None
    color: str | None = None
    created_at: datetime
    ticket_count: int = 0


# =============================================================================
# Ticket
# =============================================================================

class TicketCreate(CamelModel):
    queue_id: int
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=10)
    priority: int = Field(default=TicketPriority.NORMAL, ge=1, le=5)
    submitter_email: str = Field(default="", max_length=200)
    due_date: datetime | None = None
    parent_id: uuid.UUID | None = None
    mentioned_user_ids: list[str] = Field(default_factory=list)
    assigned_to: str | None = None  # username of the initial assignee


class TicketUpdate(CamelModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    status: str | None = None
    priority: int | None = Field(None, ge=1, le=5)
    queue_id: int | None = None
    assigned_to: str | None = None
    due_date: datetime | None = None
    resolution: str | None = None


class TicketSummary(CamelModel):
    """Summary view for list endpoints."""
    id: uuid.UUID
    title: str
    status: str
    priority: int
    queue_id: int
    queue_name: str | None = None
    parent_id: uuid.UUID | None = None
    submitter_email: str
    submitter_username: str | None = None
    assigned_to: str | None = None
    created_at: datetime
    updated_at: datetime
    followup_count: int = 0
    children_count: int = 0


class TicketResponse(CamelModel):
    """Full ticket detail."""
    id: uuid.UUID
    title: str
    description: str
    status: str
    priority: int
    queue_id: int
    queue_name: str | None = None
    parent_id: uuid.UUID | None = None
    submitter_email: str
    submitter_username: str | None = None
    created_by_id: str | None = None
    assigned_to: str | None = None
    resolution: str | None = None
    due_date: datetime | None = None
    created_at: datetime
    updated_at: datetime
    followups: list[FollowUpResponse] = []
    watchers: list[WatcherResponse] = []
    children: list[TicketSummary] = []
    relations: list[TicketRelationResponse] = []
    attachments: list[AttachmentResponse] = []


# =============================================================================
# FollowUp
# =============================================================================

class FollowUpCreate(CamelModel):
    comment: str = Field(min_length=1)
    is_public: bool = True
    new_status: str | None = None
    # mentioned_user_ids removed — backend parses @mentions from comment text via regex


class FollowUpResponse(CamelModel):
    id: uuid.UUID
    ticket_id: uuid.UUID
    user_id: str | None = None
    user_name: str | None = None
    comment: str
    is_public: bool
    is_staff: bool = False
    new_status: str | None = None
    new_priority: str | None = None
    mentions: list[str] = Field(default_factory=list)
    created_at: datetime


# =============================================================================
# Watcher
# =============================================================================

class WatcherResponse(CamelModel):
    ticket_id: uuid.UUID
    user_id: str
    added_at: datetime


# =============================================================================
# Identity User (proxy from identidad)
# =============================================================================

class IdentityUserSummary(CamelModel):
    id: str
    user_name: str
    mail: str | None = None
    role: str
    is_active: bool


# =============================================================================
# Filter params
# =============================================================================

class TicketFilterParams(BaseModel):
    queue_id: int | None = None
    status: str | None = None
    priority: int | None = None
    assigned_to: str | None = None
    search: str | None = None
    include_closed: bool = False


# =============================================================================
# Attachments
# =============================================================================

class AttachmentResponse(CamelModel):
    id: uuid.UUID
    ticket_id: uuid.UUID
    followup_id: uuid.UUID | None = None
    filename: str
    mime_type: str | None = None
    size: int | None = None
    uploaded_by: str | None = None
    created_at: datetime


# =============================================================================
# Ticket Relations
# =============================================================================

class TicketRelationCreate(CamelModel):
    target_ticket_id: uuid.UUID
    relation_type: str = Field(pattern="^(related|duplicate|blocks|blocked_by)$")


class TicketRelationResponse(CamelModel):
    id: uuid.UUID
    source_ticket_id: uuid.UUID
    target_ticket_id: uuid.UUID
    relation_type: str
    created_by: str | None = None
    created_at: datetime
    target_title: str | None = None
    target_status: str | None = None


# =============================================================================
# Bulk operations
# =============================================================================

class BulkStatusUpdate(CamelModel):
    ids: list[uuid.UUID]
    status: str


# =============================================================================
# Notification
# =============================================================================

class NotificationResponse(CamelModel):
    id: uuid.UUID
    ticket_id: uuid.UUID
    type: str
    is_read: bool
    content: str
    created_at: datetime


# =============================================================================
# Reminders
# =============================================================================

class ReminderResult(BaseModel):
    staff: int = 0
    submitter: int = 0
