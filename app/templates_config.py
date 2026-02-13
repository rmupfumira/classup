"""Shared Jinja2 templates configuration."""

from pathlib import Path

from starlette.templating import Jinja2Templates

from app.config import settings
from app.services.i18n_service import get_i18n_service

# Initialize templates
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates")
)

# Setup template globals
i18n = get_i18n_service()


def t(key: str, lang: str = "en", **kwargs) -> str:
    """Translation function for templates."""
    return i18n.t(key, lang, **kwargs)


# Add globals to templates
templates.env.globals["settings"] = settings
templates.env.globals["t"] = t
templates.env.globals["app_name"] = settings.app_name
