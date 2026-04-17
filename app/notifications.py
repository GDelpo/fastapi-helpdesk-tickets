"""Mailsender integration for ticket notifications."""

from __future__ import annotations

from pathlib import Path

import httpx
from jinja2 import Environment, FileSystemLoader

from app.config import settings
from app.logger import get_logger
from app.models import PRIORITY_LABELS, STATUS_LABELS, Ticket

logger = get_logger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent / "ui" / "templates"
_email_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=True,
)

_TEMPLATES: dict[str, tuple] = {
    "ticket-opened":         (lambda t: f"[{t.id.hex[:8]}] Ticket recibido: {t.title}",                   "email/ticket_opened.html"),
    "ticket-assigned-staff": (lambda t: f"[{t.id.hex[:8]}] Ticket asignado: {t.title}",                   "email/ticket_assigned_staff.html"),
    "ticket-mention":        (lambda t: f"[{t.id.hex[:8]}] Te mencionaron en: {t.title}",                 "email/ticket_mention.html"),
    "ticket-reply":          (lambda t: f"[{t.id.hex[:8]}] Nueva respuesta: {t.title}",                   "email/ticket_reply.html"),
    "ticket-reply-to-staff": (lambda t: f"[{t.id.hex[:8]}] Nueva respuesta del solicitante: {t.title}",  "email/ticket_reply.html"),
    "ticket-pending":        (lambda t: f"[{t.id.hex[:8]}] Pendiente de tu respuesta: {t.title}",         "email/ticket_pending.html"),
    "ticket-resolved":       (lambda t: f"[{t.id.hex[:8]}] Resuelto: {t.title}",                         "email/ticket_resolved.html"),
    "ticket-reminder-staff":     (lambda t: f"[{t.id.hex[:8]}] Recordatorio: ticket sin respuesta: {t.title}",  "email/ticket_reminder_staff.html"),
    "ticket-reminder-submitter": (lambda t: f"[{t.id.hex[:8]}] Tu solicitud espera tu respuesta: {t.title}",    "email/ticket_reminder_submitter.html"),
}


async def send_ticket_email(
    ticket: Ticket,
    slug: str,
    http_client: httpx.AsyncClient,
    *,
    service_token: str = "",
    to_email: str,
    queue_name: str = "",
    staff_name: str = "",
    reply_content: str = "",
    mentioned_by: str = "",
    reassigned_by: str = "",
    days_elapsed: str = "",
) -> None:
    """Send one notification email via mailsender with locally rendered HTML.

    Caller is responsible for resolving the recipient address before calling.
    """
    entry = _TEMPLATES.get(slug)
    if not entry:
        logger.warning("Unknown email template slug: %s", slug)
        return

    subject_fn, template_file = entry
    ticket_short_id = ticket.id.hex[:8]
    portal_base_url = settings.portal_base_url or ""
    ticket_url = f"{portal_base_url}/tickets/{ticket.id}" if portal_base_url else ""

    variables = {
        "ticket_id": ticket_short_id,
        "ticket_title": ticket.title,
        "ticket_status": STATUS_LABELS.get(str(ticket.status), str(ticket.status)),
        "ticket_priority": PRIORITY_LABELS.get(str(ticket.priority), str(ticket.priority)),
        "ticket_queue": queue_name,
        "submitter_name": ticket.submitter_username or ticket.submitter_email,
        "portal_url": ticket_url,
        "staff_name": staff_name,
        "reply_content": reply_content,
        "mentioned_by": mentioned_by,
        "reassigned_by": reassigned_by,
        "days_elapsed": days_elapsed,
    }

    try:
        template = _email_env.get_template(template_file)
        html_content = template.render(**variables)
    except Exception as e:
        logger.error("Failed to render email template %s: %s", template_file, e)
        return

    payload = {
        "to": [{"email": to_email}],
        "subject": subject_fn(ticket),
        "html_content": html_content,
        "source_service": "tickets",
    }

    headers = {"Authorization": f"Bearer {service_token}"} if service_token else {}

    try:
        response = await http_client.post(
            settings.mailsender_url,
            json=payload,
            headers=headers,
            timeout=settings.mailsender_timeout,
        )
        if response.status_code not in (200, 202):
            logger.warning(
                "Mailsender %s for ticket %s slug %s to %s",
                response.status_code, ticket_short_id, slug, to_email,
            )
    except httpx.RequestError as e:
        logger.error("Mailsender error for ticket %s: %s", ticket_short_id, e)


