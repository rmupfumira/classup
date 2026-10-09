"""Regression: `@require_role` on handlers in a module that uses
`from __future__ import annotations`.

PEP 563 leaves every type hint as a string. The old `require_role`
decorator used `@wraps(func)` + `*args, **kwargs`, which left the
wrapper's annotations as the string form inherited from the wrapped
function. FastAPI's schema generator then couldn't resolve the body
model (`EventCreate` as a `ForwardRef`), silently dropped it to a
query param, and every request failed with ``'body: Field required'``.

The fix in `app.utils.permissions._materialise_annotations` resolves
the strings in the wrapped function's own module globals and copies
real class references onto both the wrapper and the wrapped function
— so this test proves the shape FastAPI needs is actually there.
"""

from __future__ import annotations

import typing

from pydantic import BaseModel

from app.utils.permissions import require_role


class _SampleBody(BaseModel):
    """Dummy body model defined in THIS module so the regression
    matches the real-world setup."""
    name: str


def test_require_role_resolves_string_annotations_on_wrapper():
    """After decoration, the wrapper's annotations should contain the
    actual ``_SampleBody`` class, not a string or a ``ForwardRef``.
    FastAPI reads these to build the body-parameter schema; a string
    there means the body is silently treated as a query param."""

    @require_role("SCHOOL_ADMIN")
    async def handler(body: _SampleBody) -> dict:
        return {"ok": True}

    ann = handler.__annotations__
    assert "body" in ann, f"wrapper lost the body annotation: {ann!r}"
    assert ann["body"] is _SampleBody, (
        f"wrapper's body annotation was not resolved to the real class — got "
        f"{ann['body']!r} (type={type(ann['body']).__name__})"
    )


def test_require_role_resolves_string_annotations_on_wrapped_function():
    """FastAPI's get_type_hints uses ``follow_wrapped=True`` by
    default, so it walks ``__wrapped__`` back to the original function
    and reads ITS annotations. We mutate those too for safety."""

    @require_role("SCHOOL_ADMIN")
    async def handler(body: _SampleBody) -> dict:
        return {"ok": True}

    wrapped = getattr(handler, "__wrapped__", None)
    assert wrapped is not None, "require_role should set __wrapped__ via functools.wraps"
    assert wrapped.__annotations__.get("body") is _SampleBody


def test_require_role_still_works_when_annotations_are_already_classes():
    """A module WITHOUT ``from __future__ import annotations`` already
    has real class annotations. The decorator must not break them —
    it should just no-op the resolution."""

    # Simulate a module without PEP 563 by building a function whose
    # annotations are already real classes.
    async def handler(body: _SampleBody) -> dict:
        return {"ok": True}
    handler.__annotations__ = {"body": _SampleBody, "return": dict}

    decorated = require_role("SCHOOL_ADMIN")(handler)
    assert decorated.__annotations__["body"] is _SampleBody
