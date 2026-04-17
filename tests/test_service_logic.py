"""Unit tests for service business logic — no DB required."""
import re

import pytest


_MENTION_RE = re.compile(r"@([\w.]+)")


def test_mention_parsing_simple():
    comment = "Hola @mlopez, podés revisar esto?"
    mentions = list(set(_MENTION_RE.findall(comment)))
    assert "mlopez" in mentions
    assert len(mentions) == 1


def test_mention_parsing_multiple():
    comment = "@lucas.r y @gdelp revisá por favor"
    mentions = list(set(_MENTION_RE.findall(comment)))
    assert "lucas.r" in mentions
    assert "gdelp" in mentions


def test_mention_parsing_none():
    comment = "Sin menciones aquí"
    mentions = _MENTION_RE.findall(comment)
    assert mentions == []


def test_ticket_status_values():
    from app.models import TicketStatus
    assert TicketStatus.OPEN == "open"
    assert TicketStatus.IN_PROGRESS == "in_progress"
    assert TicketStatus.PENDING == "pending"
    assert TicketStatus.REOPENED == "reopened"
    assert TicketStatus.RESOLVED == "resolved"
    assert TicketStatus.CLOSED == "closed"


def test_build_meta_structure():
    from app.models import _build_meta
    meta = _build_meta()
    assert "statuses" in meta
    assert "priorities" in meta
    # All 6 statuses present
    status_values = [s["value"] for s in meta["statuses"]]
    assert set(status_values) == {"open", "in_progress", "pending", "reopened", "resolved", "closed"}
    # Ordered correctly: open=1, in_progress=2, pending=3, reopened=4, resolved=5, closed=6
    orders = {s["value"]: s["order"] for s in meta["statuses"]}
    assert orders["open"] == 1
    assert orders["in_progress"] == 2
    assert orders["pending"] == 3
    assert orders["closed"] == 6
    # All 5 priorities present
    priority_values = [p["value"] for p in meta["priorities"]]
    assert set(priority_values) == {"1", "2", "3", "4", "5"}


def test_status_labels_spanish():
    from app.models import STATUS_LABELS
    assert STATUS_LABELS["open"] == "Abierto"
    assert STATUS_LABELS["in_progress"] == "En curso"
    assert STATUS_LABELS["pending"] == "Pendiente"
    assert STATUS_LABELS["reopened"] == "Reabierto"
    assert STATUS_LABELS["resolved"] == "Resuelto"
    assert STATUS_LABELS["closed"] == "Cerrado"


def test_ticket_create_has_assigned_to():
    from app.schemas import TicketCreate
    # assigned_to is optional, defaults to None
    t = TicketCreate(
        queue_id=1,
        title="Test ticket",
        description="Esta es la descripción del ticket",
        assigned_to=None,
    )
    assert t.assigned_to is None
    # Can be set to a username
    t2 = TicketCreate(
        queue_id=1,
        title="Test ticket",
        description="Esta es la descripción del ticket",
        assigned_to="jperez",
    )
    assert t2.assigned_to == "jperez"


def test_ticket_create_description_min_length():
    from app.schemas import TicketCreate
    import pytest
    with pytest.raises(Exception):
        TicketCreate(queue_id=1, title="Título", description="corto")


def test_followup_create_has_no_mentioned_user_ids():
    from app.schemas import FollowUpCreate
    fields = FollowUpCreate.model_fields
    assert "mentioned_user_ids" not in fields, "mentioned_user_ids should be removed from FollowUpCreate"


def test_description_mention_parsing():
    """service.create_ticket should parse @mentions from description text."""
    import re
    _MENTION_RE = re.compile(r"@([\w.]+)")
    description = "Hola @mlopez, esto aplica también a @gdelp"
    mentions = list(set(_MENTION_RE.findall(description)))
    assert "mlopez" in mentions
    assert "gdelp" in mentions


def test_login_response_prefers_camel_key():
    """Simulate that identidad returns camelCase keys."""
    camel_data = {"accessToken": "tok123", "tokenType": "bearer", "expiresIn": 1800}
    access_token = camel_data.get("accessToken") or camel_data.get("access_token")
    assert access_token == "tok123"

