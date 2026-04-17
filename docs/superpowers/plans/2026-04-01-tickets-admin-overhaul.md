# Tickets Admin Overhaul — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement five coordinated improvements: fix login TypeError, enhance audit trail and bulk actions, add attachment deletion with per-role rules, overhaul queue management UI, and add asyncio email reminder system.

**Architecture:** Backend changes layer on existing SQLModel/asyncpg patterns; all audit FollowUps use `user_id=None, is_staff=True` for system events. Email reminders use an asyncio background task (no ARQ/Redis needed), with deduplication via two new nullable datetime columns on `Ticket`.

**Tech Stack:** FastAPI 0.128, SQLModel 0.0.31, asyncpg, Alembic 1.14, Jinja2 3.1.6, vanilla JS + Tailwind v4

---

## File Map

| File | Action | Reason |
|---|---|---|
| `app/api.py` | Modify | Fix login key lookup; fix `bulk_update_tickets`; add DELETE attachment endpoint; add `POST /admin/reminders/run` |
| `app/config.py` | Modify | Add `reminder_*` settings |
| `app/models.py` | Modify | Add `last_staff_reminder_at`, `last_submitter_reminder_at` to `Ticket` |
| `app/schemas.py` | Modify | Add `slug` to `QueueUpdate`; add `ReminderResult` schema |
| `app/service.py` | Modify | Update audit comment format; add resolution audit; add `delete_attachment()` method |
| `app/repository.py` | Modify | Add `delete_attachment()` and `list_tickets_needing_reminder()` |
| `app/notifications.py` | Modify | Add `send_staff_reminder()` and `send_submitter_reminder()` to `NotificationDispatcher` |
| `app/reminders.py` | Create | `check_and_send_reminders()` function |
| `app/main.py` | Modify | Add reminder asyncio background task in lifespan |
| `app/ui/templates/admin/ticket_detail.html` | Modify | Add attachment delete buttons |
| `app/ui/templates/admin/queues.html` | Rewrite | Full hierarchy tree + create/edit/deactivate modal |
| `app/ui/templates/portal/ticket_detail.html` | Modify | Add attachment delete button (24h rule) |
| `app/ui/templates/email/ticket_reminder_staff.html` | Create | Staff reminder email |
| `app/ui/templates/email/ticket_reminder_submitter.html` | Create | Submitter reminder email |
| `alembic/versions/003_reminder_columns.py` | Create | Add `last_staff_reminder_at`, `last_submitter_reminder_at` |
| `tests/test_service_logic.py` | Modify | Add unit tests for new logic |

---

## Task 1: Fix Login TypeError

**Files:**
- Modify: `app/api.py` (line 91–95)
- Modify: `tests/test_service_logic.py`

### Background

`identidad` serializes `LoginResponse` with `alias_generator=to_camel`, so the key is `accessToken`. The tickets login endpoint accesses `data["access_token"]` (snake_case) → `KeyError` caught by `general_exception_handler` → `TypeError` in JSON.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_service_logic.py — add at end

def test_login_response_prefers_camel_key():
    """Simulate that identidad returns camelCase keys."""
    from app.schemas import LoginResponse
    # Should not raise — camel key present
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
```

- [ ] **Step 2: Run test to verify it passes conceptually (these are pure-logic tests)**

```
cd C:/Users/gdelponte/Desktop/fastapi_microservicios/tickets
pytest tests/test_service_logic.py::test_login_response_prefers_camel_key tests/test_service_logic.py::test_login_response_falls_back_to_snake_key tests/test_service_logic.py::test_login_response_raises_on_missing_token -v
```

Expected: PASS (all three)

- [ ] **Step 3: Apply fix to `app/api.py`**

Find in `app/api.py` (around line 88):
```python
    data = await identity_client.login(form_data.username, form_data.password, client)
    if not data:
        raise AuthenticationError("Usuario o contraseña incorrectos")
    return LoginResponse(
        access_token=data["access_token"],
        token_type=data.get("token_type", "bearer"),
        expires_in=data.get("expires_in", 1800),
    )
```

Replace with:
```python
    data = await identity_client.login(form_data.username, form_data.password, client)
    if not data:
        raise AuthenticationError("Usuario o contraseña incorrectos")
    access_token = data.get("accessToken") or data.get("access_token")
    if not access_token:
        raise AuthenticationError("Respuesta de autenticación inválida")
    return LoginResponse(
        access_token=access_token,
        token_type=data.get("tokenType") or data.get("token_type", "bearer"),
        expires_in=data.get("expiresIn") or data.get("expires_in", 1800),
    )
```

- [ ] **Step 4: Run all tests**

```
pytest -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add app/api.py tests/test_service_logic.py
git commit -m "fix(api): handle camelCase keys in login response from identidad"
```

---

## Task 2: Audit Trail — Resolution and Comment Format

**Files:**
- Modify: `app/service.py` (lines 261–307)
- Modify: `tests/test_service_logic.py`

### Background

The existing `update_ticket` already creates audit FollowUps for assignment, priority, and status changes, but:
1. The comments don't include "por @{actor}" — makes the timeline less informative for admins reading who made the change
2. There is no FollowUp for `resolution` field changes

- [ ] **Step 1: Write the failing test**

```python
# tests/test_service_logic.py — add at end

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
```

- [ ] **Step 2: Run tests**

```
pytest tests/test_service_logic.py::test_audit_comment_includes_actor_assignment tests/test_service_logic.py::test_audit_comment_includes_actor_priority tests/test_service_logic.py::test_audit_comment_resolution -v
```

Expected: PASS (pure string tests)

- [ ] **Step 3: Update `app/service.py` — assignment FollowUp comment (around line 261)**

Find:
```python
            await self.ticket_repo.add_followup(FollowUp(
                ticket_id=ticket_id,
                user_name=current_user.user_name,
                comment=f"Responsable asignado: @{new_assignee}.",
                is_public=True,
                is_staff=self._is_admin(current_user),
            ))
```

Replace with:
```python
            await self.ticket_repo.add_followup(FollowUp(
                ticket_id=ticket_id,
                user_name=current_user.user_name,
                comment=f"Responsable asignado: @{new_assignee} por @{current_user.user_name}.",
                is_public=True,
                is_staff=True,
            ))
```

- [ ] **Step 4: Update `app/service.py` — priority FollowUp comment (around line 274)**

Find:
```python
            await self.ticket_repo.add_followup(FollowUp(
                ticket_id=ticket_id,
                user_name=current_user.user_name,
                comment=f"Prioridad cambiada de «{old_pri_label}» a «{new_pri_label}».",
                is_public=True,
                is_staff=self._is_admin(current_user),
                new_priority=str(new_priority),
            ))
```

Replace with:
```python
            await self.ticket_repo.add_followup(FollowUp(
                ticket_id=ticket_id,
                user_name=current_user.user_name,
                comment=f"Prioridad cambiada de «{old_pri_label}» a «{new_pri_label}» por @{current_user.user_name}.",
                is_public=True,
                is_staff=True,
                new_priority=str(new_priority),
            ))
```

- [ ] **Step 5: Update `app/service.py` — status FollowUp comment (around line 300)**

Find:
```python
            await self.ticket_repo.add_followup(FollowUp(
                ticket_id=ticket_id,
                user_name=current_user.user_name,
                comment=f"Estado cambiado de «{old_label}» a «{new_label}».",
                is_public=True,
                is_staff=self._is_admin(current_user),
                new_status=str(new_status),
            ))
```

Replace with:
```python
            await self.ticket_repo.add_followup(FollowUp(
                ticket_id=ticket_id,
                user_name=current_user.user_name,
                comment=f"Estado cambiado de «{old_label}» a «{new_label}» por @{current_user.user_name}.",
                is_public=True,
                is_staff=True,
                new_status=str(new_status),
            ))
