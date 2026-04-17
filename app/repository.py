"""Data access layer for Tickets Service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import case, cast, func, or_, select, Text, update
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import (
    Attachment, FollowUp, Notification, Queue, RelationType,
    Ticket, TicketRelation, TicketStatus, TicketWatcher,
)


class QueueRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, queue_id: int) -> Queue | None:
        result = await self.session.execute(select(Queue).where(Queue.id == queue_id))
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> Queue | None:
        result = await self.session.execute(select(Queue).where(Queue.slug == slug))
        return result.scalar_one_or_none()

    async def list_all(self, active_only: bool = True) -> list[Queue]:
        stmt = select(Queue)
        if active_only:
            stmt = stmt.where(Queue.is_active == True)  # noqa: E712
        stmt = stmt.order_by(Queue.name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, queue: Queue) -> Queue:
        self.session.add(queue)
        await self.session.commit()
        await self.session.refresh(queue)
        return queue

    async def update(self, queue: Queue) -> Queue:
        self.session.add(queue)
        await self.session.commit()
        await self.session.refresh(queue)
        return queue

    async def delete(self, queue: Queue) -> None:
        await self.session.delete(queue)
        await self.session.commit()

    async def count_tickets(self, queue_id: int) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(Ticket).where(Ticket.queue_id == queue_id)
        )
        return result.scalar_one() or 0


class TicketRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, ticket_id: uuid.UUID, with_relations: bool = False) -> Ticket | None:
        result = await self.session.execute(
            select(Ticket).where(Ticket.id == ticket_id)
        )
        ticket = result.scalar_one_or_none()
        if ticket and with_relations:
            fu_result = await self.session.execute(
                select(FollowUp).where(FollowUp.ticket_id == ticket_id).order_by(FollowUp.created_at)
            )
            # __dict__ evita el lazy loader de SQLAlchemy (MissingGreenlet en async)
            ticket.__dict__["followups"] = list(fu_result.scalars().all())
            watcher_result = await self.session.execute(
                select(TicketWatcher).where(TicketWatcher.ticket_id == ticket_id)
            )
            ticket.__dict__["watchers"] = list(watcher_result.scalars().all())
            if ticket.queue_id:
                q_result = await self.session.execute(select(Queue).where(Queue.id == ticket.queue_id))
                q = q_result.scalar_one_or_none()
                if q:
                    ticket.__dict__["queue_name"] = q.name
        return ticket

    async def list_tickets(
        self,
        skip: int = 0,
        limit: int = 20,
        queue_id: int | None = None,
        status: str | None = None,
        priority: int | None = None,
        assigned_to: str | None = None,
        search: str | None = None,
        submitter_username: str | None = None,
        watcher_user_id: str | None = None,
        include_closed: bool = False,
    ) -> list[Ticket]:
        stmt = select(Ticket)

        if not include_closed:
            stmt = stmt.where(Ticket.status != "closed")
        if queue_id is not None:
            stmt = stmt.where(Ticket.queue_id == queue_id)
        if status:
            stmt = stmt.where(Ticket.status == status)
        if priority is not None:
            stmt = stmt.where(Ticket.priority == priority)
        if assigned_to:
            stmt = stmt.where(Ticket.assigned_to == assigned_to)
        if search:
            # Match UUID prefix (cast to text) OR title ilike OR description ilike
            uuid_prefix = cast(Ticket.id, Text).startswith(search.lower().replace("-", ""))
            stmt = stmt.where(
                or_(
                    uuid_prefix,
                    Ticket.title.ilike(f"%{search}%"),
                    Ticket.description.ilike(f"%{search}%"),
                )
            )
        if submitter_username and watcher_user_id:
            watcher_subq = select(TicketWatcher.ticket_id).where(
                TicketWatcher.user_id == watcher_user_id
            )
            stmt = stmt.where(
                or_(
                    Ticket.submitter_username == submitter_username,
                    Ticket.id.in_(watcher_subq),
                )
            )

        stmt = stmt.order_by(Ticket.updated_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        tickets = list(result.scalars().all())

        # Enrich queue names
        queue_ids = {t.queue_id for t in tickets if t.queue_id}
        if queue_ids:
            q_result = await self.session.execute(select(Queue).where(Queue.id.in_(queue_ids)))
            queue_map = {q.id: q.name for q in q_result.scalars().all()}
            for t in tickets:
                t.__dict__["queue_name"] = queue_map.get(t.queue_id)

        # Count followups
        for t in tickets:
            fu_count = await self.session.execute(
                select(func.count()).where(FollowUp.ticket_id == t.id)
            )
            t.__dict__["followup_count"] = fu_count.scalar_one() or 0

        return tickets

    async def count_tickets(
        self,
        queue_id: int | None = None,
        status: str | None = None,
        priority: int | None = None,
        assigned_to: str | None = None,
        search: str | None = None,
        submitter_username: str | None = None,
        watcher_user_id: str | None = None,
        include_closed: bool = False,
    ) -> int:
        stmt = select(func.count()).select_from(Ticket)

        if not include_closed:
            stmt = stmt.where(Ticket.status != "closed")
        if queue_id is not None:
            stmt = stmt.where(Ticket.queue_id == queue_id)
        if status:
            stmt = stmt.where(Ticket.status == status)
        if priority is not None:
            stmt = stmt.where(Ticket.priority == priority)
        if assigned_to:
            stmt = stmt.where(Ticket.assigned_to == assigned_to)
        if search:
            uuid_prefix = cast(Ticket.id, Text).startswith(search.lower().replace("-", ""))
            stmt = stmt.where(
                or_(
                    uuid_prefix,
                    Ticket.title.ilike(f"%{search}%"),
                    Ticket.description.ilike(f"%{search}%"),
                )
            )
        if submitter_username and watcher_user_id:
            watcher_subq = select(TicketWatcher.ticket_id).where(
                TicketWatcher.user_id == watcher_user_id
            )
            stmt = stmt.where(
                or_(
                    Ticket.submitter_username == submitter_username,
                    Ticket.id.in_(watcher_subq),
                )
            )

        result = await self.session.execute(stmt)
        return result.scalar_one() or 0

    async def create(self, ticket: Ticket) -> Ticket:
        self.session.add(ticket)
        await self.session.commit()
        await self.session.refresh(ticket)
        return ticket

    async def update(self, ticket: Ticket) -> Ticket:
        ticket.updated_at = datetime.now(UTC)
        self.session.add(ticket)
        await self.session.commit()
        await self.session.refresh(ticket)
        return ticket

    async def add_followup(self, followup: FollowUp) -> FollowUp:
        self.session.add(followup)
        await self.session.commit()
        await self.session.refresh(followup)
        return followup

    async def get_watcher(self, ticket_id: uuid.UUID, user_id: str) -> TicketWatcher | None:
        result = await self.session.execute(
            select(TicketWatcher).where(
                TicketWatcher.ticket_id == ticket_id,
                TicketWatcher.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def add_watcher(self, watcher: TicketWatcher) -> TicketWatcher:
        self.session.add(watcher)
        await self.session.commit()
        return watcher

    async def get_watchers(self, ticket_id: uuid.UUID) -> list[TicketWatcher]:
        result = await self.session.execute(
            select(TicketWatcher).where(TicketWatcher.ticket_id == ticket_id)
        )
        return list(result.scalars().all())

    # --- Attachments ---

    async def get_attachment(self, attachment_id: uuid.UUID) -> Attachment | None:
        result = await self.session.execute(
            select(Attachment).where(Attachment.id == attachment_id)
        )
        return result.scalar_one_or_none()

    async def get_attachments(self, ticket_id: uuid.UUID) -> list[Attachment]:
        result = await self.session.execute(
            select(Attachment)
            .where(Attachment.ticket_id == ticket_id)
            .order_by(Attachment.created_at)
        )
        return list(result.scalars().all())

    async def delete_attachment(self, attachment: Attachment) -> None:
        await self.session.delete(attachment)
        await self.session.commit()

    async def list_tickets_needing_reminder(
        self,
        staff_cutoff: datetime,
        submitter_cutoff: datetime,
        reminder_dedup_cutoff: datetime,
    ) -> tuple[list[Ticket], list[Ticket]]:
        """
        Returns (staff_tickets, submitter_tickets) needing email reminders.
        staff_tickets: open/in_progress, assigned, no staff followup since staff_cutoff,
                       last_staff_reminder_at is NULL or older than reminder_dedup_cutoff.
        submitter_tickets: pending, no submitter followup since submitter_cutoff,
                           last_submitter_reminder_at is NULL or older than reminder_dedup_cutoff.
        """
        staff_fu_subq = (
            select(
                FollowUp.ticket_id,
                func.max(FollowUp.created_at).label("last_staff_fu"),
            )
            .where(FollowUp.is_staff == True)
            .group_by(FollowUp.ticket_id)
            .subquery()
        )

        staff_result = await self.session.execute(
            select(Ticket)
            .outerjoin(staff_fu_subq, Ticket.id == staff_fu_subq.c.ticket_id)
            .where(
                Ticket.status.in_([TicketStatus.OPEN, TicketStatus.IN_PROGRESS]),
                Ticket.assigned_to.isnot(None),
                or_(
                    staff_fu_subq.c.last_staff_fu == None,
                    staff_fu_subq.c.last_staff_fu < staff_cutoff,
                ),
                or_(
                    Ticket.last_staff_reminder_at == None,
                    Ticket.last_staff_reminder_at < reminder_dedup_cutoff,
                ),
            )
        )
        staff_tickets = list(staff_result.scalars().all())

        submitter_fu_subq = (
            select(
                FollowUp.ticket_id,
                func.max(FollowUp.created_at).label("last_submitter_fu"),
            )
            .where(FollowUp.is_staff == False)
            .group_by(FollowUp.ticket_id)
            .subquery()
        )

        submitter_result = await self.session.execute(
            select(Ticket)
            .outerjoin(submitter_fu_subq, Ticket.id == submitter_fu_subq.c.ticket_id)
            .where(
                Ticket.status == TicketStatus.PENDING,
                or_(
                    submitter_fu_subq.c.last_submitter_fu == None,
                    submitter_fu_subq.c.last_submitter_fu < submitter_cutoff,
                ),
                or_(
                    Ticket.last_submitter_reminder_at == None,
                    Ticket.last_submitter_reminder_at < reminder_dedup_cutoff,
                ),
            )
        )
        submitter_tickets = list(submitter_result.scalars().all())

        return staff_tickets, submitter_tickets

    # --- Sub-tickets ---

    async def get_children(self, parent_id: uuid.UUID) -> list[Ticket]:
        result = await self.session.execute(
            select(Ticket).where(Ticket.parent_id == parent_id).order_by(Ticket.created_at)
        )
        return list(result.scalars().all())

    # --- Relations ---

    async def get_relations(self, ticket_id: uuid.UUID) -> list[TicketRelation]:
        result = await self.session.execute(
            select(TicketRelation)
            .where(TicketRelation.source_ticket_id == ticket_id)
            .order_by(TicketRelation.created_at)
        )
        return list(result.scalars().all())

    async def get_relation(self, relation_id: uuid.UUID) -> TicketRelation | None:
        result = await self.session.execute(
            select(TicketRelation).where(TicketRelation.id == relation_id)
        )
        return result.scalar_one_or_none()

    async def add_relation(self, relation: TicketRelation) -> TicketRelation:
        self.session.add(relation)
        await self.session.commit()
        await self.session.refresh(relation)
        return relation

    async def remove_relation(self, relation_id: uuid.UUID) -> None:
        result = await self.session.execute(
            select(TicketRelation).where(TicketRelation.id == relation_id)
        )
        relation = result.scalar_one_or_none()
        if relation:
            await self.session.delete(relation)
            await self.session.commit()

    async def remove_inverse_relation(
        self, source_id: uuid.UUID, target_id: uuid.UUID, relation_type: str,
    ) -> None:
        inverse_type = {
            RelationType.RELATED: RelationType.RELATED,
            RelationType.DUPLICATE: RelationType.DUPLICATE,
            RelationType.BLOCKS: RelationType.BLOCKED_BY,
            RelationType.BLOCKED_BY: RelationType.BLOCKS,
        }.get(RelationType(relation_type))
        if not inverse_type:
            return
        result = await self.session.execute(
            select(TicketRelation).where(
                TicketRelation.source_ticket_id == source_id,
                TicketRelation.target_ticket_id == target_id,
                TicketRelation.relation_type == inverse_type,
            )
        )
        inverse = result.scalar_one_or_none()
        if inverse:
            await self.session.delete(inverse)
            await self.session.commit()

    # --- Stats ---

    async def get_stats(self) -> dict[str, int]:
        """Return ticket counts grouped by status in a single aggregate query."""
        stmt = select(
            func.count().label("total"),
            func.sum(case((Ticket.status == TicketStatus.OPEN, 1), else_=0)).label("open"),
            func.sum(case((Ticket.status == TicketStatus.IN_PROGRESS, 1), else_=0)).label("in_progress"),
            func.sum(case((Ticket.status == TicketStatus.PENDING, 1), else_=0)).label("pending"),
            func.sum(case((Ticket.status == TicketStatus.REOPENED, 1), else_=0)).label("reopened"),
            func.sum(case((Ticket.status == TicketStatus.RESOLVED, 1), else_=0)).label("resolved"),
            func.sum(case((Ticket.status == TicketStatus.CLOSED, 1), else_=0)).label("closed"),
        )
        result = await self.session.execute(stmt)
        row = result.one()
        return {
            "total": row.total or 0,
            "open": row.open or 0,
            "in_progress": row.in_progress or 0,
            "pending": row.pending or 0,
            "reopened": row.reopened or 0,
            "resolved": row.resolved or 0,
            "closed": row.closed or 0,
        }

    async def get_my_stats(self, username: str) -> dict[str, int]:
        """Count user's tickets by status — submitter OR watcher."""
        watcher_sub = select(TicketWatcher.ticket_id).where(TicketWatcher.user_id == username)
        stmt = select(
            func.count().label("total"),
            func.sum(case((Ticket.status == TicketStatus.OPEN, 1), else_=0)).label("open"),
            func.sum(case((Ticket.status == TicketStatus.IN_PROGRESS, 1), else_=0)).label("in_progress"),
            func.sum(case((Ticket.status == TicketStatus.PENDING, 1), else_=0)).label("pending"),
            func.sum(case((Ticket.status == TicketStatus.REOPENED, 1), else_=0)).label("reopened"),
            func.sum(case((Ticket.status == TicketStatus.RESOLVED, 1), else_=0)).label("resolved"),
            func.sum(case((Ticket.status == TicketStatus.CLOSED, 1), else_=0)).label("closed"),
        ).where(
            or_(
                Ticket.submitter_username == username,
                Ticket.id.in_(watcher_sub),
            )
        )
        result = await self.session.execute(stmt)
        row = result.one()
        return {
            "total": row.total or 0,
            "open": row.open or 0,
            "in_progress": row.in_progress or 0,
            "pending": row.pending or 0,
            "reopened": row.reopened or 0,
            "resolved": row.resolved or 0,
            "closed": row.closed or 0,
        }


class NotificationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, notif: Notification) -> Notification:
        self.session.add(notif)
        await self.session.commit()
        await self.session.refresh(notif)
        return notif

    async def list_for_user(
        self,
        user_id: str,
        unread_only: bool = False,
        limit: int = 10,
        offset: int = 0,
    ) -> list[Notification]:
        stmt = select(Notification).where(Notification.user_id == user_id)
        if unread_only:
            stmt = stmt.where(Notification.is_read == False)  # noqa: E712
        stmt = stmt.order_by(Notification.created_at.desc()).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_unread(self, user_id: str) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(Notification).where(
                Notification.user_id == user_id,
                Notification.is_read == False,  # noqa: E712
            )
        )
        return result.scalar_one() or 0

    async def mark_all_read(self, user_id: str) -> None:
        await self.session.execute(
            update(Notification)
            .where(Notification.user_id == user_id, Notification.is_read == False)  # noqa: E712
            .values(is_read=True)
        )
        await self.session.commit()

    async def mark_one_read(self, notif_id: uuid.UUID, user_id: str) -> Notification | None:
        result = await self.session.execute(
            select(Notification).where(
                Notification.id == notif_id,
                Notification.user_id == user_id,
            )
        )
        notif = result.scalar_one_or_none()
        if notif:
            notif.is_read = True
            self.session.add(notif)
            await self.session.commit()
            await self.session.refresh(notif)
        return notif
