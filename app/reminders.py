"""Email reminder background task for unreviewed tickets."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings
from app.logger import get_logger
from app.notifications import NotificationDispatcher
from app.repository import QueueRepository, TicketRepository

logger = get_logger(__name__)


async def check_and_send_reminders(
    session: AsyncSession,
    dispatcher: NotificationDispatcher,
) -> dict[str, int]:
    """
    Query DB for tickets needing reminders and send emails.
    Returns {"staff": N, "submitter": M} with counts sent.
    Idempotent: dedup via last_staff_reminder_at / last_submitter_reminder_at on Ticket.
    """
    now = datetime.now(UTC)
    staff_cutoff = now - timedelta(days=settings.reminder_staff_days)
    submitter_cutoff = now - timedelta(days=settings.reminder_submitter_days)
    reminder_dedup_cutoff = now - timedelta(hours=24)

    ticket_repo = TicketRepository(session)
    queue_repo = QueueRepository(session)

    staff_tickets, submitter_tickets = await ticket_repo.list_tickets_needing_reminder(
        staff_cutoff=staff_cutoff,
        submitter_cutoff=submitter_cutoff,
        reminder_dedup_cutoff=reminder_dedup_cutoff,
    )

    staff_sent = 0
    for ticket in staff_tickets:
        try:
            queue = await queue_repo.get_by_id(ticket.queue_id)
            queue_name = queue.name if queue else ""
            days_elapsed = int((now - ticket.created_at).total_seconds() // 86400)
            await dispatcher.send_staff_reminder(ticket, queue_name, days_elapsed)
            ticket.last_staff_reminder_at = now
            await ticket_repo.update(ticket)
            staff_sent += 1
            logger.info(
                "Staff reminder sent",
                extra={"extra_fields": {"ticket_id": str(ticket.id), "assigned_to": ticket.assigned_to}},
            )
        except Exception as e:
            logger.error("Failed to send staff reminder for ticket %s: %s", ticket.id, e)

    submitter_sent = 0
    for ticket in submitter_tickets:
        try:
            queue = await queue_repo.get_by_id(ticket.queue_id)
            queue_name = queue.name if queue else ""
            days_elapsed = int((now - ticket.created_at).total_seconds() // 86400)
            await dispatcher.send_submitter_reminder(ticket, queue_name, days_elapsed)
            ticket.last_submitter_reminder_at = now
            await ticket_repo.update(ticket)
            submitter_sent += 1
            logger.info(
                "Submitter reminder sent",
                extra={"extra_fields": {"ticket_id": str(ticket.id), "submitter_email": ticket.submitter_email}},
            )
        except Exception as e:
            logger.error("Failed to send submitter reminder for ticket %s: %s", ticket.id, e)

    return {"staff": staff_sent, "submitter": submitter_sent}