```

- [ ] **Step 6: Add resolution audit FollowUp — add after the status-change block in `update_ticket`, before `self._populate_defaults(ticket)` (around line 308)**

```python
        # Resolution change audit trail
        new_resolution = update_fields.get("resolution")
        if "resolution" in update_fields and new_resolution:
            await self.ticket_repo.add_followup(FollowUp(
                ticket_id=ticket_id,
                user_name=current_user.user_name,
                comment=f"Resolución registrada por @{current_user.user_name}.",
                is_public=True,
                is_staff=True,
            ))
```

- [ ] **Step 7: Run all tests**

```
pytest -v
```

Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add app/service.py tests/test_service_logic.py
git commit -m "feat(service): update audit FollowUp comments with actor, add resolution audit"
```

---

## Task 3: Fix bulk_update_tickets to Use Service Layer

**Files:**
- Modify: `app/api.py` (lines 258–272)

### Background

`bulk_update_tickets` manually patches `ticket.status` + `ticket_repo.update()`, bypassing the service layer. This means no audit FollowUps are created for bulk status changes. Fix: call `service.update_ticket()` per ticket.

- [ ] **Step 1: Update `bulk_update_tickets` in `app/api.py`**

Find:
```python
@router.patch("/tickets/bulk", status_code=200, tags=["Tickets"])
async def bulk_update_tickets(
    payload: BulkStatusUpdate,
    _: AdminUser,
    ticket_repo: TicketRepository = Depends(get_ticket_repository),
):
    """Bulk-update status for multiple tickets. Admin only."""
    updated = 0
    for ticket_id in payload.ids:
        ticket = await ticket_repo.get_by_id(ticket_id)
        if ticket:
            ticket.status = payload.status
            await ticket_repo.update(ticket)
            updated += 1
    return {"updated": updated}
```

Replace with:
```python
@router.patch("/tickets/bulk", status_code=200, tags=["Tickets"])
async def bulk_update_tickets(
    payload: BulkStatusUpdate,
    current_user: AdminUser,
    request: Request,
    service: TicketService = Depends(get_ticket_service),
):
    """Bulk-update status for multiple tickets. Admin only. Creates audit FollowUps."""
    from app.schemas import TicketUpdate
    dispatcher = await _build_dispatcher(request)
    updated = 0
    for ticket_id in payload.ids:
        try:
            await service.update_ticket(
                ticket_id,
                TicketUpdate(status=payload.status),
                current_user,
                dispatcher=dispatcher,
            )
            updated += 1
        except Exception:
            pass
    return {"updated": updated}
```

- [ ] **Step 2: Run tests**

```
pytest -v
```

Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add app/api.py
git commit -m "fix(api): bulk_update_tickets now uses service layer for audit trail"
```

---

## Task 4: Attachment Delete — Repository + Service + Endpoint

**Files:**
- Modify: `app/repository.py`
- Modify: `app/service.py`
- Modify: `app/api.py`
- Modify: `tests/test_service_logic.py`

### Background

`delete_attachment_file(storage_name)` already exists in `attachment_storage.py`. We need:
1. `TicketRepository.delete_attachment(attachment)` — removes DB record
2. `TicketService.delete_attachment(ticket_id, attachment_id, current_user)` — enforces rules + creates audit FollowUp
3. `DELETE /tickets/{ticket_id}/attachments/{attachment_id}` endpoint

Delete rules:
- Admin: can delete any attachment
- Non-admin: own attachments only, within 24h of upload

- [ ] **Step 1: Write unit tests for delete permission logic**

```python
# tests/test_service_logic.py — add at end
from datetime import UTC, datetime, timedelta

def test_attachment_delete_permission_admin():
    """Admin can always delete."""
    is_admin = True
    uploaded_by = "otro_user"
    current_user = "admin_user"
    age_hours = 999
    # Admin bypass
    can_delete = is_admin or (uploaded_by == current_user and age_hours <= 24)
    assert can_delete

def test_attachment_delete_permission_owner_within_24h():
    """Uploader can delete within 24h."""
    is_admin = False
    uploaded_by = "gdelp"
    current_user = "gdelp"
    created_at = datetime.now(UTC) - timedelta(hours=12)
    age_hours = (datetime.now(UTC) - created_at).total_seconds() / 3600
    can_delete = is_admin or (uploaded_by == current_user and age_hours <= 24)
    assert can_delete

def test_attachment_delete_permission_owner_after_24h():
    """Uploader cannot delete after 24h."""
    is_admin = False
    uploaded_by = "gdelp"
    current_user = "gdelp"
    created_at = datetime.now(UTC) - timedelta(hours=25)
    age_hours = (datetime.now(UTC) - created_at).total_seconds() / 3600
    can_delete = is_admin or (uploaded_by == current_user and age_hours <= 24)
    assert not can_delete

def test_attachment_delete_permission_other_user():
    """Other user cannot delete."""
    is_admin = False
    uploaded_by = "otro"
    current_user = "gdelp"
    age_hours = 1
    can_delete = is_admin or (uploaded_by == current_user and age_hours <= 24)
    assert not can_delete
```

- [ ] **Step 2: Run tests**

```
pytest tests/test_service_logic.py::test_attachment_delete_permission_admin tests/test_service_logic.py::test_attachment_delete_permission_owner_within_24h tests/test_service_logic.py::test_attachment_delete_permission_owner_after_24h tests/test_service_logic.py::test_attachment_delete_permission_other_user -v
```

Expected: PASS

- [ ] **Step 3: Add `delete_attachment()` to `app/repository.py`**

After the `get_attachments` method (around line 255), add:

```python
    async def delete_attachment(self, attachment: Attachment) -> None:
        await self.session.delete(attachment)
        await self.session.commit()
```

- [ ] **Step 4: Add `delete_attachment()` to `app/service.py`**

Add this method to `TicketService` after `add_followup` (look for the `assign_ticket` method, add after it or near other attachment methods):

```python
    async def delete_attachment(
        self,
        ticket_id: uuid.UUID,
        attachment_id: uuid.UUID,
        current_user: TokenData,
    ) -> None:
        """Delete an attachment. Admin: any. Non-admin: own, within 24h."""
        from datetime import UTC, datetime, timedelta
        from app.attachment_storage import delete_attachment_file

        ticket = await self.ticket_repo.get_by_id(ticket_id)
        if not ticket:
            raise EntityNotFoundError("Ticket", ticket_id)

        attachment = await self.ticket_repo.get_attachment(attachment_id)
        if not attachment or attachment.ticket_id != ticket_id:
            raise EntityNotFoundError("Attachment", attachment_id)

        if not self._is_admin(current_user):
            if attachment.uploaded_by != current_user.user_name:
                raise AuthorizationError("No podés eliminar adjuntos de otros usuarios")
            age = (datetime.now(UTC) - attachment.created_at).total_seconds() / 3600
            if age > 24:
                raise AuthorizationError(
                    "Solo podés eliminar adjuntos dentro de las 24hs de haberlos subido"
                )

        storage_name = attachment.storage_name
        filename = attachment.filename
        await self.ticket_repo.delete_attachment(attachment)
        await delete_attachment_file(storage_name)

        await self.ticket_repo.add_followup(FollowUp(
            ticket_id=ticket_id,
            user_name=current_user.user_name,
            comment=f"Adjunto '{filename}' eliminado por @{current_user.user_name}.",
            is_public=True,
            is_staff=True,
        ))
