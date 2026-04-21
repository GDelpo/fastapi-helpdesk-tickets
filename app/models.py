"""SQLModel table definitions for the Tickets Service."""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, JSON
from sqlmodel import Field, Relationship, SQLModel

# asyncpg is strict: TIMESTAMPTZ requires SQLAlchemy to declare timezone=True
_TZ = DateTime(timezone=True)


class TicketStatus(str, enum.Enum):
    OPEN        = "open"
    IN_PROGRESS = "in_progress"
    PENDING     = "pending"
    REOPENED    = "reopened"
    RESOLVED    = "resolved"
    CLOSED      = "closed"


class TicketPriority(int, enum.Enum):
    CRITICAL = 1
    HIGH     = 2
    NORMAL   = 3
    LOW      = 4
    VERY_LOW = 5


class RelationType(str, enum.Enum):
    RELATED    = "related"
    DUPLICATE  = "duplicate"
    BLOCKS     = "blocks"
    BLOCKED_BY = "blocked_by"


# =============================================================================
# Display metadata — single source of truth for labels and ordering
# =============================================================================

STATUS_LABELS: dict[str, str] = {
    "open":        "Open",
    "in_progress": "In progress",
    "pending":     "Pending",
    "reopened":    "Reopened",
    "resolved":    "Resolved",
    "closed":      "Closed",
}

STATUS_ORDER: dict[str, int] = {
    "open": 1, "in_progress": 2, "pending": 3,
    "reopened": 4, "resolved": 5, "closed": 6,
}

PRIORITY_LABELS: dict[str, str] = {
    "1": "Critical",
    "2": "High",
    "3": "Normal",
    "4": "Low",
    "5": "Very low",
}


def _build_meta() -> dict:
    """Build the metadata dict for Jinja2 and the /meta endpoint.
    Import from: app/ui/admin.py, app/ui/portal.py, app/api.py
    """
    return {
        "statuses": [
            {
                "value": s.value,
                "label": STATUS_LABELS[s.value],
                "order": STATUS_ORDER[s.value],
            }
            for s in sorted(TicketStatus, key=lambda x: STATUS_ORDER[x.value])
        ],
        "priorities": [
            {
                "value": str(p.value),
                "label": PRIORITY_LABELS[str(p.value)],
            }
            for p in TicketPriority
        ],
    }


# =============================================================================
# Queue (ticket category)
# =============================================================================

class Queue(SQLModel, table=True):
    """Ticket category/queue. Hierarchical (parent/child) with optional assignee."""

    __tablename__ = "queues"

    id: int | None = Field(default=None, primary_key=True)
    parent_id: int | None = Field(default=None, foreign_key="queues.id")
    name: str = Field(max_length=200)
    slug: str = Field(max_length=100, sa_column_kwargs={"unique": True})
    description: str | None = None
    email: str | None = None
    assigned_to_id: str | None = None  # Default agent username
    is_active: bool = Field(default=True)
    sort_order: int = Field(default=0)
    icon: str | None = Field(default=None, max_length=50)
    color: str | None = Field(default=None, max_length=30)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=_TZ)

    parent: "Queue" = Relationship(
        back_populates="children",
        sa_relationship_kwargs={"remote_side": "Queue.id"},
    )
    children: list["Queue"] = Relationship(back_populates="parent")
    tickets: list["Ticket"] = Relationship(back_populates="queue")


# =============================================================================
# Ticket
# =============================================================================

