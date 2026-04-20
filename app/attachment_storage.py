"""Attachment storage — files on disk volume, metadata in DB."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import UploadFile
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings
from app.exceptions import ValidationError
from app.logger import get_logger
from app.models import Attachment

logger = get_logger(__name__)

STORAGE_ROOT = Path(settings.attachments_path)


def get_attachment_path(storage_name: str) -> Path:
    """Resolve a storage_name to a full filesystem path."""
    return STORAGE_ROOT / storage_name


def _safe_extension(filename: str) -> str:
    """Extract file extension, lowercase, max 10 chars."""
    ext = Path(filename).suffix.lower()
    return ext[:11] if ext else ""


async def save_attachments(
    ticket_id: uuid.UUID,
    files: list[UploadFile],
    uploaded_by: str,
    session: AsyncSession,
    followup_id: uuid.UUID | None = None,
) -> list[Attachment]:
    """Save uploaded files to disk and create DB records.

    Naming: {ticket_short_id}/{attachment_uuid}{ext}
    This is collision-free and reversible (ticket id in path, attachment id in filename).
    """
    if not files:
        return []

    if len(files) > settings.attachments_max_per_ticket:
        raise ValidationError(
            f"Max {settings.attachments_max_per_ticket} files per ticket"
        )

    ticket_dir = STORAGE_ROOT / ticket_id.hex[:8]
    ticket_dir.mkdir(parents=True, exist_ok=True)

    saved: list[Attachment] = []

    for file in files:
        # Validate MIME type
        content_type = file.content_type or "application/octet-stream"
        if settings.attachments_allowed_types and content_type not in settings.attachments_allowed_types:
            raise ValidationError(
                f"File type not allowed: {content_type}. "
                f"Allowed: {', '.join(settings.attachments_allowed_types)}"
            )

        # Read and validate size
        content = await file.read()
        size = len(content)
        if size > settings.attachments_max_size_bytes:
            raise ValidationError(
                f"File '{file.filename}' exceeds the max size of {settings.attachments_max_size_mb} MB"
            )

        # Generate unique storage name
        att_id = uuid.uuid4()
        ext = _safe_extension(file.filename or "file")
        storage_name = f"{ticket_id.hex[:8]}/{att_id.hex}{ext}"

        # Write to disk
        file_path = STORAGE_ROOT / storage_name
        file_path.write_bytes(content)

        # Create DB record
        attachment = Attachment(
            id=att_id,
            ticket_id=ticket_id,
            followup_id=followup_id,
            filename=file.filename or "file",
            storage_name=storage_name,
            mime_type=content_type,
            size=size,
            uploaded_by=uploaded_by,
        )
        session.add(attachment)
        saved.append(attachment)

    await session.commit()
    for a in saved:
        await session.refresh(a)

    logger.info(
        "Saved %d attachments for ticket %s", len(saved), ticket_id.hex[:8],
    )
    return saved


async def delete_attachment_file(storage_name: str) -> None:
    """Remove a file from disk (for cleanup)."""
    path = STORAGE_ROOT / storage_name
    if path.exists():
        path.unlink()