```

- [ ] **Step 5: Add `DELETE /tickets/{ticket_id}/attachments/{attachment_id}` endpoint to `app/api.py`**

Add after `close_ticket` endpoint (around line 296):

```python
@router.delete(
    "/tickets/{ticket_id}/attachments/{attachment_id}",
    status_code=204,
    tags=["Tickets"],
)
async def delete_attachment(
    ticket_id: Annotated[uuid.UUID, Path(...)],
    attachment_id: Annotated[uuid.UUID, Path(...)],
    current_user: CurrentUser,
    service: TicketService = Depends(get_ticket_service),
):
    """Delete an attachment. Admin: any. Non-admin: own uploads within 24h."""
    await service.delete_attachment(ticket_id, attachment_id, current_user)
```

- [ ] **Step 6: Run all tests**

```
pytest -v
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/repository.py app/service.py app/api.py tests/test_service_logic.py
git commit -m "feat(attachments): add delete endpoint with per-role rules and audit trail"
```

---

## Task 5: Admin UI — Attachment Delete Buttons

**Files:**
- Modify: `app/ui/templates/admin/ticket_detail.html`

### Background

The `renderAttachmentLink` function in `attachments.js` renders a download button. Admins need a delete button next to each attachment. After delete, we call `loadTicket()` to refresh.

- [ ] **Step 1: Find the attachments rendering section in `admin/ticket_detail.html`**

Around line 232:
```javascript
    const tkAtts = (t.attachments || []).filter(a => !a.followupId);
    const tkAttBlock = document.getElementById('tk-attachments-block');
    const tkAttList  = document.getElementById('tk-attachments');
    if (tkAttBlock && tkAtts.length) {
      tkAttBlock.classList.remove('hidden');
      tkAttList.innerHTML = tkAtts.map(a => renderAttachmentLink(a)).join('');
    }
```

Replace with:
```javascript
    const tkAtts = (t.attachments || []).filter(a => !a.followupId);
    const tkAttBlock = document.getElementById('tk-attachments-block');
    const tkAttList  = document.getElementById('tk-attachments');
    if (tkAttBlock && tkAtts.length) {
      tkAttBlock.classList.remove('hidden');
      tkAttList.innerHTML = tkAtts.map(a => `
        <div class="inline-flex items-center gap-1">
          ${renderAttachmentLink(a)}
          <button type="button" onclick="deleteAttachment('${a.ticketId}','${a.id}',${esc(JSON.stringify(a.filename))})"
            class="btn btn-ghost p-1 text-xs ml-1" style="color: var(--color-error);" title="Eliminar adjunto">
            <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
          </button>
        </div>`).join('');
      lucide.createIcons();
    }
```

- [ ] **Step 2: Also update FollowUp attachment rendering in the same file**

Find around line 274 in `admin/ticket_detail.html`:
```javascript
          ${(f.attachments || []).length ? `<div class="flex flex-wrap gap-2 mt-1">${(f.attachments || []).map(a => renderAttachmentLink(a)).join('')}</div>` : ''}
```

Replace with:
```javascript
          ${(f.attachments || []).length ? `<div class="flex flex-wrap gap-2 mt-1">${(f.attachments || []).map(a => `<div class="inline-flex items-center gap-1">${renderAttachmentLink(a)}<button type="button" onclick="deleteAttachment('${a.ticketId}','${a.id}',${esc(JSON.stringify(a.filename))})" class="btn btn-ghost p-1" style="color: var(--color-error);" title="Eliminar"><i data-lucide="trash-2" class="w-3.5 h-3.5"></i></button></div>`).join('')}</div>` : ''}
```

- [ ] **Step 3: Add `deleteAttachment` function to the page script in `admin/ticket_detail.html`**

Find the closing `</script>` tag in `{% block page_scripts %}` (near the bottom). Before the last `</script>`, add:

```javascript
  async function deleteAttachment(ticketId, attachmentId, filename) {
    if (!confirm(`¿Eliminar adjunto "${filename}"?`)) return;
    const resp = await apiFetch(`/tickets/${ticketId}/attachments/${attachmentId}`, {
      method: 'DELETE',
    });
    if (resp && resp.status === 204) {
      showToast('Adjunto eliminado');
      loadTicket();
    } else {
      const err = await resp?.json().catch(() => ({}));
      showToast(err.message || 'Error al eliminar adjunto', 'error');
    }
  }
```

- [ ] **Step 4: Commit**

```bash
git add app/ui/templates/admin/ticket_detail.html
git commit -m "feat(admin-ui): add attachment delete button in ticket detail"
```

---

## Task 6: Portal UI — Attachment Delete Button (24h rule)

**Files:**
- Modify: `app/ui/templates/portal/ticket_detail.html`

### Background

Portal users can only delete their own attachments within 24h. The check is enforced server-side, but we show the button client-side when the user is the uploader and the attachment age < 24h (to avoid confusing UX). The current user's `userName` is available via `getUser().userName`.

- [ ] **Step 1: Find `renderTicket` function in `portal/ticket_detail.html`**

Find around line 329:
```javascript
    const ticketAttachments = (t.attachments || []).filter(a => !a.followupId);
    const tkAttBlock = document.getElementById('ticket-attachments-block');
    const tkAttList = document.getElementById('ticket-attachments');
    if (ticketAttachments.length) {
      tkAttBlock.classList.remove('hidden');
      tkAttList.innerHTML = ticketAttachments.map(renderAttachmentLink).join('');
    } else {
      tkAttBlock.classList.add('hidden');
      tkAttList.innerHTML = '';
    }
```

Replace with:
```javascript
    const ticketAttachments = (t.attachments || []).filter(a => !a.followupId);
    const tkAttBlock = document.getElementById('ticket-attachments-block');
    const tkAttList = document.getElementById('ticket-attachments');
    if (ticketAttachments.length) {
      tkAttBlock.classList.remove('hidden');
      tkAttList.innerHTML = ticketAttachments.map(a => renderAttachmentWithDelete(a)).join('');
    } else {
      tkAttBlock.classList.add('hidden');
      tkAttList.innerHTML = '';
    }
```

- [ ] **Step 2: Also update FollowUp attachment rendering in `renderFollowUps` (around line 419)**

Find:
```javascript
      const attachments = (byFollowup[f.id] || []).map(renderAttachmentLink).join('');
```

Replace with:
```javascript
      const attachments = (byFollowup[f.id] || []).map(a => renderAttachmentWithDelete(a)).join('');
```

- [ ] **Step 3: Add `renderAttachmentWithDelete` and `deleteAttachment` functions to the page script**

Find the `<script>` block for page scripts (look for `function renderTicket` or the main script tag). Add before `loadTicket()` is called:

```javascript
  function canDeleteAttachment(a) {
    const user = getUser();
    if (!user) return false;
    const isOwner = a.uploadedBy === user.userName;
    const ageMs = Date.now() - new Date(a.createdAt).getTime();
    const ageHours = ageMs / 3600000;
    return isOwner && ageHours <= 24;
  }

  function renderAttachmentWithDelete(a) {
    const deleteBtn = canDeleteAttachment(a)
      ? `<button type="button" onclick="deleteAttachment('${a.ticketId}','${a.id}',${JSON.stringify(esc(a.filename))})"
           class="btn btn-ghost p-1 text-xs" style="color:#ef4444;" title="Eliminar adjunto">
           <i class="fas fa-trash text-xs"></i>
         </button>`
      : '';
    return `<div class="inline-flex items-center gap-1">${renderAttachmentLink(a)}${deleteBtn}</div>`;
  }

  async function deleteAttachment(ticketId, attachmentId, filename) {
    if (!confirm(`¿Eliminar adjunto "${filename}"?`)) return;
    const token = getToken();
    if (!token) { goLogin(); return; }
    const resp = await fetch(API_BASE + `/tickets/${ticketId}/attachments/${attachmentId}`, {
      method: 'DELETE',
      headers: { 'Authorization': 'Bearer ' + token },
    });
    if (resp && resp.status === 204) {
      showToast('Adjunto eliminado', 'success');
      loadTicket();
    } else {
      const err = await resp?.json().catch(() => ({}));
      showToast(err.message || 'Error al eliminar adjunto', 'error');
    }
  }
