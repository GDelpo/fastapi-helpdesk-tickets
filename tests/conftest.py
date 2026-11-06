"""Shared fixtures: in-memory fakes for the repositories and a TestClient wired to them.

The fakes implement only what TicketService / the routers call, with the same return shapes
as app/repository.py. SQL itself is covered by tests/test_reminders_db.py against Postgres.
"""

import os
import uuid

import pytest
from fastapi.testclient import TestClient

# Fake settings: no identity, email service or Postgres is reached by the API tests.
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("REMINDER_ENABLED", "true")

from app.dependencies import get_current_user, get_ticket_service  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Queue, Ticket, TicketWatcher  # noqa: E402
from app.schemas import TokenData  # noqa: E402
from app.service import TicketService  # noqa: E402


class FakeSession:
    """Stands in for AsyncSession inside attachment_storage.save_attachments."""

    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass


class FakeTicketRepo:
    def __init__(self):
        self.session = FakeSession()
        self.tickets: dict[uuid.UUID, Ticket] = {}
        self.followups = []
        self.watchers: list[TicketWatcher] = []
        self.relations = {}
        self.attachments = {}

    async def get_by_id(self, ticket_id, with_relations=False):
        ticket = self.tickets.get(ticket_id)
        if ticket and with_relations:
            ticket.__dict__["followups"] = [f for f in self.followups if f.ticket_id == ticket_id]
            ticket.__dict__["watchers"] = [w for w in self.watchers if w.ticket_id == ticket_id]
        return ticket

    async def create(self, ticket):
        self.tickets[ticket.id] = ticket
        return ticket

    async def update(self, ticket):
        self.tickets[ticket.id] = ticket
        return ticket

    async def add_followup(self, followup):
        self.followups.append(followup)
        return followup

    async def get_followup(self, followup_id):
        return next((f for f in self.followups if f.id == followup_id), None)

    async def get_watcher(self, ticket_id, user_id):
        return next(
            (w for w in self.watchers if w.ticket_id == ticket_id and w.user_id == user_id), None
        )

    async def add_watcher(self, watcher):
        self.watchers.append(watcher)
        return watcher

    async def get_watchers(self, ticket_id):
        return [w for w in self.watchers if w.ticket_id == ticket_id]

    async def get_attachment(self, attachment_id):
        return self.attachments.get(attachment_id)

    async def get_attachments(self, ticket_id):
        return [a for a in self.attachments.values() if a.ticket_id == ticket_id]

    async def delete_attachment(self, attachment):
        self.attachments.pop(attachment.id, None)

    async def get_children(self, parent_id):
        return [t for t in self.tickets.values() if t.parent_id == parent_id]

    async def get_relations(self, ticket_id):
        return [r for r in self.relations.values() if r.source_ticket_id == ticket_id]

    async def get_relation(self, relation_id):
        return self.relations.get(relation_id)

    async def add_relation(self, relation):
        self.relations[relation.id] = relation
        return relation

    async def remove_relation(self, relation_id):
        self.relations.pop(relation_id, None)

    async def remove_inverse_relation(self, source_id, target_id, relation_type):
        for r in list(self.relations.values()):
            if r.source_ticket_id == source_id and r.target_ticket_id == target_id:
                self.relations.pop(r.id)


class FakeQueueRepo:
    def __init__(self):
        self.queues = {1: Queue(id=1, name="General", slug="general")}

    async def get_by_id(self, queue_id):
        return self.queues.get(queue_id)


class FakeNotifRepo:
    def __init__(self):
        self.created = []

    async def create(self, notif):
        self.created.append(notif)
        return notif


class FakeDispatcher:
    """Records the email notifications the service asks for."""

    def __init__(self):
        self.calls = []

    async def ticket_created(self, *args, **kwargs):
        self.calls.append(("ticket_created", args, kwargs))

    async def ticket_assigned(self, *args, **kwargs):
        self.calls.append(("ticket_assigned", args, kwargs))

    async def ticket_status_changed(self, *args, **kwargs):
        self.calls.append(("ticket_status_changed", args, kwargs))

    async def followup_added(self, *args, **kwargs):
        self.calls.append(("followup_added", args, kwargs))


def make_user(name: str, role: str = "employee") -> TokenData:
    return TokenData(id=f"id-{name}", user_name=name, mail=f"{name}@example.com", role=role)


def make_ticket(repo: FakeTicketRepo, submitter: str = "alice", **kwargs) -> Ticket:
    ticket = Ticket(
        queue_id=1,
        title=kwargs.pop("title", "Printer on fire"),
        description="The printer is on fire again.",
        submitter_email=f"{submitter}@example.com",
        submitter_username=submitter,
        priority=kwargs.pop("priority", 3),  # an int, as it comes back from the database
        **kwargs,
    )
    repo.tickets[ticket.id] = ticket
    repo.watchers.append(TicketWatcher(ticket_id=ticket.id, user_id=submitter))
    return ticket


@pytest.fixture
def repos():
    return FakeTicketRepo(), FakeQueueRepo(), FakeNotifRepo()


@pytest.fixture
def service(repos):
    ticket_repo, queue_repo, notif_repo = repos
    return TicketService(ticket_repo=ticket_repo, queue_repo=queue_repo, notif_repo=notif_repo)


@pytest.fixture
def client_as(service):
    """TestClient whose caller is the given user. No lifespan: identity is never contacted."""

    def make(user: TokenData) -> TestClient:
        app.dependency_overrides[get_ticket_service] = lambda: service
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()
