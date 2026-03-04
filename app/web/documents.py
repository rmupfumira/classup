"""Documents web routes for HTML pages."""

import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.school_class import SchoolClass
from app.models.user import Role, User
from app.services.auth_service import get_auth_service
from app.services.class_service import get_class_service
from app.services.document_service import get_document_service
from app.services.file_service import get_file_service
from app.templates_config import templates
from app.utils.permissions import PermissionChecker
from app.web.helpers import get_teacher_class_context
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id_or_none,
    get_tenant_id,
)

router = APIRouter(prefix="/documents")


async def _get_current_user(db: AsyncSession) -> User | None:
    """Get the current user from the database."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return None
    auth_service = get_auth_service()
    try:
        return await auth_service.get_current_user(db, user_id)
    except Exception:
        return None


def _require_auth(request: Request):
    """Check authentication and return redirect if not authenticated."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        response = RedirectResponse(url="/login", status_code=302)
        response.delete_cookie("access_token")
        return response
    return None


@router.get("", response_class=HTMLResponse)
async def documents_list(
    request: Request,
    class_id: uuid.UUID | None = None,
    scope: str | None = None,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Render the documents list page."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)
    document_service = get_document_service()
    file_service = get_file_service()
    class_service = get_class_service()

    # Get classes for filter dropdown
    if user.role == Role.TEACHER.value:
        classes = await class_service.get_my_classes(db)
    elif user.role in (Role.SUPER_ADMIN.value, Role.SCHOOL_ADMIN.value):
        classes, _ = await class_service.get_classes(db, is_active=True, page_size=100)
    elif user.role == Role.PARENT.value:
        parent_class_ids = await document_service._get_parent_class_ids(db, user.id)
        if parent_class_ids:
            result = await db.execute(
                select(SchoolClass).where(
                    SchoolClass.id.in_(parent_class_ids),
                    SchoolClass.tenant_id == get_tenant_id(),
                    SchoolClass.deleted_at.is_(None),
                )
            )
            classes = list(result.scalars().all())
        else:
            classes = []
    else:
        classes = []

    # Get document shares
    shares, total = await document_service.get_document_shares(
        db,
        class_id=class_id,
        scope=scope,
        page=page,
        page_size=20,
    )

    total_pages = (total + 19) // 20

    # Build document items with presigned URLs
    documents = []
    for share in shares:
        # Build file URLs
        share_files = []
        for dsf in (share.files or []):
            fe = dsf.file_entity
            if not fe:
                continue
            share_files.append({
                "file_entity_id": str(fe.id),
                "original_name": fe.original_name,
                "content_type": fe.content_type,
                "file_size": fe.file_size,
                "view_url": file_service.generate_presigned_url(fe, expires_in=3600, inline=True),
                "download_url": file_service.generate_presigned_url(fe, expires_in=3600),
                "is_pdf": fe.content_type == "application/pdf" if fe.content_type else False,
            })

        documents.append({
            "id": str(share.id),
            "scope": share.scope,
            "title": share.title,
            "description": share.description,
            "class_name": share.class_name,
            "sharer_name": share.sharer_name,
            "file_count": share.file_count,
            "files": share_files,
            "primary_file": share_files[0] if share_files else None,
            "tagged_student_names": share.tagged_student_names,
            "created_at": share.created_at,
            "shared_by": str(share.shared_by),
        })

    context = {
        "request": request,
        "user": user,
        "documents": documents,
        "classes": classes,
        "current_class_id": class_id,
        "current_scope": scope,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    if user.role == Role.TEACHER.value:
        context.update(await get_teacher_class_context(request, db))
    return templates.TemplateResponse("documents/list.html", context)


@router.get("/upload", response_class=HTMLResponse)
async def documents_upload(
    request: Request,
    class_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Render the document upload page."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)

    # Only staff can upload documents
    if user.role not in (Role.SUPER_ADMIN.value, Role.SCHOOL_ADMIN.value, Role.TEACHER.value):
        return RedirectResponse(url="/documents", status_code=302)

    class_service = get_class_service()

    # Get classes for dropdown
    if user.role == Role.TEACHER.value:
        classes = await class_service.get_my_classes(db)
    else:
        classes, _ = await class_service.get_classes(db, is_active=True, page_size=100)

    context = {
        "request": request,
        "user": user,
        "classes": classes,
        "selected_class_id": class_id,
        "is_admin": user.role in (Role.SUPER_ADMIN.value, Role.SCHOOL_ADMIN.value),
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    if user.role == Role.TEACHER.value:
        context.update(await get_teacher_class_context(request, db))
    return templates.TemplateResponse("documents/upload.html", context)