```

Note: In the portal, `API_BASE`, `getToken()`, `getUser()`, `goLogin()`, `showToast()` are defined in base.html scripts.

- [ ] **Step 4: Commit**

```bash
git add app/ui/templates/portal/ticket_detail.html
git commit -m "feat(portal-ui): add attachment delete button with 24h rule"
```

---

## Task 7: Queue Management UI Overhaul

**Files:**
- Modify: `app/schemas.py` (add `slug` to `QueueUpdate`)
- Rewrite: `app/ui/templates/admin/queues.html`

### Background

The current `queues.html` shows a flat table with minimal "nueva categoría" modal. Replace with:
- Hierarchy tree (parents as group headers, children as rows)
- Unified `modal-queue` for create/edit
- Deactivate/reactivate/hard-delete buttons
- Ticket count per queue

`QueueUpdate` is missing `slug` field.

- [ ] **Step 1: Add `slug` to `QueueUpdate` in `app/schemas.py`**

Find in `app/schemas.py`:
```python
class QueueUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    email: str | None = None
    assigned_to_id: str | None = None
    is_active: bool | None = None
    parent_id: int | None = None
    sort_order: int | None = None
    icon: str | None = None
    color: str | None = None
```

Replace with:
```python
class QueueUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    slug: str | None = Field(None, min_length=1, max_length=100, pattern="^[a-z0-9-]+$")
    description: str | None = None
    email: str | None = None
    assigned_to_id: str | None = None
    is_active: bool | None = None
    parent_id: int | None = None
    sort_order: int | None = None
    icon: str | None = None
    color: str | None = None
```

- [ ] **Step 2: Run tests**

```
pytest -v
```

Expected: PASS

- [ ] **Step 3: Rewrite `app/ui/templates/admin/queues.html`**

```html
{% extends "admin/base.html" %}
{% block title %}Categorías — Mesa de Ayuda{% endblock %}

{% block page_content %}
<div class="space-y-4">
  <div class="flex items-center justify-between">
    <h2 class="text-xl font-bold">Categorías</h2>
    <button onclick="openQueueModal(null)" class="btn btn-primary" data-admin-only>
      <i data-lucide="plus" class="w-4 h-4"></i>
      Nueva Categoría
    </button>
  </div>

  <div class="card overflow-hidden" id="queues-tree">
    <div class="py-10 text-center">
      <div class="skeleton h-4 w-40 mx-auto mb-2"></div>
      <div class="skeleton h-4 w-32 mx-auto"></div>
    </div>
  </div>
</div>

<!-- Modal: Crear / Editar Categoría -->
<div id="modal-queue" class="modal-overlay hidden">
  <div class="modal max-w-xl w-full">
    <div class="flex items-center justify-between">
      <h3 class="text-lg font-semibold" id="modal-queue-title">Nueva Categoría</h3>
      <button onclick="closeModal('modal-queue')" class="btn btn-ghost p-1"><i data-lucide="x" class="w-5 h-5"></i></button>
    </div>
    <form id="form-queue" class="space-y-3 mt-2">
      <input type="hidden" id="queue-id" value="" />
      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="block text-xs uppercase tracking-wider mb-1" style="color: var(--color-text-muted);">Nombre *</label>
          <input type="text" id="queue-name" class="input" required />
        </div>
        <div>
          <label class="block text-xs uppercase tracking-wider mb-1" style="color: var(--color-text-muted);">Slug *</label>
          <input type="text" id="queue-slug" class="input" required pattern="[a-z0-9-]+" />
        </div>
      </div>
      <div>
        <label class="block text-xs uppercase tracking-wider mb-1" style="color: var(--color-text-muted);">Categoría padre</label>
        <select id="queue-parent" class="select w-full">
          <option value="">— Sin padre (categoría raíz) —</option>
        </select>
      </div>
      <div>
        <label class="block text-xs uppercase tracking-wider mb-1" style="color: var(--color-text-muted);">Descripción</label>
        <textarea id="queue-desc" class="textarea" rows="2"></textarea>
      </div>
      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="block text-xs uppercase tracking-wider mb-1" style="color: var(--color-text-muted);">Email de notificación</label>
          <input type="email" id="queue-email" class="input" />
        </div>
        <div>
          <label class="block text-xs uppercase tracking-wider mb-1" style="color: var(--color-text-muted);">Agente por defecto</label>
          <input type="text" id="queue-agent" class="input" placeholder="username" />
        </div>
      </div>
      <div class="grid grid-cols-3 gap-3">
        <div>
          <label class="block text-xs uppercase tracking-wider mb-1" style="color: var(--color-text-muted);">Icono (Lucide)</label>
          <input type="text" id="queue-icon" class="input" placeholder="inbox" />
        </div>
        <div>
          <label class="block text-xs uppercase tracking-wider mb-1" style="color: var(--color-text-muted);">Color</label>
          <input type="color" id="queue-color" class="input h-10 p-1 cursor-pointer" value="#3b82f6" />
        </div>
        <div>
          <label class="block text-xs uppercase tracking-wider mb-1" style="color: var(--color-text-muted);">Orden</label>
          <input type="number" id="queue-order" class="input" value="0" min="0" />
        </div>
      </div>
      <div id="queue-error" class="hidden text-sm rounded-lg px-3 py-2" style="background: var(--color-error-soft); color: var(--color-error);"></div>
      <div class="flex justify-end gap-2 pt-2">
        <button type="button" onclick="closeModal('modal-queue')" class="btn btn-ghost">Cancelar</button>
        <button type="submit" class="btn btn-primary">Guardar</button>
      </div>
    </form>
  </div>
</div>
{% endblock %}

