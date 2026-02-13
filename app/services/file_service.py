"""File service for managing file uploads and R2 storage."""

import io
import mimetypes
import uuid
from datetime import datetime

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.exceptions import ForbiddenException, NotFoundException, ValidationException
from app.models import FileEntity, Message, MessageAttachment, Student, User
from app.models.file_entity import FileCategory
from app.utils.tenant_context import get_current_user_id, get_tenant_id


# Allowed MIME types for each category
ALLOWED_MIME_TYPES = {
    FileCategory.PHOTO: {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/heic",
        "image/heif",
    },
    FileCategory.DOCUMENT: {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    },
    FileCategory.AVATAR: {
        "image/jpeg",
        "image/png",
        "image/webp",
    },
    FileCategory.LOGO: {
        "image/jpeg",
        "image/png",
        "image/svg+xml",
        "image/webp",
    },
}

# File extension mappings
EXTENSION_MIME_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".svg": "image/svg+xml",
}


class FileService:
    """Service for managing file uploads and R2 storage."""

    def __init__(self):
        """Initialize the S3 client for R2."""
        self._client = None

    @property
    def s3_client(self):
        """Get or create the S3 client lazily."""
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=settings.r2_endpoint_url,
                aws_access_key_id=settings.r2_access_key_id,
                aws_secret_access_key=settings.r2_secret_access_key,
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                ),
            )
        return self._client

    async def upload_file(
        self,
        db: AsyncSession,
        file: UploadFile,
        category: FileCategory,
        entity_id: uuid.UUID | None = None,
    ) -> FileEntity:
        """Upload a file to R2 and create a FileEntity record.

        Args:
            db: Database session
            file: Uploaded file from FastAPI
            category: File category (PHOTO, DOCUMENT, AVATAR, LOGO)
            entity_id: Optional related entity ID (student_id, message_id, etc.)

        Returns:
            Created FileEntity
        """
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()

        # Validate file
        await self._validate_file(file, category)

        # Read file content
        content = await file.read()
        file_size = len(content)

        # Validate file size
        if file_size > settings.max_upload_size_bytes:
            raise ValidationException([{
                "field": "file",
                "message": f"File size exceeds maximum allowed ({settings.max_upload_size_mb}MB)",
            }])

        # Determine content type
        content_type = file.content_type or self._guess_content_type(file.filename)

        # Generate storage path
        file_uuid = uuid.uuid4()
        file_ext = self._get_extension(file.filename)
        storage_path = self._generate_storage_path(
            tenant_id, category, entity_id, file_uuid, file_ext
        )

        # Upload to R2
        try:
            self.s3_client.put_object(
                Bucket=settings.r2_bucket_name,
                Key=storage_path,
                Body=content,
                ContentType=content_type,
            )
        except ClientError as e:
            raise ValidationException([{
                "field": "file",
                "message": f"Failed to upload file: {str(e)}",
            }])

        # Create FileEntity record
        file_entity = FileEntity(
            tenant_id=tenant_id,
            storage_path=storage_path,
            original_name=file.filename or "unnamed_file",
            content_type=content_type,
            file_size=file_size,
            file_category=category.value,
            uploaded_by=user_id,
        )

        db.add(file_entity)
        await db.flush()
        await db.refresh(file_entity)

        return file_entity

    async def get_file(
        self,
        db: AsyncSession,
        file_id: uuid.UUID,
    ) -> FileEntity:
        """Get a file entity by ID."""
        tenant_id = get_tenant_id()

        query = (
            select(FileEntity)
            .where(
                FileEntity.id == file_id,
                FileEntity.tenant_id == tenant_id,
                FileEntity.deleted_at.is_(None),
            )
            .options(selectinload(FileEntity.uploader))
        )

        result = await db.execute(query)
        file_entity = result.scalar_one_or_none()

        if not file_entity:
            raise NotFoundException("File")

        return file_entity

    async def get_files(
        self,
        db: AsyncSession,
        category: FileCategory | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[FileEntity], int]:
        """Get a list of files with optional filtering."""
        tenant_id = get_tenant_id()

        query = (
            select(FileEntity)
            .where(
                FileEntity.tenant_id == tenant_id,
                FileEntity.deleted_at.is_(None),
            )
            .options(selectinload(FileEntity.uploader))
        )

        if category:
            query = query.where(FileEntity.file_category == category.value)

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        # Apply pagination and ordering
        query = query.order_by(FileEntity.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        files = list(result.scalars().all())

        return files, total

    async def get_photos(
        self,
        db: AsyncSession,
        class_id: uuid.UUID | None = None,
        student_id: uuid.UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], int]:
        """Get photos, optionally filtered by class or student."""
        tenant_id = get_tenant_id()

        # Get photos from message attachments
        query = (
            select(FileEntity, Message)
            .join(MessageAttachment, MessageAttachment.file_entity_id == FileEntity.id)
            .join(Message, Message.id == MessageAttachment.message_id)
            .where(
                FileEntity.tenant_id == tenant_id,
                FileEntity.deleted_at.is_(None),
                FileEntity.file_category == FileCategory.PHOTO.value,
                Message.deleted_at.is_(None),
            )
            .options(selectinload(FileEntity.uploader))
        )

        if class_id:
            query = query.where(Message.class_id == class_id)
        if student_id:
            query = query.where(Message.student_id == student_id)

        # Count query
        count_subquery = query.subquery()
        count_query = select(func.count()).select_from(count_subquery)
        total = (await db.execute(count_query)).scalar() or 0

        # Apply pagination
        query = query.order_by(FileEntity.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        rows = result.all()

        photos = []
        for file_entity, message in rows:
            photos.append({
                "file": file_entity,
                "message": message,
                "class_name": message.school_class.name if message.school_class else None,
                "student_name": f"{message.student.first_name} {message.student.last_name}" if message.student else None,
            })

        return photos, total

    async def get_documents(
        self,
        db: AsyncSession,
        class_id: uuid.UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], int]:
        """Get documents, optionally filtered by class."""
        tenant_id = get_tenant_id()

        query = (
            select(FileEntity, Message)
            .join(MessageAttachment, MessageAttachment.file_entity_id == FileEntity.id)
            .join(Message, Message.id == MessageAttachment.message_id)
            .where(
                FileEntity.tenant_id == tenant_id,
                FileEntity.deleted_at.is_(None),
                FileEntity.file_category == FileCategory.DOCUMENT.value,
                Message.deleted_at.is_(None),
            )
            .options(selectinload(FileEntity.uploader))
        )

        if class_id:
            query = query.where(Message.class_id == class_id)

        # Count query
        count_subquery = query.subquery()
        count_query = select(func.count()).select_from(count_subquery)
        total = (await db.execute(count_query)).scalar() or 0

        # Apply pagination
        query = query.order_by(FileEntity.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        rows = result.all()

        documents = []
        for file_entity, message in rows:
            documents.append({
                "file": file_entity,
                "message": message,
                "class_name": message.school_class.name if message.school_class else None,
                "category": self._get_document_category(message),
            })

        return documents, total

    def generate_presigned_url(
        self,
        file_entity: FileEntity,
        expires_in: int = 3600,
    ) -> str:
        """Generate a presigned URL for downloading a file.

        Args:
            file_entity: The file entity
            expires_in: URL expiry time in seconds (default 1 hour)

        Returns:
            Presigned URL string
        """
        try:
            url = self.s3_client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": settings.r2_bucket_name,
                    "Key": file_entity.storage_path,
                    "ResponseContentDisposition": f'attachment; filename="{file_entity.original_name}"',
                },
                ExpiresIn=expires_in,
            )
            return url
        except ClientError:
            return ""

    async def delete_file(
        self,
        db: AsyncSession,
        file_id: uuid.UUID,
    ) -> bool:
        """Soft delete a file entity.

        The actual R2 object is not deleted immediately to allow for recovery.
        A background job can clean up orphaned R2 objects periodically.
        """
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()

        file_entity = await self.get_file(db, file_id)

        # Check permissions (only uploader or admin can delete)
        if file_entity.uploaded_by != user_id:
            # TODO: Check if user is admin
            raise ForbiddenException("You can only delete files you uploaded")

        file_entity.deleted_at = datetime.utcnow()
        return True

    async def _validate_file(
        self,
        file: UploadFile,
        category: FileCategory,
    ) -> None:
        """Validate file type and size."""
        if not file.filename:
            raise ValidationException([{
                "field": "file",
                "message": "File name is required",
            }])

        # Check content type
        content_type = file.content_type or self._guess_content_type(file.filename)
        allowed_types = ALLOWED_MIME_TYPES.get(category, set())

        if content_type not in allowed_types:
            raise ValidationException([{
                "field": "file",
                "message": f"File type '{content_type}' is not allowed for {category.value}",
            }])

    def _guess_content_type(self, filename: str | None) -> str:
        """Guess content type from filename."""
        if not filename:
            return "application/octet-stream"

        ext = self._get_extension(filename).lower()
        return EXTENSION_MIME_MAP.get(ext) or mimetypes.guess_type(filename)[0] or "application/octet-stream"

    def _get_extension(self, filename: str | None) -> str:
        """Get file extension including the dot."""
        if not filename or "." not in filename:
            return ""
        return "." + filename.rsplit(".", 1)[-1].lower()

    def _generate_storage_path(
        self,
        tenant_id: uuid.UUID,
        category: FileCategory,
        entity_id: uuid.UUID | None,
        file_uuid: uuid.UUID,
        file_ext: str,
    ) -> str:
        """Generate R2 storage path.

        Format: {tenant_id}/{category}/{entity_id}/{uuid}{ext}
        """
        entity_part = str(entity_id) if entity_id else "general"
        return f"{tenant_id}/{category.value}/{entity_part}/{file_uuid}{file_ext}"

    def _get_document_category(self, message: Message) -> str:
        """Get document category based on message type."""
        if message.message_type == "SCHOOL_DOCUMENT":
            return "School-wide"
        elif message.message_type == "CLASS_DOCUMENT":
            return "Class"
        elif message.message_type == "STUDENT_DOCUMENT":
            return "Student"
        return "Other"


def get_file_service() -> FileService:
    """Get file service instance."""
    return FileService()
