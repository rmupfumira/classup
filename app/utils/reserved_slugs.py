"""Reserved tenant slugs.

Slugs in this set cannot be used by tenants because they collide with
application routes or future product paths.
"""

RESERVED_SLUGS: frozenset[str] = frozenset(
    {
        # Auth / account
        "login",
        "logout",
        "register",
        "forgot-password",
        "reset-password",
        "profile",
        # Core app
        "dashboard",
        "students",
        "classes",
        "attendance",
        "reports",
        "photos",
        "documents",
        "messages",
        "announcements",
        "invitations",
        "imports",
        "teachers",
        "billing",
        "academic",
        "timetable",
        "settings",
        "onboarding",
        "subscription",
        "t",
        # Admin
        "admin",
        "super-admin",
        # API / infra
        "api",
        "static",
        "health",
        "favicon.ico",
        "robots.txt",
        "sitemap.xml",
        # Marketing / reserved for future
        "home",
        "about",
        "contact",
        "pricing",
        "features",
        "support",
        "help",
        "terms",
        "privacy",
        "blog",
        "docs",
        "www",
        "mail",
        "app",
    }
)


def is_reserved_slug(slug: str) -> bool:
    """Return True if the slug is reserved and cannot be used by a tenant."""
    return slug.strip().lower() in RESERVED_SLUGS