class Ticket(SQLModel, table=True):
    """Support ticket."""

    __tablename__ = "tickets"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    queue_id: int = Field(foreign_key="queues.id")
    parent_id: uuid.UUID | None = Field(default=None, foreign_key="tickets.id")

    title: str = Field(max_length=200)
    description: str

    status: str = Field(default=TicketStatus.OPEN)
    priority: int = Field(default=TicketPriority.NORMAL)

    # Who opened the ticket
    submitter_email: str = Field(max_length=200)
    submitter_username: str | None = None
    created_by_id: str | None = None    # identidad UUID (as string)

    # Assigned to (staff) — username, not UUID
    assigned_to: str | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=_TZ)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=_TZ)
    due_date: datetime | None = Field(default=None, sa_type=_TZ)
    resolution: str | None = None
    last_staff_reminder_at: datetime | None = Field(default=None, sa_type=_TZ)
    last_submitter_reminder_at: datetime | None = Field(default=None, sa_type=_TZ)

    queue: Queue | None = Relationship(back_populates="tickets")
    parent: "Ticket" = Relationship(
        back_populates="children",
        sa_relationship_kwargs={"remote_side": "Ticket.id"},
    )
    children: list["Ticket"] = Relationship(back_populates="parent")
    followups: list["FollowUp"] = Relationship(back_populates="ticket")
    watchers: list["TicketWatcher"] = Relationship(back_populates="ticket")
    attachments: list["Attachment"] = Relationship(back_populates="ticket")


# =============================================================================
# FollowUp (comment/reply on a ticket)
# =============================================================================

class FollowUp(SQLModel, table=True):
    """Comment or update on a ticket."""

    __tablename__ = "followups"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    ticket_id: uuid.UUID = Field(foreign_key="tickets.id")

    user_id: str | None = None      # identidad UUID
    user_name: str | None = None    # Display name

    comment: str
    is_public: bool = True
    is_staff: bool = False
    new_status: str | None = None    # If the followup changes the ticket status
    new_priority: str | None = None  # If the followup changes the ticket priority
    mentions: list[str] = Field(default=[], sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=_TZ)

    ticket: Ticket | None = Relationship(back_populates="followups")
    attachments: list["Attachment"] = Relationship(back_populates="followup")


# =============================================================================
# Attachment (followup attachment)
# =============================================================================

class Attachment(SQLModel, table=True):
    """File attached to a followup or directly to a ticket."""

    __tablename__ = "attachments"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    ticket_id: uuid.UUID = Field(foreign_key="tickets.id", index=True)
    followup_id: uuid.UUID | None = Field(default=None, foreign_key="followups.id")
    filename: str = Field(max_length=255)
    storage_name: str = Field(max_length=300)  # on-disk name: {ticket_short}/{uuid}.{ext}
    mime_type: str | None = None
    size: int | None = None
    uploaded_by: str | None = Field(default=None, max_length=100)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=_TZ)

    ticket: Ticket | None = Relationship(back_populates="attachments")
    followup: FollowUp | None = Relationship(back_populates="attachments")


# =============================================================================
# TicketWatcher (visibility + notifications)
# =============================================================================

class TicketWatcher(SQLModel, table=True):
    """User watching a ticket (creator, assignee, mentioned)."""

    __tablename__ = "ticket_watchers"

    ticket_id: uuid.UUID = Field(foreign_key="tickets.id", primary_key=True)
    user_id: str = Field(max_length=100, primary_key=True)
    added_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=_TZ)

    ticket: Ticket | None = Relationship(back_populates="watchers")


# =============================================================================
# TicketRelation (related, duplicate, blocking tickets)
# =============================================================================

class TicketRelation(SQLModel, table=True):
    """Relation between two tickets (related, duplicate, blocks/blocked_by)."""

    __tablename__ = "ticket_relations"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    source_ticket_id: uuid.UUID = Field(foreign_key="tickets.id", index=True)
    target_ticket_id: uuid.UUID = Field(foreign_key="tickets.id", index=True)
    relation_type: str = Field(max_length=20)  # RelationType values
    created_by: str | None = Field(default=None, max_length=100)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=_TZ)


# =============================================================================
# Notification
# =============================================================================

class NotificationType(str, enum.Enum):
    NEW_TICKET      = "new_ticket"
    REPLY           = "reply"
    STATUS_CHANGE   = "status_change"
    MENTION         = "mention"
    PENDING_WAITING = "pending_waiting"


class Notification(SQLModel, table=True):
    """Notification for a user about activity on a ticket."""

    __tablename__ = "notifications"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: str = Field(max_length=100, index=True)
    ticket_id: uuid.UUID = Field(foreign_key="tickets.id", index=True)
    type: str = Field(max_length=30)
    is_read: bool = Field(default=False)
    content: str = Field(max_length=500)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=_TZ)
