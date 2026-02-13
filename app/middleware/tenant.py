"""Tenant context middleware.

Note: Most tenant context setup is handled by AuthMiddleware.
This middleware handles additional tenant-specific context like language.
"""

from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.utils.tenant_context import set_current_language


class TenantMiddleware(BaseHTTPMiddleware):
    """Middleware for tenant-specific context setup.

    Handles:
    - Language detection from Accept-Language header
    - Additional tenant-specific context (loaded lazily when needed)
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and set up tenant context."""
        # Detect language preference
        language = self._detect_language(request)
        set_current_language(language)

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
