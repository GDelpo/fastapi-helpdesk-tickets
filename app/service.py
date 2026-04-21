"""Business logic for the Tickets Service."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

from app.exceptions import AuthorizationError, EntityNotFoundError
from app.models import (
    FollowUp, Notification, NotificationType, PRIORITY_LABELS, Queue, RelationType,
    STATUS_LABELS, Ticket, TicketRelation, TicketStatus, TicketWatcher,
)
from app.repository import NotificationRepository, QueueRepository, TicketRepository
from app.schemas import (
    FollowUpCreate, QueueCreate, QueueUpdate,
    TicketCreate, TicketFilterParams, TicketRelationCreate, TicketUpdate, TokenData,
)
from app.notifications import NotificationDispatcher

_MENTION_RE = re.compile(r"@([\w.]+)")


class QueueService:
    def __init__(self, repo: QueueRepository):
        self.repo = repo

    async def list_queues(self, active_only: bool = True) -> list[Queue]:
        queues = await self.repo.list_all(active_only=active_only)
        for q in queues:
            q.__dict__["ticket_count"] = await self.repo.count_tickets(q.id)
        return queues

    async def get_queue(self, queue_id: int) -> Queue:
        q = await self.repo.get_by_id(queue_id)
        if not q:
            raise EntityNotFoundError("Queue", queue_id)
        q.__dict__["ticket_count"] = await self.repo.count_tickets(q.id)
        return q

    async def create_queue(self, data: QueueCreate, current_user: TokenData) -> Queue:
        existing = await self.repo.get_by_slug(data.slug)
        from app.exceptions import EntityAlreadyExistsError
        if existing:
            raise EntityAlreadyExistsError("Queue", data.slug)
        queue = Queue(name=data.name, slug=data.slug, description=data.description, email=data.email)
        return await self.repo.create(queue)

    async def update_queue(self, queue_id: int, data: QueueUpdate, current_user: TokenData) -> Queue:
        queue = await self.repo.get_by_id(queue_id)
        if not queue:
            raise EntityNotFoundError("Queue", queue_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(queue, field, value)
        return await self.repo.update(queue)

    async def delete_queue(self, queue_id: int, current_user: TokenData) -> None:
        queue = await self.repo.get_by_id(queue_id)
        if not queue:
            raise EntityNotFoundError("Queue", queue_id)
        count = await self.repo.count_tickets(queue_id)
        if count > 0:
            from app.exceptions import ValidationError
            raise ValidationError(f"Cannot delete: the category has {count} active tickets")
        await self.repo.delete(queue)


class TicketService:
    def __init__(
        self,
        ticket_repo: TicketRepository,
        queue_repo: QueueRepository,
        notif_repo: NotificationRepository | None = None,
    ):
        self.ticket_repo = ticket_repo
        self.queue_repo = queue_repo
        self.notif_repo = notif_repo

    def _is_admin(self, user: TokenData) -> bool:
        return user.role in ("admin", "service")

    async def list_tickets(
        self,
        filters: TicketFilterParams,
        skip: int,
        limit: int,
        current_user: TokenData,
    ) -> tuple[list[Ticket], int]:
        kwargs = dict(
            queue_id=filters.queue_id,
            status=filters.status,
            priority=filters.priority,
            assigned_to=filters.assigned_to,
            search=filters.search,
            include_closed=filters.include_closed,
        )
        if not self._is_admin(current_user):
            kwargs["submitter_username"] = current_user.user_name
            kwargs["watcher_user_id"] = current_user.user_name
        tickets = await self.ticket_repo.list_tickets(skip=skip, limit=limit, **kwargs)
        total = await self.ticket_repo.count_tickets(**kwargs)
        return tickets, total

    async def get_ticket(self, ticket_id: uuid.UUID, current_user: TokenData) -> Ticket:
        ticket = await self.ticket_repo.get_by_id(ticket_id, with_relations=True)
        if not ticket:
            raise EntityNotFoundError("Ticket", ticket_id)
        if not self._is_admin(current_user):
            is_submitter = ticket.submitter_username == current_user.user_name
            is_watcher = any(w.user_id == current_user.user_name for w in ticket.watchers)
            if not is_submitter and not is_watcher:
                raise AuthorizationError("You don't have permission to view this ticket")
        if ticket.queue_id:
            q = await self.queue_repo.get_by_id(ticket.queue_id)
            if q:
                ticket.__dict__["queue_name"] = q.name
        # Enrich with children, relations, attachments
        ticket.__dict__["children"] = await self.ticket_repo.get_children(ticket_id)
        raw_relations = await self.ticket_repo.get_relations(ticket_id)
        for rel in raw_relations:
            target = await self.ticket_repo.get_by_id(rel.target_ticket_id)
            if target:
                rel.__dict__["target_title"] = target.title
                rel.__dict__["target_status"] = target.status
        ticket.__dict__["relations"] = raw_relations
        ticket.__dict__["attachments"] = await self.ticket_repo.get_attachments(ticket_id)
        return ticket

    async def create_ticket(
        self,
        data: TicketCreate,
        current_user: TokenData,
        dispatcher: NotificationDispatcher | None = None,
    ) -> Ticket:
        queue = await self.queue_repo.get_by_id(data.queue_id)
        if not queue:
            raise EntityNotFoundError("Queue", data.queue_id)

        # Validate parent exists if creating a sub-ticket
        if data.parent_id:
            parent = await self.ticket_repo.get_by_id(data.parent_id)
            if not parent:
                raise EntityNotFoundError("Ticket (parent)", data.parent_id)

        ticket = Ticket(
            queue_id=data.queue_id,
            parent_id=data.parent_id,
            title=data.title,
            description=data.description,
            priority=data.priority,
            submitter_email=data.submitter_email or current_user.mail or "",
            submitter_username=current_user.user_name,
            created_by_id=current_user.id,
            due_date=data.due_date,
        )
        ticket = await self.ticket_repo.create(ticket)

        # Submitter becomes watcher
        await self._ensure_watcher(ticket.id, current_user.user_name)

        # Assigned to — set on ticket and add as watcher + notification
        if data.assigned_to:
            ticket.assigned_to = data.assigned_to
            ticket = await self.ticket_repo.update(ticket)
            await self._ensure_watcher(ticket.id, data.assigned_to)
            if self.notif_repo:
                await self.notif_repo.create(Notification(
                    user_id=data.assigned_to,
                    ticket_id=ticket.id,
                    type=NotificationType.NEW_TICKET,
                    content=f"You were assigned the ticket: {ticket.title}",
                ))

        # Mentions: union of explicit list + @mentions parsed from description text
        description_mentions = list(set(_MENTION_RE.findall(data.description)))
        all_mentions = list(set((data.mentioned_user_ids or []) + description_mentions))
        for username in all_mentions:
            if username == current_user.user_name:
                continue  # submitter already added as watcher
            if username == data.assigned_to:
                continue  # already notified as assignee above
            await self._ensure_watcher(ticket.id, username)
            if self.notif_repo:
                await self.notif_repo.create(Notification(
                    user_id=username,
                    ticket_id=ticket.id,
                    type=NotificationType.MENTION,
                    content=f"You were mentioned in: {ticket.title}",
                ))

        # In-app notification to default agent (if queue has one)
        if queue.assigned_to_id and self.notif_repo:
            await self.notif_repo.create(Notification(
                user_id=queue.assigned_to_id,
                ticket_id=ticket.id,
                type=NotificationType.NEW_TICKET,
                content=f"New ticket: {ticket.title}",
            ))

        # Email notifications
        if dispatcher:
            await dispatcher.ticket_created(ticket, queue, all_mentions)

        self._populate_defaults(ticket)
        ticket.__dict__["queue_name"] = queue.name
        return ticket

    async def update_ticket(
        self,
        ticket_id: uuid.UUID,
        data: TicketUpdate,
        current_user: TokenData,
        dispatcher: NotificationDispatcher | None = None,
    ) -> Ticket:
        ticket = await self.ticket_repo.get_by_id(ticket_id)
        if not ticket:
            raise EntityNotFoundError("Ticket", ticket_id)
        old_status = ticket.status
        old_priority = ticket.priority
        update_fields = data.model_dump(exclude_unset=True)
        if not self._is_admin(current_user):
            # Access check: must be submitter or watcher to modify anything
            if ticket.submitter_username != current_user.user_name:
                watchers = await self.ticket_repo.get_watchers(ticket_id)
                if current_user.user_name not in {w.user_id for w in watchers}:
                    raise AuthorizationError("You don't have permission to modify this ticket")

            allowed = {"title", "description", "priority"}
            # Allow employees to resolve/reopen
            if "status" in update_fields:
                employee_statuses = {TicketStatus.RESOLVED, TicketStatus.REOPENED}
                if update_fields["status"] not in employee_statuses:
                    raise AuthorizationError("You can only mark the ticket as Resolved or Reopened")
                allowed.add("status")
            # Allow submitter or current assignee to reassign
            if "assigned_to" in update_fields:
                if (ticket.submitter_username == current_user.user_name or
                        ticket.assigned_to == current_user.user_name):
                    allowed.add("assigned_to")
            restricted = set(update_fields.keys()) - allowed
            if restricted:
                raise AuthorizationError(f"You cannot modify: {', '.join(restricted)}")
        for field, value in update_fields.items():
            setattr(ticket, field, value)
        ticket = await self.ticket_repo.update(ticket)

        # Assignment change: watcher, in-app notification, email, audit trail
        if "assigned_to" in update_fields and update_fields["assigned_to"]:
            new_assignee = update_fields["assigned_to"]
            await self._ensure_watcher(ticket_id, new_assignee)
            if self.notif_repo:
                await self.notif_repo.create(Notification(
                    user_id=new_assignee,
                    ticket_id=ticket.id,
                    type=NotificationType.NEW_TICKET,
                    content=f"You were assigned the ticket: {ticket.title}",
                ))
            if dispatcher:
                q = await self.queue_repo.get_by_id(ticket.queue_id)
                await dispatcher.ticket_assigned(ticket, q, new_assignee,
                                                 reassigned_by=current_user.user_name)
            await self.ticket_repo.add_followup(FollowUp(
                ticket_id=ticket_id,
                user_name=current_user.user_name,
                comment=f"Owner assigned: @{new_assignee} by @{current_user.user_name}.",
                is_public=True,
                is_staff=True,
            ))

        # Priority change audit trail
        new_priority = update_fields.get("priority")
        if new_priority is not None and new_priority != old_priority:
            old_pri_label = PRIORITY_LABELS.get(str(old_priority), str(old_priority))
            new_pri_label = PRIORITY_LABELS.get(str(new_priority), str(new_priority))
            await self.ticket_repo.add_followup(FollowUp(
                ticket_id=ticket_id,
                user_name=current_user.user_name,
                comment=f"Priority changed from «{old_pri_label}» to «{new_pri_label}» by @{current_user.user_name}.",
                is_public=True,
                is_staff=True,
                new_priority=str(new_priority),
            ))

        # Notify + audit trail on status change
        new_status = update_fields.get("status")
        if new_status and new_status != old_status:
            if self.notif_repo:
                watchers = await self.ticket_repo.get_watchers(ticket_id)
                for w in watchers:
                    if w.user_id != current_user.user_name:
                        await self.notif_repo.create(Notification(
                            user_id=w.user_id,
                            ticket_id=ticket.id,
                            type=NotificationType.STATUS_CHANGE,
                            content=f"Status changed to {new_status}: {ticket.title}",
                        ))
            if dispatcher:
                await dispatcher.ticket_status_changed(ticket, new_status)
            old_label = STATUS_LABELS.get(str(old_status), str(old_status))
            new_label = STATUS_LABELS.get(str(new_status), str(new_status))
            await self.ticket_repo.add_followup(FollowUp(
                ticket_id=ticket_id,
                user_name=current_user.user_name,
                comment=f"Status changed from «{old_label}» to «{new_label}» by @{current_user.user_name}.",
                is_public=True,
                is_staff=True,
                new_status=str(new_status),
            ))

        # Resolution change audit trail
        new_resolution = update_fields.get("resolution")
        if "resolution" in update_fields and new_resolution:
            await self.ticket_repo.add_followup(FollowUp(
                ticket_id=ticket_id,
                user_name=current_user.user_name,
                comment=f"Resolution recorded by @{current_user.user_name}.",
                is_public=True,
                is_staff=True,
            ))

        self._populate_defaults(ticket)
        return ticket

    async def close_ticket(
        self,
        ticket_id: uuid.UUID,
        current_user: TokenData,
        dispatcher: NotificationDispatcher | None = None,
    ) -> Ticket:
        ticket = await self.ticket_repo.get_by_id(ticket_id)
        if not ticket:
            raise EntityNotFoundError("Ticket", ticket_id)
        if not self._is_admin(current_user):
            raise AuthorizationError("Only admins can close tickets")
        old_status = ticket.status
        ticket.status = TicketStatus.CLOSED
        ticket = await self.ticket_repo.update(ticket)

        # Audit FollowUp
        old_label = STATUS_LABELS.get(str(old_status), str(old_status))
        new_label = STATUS_LABELS.get(str(TicketStatus.CLOSED), "Cerrado")
        await self.ticket_repo.add_followup(FollowUp(
            ticket_id=ticket_id,
            user_name=current_user.user_name,
            comment=f"Status changed from «{old_label}» to «{new_label}» by @{current_user.user_name}.",
            is_public=True,
            is_staff=True,
            new_status=str(TicketStatus.CLOSED),
        ))

        # In-app notification for watchers
        if self.notif_repo:
            watchers = await self.ticket_repo.get_watchers(ticket_id)
            for w in watchers:
                if w.user_id != current_user.user_name:
                    await self.notif_repo.create(Notification(
                        user_id=w.user_id,
                        ticket_id=ticket.id,
                        type=NotificationType.STATUS_CHANGE,
                        content=f"Ticket closed: {ticket.title}",
                    ))

        # Email notification — ticket_status_changed only sends for "resolved"; close is
        # admin-only and intentionally does not trigger the resolved email.
        if dispatcher:
            await dispatcher.ticket_status_changed(ticket, TicketStatus.CLOSED)

        self._populate_defaults(ticket)
        return ticket

    async def delete_attachment(
        self,
        ticket_id: uuid.UUID,
        attachment_id: uuid.UUID,
        current_user: TokenData,
    ) -> None:
        from app.attachment_storage import delete_attachment_file

        ticket = await self.ticket_repo.get_by_id(ticket_id)
        if not ticket:
            raise EntityNotFoundError("Ticket", ticket_id)

        attachment = await self.ticket_repo.get_attachment(attachment_id)
        if not attachment or attachment.ticket_id != ticket_id:
            raise EntityNotFoundError("Attachment", attachment_id)

        if not self._is_admin(current_user):
            if attachment.uploaded_by != current_user.user_name:
                raise AuthorizationError("You cannot delete attachments from other users")
            age = (datetime.now(UTC) - attachment.created_at).total_seconds() / 3600
            if age > 24:
                raise AuthorizationError(
                    "You can only delete attachments within 24 hours of uploading them"
                )

        storage_name = attachment.storage_name
        filename = attachment.filename
        await self.ticket_repo.delete_attachment(attachment)
        await delete_attachment_file(storage_name)

        await self.ticket_repo.add_followup(FollowUp(
            ticket_id=ticket_id,
            user_name=current_user.user_name,
            comment=f"Attachment '{filename}' deleted by @{current_user.user_name}.",
            is_public=True,
            is_staff=True,
        ))

    async def add_followup(
        self,
        ticket_id: uuid.UUID,
        data: FollowUpCreate,
        current_user: TokenData,
        dispatcher: NotificationDispatcher | None = None,
    ) -> FollowUp:
        ticket = await self.ticket_repo.get_by_id(ticket_id)
        if not ticket:
            raise EntityNotFoundError("Ticket", ticket_id)

        # Access check for non-admin
        if not self._is_admin(current_user):
            is_submitter = ticket.submitter_username == current_user.user_name
            watcher = await self.ticket_repo.get_watcher(ticket_id, current_user.user_name)
            if not is_submitter and not watcher:
                raise AuthorizationError("You don't have permission to reply to this ticket")

        is_staff = self._is_admin(current_user)

        # Auto-REOPENED: employee replies to PENDING ticket
        if not is_staff and ticket.status == TicketStatus.PENDING:
            ticket.status = TicketStatus.REOPENED
            await self.ticket_repo.update(ticket)
            # Audit trail for auto-reopen
            old_label = STATUS_LABELS.get(str(TicketStatus.PENDING), "Pendiente")
            new_label = STATUS_LABELS.get(str(TicketStatus.REOPENED), "Reabierto")
            await self.ticket_repo.add_followup(FollowUp(
                ticket_id=ticket_id,
                user_name=current_user.user_name,
                comment=f"Status changed from «{old_label}» to «{new_label}».",
                is_public=True,
                is_staff=False,
                new_status=str(TicketStatus.REOPENED),
            ))
            if self.notif_repo:
                watchers = await self.ticket_repo.get_watchers(ticket_id)
                for w in watchers:
                    if w.user_id != current_user.user_name:
                        await self.notif_repo.create(Notification(
                            user_id=w.user_id,
                            ticket_id=ticket.id,
                            type=NotificationType.STATUS_CHANGE,
                            content=f"Ticket reopened by submitter: {ticket.title}",
                        ))

        # Parse @mentions from comment
        mentioned_usernames = list(set(_MENTION_RE.findall(data.comment)))

        followup = FollowUp(
            ticket_id=ticket_id,
            user_id=current_user.id,
            user_name=current_user.user_name,
            comment=data.comment,
            is_public=data.is_public,
            is_staff=is_staff,
            new_status=data.new_status,
            mentions=mentioned_usernames,
        )
        followup = await self.ticket_repo.add_followup(followup)

        # Apply explicit status change
        old_status = ticket.status
        if data.new_status and data.new_status != ticket.status:
            ticket.status = data.new_status
            await self.ticket_repo.update(ticket)

        # Add mentioned users as watchers + MENTION notifications
        for username in mentioned_usernames:
            await self._ensure_watcher(ticket_id, username)
            if self.notif_repo:
                await self.notif_repo.create(Notification(
                    user_id=username,
                    ticket_id=ticket.id,
                    type=NotificationType.MENTION,
                    content=f"You were mentioned in: {ticket.title}",
                ))

        # REPLY notifications
        if self.notif_repo:
            if is_staff:
                # Staff reply → notify all watchers
                watchers = await self.ticket_repo.get_watchers(ticket_id)
                for w in watchers:
                    if w.user_id != current_user.user_name:
                        await self.notif_repo.create(Notification(
                            user_id=w.user_id,
                            ticket_id=ticket.id,
                            type=NotificationType.REPLY,
                            content=f"Support reply on: {ticket.title}",
                        ))
            else:
                # Employee reply → notify assigned agent only (in-app)
                if ticket.assigned_to:
                    await self.notif_repo.create(Notification(
                        user_id=ticket.assigned_to,
                        ticket_id=ticket.id,
                        type=NotificationType.REPLY,
                        content=f"New reply from submitter on: {ticket.title}",
                    ))

        # PENDING_WAITING notification
        if data.new_status == TicketStatus.PENDING and self.notif_repo:
            if ticket.submitter_username:
                await self.notif_repo.create(Notification(
                    user_id=ticket.submitter_username,
                    ticket_id=ticket.id,
                    type=NotificationType.PENDING_WAITING,
                    content=f"Your ticket is waiting for your reply: {ticket.title}",
                ))

        # STATUS_CHANGE notification on explicit change
        if data.new_status and data.new_status != old_status and self.notif_repo:
            watchers = await self.ticket_repo.get_watchers(ticket_id)
            for w in watchers:
                if w.user_id != current_user.user_name:
                    await self.notif_repo.create(Notification(
                        user_id=w.user_id,
                        ticket_id=ticket.id,
                        type=NotificationType.STATUS_CHANGE,
                        content=f"Status changed to {data.new_status}: {ticket.title}",
                    ))

        # Email notifications
        if dispatcher:
            q = await self.queue_repo.get_by_id(ticket.queue_id)
            await dispatcher.followup_added(
                ticket=ticket,
                queue=q,
                is_staff=is_staff,
                is_public=data.is_public,
                actor_username=current_user.user_name,
                mentioned_usernames=mentioned_usernames,
                new_status=data.new_status,
                reply_content=data.comment,
            )

        return followup

    async def add_relation(
        self,
        ticket_id: uuid.UUID,
        data: TicketRelationCreate,
        current_user: TokenData,
    ) -> TicketRelation:
        """Create a relation between two tickets. Creates inverse for blocks/blocked_by."""
        source = await self.ticket_repo.get_by_id(ticket_id)
        if not source:
            raise EntityNotFoundError("Ticket", ticket_id)
        target = await self.ticket_repo.get_by_id(data.target_ticket_id)
        if not target:
            raise EntityNotFoundError("Ticket (target)", data.target_ticket_id)

        relation = TicketRelation(
            source_ticket_id=ticket_id,
            target_ticket_id=data.target_ticket_id,
            relation_type=data.relation_type,
            created_by=current_user.user_name,
        )
        relation = await self.ticket_repo.add_relation(relation)

        # Automatically create the inverse relation
        inverse_type = {
            RelationType.RELATED: RelationType.RELATED,
            RelationType.DUPLICATE: RelationType.DUPLICATE,
            RelationType.BLOCKS: RelationType.BLOCKED_BY,
            RelationType.BLOCKED_BY: RelationType.BLOCKS,
        }.get(RelationType(data.relation_type))

        if inverse_type:
            inverse = TicketRelation(
                source_ticket_id=data.target_ticket_id,
                target_ticket_id=ticket_id,
                relation_type=inverse_type,
                created_by=current_user.user_name,
            )
            await self.ticket_repo.add_relation(inverse)

        return relation

    async def remove_relation(
        self,
        ticket_id: uuid.UUID,
        relation_id: uuid.UUID,
        current_user: TokenData,
    ) -> None:
        """Remove a relation and its inverse."""
        relation = await self.ticket_repo.get_relation(relation_id)
        if not relation or relation.source_ticket_id != ticket_id:
            raise EntityNotFoundError("TicketRelation", relation_id)
        # Remove inverse
        await self.ticket_repo.remove_inverse_relation(
            relation.target_ticket_id, relation.source_ticket_id, relation.relation_type,
        )
        await self.ticket_repo.remove_relation(relation_id)

    async def get_children(self, ticket_id: uuid.UUID) -> list[Ticket]:
        """Get sub-tickets for a parent ticket."""
        return await self.ticket_repo.get_children(ticket_id)

    @staticmethod
    def _populate_defaults(ticket: Ticket) -> None:
        """Set empty relationship defaults to avoid MissingGreenlet on serialization."""
        for attr in ("followups", "watchers", "children", "attachments", "relations"):
            ticket.__dict__.setdefault(attr, [])
        ticket.__dict__.setdefault("queue_name", None)

    async def _ensure_watcher(
        self,
        ticket_id: uuid.UUID,
        user_id: str,  # username
    ) -> TicketWatcher:
        existing = await self.ticket_repo.get_watcher(ticket_id, user_id)
        if existing:
            return existing
        watcher = TicketWatcher(ticket_id=ticket_id, user_id=user_id)
        return await self.ticket_repo.add_watcher(watcher)
