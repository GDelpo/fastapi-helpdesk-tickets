# Tickets Admin Overhaul — Design Spec
**Date:** 2026-04-01
**Status:** Approved
**Scope:** tickets service (`tickets/`)

---

## Overview

This spec covers five coordinated improvements to the tickets service:

1. Fix the login `TypeError` that blocks dashboard access
2. Admin audit trail — every staff action recorded in the ticket timeline
3. Attachment deletion with per-role rules and audit
4. Queue/category management UI with parent-child hierarchy
5. Email reminders for unreviewed tickets (asyncio background task)

All changes are **backend + admin UI only** unless noted. The portal is not affected except for the attachment delete endpoint (which portal users can call within the 24h window).

---

## Section 1 — Login Bug Fix

### Problem

`POST /api/v1/login` raises an unhandled exception that `general_exception_handler` catches and returns as `{"success":false,"message":"Internal server error","details":{"type":"TypeError"}}`.

### Root Cause

`IdentityServiceClient.login()` returns the raw JSON from identidad. Identidad serializes its `LoginResponse` with `CamelModel` (alias_generator=to_camel), so the key is `"accessToken"`. The tickets login endpoint accesses `data["access_token"]` (snake_case), causing a `KeyError` or `TypeError` depending on the actual identidad response shape.

### Fix

**File:** `app/api.py`

```python
# Before
return LoginResponse(
    access_token=data["access_token"],
    token_type=data.get("token_type", "bearer"),
    expires_in=data.get("expires_in", 1800),
)

# After
access_token = data.get("accessToken") or data.get("access_token")
if not access_token:
    raise AuthenticationError("Respuesta de autenticación inválida")
return LoginResponse(
    access_token=access_token,
    token_type=data.get("tokenType") or data.get("token_type", "bearer"),
    expires_in=data.get("expiresIn") or data.get("expires_in", 1800),
)
```

**No DB changes. No schema changes.**

---

## Section 2 — Admin Actions + Audit Trail

### Current State

- `PATCH /tickets/{id}` updates fields but creates no audit record for admin-initiated changes.
- `FollowUp` model already has `new_status`, `new_priority`, `is_staff`, `user_id=None` — all fields needed for system events.
- `modal-edit` in `admin/ticket_detail.html` already shows status/priority/assigned selects but gives no feedback after save and doesn't reload the timeline.
- `bulk_update_tickets` endpoint skips audit entirely.

### Design

#### 2.1 `service.py` — Audit FollowUp creation

`TicketService.update_ticket()` already receives the actor (`current_user`) and the `data: TicketUpdate` payload. After applying changes, detect what changed and create a system `FollowUp` per significant change:

| Changed field | FollowUp comment | new_status / new_priority |
|---|---|---|
| `status` | `"Estado cambiado a {label} por @{actor}"` | `new_status=value` |
| `priority` | `"Prioridad cambiada a {label} por @{actor}"` | `new_priority=value` |
| `assigned_to` | `"Reasignado a @{new} por @{actor}"` | — |
| `resolution` | `"Resolución registrada por @{actor}"` | — |

Each `FollowUp` gets: `user_id=None`, `is_staff=True`, `is_public=True`, `user_name=actor.user_name`.

Multiple changes in one PATCH → one `FollowUp` per changed field (keeps timeline granular).

The existing `NotificationDispatcher` already handles watcher notifications on assignment — no change needed there.

#### 2.2 Bulk update audit

`bulk_update_tickets` endpoint currently iterates tickets and patches status silently. Change to call `service.update_ticket()` per ticket so audit FollowUps are created automatically. Drop the manual `ticket_repo.update()` loop.

#### 2.3 Admin UI feedback

In `admin/ticket_detail.html`, after the `form-edit` submit:
- Show success toast
- Call `loadTicket()` to reload the timeline (already exists as a function)
- Close the modal

No HTML structure changes needed.

#### 2.4 Scope of admin actions

All existing fields in `TicketUpdate` schema are available to admin: `status`, `priority`, `assigned_to`, `resolution`. No new fields needed. Permissions are already enforced by `AdminUser` dependency on the relevant endpoints.

---

## Section 3 — Attachment Deletion

### Rules

| Who | Can delete | Audit |
|---|---|---|
| Admin | Any attachment | FollowUp system event |
| Uploader (any role) | Own attachments uploaded within the last 24 hours | FollowUp system event |
| Other users | ❌ | — |

### API

**New endpoint:** `DELETE /tickets/{ticket_id}/attachments/{attachment_id}`