{% block page_scripts %}
<script>
  let allQueues = [];

  async function loadQueues() {
    const resp = await apiFetch('/queues/?active_only=false');
    if (!resp || !resp.ok) return;
    allQueues = await resp.json();
    renderTree(allQueues);
  }

  function renderTree(queues) {
    const container = document.getElementById('queues-tree');
    if (!queues.length) {
      container.innerHTML = '<div class="py-10 text-center text-sm" style="color: var(--color-text-muted);">Sin categorías</div>';
      return;
    }

    const parents = queues.filter(q => !q.parentId);
    const children = queues.filter(q => q.parentId);
    const childrenByParent = {};
    for (const c of children) {
      if (!childrenByParent[c.parentId]) childrenByParent[c.parentId] = [];
      childrenByParent[c.parentId].push(c);
    }

    // Standalone root nodes (no children)
    const standalone = parents.filter(p => !(childrenByParent[p.id] || []).length);
    // Parent group nodes
    const groups = parents.filter(p => (childrenByParent[p.id] || []).length > 0);

    let html = '';

    function rowActions(q) {
      if (!q.isActive) {
        return `
          <button onclick="toggleQueue(${q.id}, true)" class="btn btn-ghost text-xs" title="Reactivar">
            <i data-lucide="eye" class="w-3.5 h-3.5"></i> Reactivar
          </button>
          ${q.ticketCount === 0 ? `<button onclick="deleteQueue(${q.id})" class="btn btn-danger text-xs" title="Eliminar">
            <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
          </button>` : ''}
        `;
      }
      return `
        <button onclick="openQueueModal(${q.id})" class="btn btn-ghost text-xs" title="Editar">
          <i data-lucide="edit-2" class="w-3.5 h-3.5"></i>
        </button>
        <button onclick="toggleQueue(${q.id}, false)" class="btn btn-ghost text-xs" title="Desactivar">
          <i data-lucide="eye-off" class="w-3.5 h-3.5"></i>
        </button>
      `;
    }

    function queueRow(q, isChild) {
      const indent = isChild ? 'pl-8' : 'pl-4';
      const muted = !q.isActive ? 'opacity-50' : '';
      const icon = q.icon ? `<i data-lucide="${esc(q.icon)}" class="w-4 h-4 shrink-0" style="color:${esc(q.color||'#94a3b8')};"></i>` : '';
      return `
        <div class="flex items-center gap-3 px-4 py-3 border-b ${muted}" style="border-color: var(--color-border-subtle);">
          <div class="${indent} flex items-center gap-2 flex-1 min-w-0">
            ${isChild ? '<span style="color: var(--color-text-muted);">└</span>' : ''}
            ${icon}
            <div class="min-w-0">
              <div class="text-sm font-medium truncate" style="color: var(--color-text-primary);">${esc(q.name)}</div>
              <div class="text-xs truncate" style="color: var(--color-text-muted);">${esc(q.slug)}</div>
            </div>
          </div>
          <div class="text-xs flex-shrink-0" style="color: var(--color-text-muted);">${q.ticketCount ?? 0} tickets</div>
          <div class="flex-shrink-0">${q.isActive ? '<span class="badge badge-open text-[10px]">Activa</span>' : '<span class="badge badge-closed text-[10px]">Inactiva</span>'}</div>
          <div class="flex gap-1 flex-shrink-0" data-admin-only>${rowActions(q)}</div>
        </div>`;
    }

    // Groups with children
    for (const parent of groups) {
      const kids = childrenByParent[parent.id] || [];
      const allCount = kids.reduce((s, c) => s + (c.ticketCount || 0), 0);
      const muted = !parent.isActive ? 'opacity-50' : '';
      html += `
        <div class="px-4 py-2 flex items-center gap-2 ${muted}" style="background: var(--color-surface-2); border-bottom: 1px solid var(--color-border-subtle);">
          <i data-lucide="folder" class="w-4 h-4" style="color: var(--color-accent);"></i>
          <span class="text-sm font-semibold" style="color: var(--color-text-primary);">${esc(parent.name)}</span>
          <span class="text-xs ml-1" style="color: var(--color-text-muted);">${allCount} tickets</span>
          <div class="ml-auto flex gap-1" data-admin-only>${rowActions(parent)}</div>
        </div>`;
      for (const child of kids) {
        html += queueRow(child, true);
      }
    }

    // Standalone roots
    for (const q of standalone) {
      html += queueRow(q, false);
    }

    container.innerHTML = html;
    lucide.createIcons();
  }

  function openQueueModal(id) {
    const errEl = document.getElementById('queue-error');
    errEl.classList.add('hidden');
    document.getElementById('modal-queue-title').textContent = id ? 'Editar Categoría' : 'Nueva Categoría';
    document.getElementById('queue-id').value = id || '';

    // Populate parent select (only active root queues, exclude current queue)
    const parentSelect = document.getElementById('queue-parent');
    parentSelect.innerHTML = '<option value="">— Sin padre (categoría raíz) —</option>';
    for (const q of allQueues) {
      if (!q.parentId && q.isActive && q.id !== id) {
        const opt = document.createElement('option');
        opt.value = q.id;
        opt.textContent = q.name;
        parentSelect.appendChild(opt);
      }
    }

    if (id) {
      const q = allQueues.find(x => x.id === id);
      if (q) {
        document.getElementById('queue-name').value = q.name || '';
        document.getElementById('queue-slug').value = q.slug || '';
        document.getElementById('queue-parent').value = q.parentId || '';
        document.getElementById('queue-desc').value = q.description || '';
        document.getElementById('queue-email').value = q.email || '';
        document.getElementById('queue-agent').value = q.assignedToId || '';
        document.getElementById('queue-icon').value = q.icon || '';
        document.getElementById('queue-color').value = q.color || '#3b82f6';
        document.getElementById('queue-order').value = q.sortOrder ?? 0;
      }
    } else {
      document.getElementById('form-queue').reset();
      document.getElementById('queue-color').value = '#3b82f6';
    }

    openModal('modal-queue');
  }

  document.getElementById('queue-name').addEventListener('input', (e) => {
    const idVal = document.getElementById('queue-id').value;
    if (!idVal) {
      // Auto-generate slug only on create
      document.getElementById('queue-slug').value = e.target.value
        .toLowerCase()
        .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
        .replace(/\s+/g, '-')
        .replace(/[^a-z0-9-]/g, '');
    }
  });

  document.getElementById('form-queue').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errEl = document.getElementById('queue-error');
    errEl.classList.add('hidden');
    const id = document.getElementById('queue-id').value;
    const body = {
      name: document.getElementById('queue-name').value.trim(),
      slug: document.getElementById('queue-slug').value.trim(),
      parentId: document.getElementById('queue-parent').value ? parseInt(document.getElementById('queue-parent').value) : null,
      description: document.getElementById('queue-desc').value.trim() || null,
      email: document.getElementById('queue-email').value.trim() || null,
      assignedToId: document.getElementById('queue-agent').value.trim() || null,
      icon: document.getElementById('queue-icon').value.trim() || null,
      color: document.getElementById('queue-color').value || null,
      sortOrder: parseInt(document.getElementById('queue-order').value) || 0,
    };

    const url = id ? `/queues/${id}` : '/queues/';
    const method = id ? 'PATCH' : 'POST';
    const resp = await apiFetch(url, {
      method,
      body: JSON.stringify(body),
      headers: { 'Content-Type': 'application/json' },
    });

    if (resp && (resp.status === 200 || resp.status === 201)) {
      closeModal('modal-queue');
      showToast(id ? 'Categoría actualizada' : 'Categoría creada');
      loadQueues();
    } else {
      const err = await resp?.json().catch(() => ({}));
      errEl.textContent = err.message || 'Error al guardar';
      errEl.classList.remove('hidden');
    }
  });

  async function toggleQueue(id, isActive) {
    const resp = await apiFetch('/queues/' + id, {
      method: 'PATCH',
      body: JSON.stringify({ isActive }),
      headers: { 'Content-Type': 'application/json' },
    });
    if (resp && resp.ok) {
      showToast(isActive ? 'Categoría reactivada' : 'Categoría desactivada');
      loadQueues();
    } else {
      showToast('Error al actualizar', 'error');
    }
  }

  async function deleteQueue(id) {
    if (!confirm('¿Eliminar esta categoría permanentemente?')) return;
    const resp = await apiFetch('/queues/' + id, { method: 'DELETE' });
    if (resp && (resp.status === 200 || resp.status === 204)) {
      showToast('Categoría eliminada');
      loadQueues();
    } else {
      const err = await resp?.json().catch(() => ({}));
      showToast(err.message || 'Error al eliminar', 'error');
    }
  }

  loadQueues();
</script>
{% endblock %}
```

- [ ] **Step 4: Commit**

```bash
git add app/schemas.py app/ui/templates/admin/queues.html
git commit -m "feat(admin-ui): full queue management UI with hierarchy tree and CRUD modal"
```

---

## Task 8: Reminder Config + Model + Migration

**Files:**
- Modify: `app/config.py`
- Modify: `app/models.py`
- Modify: `app/schemas.py` (add `ReminderResult`)
- Create: `alembic/versions/003_reminder_columns.py`
- Modify: `tests/test_service_logic.py`

- [ ] **Step 1: Write tests for config defaults**

```python
# tests/test_service_logic.py — add at end

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
```

- [ ] **Step 2: Run test to verify it fails (settings don't have these fields yet)**

```
pytest tests/test_service_logic.py::test_reminder_config_defaults -v
```

Expected: FAIL with `AttributeError`

- [ ] **Step 3: Add reminder settings to `app/config.py`**

In `app/config.py`, add after the `portal_base_url` field (before `# Validation`):

