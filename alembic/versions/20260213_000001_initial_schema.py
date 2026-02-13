"""Initial schema with all tables

Revision ID: 20260213_000001
Revises:
Create Date: 2026-02-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '20260213_000001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # === TENANTS ===
    op.create_table(
        'tenants',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('slug', sa.String(length=100), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('phone', sa.String(length=50), nullable=True),
        sa.Column('address', sa.Text(), nullable=True),
        sa.Column('logo_path', sa.String(length=500), nullable=True),
        sa.Column('education_type', sa.String(length=50), nullable=False, server_default='DAYCARE'),
        sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('onboarding_completed', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug')
    )
    op.create_index('idx_tenants_slug', 'tenants', ['slug'], postgresql_where=sa.text('deleted_at IS NULL'))

    # === USERS ===
    op.create_table(
        'users',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('first_name', sa.String(length=100), nullable=False),
        sa.Column('last_name', sa.String(length=100), nullable=False),
        sa.Column('phone', sa.String(length=50), nullable=True),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('avatar_path', sa.String(length=500), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('language', sa.String(length=5), nullable=False, server_default='en'),
        sa.Column('whatsapp_phone', sa.String(length=50), nullable=True),
        sa.Column('whatsapp_opted_in', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_users_email_tenant', 'users', ['email', 'tenant_id'], unique=True,
                    postgresql_where=sa.text('deleted_at IS NULL AND tenant_id IS NOT NULL'))
    op.create_index('idx_users_email_super', 'users', ['email'], unique=True,
                    postgresql_where=sa.text('deleted_at IS NULL AND tenant_id IS NULL'))
    op.create_index('idx_users_tenant_role', 'users', ['tenant_id', 'role'],
                    postgresql_where=sa.text('deleted_at IS NULL'))

    # === SCHOOL CLASSES ===
    op.create_table(
        'school_classes',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('age_group', sa.String(length=30), nullable=True),
        sa.Column('grade_level', sa.String(length=50), nullable=True),
        sa.Column('capacity', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_classes_tenant', 'school_classes', ['tenant_id'],
                    postgresql_where=sa.text('deleted_at IS NULL'))

    # === STUDENTS ===
    op.create_table(
        'students',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('first_name', sa.String(length=100), nullable=False),
        sa.Column('last_name', sa.String(length=100), nullable=False),
        sa.Column('date_of_birth', sa.Date(), nullable=True),
        sa.Column('gender', sa.String(length=10), nullable=True),
        sa.Column('age_group', sa.String(length=30), nullable=True),
        sa.Column('grade_level', sa.String(length=50), nullable=True),
        sa.Column('class_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('photo_path', sa.String(length=500), nullable=True),
        sa.Column('medical_info', sa.Text(), nullable=True),
        sa.Column('allergies', sa.Text(), nullable=True),
        sa.Column('emergency_contacts', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('enrollment_date', sa.Date(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['class_id'], ['school_classes.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_students_tenant_class', 'students', ['tenant_id', 'class_id'],
                    postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('idx_students_tenant_age', 'students', ['tenant_id', 'age_group'],
                    postgresql_where=sa.text('deleted_at IS NULL'))

    # === PARENT STUDENTS (JOIN TABLE) ===
    op.create_table(
        'parent_students',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('parent_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('student_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('relationship', sa.String(length=30), nullable=False, server_default='PARENT'),
        sa.Column('is_primary', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['parent_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['student_id'], ['students.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('parent_id', 'student_id')
    )
    op.create_index('idx_parent_students_parent', 'parent_students', ['parent_id'])
    op.create_index('idx_parent_students_student', 'parent_students', ['student_id'])

    # === TEACHER CLASSES (JOIN TABLE) ===
    op.create_table(
        'teacher_classes',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('teacher_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('class_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('is_primary', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('assigned_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['class_id'], ['school_classes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['teacher_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('teacher_id', 'class_id')
    )
    op.create_index('idx_teacher_classes_teacher', 'teacher_classes', ['teacher_id'])
    op.create_index('idx_teacher_classes_class', 'teacher_classes', ['class_id'])

    # === ATTENDANCE RECORDS ===
    op.create_table(
        'attendance_records',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('student_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('class_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='ABSENT'),
        sa.Column('check_in_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('check_out_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('recorded_by', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['class_id'], ['school_classes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['recorded_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['student_id'], ['students.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('student_id', 'date', name='uq_attendance_student_date')
    )
    op.create_index('idx_attendance_tenant_date', 'attendance_records', ['tenant_id', 'date'])
    op.create_index('idx_attendance_class_date', 'attendance_records', ['class_id', 'date'])

    # === FILE ENTITIES ===
    op.create_table(
        'file_entities',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('storage_path', sa.String(length=500), nullable=False),
        sa.Column('original_name', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=100), nullable=False),
        sa.Column('file_size', sa.BigInteger(), nullable=False),
        sa.Column('file_category', sa.String(length=20), nullable=False),
        sa.Column('uploaded_by', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_files_tenant', 'file_entities', ['tenant_id'],
                    postgresql_where=sa.text('deleted_at IS NULL'))

    # === MESSAGES ===
    op.create_table(
        'messages',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('sender_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('message_type', sa.String(length=30), nullable=False),
        sa.Column('subject', sa.String(length=255), nullable=True),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('class_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('student_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('parent_message_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('is_read', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='SENT'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['class_id'], ['school_classes.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['parent_message_id'], ['messages.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['sender_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['student_id'], ['students.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_messages_tenant_type', 'messages', ['tenant_id', 'message_type'],
                    postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('idx_messages_thread', 'messages', ['parent_message_id'],
                    postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('idx_messages_class', 'messages', ['class_id'],
                    postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('idx_messages_student', 'messages', ['student_id'],
                    postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('idx_messages_sender', 'messages', ['sender_id'],
                    postgresql_where=sa.text('deleted_at IS NULL'))

    # === MESSAGE RECIPIENTS ===
    op.create_table(
        'message_recipients',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('message_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('is_read', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('message_id', 'user_id')
    )
    op.create_index('idx_msg_recipients_user_unread', 'message_recipients', ['user_id', 'is_read'],
                    postgresql_where=sa.text('is_read = false'))

    # === MESSAGE ATTACHMENTS ===
    op.create_table(
        'message_attachments',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('message_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('file_entity_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('display_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['file_entity_id'], ['file_entities.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_msg_attachments_message', 'message_attachments', ['message_id'])

    # === REPORT TEMPLATES ===
    op.create_table(
        'report_templates',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('report_type', sa.String(length=30), nullable=False),
        sa.Column('frequency', sa.String(length=20), nullable=False, server_default='DAILY'),
        sa.Column('applies_to_grade_level', sa.String(length=255), nullable=True),
        sa.Column('sections', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('display_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_templates_tenant', 'report_templates', ['tenant_id'],
                    postgresql_where=sa.text('deleted_at IS NULL AND is_active = true'))

    # === DAILY REPORTS ===
    op.create_table(
        'daily_reports',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('student_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('class_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('template_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('report_date', sa.Date(), nullable=False),
        sa.Column('report_data', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='DRAFT'),
        sa.Column('finalized_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['class_id'], ['school_classes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['student_id'], ['students.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['template_id'], ['report_templates.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('student_id', 'template_id', 'report_date', name='uq_report_student_template_date')
    )
    op.create_index('idx_reports_tenant_date', 'daily_reports', ['tenant_id', 'report_date'],
                    postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('idx_reports_student', 'daily_reports', ['student_id'],
                    postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('idx_reports_class_date', 'daily_reports', ['class_id', 'report_date'],
                    postgresql_where=sa.text('deleted_at IS NULL'))

    # === PARENT INVITATIONS ===
    op.create_table(
        'parent_invitations',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('student_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('invitation_code', sa.String(length=8), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='PENDING'),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['student_id'], ['students.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('invitation_code')
    )
    op.create_index('idx_invitations_code', 'parent_invitations', ['invitation_code'],
                    postgresql_where=sa.text("status = 'PENDING'"))
    op.create_index('idx_invitations_email', 'parent_invitations', ['email', 'tenant_id'],
                    postgresql_where=sa.text("status = 'PENDING'"))

    # === NOTIFICATIONS ===
    op.create_table(
        'notifications',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('notification_type', sa.String(length=50), nullable=False),
        sa.Column('reference_type', sa.String(length=50), nullable=True),
        sa.Column('reference_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('is_read', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_notifications_user_unread', 'notifications', ['user_id', 'is_read'],
                    postgresql_where=sa.text('is_read = false'))

    # === WEBHOOK ENDPOINTS ===
    op.create_table(
        'webhook_endpoints',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('url', sa.String(length=500), nullable=False),
        sa.Column('secret', sa.String(length=255), nullable=False),
        sa.Column('events', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_webhooks_tenant', 'webhook_endpoints', ['tenant_id'],
                    postgresql_where=sa.text('is_active = true'))

    # === WEBHOOK EVENTS ===
    op.create_table(
        'webhook_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('endpoint_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('event_type', sa.String(length=100), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='PENDING'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('response_code', sa.Integer(), nullable=True),
        sa.Column('response_body', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['endpoint_id'], ['webhook_endpoints.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_webhook_events_status', 'webhook_events', ['status'],
                    postgresql_where=sa.text("status IN ('PENDING', 'FAILED')"))

    # === BULK IMPORT JOBS ===
    op.create_table(
        'bulk_import_jobs',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('import_type', sa.String(length=30), nullable=False),
        sa.Column('file_name', sa.String(length=255), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='PENDING'),
        sa.Column('total_rows', sa.Integer(), nullable=True),
        sa.Column('processed_rows', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('success_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('errors', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('column_mapping', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_imports_tenant', 'bulk_import_jobs', ['tenant_id'])


def downgrade() -> None:
    # Drop tables in reverse order of creation (respecting foreign key dependencies)
    op.drop_table('bulk_import_jobs')
    op.drop_table('webhook_events')
    op.drop_table('webhook_endpoints')
    op.drop_table('notifications')
    op.drop_table('parent_invitations')
    op.drop_table('daily_reports')
    op.drop_table('report_templates')
    op.drop_table('message_attachments')
    op.drop_table('message_recipients')
    op.drop_table('messages')
    op.drop_table('file_entities')
    op.drop_table('attendance_records')
    op.drop_table('teacher_classes')
    op.drop_table('parent_students')
    op.drop_table('students')
    op.drop_table('school_classes')
    op.drop_table('users')
    op.drop_table('tenants')