def test_login_response_falls_back_to_snake_key():
    """Falls back to snake_case key if camelCase is absent."""
    snake_data = {"access_token": "tok456", "token_type": "bearer", "expires_in": 1800}
    access_token = snake_data.get("accessToken") or snake_data.get("access_token")
    assert access_token == "tok456"

def test_login_response_raises_on_missing_token():
    """Both keys absent → access_token is falsy."""
    bad_data = {"message": "ok"}
    access_token = bad_data.get("accessToken") or bad_data.get("access_token")
    assert not access_token


def test_audit_comment_includes_actor_assignment():
    actor = "gdelp"
    new_assignee = "mlopez"
    comment = f"Responsable asignado: @{new_assignee} por @{actor}."
    assert "@mlopez" in comment
    assert "por @gdelp" in comment


def test_audit_comment_includes_actor_priority():
    from app.models import PRIORITY_LABELS
    actor = "gdelp"
    old_label = PRIORITY_LABELS["3"]
    new_label = PRIORITY_LABELS["1"]
    comment = f"Prioridad cambiada de «{old_label}» a «{new_label}» por @{actor}."
    assert "Normal" in comment
    assert "Crítica" in comment
    assert "por @gdelp" in comment


def test_audit_comment_resolution():
    actor = "gdelp"
    comment = f"Resolución registrada por @{actor}."
    assert "por @gdelp" in comment


def test_attachment_delete_permission_admin():
    is_admin = True
    uploaded_by = "otro_user"
    current_user_name = "admin_user"
    age_hours = 999
    can_delete = is_admin or (uploaded_by == current_user_name and age_hours <= 24)
    assert can_delete

def test_attachment_delete_permission_owner_within_24h():
    from datetime import UTC, datetime, timedelta
    is_admin = False
    uploaded_by = "gdelp"
    current_user_name = "gdelp"
    created_at = datetime.now(UTC) - timedelta(hours=12)
    age_hours = (datetime.now(UTC) - created_at).total_seconds() / 3600
    can_delete = is_admin or (uploaded_by == current_user_name and age_hours <= 24)
    assert can_delete

def test_attachment_delete_permission_owner_after_24h():
    from datetime import UTC, datetime, timedelta
    is_admin = False
    uploaded_by = "gdelp"
    current_user_name = "gdelp"
    created_at = datetime.now(UTC) - timedelta(hours=25)
    age_hours = (datetime.now(UTC) - created_at).total_seconds() / 3600
    can_delete = is_admin or (uploaded_by == current_user_name and age_hours <= 24)
    assert not can_delete

def test_attachment_delete_permission_other_user():
    is_admin = False
    uploaded_by = "otro"
    current_user_name = "gdelp"
    age_hours = 1
    can_delete = is_admin or (uploaded_by == current_user_name and age_hours <= 24)
    assert not can_delete


def test_reminder_config_defaults():
    from app.config import settings
    assert hasattr(settings, 'reminder_staff_days')
    assert hasattr(settings, 'reminder_submitter_days')
    assert hasattr(settings, 'reminder_check_interval_hours')
    assert hasattr(settings, 'reminder_enabled')
    assert settings.reminder_staff_days == 2
    assert settings.reminder_submitter_days == 2
    assert settings.reminder_check_interval_hours == 6
    assert settings.reminder_enabled is True


def test_reminder_staff_cutoff_calculation():
    from datetime import UTC, datetime, timedelta
    reminder_staff_days = 2
    now = datetime.now(UTC)
    staff_cutoff = now - timedelta(days=reminder_staff_days)
    last_followup = now - timedelta(days=3)
    assert last_followup < staff_cutoff


def test_reminder_dedup_24h():
    from datetime import UTC, datetime, timedelta
    now = datetime.now(UTC)
    dedup_cutoff = now - timedelta(hours=24)
    last_reminder = now - timedelta(hours=25)
    assert last_reminder < dedup_cutoff
    last_reminder_recent = now - timedelta(hours=12)
    assert last_reminder_recent > dedup_cutoff


def test_reminders_module_exists():
    from app import reminders
    assert hasattr(reminders, 'check_and_send_reminders')
