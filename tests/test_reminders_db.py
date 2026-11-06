"""Reminder selection against a real Postgres (the SQL in list_tickets_needing_reminder).

Uses the DB_* settings with the database "<DB_NAME>_test". Locally the module is skipped when Postgres is unreachable; in CI
(CI=true) an unreachable database is a failure, not a skip.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings
from app.models import FollowUp, Queue, Ticket, TicketStatus
from app.reminders import check_and_send_reminders


class RecordingDispatcher:
    def __init__(self):
        self.staff = []
        self.submitter = []

    async def send_staff_reminder(self, ticket, queue_name, days_elapsed):
        self.staff.append(ticket.id)

    async def send_submitter_reminder(self, ticket, queue_name, days_elapsed):
        self.submitter.append(ticket.id)


# INTENTIONAL: the fixture drops and recreates every table, so it never touches DB_NAME itself,
# only its "<DB_NAME>_test" sibling (a dev database cannot be wiped by running pytest).
TEST_DB_NAME = (
    settings.db_name if settings.db_name.endswith("_test") else f"{settings.db_name}_test"
)
TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + f"/{TEST_DB_NAME}"


@pytest.fixture
async def session():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.drop_all)
            await conn.run_sync(SQLModel.metadata.create_all)
    except Exception as e:  # connection refused, bad credentials, missing database...
        await engine.dispose()
        if os.getenv("CI"):
            raise
        pytest.skip(f"Postgres not reachable: {e}")
    async with AsyncSession(engine, expire_on_commit=False) as s:
        s.add(Queue(id=1, name="General", slug="general"))
        await s.commit()
        yield s
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    await engine.dispose()


NOW = datetime.now(UTC)


def _ticket(**kwargs) -> Ticket:
    return Ticket(
        queue_id=1,
        title="Laptop broken",
        description="Screen flickers.",
        submitter_email="alice@example.com",
        submitter_username="alice",
        created_at=NOW - timedelta(days=5),
        **kwargs,
    )


def _followup(ticket_id: uuid.UUID, is_staff: bool, age: timedelta) -> FollowUp:
    return FollowUp(ticket_id=ticket_id, comment="x", is_staff=is_staff, created_at=NOW - age)


async def test_staff_reminder_selection_and_dedup(session):
    silent = _ticket(status=TicketStatus.OPEN, assigned_to="agent")
    answered = _ticket(status=TicketStatus.OPEN, assigned_to="agent")
    unassigned = _ticket(status=TicketStatus.OPEN)
    reminded_recently = _ticket(
        status=TicketStatus.IN_PROGRESS,
        assigned_to="agent",
        last_staff_reminder_at=NOW - timedelta(hours=12),
    )
    reminded_long_ago = _ticket(
        status=TicketStatus.IN_PROGRESS,
        assigned_to="agent",
        last_staff_reminder_at=NOW - timedelta(hours=25),
    )
    session.add_all([silent, answered, unassigned, reminded_recently, reminded_long_ago])
    await session.flush()
    session.add(_followup(answered.id, is_staff=True, age=timedelta(hours=3)))
    await session.commit()

    dispatcher = RecordingDispatcher()
    result = await check_and_send_reminders(session, dispatcher)

    assert set(dispatcher.staff) == {silent.id, reminded_long_ago.id}
    assert result == {"staff": 2, "submitter": 0}
    await session.refresh(silent)
    assert silent.last_staff_reminder_at is not None

    # Second run inside the 24h window sends nothing (dedup through last_staff_reminder_at).
    again = RecordingDispatcher()
    assert await check_and_send_reminders(session, again) == {"staff": 0, "submitter": 0}


async def test_submitter_reminder_only_for_silent_pending_tickets(session):
    waiting = _ticket(status=TicketStatus.PENDING)
    replied = _ticket(status=TicketStatus.PENDING)
    session.add_all([waiting, replied])
    await session.flush()
    session.add(_followup(waiting.id, is_staff=False, age=timedelta(days=3)))
    session.add(_followup(replied.id, is_staff=False, age=timedelta(hours=1)))
    await session.commit()

    dispatcher = RecordingDispatcher()
    await check_and_send_reminders(session, dispatcher)

    assert dispatcher.submitter == [waiting.id]
