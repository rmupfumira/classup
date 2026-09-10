"""Add school_events + event_rsvps tables.

Revision ID: 20260910_000002
Revises: 20260910_000001
Create Date: 2026-09-10

Schools + teachers create events (parent meetings, PTCs, assemblies,
outings) targeted at a scope: whole school, one class, or one specific
student's parents. Parents RSVP Yes / No / Maybe. Every invitation also
lands as a calendar attachment (ICS) in their email so they can add it
to Google/Apple/Outlook.

Audience is COMPUTED per event from (scope, class_id, student_id) — no
event_invitees table. Keeps the model simple and audience always reflects
the current student/parent roster (a parent who joins after an event was
created still gets the reminder).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260910_000002"
down_revision: Union[str, None] = "20260910_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "school_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        # PARENT_MEETING | PARENT_TEACHER_CONFERENCE | ASSEMBLY | OUTING | OTHER
        sa.Column("event_type", sa.String(40), nullable=False),
        # SCHOOL | CLASS | STUDENT — determines who gets invited
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column(
            "class_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("school_classes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "student_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("students.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=True),
        sa.Column("location", sa.String(300), nullable=True),
        sa.Column("rsvp_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("rsvp_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_school_events_tenant_time",
        "school_events",
        ["tenant_id", "starts_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_school_events_class",
        "school_events",
        ["class_id"],
        postgresql_where=sa.text("class_id IS NOT NULL AND deleted_at IS NULL"),
    )
    op.create_index(
        "idx_school_events_student",
        "school_events",
        ["student_id"],
        postgresql_where=sa.text("student_id IS NOT NULL AND deleted_at IS NULL"),
    )

    op.create_table(
        "event_rsvps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("school_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # YES | NO | MAYBE
        sa.Column("response", sa.String(10), nullable=False),
        sa.Column(
            "responded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # One RSVP per (event, user) — the latest response replaces the prior.
    op.create_index(
        "uq_event_rsvps_event_user",
        "event_rsvps",
        ["event_id", "user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_event_rsvps_event_user", table_name="event_rsvps")
    op.drop_table("event_rsvps")
    op.drop_index("idx_school_events_student", table_name="school_events")
    op.drop_index("idx_school_events_class", table_name="school_events")
    op.drop_index("idx_school_events_tenant_time", table_name="school_events")
    op.drop_table("school_events")
