"""Internationalization service for translations."""

import json
from functools import lru_cache
from pathlib import Path

from app.config import settings


class I18nService:
    """Service for handling translations and internationalization."""

    def __init__(self):
        self.translations: dict[str, dict] = {}
        self._load_translations()

    def _load_translations(self) -> None:
        """Load all translation files from the translations directory."""
        translations_dir = Path(__file__).resolve().parent.parent.parent / "translations"

        for lang in settings.supported_languages_list:
            lang_file = translations_dir / lang / "messages.json"
            if lang_file.exists():
                with open(lang_file, encoding="utf-8") as f:
                    self.translations[lang] = json.load(f)
            else:
                # Create empty translations for missing languages
                self.translations[lang] = {}

    def t(self, key: str, lang: str = "en", **kwargs) -> str:
        """Translate a dot-notation key.

        Args:
            key: Dot-notation key (e.g., 'common.save', 'auth.login')
            lang: Language code (e.g., 'en', 'af')
            **kwargs: Variables to interpolate (e.g., child_name='John')

        Returns:
            Translated string, or the key if translation not found
        """
        # Try requested language first, fall back to English
        value = self._get_translation(key, lang)
        if value is None and lang != "en":
            value = self._get_translation(key, "en")
        if value is None:
            return key

        # Interpolate variables
        if kwargs:
            for param_key, param_value in kwargs.items():
                value = value.replace(f"{{{{{param_key}}}}}", str(param_value))

        return value

    def _get_translation(self, key: str, lang: str) -> str | None:
        """Get a translation value by dot-notation key."""
        keys = key.split(".")
        value = self.translations.get(lang, {})

        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return None
            if value is None:
                return None

        return value if isinstance(value, str) else None

    def get_all_translations(self, lang: str) -> dict:
        """Get all translations for a language."""
        return self.translations.get(lang, self.translations.get("en", {}))


@lru_cache
def get_i18n_service() -> I18nService:
    """Get cached i18n service instance."""
    return I18nService()
