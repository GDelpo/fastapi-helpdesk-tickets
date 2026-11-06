"""TicketService business rules, exercised through the real service with in-memory repos."""

from datetime import UTC, datetime, timedelta

import pydantic
import pytest

from app import attachment_storage
from app.config import Settings
from app.exceptions import AuthorizationError
from app.models import (
    STATUS_LABELS,
    Attachment,
    NotificationType,
    TicketStatus,
    _build_meta,
)
from app.schemas import FollowUpCreate, TicketCreate, TicketRelationCreate, TicketUpdate
from tests.conftest import FakeDispatcher, make_ticket, make_user

# --- Enums, metadata and schemas ---------------------------------------------------------


def test_ticket_status_values():
    assert [s.value for s in TicketStatus] == [
        "open",
        "in_progress",
        "pending",
        "reopened",
        "resolved",
        "closed",
    ]


def test_build_meta_structure():
    meta = _build_meta()
    orders = {s["value"]: s["order"] for s in meta["statuses"]}
    assert orders == {
        "open": 1,
        "in_progress": 2,
        "pending": 3,
        "reopened": 4,
        "resolved": 5,
        "closed": 6,
    }
    assert {p["value"] for p in meta["priorities"]} == {"1", "2", "3", "4", "5"}


def test_status_labels_english():
    assert STATUS_LABELS["in_progress"] == "In progress"
    assert STATUS_LABELS["closed"] == "Closed"


def test_ticket_create_assigned_to_is_optional():
    base = {"queue_id": 1, "title": "Test ticket", "description": "Long enough description"}
    assert TicketCreate(**base).assigned_to is None
    assert TicketCreate(**base, assigned_to="cwhite").assigned_to == "cwhite"


def test_ticket_create_description_min_length():
    with pytest.raises(pydantic.ValidationError):
        TicketCreate(queue_id=1, title="Title", description="short")


def test_followup_create_has_no_mentioned_user_ids():
    assert "mentioned_user_ids" not in FollowUpCreate.model_fields


def test_reminder_settings_defaults():
    s = Settings(_env_file=None)
    assert (s.reminder_staff_days, s.reminder_submitter_days) == (2, 2)
    assert s.reminder_check_interval_hours == 6
    assert s.reminder_enabled is True


def test_email_service_url_accepts_new_and_legacy_names(monkeypatch):
    monkeypatch.delenv("EMAIL_SERVICE_URL", raising=False)
    monkeypatch.setenv("MAILSENDER_URL", "http://legacy.example/api/v1/emails")
    assert Settings(_env_file=None).email_service_url == "http://legacy.example/api/v1/emails"

    monkeypatch.setenv("EMAIL_SERVICE_URL", "http://email.example/api/v1/emails")
    assert Settings(_env_file=None).email_service_url == "http://email.example/api/v1/emails"


# --- Mentions ----------------------------------------------------------------------------


async def test_followup_mentions_become_watchers_and_notifications(service, repos):
    ticket_repo, _, notif_repo = repos
    ticket = make_ticket(ticket_repo)

    followup = await service.add_followup(
        ticket.id,
        FollowUpCreate(comment="Hi @asmith, can you check this? cc @b.jones"),
        make_user("alice"),
    )

    assert sorted(followup.mentions) == ["asmith", "b.jones"]
    watchers = {w.user_id for w in await ticket_repo.get_watchers(ticket.id)}
    assert {"asmith", "b.jones"} <= watchers
    mentioned = {n.user_id for n in notif_repo.created if n.type == NotificationType.MENTION}
    assert mentioned == {"asmith", "b.jones"}


async def test_followup_without_mentions(service, repos):
    ticket_repo, _, notif_repo = repos
    ticket = make_ticket(ticket_repo)

    followup = await service.add_followup(
        ticket.id, FollowUpCreate(comment="No mentions here"), make_user("alice")
    )

    assert followup.mentions == []
    assert not [n for n in notif_repo.created if n.type == NotificationType.MENTION]


async def test_create_ticket_parses_mentions_from_description(service, repos):
    ticket_repo, _, notif_repo = repos
    dispatcher = FakeDispatcher()
    data = TicketCreate(
        queue_id=1,
        title="VPN down",
        description="Hi @asmith, this also affects @jdoe and me (@alice)",
        mentioned_user_ids=["cwhite"],
    )

    ticket = await service.create_ticket(data, make_user("alice"), dispatcher=dispatcher)

    watchers = {w.user_id for w in await ticket_repo.get_watchers(ticket.id)}
    assert watchers == {"alice", "asmith", "jdoe", "cwhite"}
    mentioned = {n.user_id for n in notif_repo.created if n.type == NotificationType.MENTION}
    assert mentioned == {"asmith", "jdoe", "cwhite"}  # the submitter is not notified
    assert dispatcher.calls[0][0] == "ticket_created"


# --- Audit trail -------------------------------------------------------------------------


def _last_comment(ticket_repo) -> str:
    return ticket_repo.followups[-1].comment


async def test_audit_comment_on_assignment_names_the_actor(service, repos):
    ticket_repo, _, _ = repos
    ticket = make_ticket(ticket_repo)

    await service.update_ticket(
        ticket.id, TicketUpdate(assigned_to="asmith"), make_user("jdoe", "admin")
    )

    assert _last_comment(ticket_repo) == "Owner assigned: @asmith by @jdoe."


