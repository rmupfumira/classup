"""Rate limiting configuration using slowapi."""

from slowapi import Limiter
from slowapi.util import get_remote_address

# Shared limiter instance — imported by API endpoints
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