```python
    # =========================================================================
    # Email Reminders (asyncio background task)
    # =========================================================================
    reminder_staff_days: int = 2          # Days with no staff followup → remind assigned agent
    reminder_submitter_days: int = 2      # Days with no submitter followup on pending → remind submitter
    reminder_check_interval_hours: int = 6  # How often the background task runs
    reminder_enabled: bool = True         # Kill switch
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/test_service_logic.py::test_reminder_config_defaults -v
```

Expected: PASS

- [ ] **Step 5: Add reminder timestamp columns to `Ticket` in `app/models.py`**

Find in `app/models.py`:
```python
    resolution: str | None = None
```

Add after it:
```python
    last_staff_reminder_at: datetime | None = Field(default=None, sa_type=_TZ)
    last_submitter_reminder_at: datetime | None = Field(default=None, sa_type=_TZ)
```

- [ ] **Step 6: Add `ReminderResult` schema to `app/schemas.py`**

Add at the end of `app/schemas.py`:
```python
# =============================================================================
# Reminders
# =============================================================================

class ReminderResult(BaseModel):
    staff: int = 0
    submitter: int = 0
```

- [ ] **Step 7: Create Alembic migration `alembic/versions/003_reminder_columns.py`**

```python
"""Add reminder timestamp columns to tickets table.

Revision ID: 003
Revises: 002
Create Date: 2026-04-01
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("last_staff_reminder_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tickets",
        sa.Column("last_submitter_reminder_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tickets", "last_submitter_reminder_at")
    op.drop_column("tickets", "last_staff_reminder_at")
```

- [ ] **Step 8: Run all tests**

```
pytest -v
```

Expected: all PASS

- [ ] **Step 9: Commit**

```bash
git add app/config.py app/models.py app/schemas.py alembic/versions/003_reminder_columns.py tests/test_service_logic.py
git commit -m "feat(reminders): add reminder config settings, Ticket columns, migration, and ReminderResult schema"
```

---

## Task 9: Repository — list_tickets_needing_reminder

**Files:**
- Modify: `app/repository.py`
- Modify: `tests/test_service_logic.py`

### Background

The reminder query needs two groups of tickets:
1. **Staff:** status IN (open, in_progress), `assigned_to IS NOT NULL`, latest staff FollowUp (`is_staff=True`) older than `staff_cutoff`, or no staff FollowUps and ticket older than `staff_cutoff`; also dedup: `last_staff_reminder_at IS NULL OR last_staff_reminder_at < now - 24h`
2. **Submitter:** status = pending, latest non-staff FollowUp (`is_staff=False`) older than `submitter_cutoff`; also dedup: `last_submitter_reminder_at IS NULL OR last_submitter_reminder_at < now - 24h`

- [ ] **Step 1: Write a unit test for the cutoff logic (pure datetime math)**

```python
# tests/test_service_logic.py — add at end
from datetime import UTC, datetime, timedelta

def test_reminder_staff_cutoff_calculation():
    reminder_staff_days = 2
    now = datetime.now(UTC)
    staff_cutoff = now - timedelta(days=reminder_staff_days)
    # Ticket updated 3 days ago → needs reminder
    last_followup = now - timedelta(days=3)
    assert last_followup < staff_cutoff

def test_reminder_dedup_24h():
    now = datetime.now(UTC)
    dedup_cutoff = now - timedelta(hours=24)
    # Last reminder sent 25h ago → eligible again
    last_reminder = now - timedelta(hours=25)
    assert last_reminder < dedup_cutoff
    # Last reminder sent 12h ago → not eligible
    last_reminder_recent = now - timedelta(hours=12)
    assert last_reminder_recent > dedup_cutoff
```

- [ ] **Step 2: Run tests**

```
pytest tests/test_service_logic.py::test_reminder_staff_cutoff_calculation tests/test_service_logic.py::test_reminder_dedup_24h -v
```

Expected: PASS

- [ ] **Step 3: Add `list_tickets_needing_reminder` to `app/repository.py`**

First, ensure these imports are at the top of `repository.py`. Check current imports and add any missing:
```python
from datetime import datetime
from sqlalchemy import func, or_
```

Then add the method to `TicketRepository` (after `delete_attachment`, or at the end of the class before any non-class code):

```python
    async def list_tickets_needing_reminder(
        self,
        staff_cutoff: datetime,
        submitter_cutoff: datetime,
        reminder_dedup_cutoff: datetime,
    ) -> tuple[list[Ticket], list[Ticket]]:
        """
        Returns (staff_tickets, submitter_tickets) needing email reminders.

        staff_tickets: open/in_progress, assigned, no staff followup since staff_cutoff,
                       and last_staff_reminder_at is older than reminder_dedup_cutoff or NULL.

        submitter_tickets: pending, no submitter followup since submitter_cutoff,
                           and last_submitter_reminder_at is older than reminder_dedup_cutoff or NULL.
        """
        # --- Staff reminder query ---
        # Subquery: max created_at of staff followups per ticket
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

        # --- Submitter reminder query ---
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
```

Make sure `FollowUp`, `TicketStatus` are imported in `repository.py`. Check the top-of-file imports; if `FollowUp` is not there, add it to the model imports line.

- [ ] **Step 4: Run all tests**

```
pytest -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/repository.py tests/test_service_logic.py
git commit -m "feat(repository): add list_tickets_needing_reminder query method"
```

---

## Task 10: Reminder Email Templates + NotificationDispatcher Methods

**Files:**
- Create: `app/ui/templates/email/ticket_reminder_staff.html`
- Create: `app/ui/templates/email/ticket_reminder_submitter.html`
- Modify: `app/notifications.py`

### Background

Two new email templates following the style of `ticket_assigned_staff.html`. Two new methods on `NotificationDispatcher`.

- [ ] **Step 1: Create `app/ui/templates/email/ticket_reminder_staff.html`**