async def test_audit_comment_on_priority_change(service, repos):
    ticket_repo, _, _ = repos
    ticket = make_ticket(ticket_repo)  # default priority 3 = Normal

    await service.update_ticket(ticket.id, TicketUpdate(priority=1), make_user("jdoe", "admin"))

    assert _last_comment(ticket_repo) == "Priority changed from «Normal» to «Critical» by @jdoe."
    assert ticket_repo.followups[-1].new_priority == "1"


async def test_audit_comment_on_resolution(service, repos):
    ticket_repo, _, _ = repos
    ticket = make_ticket(ticket_repo)

    await service.update_ticket(
        ticket.id, TicketUpdate(resolution="Replaced toner"), make_user("jdoe", "admin")
    )

    assert _last_comment(ticket_repo) == "Resolution recorded by @jdoe."


async def test_employee_cannot_set_arbitrary_status(service, repos):
    ticket_repo, _, _ = repos
    ticket = make_ticket(ticket_repo)

    with pytest.raises(AuthorizationError):
        await service.update_ticket(ticket.id, TicketUpdate(status="closed"), make_user("alice"))


# --- Attachment deletion rules -----------------------------------------------------------


@pytest.fixture
def attachment(repos, monkeypatch):
    ticket_repo, _, _ = repos
    deleted_files = []

    async def fake_delete(storage_name):
        deleted_files.append(storage_name)

    monkeypatch.setattr(attachment_storage, "delete_attachment_file", fake_delete)
    ticket = make_ticket(ticket_repo)

    def make(uploaded_by: str, age_hours: float) -> Attachment:
        a = Attachment(
            ticket_id=ticket.id,
            filename="report.pdf",
            storage_name="abc/report.pdf",
            uploaded_by=uploaded_by,
            created_at=datetime.now(UTC) - timedelta(hours=age_hours),
        )
        ticket_repo.attachments[a.id] = a
        return a

    make.deleted_files = deleted_files
    return make


async def test_admin_deletes_any_attachment(service, repos, attachment):
    a = attachment("someone-else", age_hours=999)
    await service.delete_attachment(a.ticket_id, a.id, make_user("root", "admin"))
    assert attachment.deleted_files == ["abc/report.pdf"]


async def test_owner_deletes_own_attachment_within_24h(service, attachment):
    a = attachment("jdoe", age_hours=12)
    await service.delete_attachment(a.ticket_id, a.id, make_user("jdoe"))
    assert attachment.deleted_files == ["abc/report.pdf"]


async def test_owner_cannot_delete_after_24h(service, attachment):
    a = attachment("jdoe", age_hours=25)
    with pytest.raises(AuthorizationError):
        await service.delete_attachment(a.ticket_id, a.id, make_user("jdoe"))
    assert attachment.deleted_files == []


async def test_user_cannot_delete_someone_elses_attachment(service, attachment):
    a = attachment("other", age_hours=1)
    with pytest.raises(AuthorizationError):
        await service.delete_attachment(a.ticket_id, a.id, make_user("jdoe"))
    assert attachment.deleted_files == []


# --- Relations: access control -----------------------------------------------------------


async def test_relation_requires_access_to_source_ticket(service, repos):
    ticket_repo, _, _ = repos
    foreign = make_ticket(ticket_repo, submitter="bob")
    mine = make_ticket(ticket_repo, submitter="mallory")

    with pytest.raises(AuthorizationError):
        await service.add_relation(
            foreign.id,
            TicketRelationCreate(target_ticket_id=mine.id, relation_type="related"),
            make_user("mallory"),
        )
    assert ticket_repo.relations == {}


async def test_relation_requires_access_to_target_ticket(service, repos):
    ticket_repo, _, _ = repos
    mine = make_ticket(ticket_repo, submitter="mallory")
    foreign = make_ticket(ticket_repo, submitter="bob")

    with pytest.raises(AuthorizationError):
        await service.add_relation(
            mine.id,
            TicketRelationCreate(target_ticket_id=foreign.id, relation_type="related"),
            make_user("mallory"),
        )
    assert ticket_repo.relations == {}


async def test_watcher_links_tickets_and_inverse_is_created(service, repos):
    ticket_repo, _, _ = repos
    a = make_ticket(ticket_repo, submitter="alice")
    b = make_ticket(ticket_repo, submitter="alice")

    relation = await service.add_relation(
        a.id,
        TicketRelationCreate(target_ticket_id=b.id, relation_type="blocks"),
        make_user("alice"),
    )

    types = sorted(
        (r.source_ticket_id == a.id, r.relation_type) for r in ticket_repo.relations.values()
    )
    assert types == [(False, "blocked_by"), (True, "blocks")]
    assert relation.created_by == "alice"


async def test_remove_relation_requires_access(service, repos):
    ticket_repo, _, _ = repos
    a = make_ticket(ticket_repo, submitter="bob")
    b = make_ticket(ticket_repo, submitter="bob")
    relation = await service.add_relation(
        a.id,
        TicketRelationCreate(target_ticket_id=b.id, relation_type="related"),
        make_user("bob"),
    )

    with pytest.raises(AuthorizationError):
        await service.remove_relation(a.id, relation.id, make_user("mallory"))
    assert relation.id in ticket_repo.relations

    await service.remove_relation(a.id, relation.id, make_user("bob"))
    assert ticket_repo.relations == {}