**Auth:** `CurrentUser` (not AdminUser — the service logic enforces the 24h+ownership rule for non-admins).

**Service logic (`service.py`):**
```
1. Load ticket (validates access via get_ticket)
2. Load attachment — 404 if not found or ticket_id mismatch
3. If not admin:
   a. attachment.uploaded_by != current_user.user_name → 403
   b. now - attachment.created_at > 24h → 403 ("Solo podés eliminar adjuntos dentro de las 24hs de haberlos subido")
4. Delete file from disk (attachment_storage)
5. Delete DB record
6. Create FollowUp: user_id=None, is_staff=True, comment="Adjunto '{filename}' eliminado por @{actor}"
7. Return 204
```

**New function in `attachment_storage.py`:** `delete_attachment_file(storage_name)` — removes file from disk, ignores if not found.

### Schema

No new schema needed. Response is `204 No Content`.

### UI

In `admin/ticket_detail.html`: add a delete button next to each attachment chip. Calls the endpoint, reloads ticket on success.

In `portal/ticket_detail.html`: same pattern, but the button only renders if `uploaded_by == currentUser && age < 24h` (checked client-side, enforced server-side).

---

## Section 4 — Queue Management UI

### Current State

API already has full CRUD for queues (`GET/POST/PATCH/DELETE /queues/`). The `admin/queues.html` template exists but its content is minimal. The `Queue` model has `parent_id`, `name`, `slug`, `description`, `email`, `assigned_to_id`, `is_active`, `sort_order`, `icon`, `color`.

### Design

#### 4.1 List view — hierarchy tree

Replace the current queues list with a two-level tree:

```
▼ Sistemas (parent, 12 tickets)
    ├── Hardware (child, 5 tickets)   [edit] [toggle]
    └── Software (child, 7 tickets)   [edit] [toggle]
▼ RRHH (parent, 3 tickets)
    └── Licencias (child, 3 tickets)  [edit] [toggle]
  Otros (root leaf, 1 ticket)         [edit] [toggle]
```

Parents are non-clickable group headers. Children are rows with ticket count badge.

Show inactive queues with muted style and a "reactivar" button instead of "edit".

#### 4.2 Create/Edit modal

Single modal `modal-queue` used for both create and edit. Fields:

| Field | Input | Notes |
|---|---|---|
| Nombre | text input | required |
| Slug | text input | required, auto-generated from name if empty |
| Categoría padre | select (optional) | `<option>` list of active root queues only |
| Descripción | textarea | optional |
| Email de notificación | email input | optional |
| Agente por defecto | user autocomplete | optional (existing pattern from ticket-form.js) |
| Icono | text input | Lucide icon name |
| Color | color picker (`<input type="color">`) | |
| Orden | number input | sort_order |

On create: POST `/queues/`. On edit: PATCH `/queues/{id}`.

#### 4.3 Deactivate / Reactivate

No hard delete if queue has tickets (already enforced in `QueueService.delete_queue`). Instead:
- "Desactivar" button → PATCH with `{ isActive: false }` → queue moves to "inactivas" section
- "Reactivar" button → PATCH with `{ isActive: true }`
- Hard delete only shown when `ticket_count == 0` and queue is inactive

#### 4.4 QueueUpdate schema

`QueueUpdate` must include `is_active`, `parent_id`, `assigned_to_id`, `icon`, `color`, `sort_order` as optional fields. Verify current schema covers all of these; add any missing.

---

## Section 5 — Email Reminders (Asyncio Background Task)

### Trigger Conditions

| Case | Condition | Recipient | Email template |
|---|---|---|---|
| **Staff reminder** | Ticket in `open` or `in_progress`, no FollowUp from staff (`is_staff=True`) since assignment, for `REMINDER_STAFF_DAYS` days | `assigned_to` | `ticket_reminder_staff.html` |
| **Submitter reminder** | Ticket in `pending`, no FollowUp from submitter (`is_staff=False`) for `REMINDER_SUBMITTER_DAYS` days | `submitter_email` | `ticket_reminder_submitter.html` |

### Configurable thresholds (`config.py`)

```python
reminder_staff_days: int = 2       # Days before reminding assigned staff
reminder_submitter_days: int = 2   # Days before reminding pending submitter
reminder_check_interval_hours: int = 6  # How often the background task runs
reminder_enabled: bool = True      # Kill switch
```

### Architecture

**`app/reminders.py`** — new module:

```
async def check_and_send_reminders(session, dispatcher) -> dict[str, int]:
    """
    Queries DB for tickets matching reminder criteria.
    Sends emails via dispatcher.
    Returns {"staff": N, "submitter": M} counts for logging.
    Idempotent: safe to call multiple times.
    """
```

**`main.py` lifespan** — new background task alongside `users_cache_refresher`:

```python
async def reminder_task():
    while True:
        await asyncio.sleep(settings.reminder_check_interval_hours * 3600)
        if not settings.reminder_enabled:
            continue
        async with async_session_maker() as session:
            dispatcher = await _build_dispatcher_for_background()
            result = await check_and_send_reminders(session, dispatcher)
            logger.info("Reminders sent", extra={"extra_fields": result})
```

**Admin trigger endpoint:**

```python
POST /api/v1/admin/reminders/run
# AdminUser only. Runs check_and_send_reminders immediately.
# Returns {"staff": N, "submitter": M}
```

This lets the admin trigger a manual check from the dashboard without waiting for the cycle.

### Repository query

`TicketRepository` gets a new method `list_tickets_needing_reminder(staff_cutoff, submitter_cutoff)` that returns two lists via two queries:

1. Tickets `status IN (open, in_progress)` where `assigned_to IS NOT NULL` and no `FollowUp` with `is_staff=True` exists newer than `staff_cutoff` datetime
2. Tickets `status = pending` where no `FollowUp` with `is_staff=False` exists newer than `submitter_cutoff` datetime

### Email templates

Two new Jinja2 email templates following the existing style (`ticket_assigned_staff.html` as reference):
- `ticket_reminder_staff.html` — "Recordatorio: el ticket #XXXX está esperando tu respuesta"
- `ticket_reminder_submitter.html` — "Tu solicitud #XXXX está pendiente de tu respuesta"

Both include ticket title, link to portal, and days elapsed.

### Trigger Conditions (precise)

- **Staff:** `MAX(followup.created_at WHERE is_staff=True)` is older than `now - staff_days`, or no staff followups exist and `ticket.created_at < now - staff_days`.
- **Submitter:** `MAX(followup.created_at WHERE is_staff=False)` is older than `now - submitter_days` (only for tickets in `pending` status).

### Deduplication

To avoid sending the same reminder every 6 hours, the system tracks the last reminder sent separately per type:

Add **two nullable datetime columns** to `Ticket` via migration:
- `last_staff_reminder_at` — updated when a staff reminder is sent
- `last_submitter_reminder_at` — updated when a submitter reminder is sent

Send staff reminder only if: ticket qualifies AND (`last_staff_reminder_at IS NULL` OR `last_staff_reminder_at < now - 24h`).
Send submitter reminder only if: ticket qualifies AND (`last_submitter_reminder_at IS NULL` OR `last_submitter_reminder_at < now - 24h`).

This guarantees at most one reminder per 24h per type per ticket, regardless of how often the task runs.

---

## Summary of Files Changed

| File | Change |
|---|---|
| `app/api.py` | Fix login key lookup; add `POST /admin/reminders/run` |
| `app/config.py` | Add `reminder_*` settings |
| `app/models.py` | Add `last_staff_reminder_at` and `last_submitter_reminder_at` to `Ticket` |
| `app/schemas.py` | Verify `QueueUpdate` has all fields; add `ReminderResult` schema |
| `app/service.py` | `update_ticket` creates audit FollowUps; attachment delete logic |
| `app/repository.py` | Add `list_tickets_needing_reminder()` |
| `app/attachment_storage.py` | Add `delete_attachment_file()` |
| `app/reminders.py` | New module — `check_and_send_reminders()` |
| `app/main.py` | Add reminder asyncio task in lifespan |
| `app/ui/templates/admin/ticket_detail.html` | Attachment delete button; audit FollowUp display; edit modal feedback |
| `app/ui/templates/admin/queues.html` | Full CRUD UI with hierarchy tree |
| `app/ui/templates/portal/ticket_detail.html` | Attachment delete button (24h rule) |
| `app/ui/templates/email/ticket_reminder_staff.html` | New email template |
| `app/ui/templates/email/ticket_reminder_submitter.html` | New email template |
| `alembic/versions/` | New migration: `last_staff_reminder_at`, `last_submitter_reminder_at` on `tickets` |

---

## Out of Scope

- Portal queue browsing / category navigation (not requested)
- Drag-and-drop sort_order (complexity not justified — numeric field editable in form)
- Read receipts or per-notification deduplication (reminder dedup via `last_reminder_sent_at` is sufficient)
- ARQ / Redis (not needed for this task frequency)
