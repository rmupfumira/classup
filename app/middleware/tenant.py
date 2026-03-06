"""Tenant context middleware.

Note: Most tenant context setup is handled by AuthMiddleware.
This middleware handles additional tenant-specific context like language
and loading the Tenant object for template rendering.
"""

import logging
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.utils.tenant_context import get_tenant_id_or_none, set_current_language

logger = logging.getLogger(__name__)


class TenantMiddleware(BaseHTTPMiddleware):
    """Middleware for tenant-specific context setup.

    Handles:
    - Language detection from Accept-Language header
    - Loading Tenant object onto request.state for template rendering
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and set up tenant context."""
        # Detect language preference
        language = self._detect_language(request)
        set_current_language(language)

        # Load tenant for web routes (not API) so sidebar/mobile_nav can check features
        request.state.tenant = None
        path = request.url.path
        if not path.startswith("/api/") and not path.startswith("/static/"):
            tenant_id = get_tenant_id_or_none()
            if tenant_id:
                try:
                    from app.database import get_db_context
                    from app.models.tenant import Tenant

                    async with get_db_context() as db:
                        request.state.tenant = await db.get(Tenant, tenant_id)
                except Exception:
                    logger.debug("Failed to load tenant for nav context", exc_info=True)

        response = await call_next(request)
        return response

    def _detect_language(self, request: Request) -> str:
        """Detect preferred language from request.

        Priority:
        1. Query parameter: ?lang=af
        2. Cookie: language=af
        3. Accept-Language header
        4. Default language from settings

        Returns:
            Language code (e.g., 'en', 'af')
        """
        # Query parameter
        lang = request.query_params.get("lang")
        if lang and lang in settings.supported_languages_list:
            return lang

        # Cookie
        lang = request.cookies.get("language")
        if lang and lang in settings.supported_languages_list:
            return lang

        # Accept-Language header
        accept_lang = request.headers.get("Accept-Language", "")
        for lang in self._parse_accept_language(accept_lang):
            if lang in settings.supported_languages_list:
                return lang

        # Default
        return settings.default_language

    def _parse_accept_language(self, header: str) -> list[str]:
        """Parse Accept-Language header and return languages in preference order.

        Example header: en-US,en;q=0.9,af;q=0.8

        Returns:
            List of language codes sorted by preference
        """
        if not header:
            return []

        languages = []
        for part in header.split(","):
            part = part.strip()
            if not part:
                continue

            if ";q=" in part:
                lang, q = part.split(";q=")
                try:
                    quality = float(q)
                except ValueError:
                    quality = 0.0
            else:
                lang = part
                quality = 1.0

            # Extract just the language code (not region)
            lang = lang.split("-")[0].lower()
            languages.append((lang, quality))

        # Sort by quality descending
        languages.sort(key=lambda x: x[1], reverse=True)

        return [lang for lang, _ in languages]
