"""Initial schema — all tables in final state.

Revision ID: 001
Revises:
Create Date: 2026-03-26
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None

INITIAL_QUEUES = [
    {"name": "ADMINISTRACIÓN",  "slug": "administracion",            "description": "Consultas sobre Administración.",              "icon": "building",    "color": "blue",   "sort_order": 1,  "parent_slug": None},
    {"name": "CONTADURÍA",      "slug": "administracion-contaduria", "description": "Consultas sobre Administración > Contaduría.", "icon": "calculator",  "color": "blue",   "sort_order": 2,  "parent_slug": "administracion"},
    {"name": "SISTEMAS",        "slug": "administracion-sistemas",   "description": "Consultas sobre Administración > Sistemas.",   "icon": "server",      "color": "blue",   "sort_order": 3,  "parent_slug": "administracion"},
    {"name": "COMERCIAL",       "slug": "comercial",                 "description": "Consultas sobre el área Comercial.",           "icon": "briefcase",   "color": "green",  "sort_order": 4,  "parent_slug": None},
    {"name": "MARKETING",       "slug": "comercial-marketing",       "description": "Consultas sobre Marketing.",                   "icon": "megaphone",   "color": "green",  "sort_order": 5,  "parent_slug": "comercial"},
    {"name": "NOBLE ONLINE",    "slug": "noble-online",              "description": "Consultas sobre Noble Online.",                "icon": "globe",       "color": "indigo", "sort_order": 7,  "parent_slug": None},
    {"name": "RR HH",           "slug": "rrhh",                      "description": "Consultas sobre Recursos Humanos.",            "icon": "users",       "color": "purple", "sort_order": 8,  "parent_slug": None},
    {"name": "TÉCNICA",         "slug": "tecnica",                   "description": "Consultas sobre el área Técnica.",             "icon": "wrench",      "color": "orange", "sort_order": 9,  "parent_slug": None},
    {"name": "EMISIÓN",         "slug": "tecnica-emision",           "description": "Consultas sobre Técnica > Emisión.",           "icon": "file-text",   "color": "orange", "sort_order": 10, "parent_slug": "tecnica"},
    {"name": "SUSCRIPCIÓN",     "slug": "tecnica-suscripcion",       "description": "Consultas sobre Técnica > Suscripción.",       "icon": "file-check",  "color": "orange", "sort_order": 11, "parent_slug": "tecnica"},
    {"name": "Otros",           "slug": "otros",                     "description": "Consultas que no encajan en las categorías.",  "icon": "help-circle", "color": "slate",  "sort_order": 99, "parent_slug": None},
]


def upgrade() -> None:
    op.create_table(
        "queues",
        sa.Column("id",          sa.Integer(),                       nullable=False),
        sa.Column("parent_id",   sa.Integer(), sa.ForeignKey("queues.id"), nullable=True),
        sa.Column("name",        sa.String(200),                     nullable=False),
        sa.Column("slug",        sa.String(100),                     nullable=False),
        sa.Column("description", sa.Text(),                          nullable=True),
        sa.Column("email",       sa.String(200),                     nullable=True),
        sa.Column("assigned_to_id", sa.String(100),                  nullable=True),
        sa.Column("is_active",   sa.Boolean(),                       nullable=False, server_default="true"),
        sa.Column("sort_order",  sa.Integer(),                       nullable=False, server_default="0"),
        sa.Column("icon",        sa.String(50),                      nullable=True),
        sa.Column("color",       sa.String(30),                      nullable=True),
        sa.Column("created_at",  sa.DateTime(timezone=True),         nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_queues_slug", "queues", ["slug"])

    op.create_table(
        "tickets",
        sa.Column("id",                  sa.UUID(),                      nullable=False),
        sa.Column("queue_id",            sa.Integer(),                   nullable=False),
        sa.Column("parent_id",           sa.UUID(),                      nullable=True),
        sa.Column("title",               sa.String(200),                 nullable=False),
        sa.Column("description",         sa.Text(),                      nullable=False),
        sa.Column("status",              sa.String(20),                  nullable=False, server_default="open"),
        sa.Column("priority",            sa.Integer(),                   nullable=False, server_default="3"),
        sa.Column("submitter_email",     sa.String(200),                 nullable=False),
        sa.Column("submitter_username",  sa.String(100),                 nullable=True),
        sa.Column("created_by_id",       sa.String(36),                  nullable=True),
        sa.Column("assigned_to",         sa.String(100),                 nullable=True),
        sa.Column("resolution",          sa.Text(),                      nullable=True),
        sa.Column("created_at",          sa.DateTime(timezone=True),     nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",          sa.DateTime(timezone=True),     nullable=False, server_default=sa.func.now()),
        sa.Column("due_date",            sa.DateTime(timezone=True),     nullable=True),
        sa.ForeignKeyConstraint(["queue_id"],  ["queues.id"]),
        sa.ForeignKeyConstraint(["parent_id"], ["tickets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tickets_queue_id",           "tickets", ["queue_id"])
    op.create_index("ix_tickets_status",             "tickets", ["status"])
    op.create_index("ix_tickets_created_by_id",      "tickets", ["created_by_id"])
    op.create_index("ix_tickets_submitter_username", "tickets", ["submitter_username"])
    op.create_index("ix_tickets_updated_at",         "tickets", ["updated_at"])
    op.create_index("ix_tickets_parent_id",          "tickets", ["parent_id"])

    op.create_table(
        "followups",
        sa.Column("id",         sa.UUID(),                  nullable=False),
        sa.Column("ticket_id",  sa.UUID(),                  nullable=False),
        sa.Column("user_id",    sa.String(100),             nullable=True),
        sa.Column("user_name",  sa.String(100),             nullable=True),
        sa.Column("comment",    sa.Text(),                  nullable=False),
        sa.Column("is_public",  sa.Boolean(),               nullable=False, server_default="true"),
        sa.Column("is_staff",   sa.Boolean(),               nullable=False, server_default="false"),
        sa.Column("new_status", sa.String(20),              nullable=True),
        sa.Column("mentions",   sa.JSON(),                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_followups_ticket_id", "followups", ["ticket_id"])

    op.create_table(
        "attachments",
        sa.Column("id",           sa.UUID(),                  nullable=False),
        sa.Column("ticket_id",    sa.UUID(),                  nullable=False),
        sa.Column("followup_id",  sa.UUID(),                  nullable=True),
        sa.Column("filename",     sa.String(255),             nullable=False),
        sa.Column("storage_name", sa.String(300),             nullable=False),
        sa.Column("mime_type",    sa.String(100),             nullable=True),
        sa.Column("size",         sa.Integer(),               nullable=True),
        sa.Column("uploaded_by",  sa.String(100),             nullable=True),
        sa.Column("created_at",   sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["ticket_id"],   ["tickets.id"]),
        sa.ForeignKeyConstraint(["followup_id"], ["followups.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_attachments_ticket_id", "attachments", ["ticket_id"])

    op.create_table(
        "ticket_watchers",
        sa.Column("ticket_id", sa.UUID(),                  nullable=False),
        sa.Column("user_id",   sa.String(100),             nullable=False),
        sa.Column("added_at",  sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.PrimaryKeyConstraint("ticket_id", "user_id"),
    )
    op.create_index("ix_watchers_user_id", "ticket_watchers", ["user_id"])

    op.create_table(
        "ticket_relations",
        sa.Column("id",               sa.UUID(),                  nullable=False),
        sa.Column("source_ticket_id", sa.UUID(),                  nullable=False),
        sa.Column("target_ticket_id", sa.UUID(),                  nullable=False),
        sa.Column("relation_type",    sa.String(20),              nullable=False),
        sa.Column("created_by",       sa.String(100),             nullable=True),
        sa.Column("created_at",       sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["source_ticket_id"], ["tickets.id"]),
        sa.ForeignKeyConstraint(["target_ticket_id"], ["tickets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ticket_relations_source", "ticket_relations", ["source_ticket_id"])
    op.create_index("ix_ticket_relations_target", "ticket_relations", ["target_ticket_id"])

    op.create_table(
        "notifications",
        sa.Column("id",         sa.UUID(),                  nullable=False),
        sa.Column("user_id",    sa.String(100),             nullable=False),
        sa.Column("ticket_id",  sa.UUID(),                  nullable=False),
        sa.Column("type",       sa.String(30),              nullable=False),
        sa.Column("is_read",    sa.Boolean(),               nullable=False, server_default="false"),
        sa.Column("content",    sa.String(500),             nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notifications_user_id",    "notifications", ["user_id"])
    op.create_index("ix_notifications_user_read",  "notifications", ["user_id", "is_read"])
    op.create_index("ix_notifications_ticket_id",  "notifications", ["ticket_id"])

    # Seed queues (inserta primero los sin parent, luego los hijos)
    conn = op.get_bind()
    queues_table = sa.table(
        "queues",
        sa.column("name",        sa.String),
        sa.column("slug",        sa.String),
        sa.column("description", sa.Text),
        sa.column("icon",        sa.String),
        sa.column("color",       sa.String),
        sa.column("sort_order",  sa.Integer),
        sa.column("is_active",   sa.Boolean),
    )
    roots   = [q for q in INITIAL_QUEUES if q["parent_slug"] is None]
    children = [q for q in INITIAL_QUEUES if q["parent_slug"] is not None]
    op.bulk_insert(queues_table, [
        {k: v for k, v in q.items() if k != "parent_slug"} | {"is_active": True}
        for q in roots
    ])
    # Assign parent_id for child queues
    for q in children:
        row = conn.execute(
            sa.text("SELECT id FROM queues WHERE slug = :s"), {"s": q["parent_slug"]}
        ).fetchone()
        parent_id = row[0] if row else None
        conn.execute(
            sa.text(
                "INSERT INTO queues (name, slug, description, icon, color, sort_order, is_active, parent_id) "
                "VALUES (:name, :slug, :description, :icon, :color, :sort_order, true, :parent_id)"
            ),
            {k: q[k] for k in ("name", "slug", "description", "icon", "color", "sort_order")} | {"parent_id": parent_id},
        )


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("ticket_watchers")
    op.drop_table("ticket_relations")
    op.drop_table("attachments")
    op.drop_table("followups")
    op.drop_table("tickets")
    op.drop_table("queues")
