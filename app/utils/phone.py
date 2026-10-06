"""E.164 phone-number helpers.

E.164 is the ITU standard for international phone numbers:

- Starts with ``+``
- Country code (1-3 digits), the first digit non-zero
- Up to 15 digits total (country code + national number)

Example: ``+263771234567`` (Zimbabwe), ``+27821234567`` (South Africa).

The validator here is deliberately permissive on the national-number
digits: real-world numbers vary wildly in length, carriers add new
prefixes constantly, and blocking a tester on "format looks weird" is
worse than letting it through. The hard rule is: starts with ``+``,
contains only digits after that, 7-15 digits total.

Owner directive 2026-10-06: enforce E.164 across every phone input so
admins can't paste "0771234567" (local format) that will later fail
when Meta WhatsApp sends refuse it.
"""

from __future__ import annotations

import re

# Country prefix (1-3 digits starting non-zero) + 6-14 digit subscriber,
# giving a total of 7-15 digits after the ``+``.
_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


class PhoneValidationError(ValueError):
    """Raised when a value cannot be normalised to E.164."""


def normalise_phone(
    value: str | None,
    *,
    default_prefix: str | None = None,
) -> str | None:
    """Return an E.164 string or raise ``PhoneValidationError``.

    Rules:
    - ``None`` or empty / whitespace-only → returns ``None`` (callers
      decide whether a missing phone is OK).
    - Already-E.164 input → stripped of whitespace, dashes and parens
      and returned.
    - Local-format input (no leading ``+``, starts with the national
      trunk ``0`` or a bare digit) → prefixed with ``default_prefix``
      when supplied, else raises.

    ``default_prefix`` is an E.164 country code without the ``+``,
    e.g. ``"263"``. Pass it when the caller knows the tenant's country
    so admins can enter local numbers (``0771234567``) and have them
    normalise to the full E.164 form.
    """
    if value is None:
        return None
    cleaned = re.sub(r"[\s()\-\.]", "", str(value))
    if not cleaned:
        return None

    if cleaned.startswith("+"):
        candidate = cleaned
    else:
        # Not already E.164 — attempt to apply the tenant prefix.
        if not default_prefix:
            raise PhoneValidationError(
                "Phone must be in E.164 format (e.g. +263771234567)"
            )
        prefix = default_prefix.lstrip("+").strip()
        if not prefix.isdigit():
            raise PhoneValidationError(
                "Internal error: country prefix is not a digit string"
            )
        # Drop a leading national trunk 0 if present.
        national = cleaned.lstrip("0") or cleaned
        candidate = f"+{prefix}{national}"

    if not _E164_RE.match(candidate):
        raise PhoneValidationError(
            f"Phone '{value}' is not a valid international number. "
            "Use the + sign and country code (e.g. +263771234567)."
        )
    return candidate


def is_e164(value: str | None) -> bool:
    """Return True if ``value`` is already in E.164 format."""
    return bool(value) and bool(_E164_RE.match(value))