```html
<!--[if mso]><table width="600" cellpadding="0" cellspacing="0" border="0" align="center"><tr><td><![endif]-->
<table width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;margin:0 auto;background-color:#ffffff;font-family:Arial,Helvetica,sans-serif;border-collapse:collapse;">

    <!-- Header -->
    <tr>
      <td bgcolor="#1d4ed8" style="background-color:#1d4ed8;padding:28px 32px 24px;text-align:center;">
        <div style="font-size:13px;font-weight:700;color:#bfdbfe;letter-spacing:.08em;text-transform:uppercase;margin-bottom:6px;">NOBLE SEGUROS</div>
        <div style="font-size:22px;font-weight:800;color:#ffffff;line-height:1.2;">Mesa de Ayuda</div>
        <div style="margin-top:10px;display:inline-block;background-color:#fef3c7;border-radius:6px;padding:4px 14px;">
          <span style="font-size:12px;font-weight:700;color:#92400e;">&#9200; Ticket sin respuesta</span>
        </div>
      </td>
    </tr>

    <!-- Cuerpo -->
    <tr>
      <td style="padding:28px 32px 16px;">
        <p style="margin:0 0 16px;font-size:14px;color:#4b5563;line-height:1.6;">
          El ticket <strong>#{{ ticket_id }}</strong> lleva <strong>{{ days_elapsed }} día{{ 's' if days_elapsed != 1 else '' }}</strong> sin respuesta de tu parte. Por favor revisalo a la brevedad.
        </p>
      </td>
    </tr>

    <!-- Card ticket -->
    <tr>
      <td style="padding:0 32px 20px;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;border-collapse:collapse;overflow:hidden;">
          <tr>
            <td bgcolor="#eff6ff" style="background-color:#eff6ff;padding:10px 16px;border-bottom:1px solid #dbeafe;">
              <span style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#1e40af;">Detalle del ticket</span>
            </td>
          </tr>
          <tr>
            <td style="padding:14px 16px;">
              <div style="font-size:15px;font-weight:700;color:#111827;margin-bottom:10px;">{{ ticket_title }}</div>
              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
                <tr>
                  <td width="25%" style="font-size:11px;color:#9ca3af;font-weight:600;padding-bottom:4px;text-transform:uppercase;letter-spacing:.04em;">ID</td>
                  <td width="25%" style="font-size:11px;color:#9ca3af;font-weight:600;padding-bottom:4px;text-transform:uppercase;letter-spacing:.04em;">Categor&iacute;a</td>
                  <td width="25%" style="font-size:11px;color:#9ca3af;font-weight:600;padding-bottom:4px;text-transform:uppercase;letter-spacing:.04em;">Estado</td>
                  <td width="25%" style="font-size:11px;color:#9ca3af;font-weight:600;padding-bottom:4px;text-transform:uppercase;letter-spacing:.04em;">Solicitante</td>
                </tr>
                <tr>
                  <td><code style="font-family:monospace;font-size:11px;background:#e2e8f0;color:#374151;padding:2px 6px;border-radius:3px;">{{ ticket_id }}</code></td>
                  <td style="font-size:13px;color:#374151;">{{ ticket_queue }}</td>
                  <td><span style="display:inline-block;background-color:#dbeafe;color:#1e40af;font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px;">{{ ticket_status }}</span></td>
                  <td style="font-size:13px;color:#374151;">{{ submitter_name }}</td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </td>
    </tr>

    <!-- CTA -->
    <tr>
      <td style="padding:4px 32px 28px;">
        <table cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
          <tr>
            <td bgcolor="#1d4ed8" style="background-color:#1d4ed8;border-radius:8px;padding:0;">
              <a href="{{ portal_url }}" style="display:inline-block;padding:12px 24px;font-size:14px;font-weight:700;color:#ffffff;text-decoration:none;">
                Ver ticket &rarr;
              </a>
            </td>
          </tr>
        </table>
      </td>
    </tr>

{% include "email/_footer.html" %}

</table>
<!--[if mso]></td></tr></table><![endif]-->
```

- [ ] **Step 2: Create `app/ui/templates/email/ticket_reminder_submitter.html`**

```html
<!--[if mso]><table width="600" cellpadding="0" cellspacing="0" border="0" align="center"><tr><td><![endif]-->
<table width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;margin:0 auto;background-color:#ffffff;font-family:Arial,Helvetica,sans-serif;border-collapse:collapse;">

    <!-- Header -->
    <tr>
      <td bgcolor="#1d4ed8" style="background-color:#1d4ed8;padding:28px 32px 24px;text-align:center;">
        <div style="font-size:13px;font-weight:700;color:#bfdbfe;letter-spacing:.08em;text-transform:uppercase;margin-bottom:6px;">NOBLE SEGUROS</div>
        <div style="font-size:22px;font-weight:800;color:#ffffff;line-height:1.2;">Mesa de Ayuda</div>
        <div style="margin-top:10px;display:inline-block;background-color:#fef3c7;border-radius:6px;padding:4px 14px;">
          <span style="font-size:12px;font-weight:700;color:#92400e;">&#9203; Tu solicitud espera tu respuesta</span>
        </div>
      </td>
    </tr>

    <!-- Cuerpo -->
    <tr>
      <td style="padding:28px 32px 16px;">
        <p style="margin:0 0 16px;font-size:14px;color:#4b5563;line-height:1.6;">
          Tu solicitud <strong>#{{ ticket_id }}</strong> est&aacute; pendiente de tu respuesta desde hace <strong>{{ days_elapsed }} día{{ 's' if days_elapsed != 1 else '' }}</strong>. Si ya fue resuelto, marcalo como resuelto en el portal. Si necesit&aacute;s agregar m&aacute;s informaci&oacute;n, respond&eacute; en el hilo.
        </p>
      </td>
    </tr>

    <!-- Card ticket -->
    <tr>
      <td style="padding:0 32px 20px;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;border-collapse:collapse;overflow:hidden;">
          <tr>
            <td bgcolor="#fefce8" style="background-color:#fefce8;padding:10px 16px;border-bottom:1px solid #fde68a;">
              <span style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#92400e;">Tu solicitud pendiente</span>
            </td>
          </tr>
          <tr>
            <td style="padding:14px 16px;">
              <div style="font-size:15px;font-weight:700;color:#111827;margin-bottom:10px;">{{ ticket_title }}</div>
              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
                <tr>
                  <td width="33%" style="font-size:11px;color:#9ca3af;font-weight:600;padding-bottom:4px;text-transform:uppercase;letter-spacing:.04em;">ID</td>
                  <td width="33%" style="font-size:11px;color:#9ca3af;font-weight:600;padding-bottom:4px;text-transform:uppercase;letter-spacing:.04em;">Categor&iacute;a</td>
                  <td width="33%" style="font-size:11px;color:#9ca3af;font-weight:600;padding-bottom:4px;text-transform:uppercase;letter-spacing:.04em;">Estado</td>
                </tr>
                <tr>
                  <td><code style="font-family:monospace;font-size:11px;background:#e2e8f0;color:#374151;padding:2px 6px;border-radius:3px;">{{ ticket_id }}</code></td>
                  <td style="font-size:13px;color:#374151;">{{ ticket_queue }}</td>
                  <td><span style="display:inline-block;background-color:#fef3c7;color:#92400e;font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px;">{{ ticket_status }}</span></td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </td>
    </tr>

    <!-- CTA -->
    <tr>
      <td style="padding:4px 32px 28px;">
        <table cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
          <tr>
            <td bgcolor="#1d4ed8" style="background-color:#1d4ed8;border-radius:8px;padding:0;">
              <a href="{{ portal_url }}" style="display:inline-block;padding:12px 24px;font-size:14px;font-weight:700;color:#ffffff;text-decoration:none;">
                Ver mi solicitud &rarr;
              </a>
            </td>
          </tr>
        </table>
      </td>
    </tr>

{% include "email/_footer.html" %}

</table>
<!--[if mso]></td></tr></table><![endif]-->
```

- [ ] **Step 3: Add reminder entries to `_TEMPLATES` in `app/notifications.py`**

Find in `app/notifications.py`:
```python
_TEMPLATES: dict[str, tuple] = {
    "ticket-opened":         (lambda t: f"[{t.id.hex[:8]}] Ticket recibido: {t.title}",                   "email/ticket_opened.html"),
    ...
    "ticket-resolved":       (lambda t: f"[{t.id.hex[:8]}] Resuelto: {t.title}",                         "email/ticket_resolved.html"),
}
```

Add two entries at the end of the dict (before the closing `}`):
```python
    "ticket-reminder-staff":     (lambda t: f"[{t.id.hex[:8]}] Recordatorio: ticket sin respuesta: {t.title}",  "email/ticket_reminder_staff.html"),
    "ticket-reminder-submitter": (lambda t: f"[{t.id.hex[:8]}] Tu solicitud espera tu respuesta: {t.title}",    "email/ticket_reminder_submitter.html"),
```

- [ ] **Step 4: Add `send_staff_reminder` and `send_submitter_reminder` methods to `NotificationDispatcher` in `app/notifications.py`**

Add these two methods at the end of the `NotificationDispatcher` class (after the last existing method):

```python
    async def send_staff_reminder(
        self,
        ticket: Ticket,
        queue_name: str,
        days_elapsed: int,
    ) -> None:
        """Send reminder email to the assigned staff member."""
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
        """Send reminder email to the ticket submitter."""
        if not ticket.submitter_email:
            return
        await self._send(
            ticket,
            "ticket-reminder-submitter",
            ticket.submitter_email,
            queue_name=queue_name,
            days_elapsed=str(days_elapsed),
        )
```

