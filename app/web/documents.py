"""Documents web routes for HTML pages."""

import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import Role, User
from app.services.auth_service import get_auth_service
from app.services.class_service import get_class_service
from app.services.file_service import get_file_service
from app.templates_config import templates
from app.utils.permissions import PermissionChecker
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id_or_none,
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
    file_service = get_file_service()
    class_service = get_class_service()

    # Get classes for filter dropdown
    if user.role == Role.TEACHER.value:
        classes = await class_service.get_my_classes(db)
    elif user.role in (Role.SUPER_ADMIN.value, Role.SCHOOL_ADMIN.value):
        classes, _ = await class_service.get_classes(db, is_active=True, page_size=100)
    else:
        classes = []

    # Get documents
    documents, total = await file_service.get_documents(
        db,
        class_id=class_id,
        page=page,
        page_size=20,
    )

    total_pages = (total + 19) // 20

    # Generate presigned URLs for downloads
    for doc in documents:
        doc["download_url"] = file_service.generate_presigned_url(doc["file"], expires_in=3600)

    return templates.TemplateResponse(
        "documents/list.html",
        {
            "request": request,
            "user": user,
            "documents": documents,
            "classes": classes,
            "current_class_id": class_id,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "current_language": get_current_language(),
            "permissions": permissions,
        },
    )


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

    return templates.TemplateResponse(
        "documents/upload.html",
        {
            "request": request,
            "user": user,
            "classes": classes,
            "selected_class_id": class_id,
            "current_language": get_current_language(),
            "permissions": permissions,
        },
    )