class NotificationDispatcher:
    """Owns all email notification routing logic.

    Constructed per-request in api.py with the shared http_client,
    service_token, and users_cache from app.state.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        service_token: str,
        users_cache: list[dict],
    ) -> None:
        self._client = http_client
        self._token = service_token
        self._cache = users_cache

    def _lookup_email(self, username: str | None) -> str | None:
        """Return email for username from users_cache. None if not found or empty."""
        if not username:
            return None
        for u in self._cache:
            if u.get("userName") == username:
                mail = u.get("mail")
                if mail:
                    return mail
        logger.warning("[notifications] No email found in cache for user: %s", username)
        return None

    async def _send(self, ticket: Ticket, slug: str, to_email: str, **kwargs) -> None:
        """Send one email. Never raises — all exceptions are logged and swallowed."""
        try:
            await send_ticket_email(
                ticket, slug, self._client,
                service_token=self._token,
                to_email=to_email,
                **kwargs,
            )
        except Exception as e:
            logger.error("[notifications] Unexpected error sending %s to %s: %s", slug, to_email, e)

    async def ticket_created(
        self,
        ticket: Ticket,
        queue,
        mentioned_usernames: list[str],
    ) -> None:
        """Emails on ticket creation: submitter confirmation + staff/queue + mentions."""
        queue_name = queue.name if queue else ""

        # 1. Confirm to submitter
        if ticket.submitter_email:
            await self._send(ticket, "ticket-opened", ticket.submitter_email,
                             queue_name=queue_name,
                             staff_name=ticket.assigned_to or "")

        # 2a. Notify individually assigned user (if set)
        if ticket.assigned_to:
            assignee_email = self._lookup_email(ticket.assigned_to)
            if assignee_email:
                await self._send(ticket, "ticket-assigned-staff", assignee_email,
                                 queue_name=queue_name,
                                 staff_name=ticket.assigned_to)

        # 2b. Notify queue / team fallback (queue.email or support_email)
        staff_dest = (queue.email if queue and queue.email else None) or settings.support_email
        # Avoid duplicate if assignee IS the queue/support address
        assignee_email_check = self._lookup_email(ticket.assigned_to) if ticket.assigned_to else None
        if staff_dest and staff_dest != assignee_email_check:
            await self._send(ticket, "ticket-assigned-staff", staff_dest,
                             queue_name=queue_name,
                             staff_name=queue_name)

        # 3. Mentions — always send, even if mentioned user is the submitter
        submitter = ticket.submitter_username or ""
        for username in mentioned_usernames:
            email = self._lookup_email(username)
            if email:
                await self._send(ticket, "ticket-mention", email,
                                 queue_name=queue_name,
                                 mentioned_by=submitter or ticket.submitter_email)

    async def ticket_assigned(
        self,
        ticket: Ticket,
        queue,
        assignee_username: str,
        reassigned_by: str = "",
    ) -> None:
        """Email to individually assigned staff member (reassignment)."""
        email = self._lookup_email(assignee_username)
        if not email:
            return
        queue_name = queue.name if queue else ""
        await self._send(ticket, "ticket-assigned-staff", email,
                         queue_name=queue_name,
                         staff_name=assignee_username,
                         reassigned_by=reassigned_by)

    async def followup_added(
        self,
        ticket: Ticket,
        queue,
        is_staff: bool,
        is_public: bool,
        actor_username: str,
        mentioned_usernames: list[str],
        new_status: str | None,
        reply_content: str = "",
    ) -> None:
        """Emails on followup: reply direction + status transitions + mentions."""
        queue_name = queue.name if queue else ""

        # Reply direction
        if is_staff and is_public:
            if ticket.submitter_email:
                await self._send(ticket, "ticket-reply", ticket.submitter_email,
                                 queue_name=queue_name,
                                 staff_name=actor_username,
                                 reply_content=reply_content)
        elif not is_staff:
            dest = self._lookup_email(ticket.assigned_to) or settings.support_email
            if dest:
                await self._send(ticket, "ticket-reply-to-staff", dest,
                                 queue_name=queue_name,
                                 staff_name=actor_username,
                                 reply_content=reply_content)

        # Status transitions — PENDING and RESOLVED only via followup
        if new_status == "pending" and ticket.submitter_email:
            await self._send(ticket, "ticket-pending", ticket.submitter_email,
                             queue_name=queue_name,
                             staff_name=actor_username,
                             reply_content=reply_content)
        elif new_status == "resolved" and ticket.submitter_email:
            await self._send(ticket, "ticket-resolved", ticket.submitter_email,
                             queue_name=queue_name,
                             staff_name=actor_username)

        # Mentions (skip actor)
        for username in mentioned_usernames:
            if username == actor_username:
                continue
            email = self._lookup_email(username)
            if email:
                await self._send(ticket, "ticket-mention", email,
                                 queue_name=queue_name,
                                 mentioned_by=actor_username)

    async def ticket_status_changed(self, ticket: Ticket, new_status: str) -> None:
        """Email for direct status patch (PATCH /tickets/{id} without a followup).
        Only RESOLVED triggers email — PENDING only fires via followup_added (staff quotes a message).
        """
        if new_status == "resolved" and ticket.submitter_email:
            await self._send(ticket, "ticket-resolved", ticket.submitter_email)

    async def send_staff_reminder(
        self,
        ticket: Ticket,
        queue_name: str,
        days_elapsed: int,
    ) -> None:
        if not ticket.assigned_to:
            return
        to_email = self._lookup_email(ticket.assigned_to)
        if not to_email:
            return
        await self._send(
            ticket,
            "ticket-reminder-staff",
            to_email,
            queue_name=queue_name,
            staff_name=ticket.assigned_to,
            days_elapsed=str(days_elapsed),
        )

    async def send_submitter_reminder(
        self,
        ticket: Ticket,
        queue_name: str,
        days_elapsed: int,
    ) -> None:
        if not ticket.submitter_email:
            return
        await self._send(
            ticket,
            "ticket-reminder-submitter",
            ticket.submitter_email,
            queue_name=queue_name,
            days_elapsed=str(days_elapsed),
        )