Note: the `_send` method passes `**kwargs` to `send_ticket_email`. The `days_elapsed` variable will be passed as an extra kwarg, but `send_ticket_email` only uses a fixed set of kwargs (see `variables` dict in `send_ticket_email`). We need to add `days_elapsed` to the `variables` dict in `send_ticket_email`.

- [ ] **Step 5: Add `days_elapsed` to the `variables` dict in `send_ticket_email`**

Find in `app/notifications.py`:
```python
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
    }
```

Replace with:
```python
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
```

Also update the `send_ticket_email` function signature to accept `days_elapsed`:

Find:
```python
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
) -> None:
```

Replace with:
```python
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
```

- [ ] **Step 6: Run all tests**

```
pytest -v
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/notifications.py app/ui/templates/email/ticket_reminder_staff.html app/ui/templates/email/ticket_reminder_submitter.html
git commit -m "feat(notifications): add reminder email templates and NotificationDispatcher methods"
```

---

## Task 11: app/reminders.py — Core Reminder Logic

**Files:**
- Create: `app/reminders.py`
- Modify: `tests/test_service_logic.py`

- [ ] **Step 1: Write unit test for the reminders module structure**

```python
# tests/test_service_logic.py — add at end

def test_reminders_module_exists():
    from app import reminders
    assert hasattr(reminders, 'check_and_send_reminders')
```

- [ ] **Step 2: Run to verify it fails**

```
pytest tests/test_service_logic.py::test_reminders_module_exists -v
```

Expected: FAIL with `ImportError` or `AttributeError`

- [ ] **Step 3: Create `app/reminders.py`**

```python
"""Email reminder background task for unreviewed tickets."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings
from app.logger import get_logger
from app.notifications import NotificationDispatcher
from app.repository import TicketRepository, QueueRepository

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
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/test_service_logic.py::test_reminders_module_exists -v
```

Expected: PASS

- [ ] **Step 5: Run all tests**

```
pytest -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add app/reminders.py tests/test_service_logic.py
git commit -m "feat(reminders): add check_and_send_reminders core logic module"
```

---

## Task 12: Background Task + Admin Trigger Endpoint

**Files:**
- Modify: `app/main.py`
- Modify: `app/api.py`

### Background

`main.py` already has a `users_cache_refresher` asyncio background task in the lifespan. We add a `reminder_task` in the same pattern. We also need to expose a manual trigger `POST /admin/reminders/run`.

For the background task, we need a `_build_dispatcher_for_background()` helper that creates a `NotificationDispatcher` without a request (uses app state stored at startup).

- [ ] **Step 1: Read the lifespan function in `app/main.py` to find the exact injection point**

Open `app/main.py` and find the `users_cache_refresher` task (around line 87). The structure is:
```python
    app_instance.state._users_cache_task = asyncio.create_task(users_cache_refresher())

    yield

    logger.info("Shutting down Tickets Service")
    app_instance.state._users_cache_task.cancel()
```

- [ ] **Step 2: Add the reminder background task to `app/main.py`**

Find in `app/main.py`:
```python
    app_instance.state._users_cache_task = asyncio.create_task(users_cache_refresher())

    yield

    logger.info("Shutting down Tickets Service")
    app_instance.state._users_cache_task.cancel()
```

Replace with:
```python
    app_instance.state._users_cache_task = asyncio.create_task(users_cache_refresher())

    async def reminder_task():
        while True:
            await asyncio.sleep(settings.reminder_check_interval_hours * 3600)
            if not settings.reminder_enabled:
                continue
            try:
                from app.database import async_session_maker
                from app.reminders import check_and_send_reminders
                from app.notifications import NotificationDispatcher
                svc_token = app_instance.state.service_token or ""
                dispatcher = NotificationDispatcher(
                    http_client=app_instance.state.http_client,
                    service_token=svc_token,
                    users_cache=app_instance.state.users_cache or [],
                )
                async with async_session_maker() as session:
                    result = await check_and_send_reminders(session, dispatcher)
                logger.info("Reminders sent", extra={"extra_fields": result})
            except Exception as e:
                logger.error("Reminder task error: %s", e)

    app_instance.state._reminder_task = asyncio.create_task(reminder_task())

    yield

    logger.info("Shutting down Tickets Service")
    app_instance.state._users_cache_task.cancel()
    app_instance.state._reminder_task.cancel()
```

**Note:** If `app_instance.state.service_token` does not exist, check how `http_client` and service token are managed in `main.py`. Look for `app_instance.state.service_token` or `app_instance.state.svc_token`. Use the correct attribute name. If needed, store the token in app state during startup (when it's fetched for `users_cache`).

- [ ] **Step 3: Verify how service_token is stored in `app/main.py`**

Search for `service_token` or `svc_token` in `main.py`:

```
grep -n "service_token\|svc_token\|http_client" app/main.py
```

Use whatever attribute name stores the token in `app_instance.state`. If the token isn't stored, find where it's obtained and store it:

```python
    # Example: if token is obtained like this during startup
    svc_token = await identity_client.get_service_token(...)
    app_instance.state.service_token = svc_token
```

Adjust the `reminder_task` accordingly.

- [ ] **Step 4: Add `POST /admin/reminders/run` endpoint to `app/api.py`**

Add after the `bulk_update_tickets` endpoint:

```python
@router.post("/admin/reminders/run", tags=["Admin"])
async def run_reminders_now(
    _: AdminUser,
    request: Request,
):
    """Manually trigger the reminder check. Admin only. Returns counts sent."""
    from app.database import async_session_maker
    from app.reminders import check_and_send_reminders
    from app.notifications import NotificationDispatcher
    svc_token = getattr(request.app.state, 'service_token', '')
    dispatcher = NotificationDispatcher(
        http_client=request.app.state.http_client,
        service_token=svc_token,
        users_cache=request.app.state.users_cache or [],
    )
    async with async_session_maker() as session:
        result = await check_and_send_reminders(session, dispatcher)
    return result
```

- [ ] **Step 5: Run all tests**

```
pytest -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/api.py
git commit -m "feat(reminders): add asyncio background task and POST /admin/reminders/run endpoint"
```

---

## Final Verification

- [ ] **Run full test suite**

```
pytest -v --tb=short
```

Expected: all PASS, no failures

- [ ] **Apply migration on server**

```bash
docker exec -it tickets_api alembic upgrade head
```

- [ ] **Smoke test checklist**

1. Login at `/tickets/dashboard/login` → no TypeError, dashboard loads
2. Edit a ticket (change status, priority, assign) → audit FollowUps appear in timeline
3. Bulk status change from tickets list → FollowUps appear on each ticket
4. Upload an attachment, then delete it (admin) → audit FollowUp "Adjunto eliminado"
5. Delete own attachment within 24h (portal user) → works
6. Try to delete another user's attachment (portal) → 403
7. Queue management: create parent → create child with parent selected → hierarchy tree shows correctly
8. Queue edit: change name/icon → modal pre-populates correctly, saves
9. Queue deactivate → row becomes muted, "Reactivar" shows
10. `POST /api/v1/admin/reminders/run` with admin token → `{"staff": N, "submitter": M}`

---

## Self-Review Notes

- `delete_attachment_file()` already exists in `attachment_storage.py` — Task 4 correctly skips creating it
- Admin edit modal toast + reload is already implemented — not duplicated in plan
- Status/priority/assignment audit FollowUps already exist in `service.py` — Task 2 only updates comment format and adds resolution case
- `QueueUpdate` already has all fields except `slug` — Task 7 adds `slug`
- `ReminderResult` added in Task 8 with the config/model changes (same commit)
- The `send_ticket_email` signature update (Task 10, Step 5) is necessary because `_send()` passes `**kwargs` directly; without `days_elapsed` in the signature, Python will raise `TypeError` on unexpected keyword argument
