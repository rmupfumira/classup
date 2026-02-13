"""Database configuration and session management."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import settings


def create_engine() -> AsyncEngine:
    """Create the async database engine."""
    # Use NullPool in development for easier debugging
    pool_class = NullPool if settings.is_development else None

    engine_kwargs = {
        "echo": settings.app_debug,
        "future": True,
    }

    if pool_class:
        engine_kwargs["poolclass"] = pool_class
    else:
        engine_kwargs["pool_size"] = settings.database_pool_size
        engine_kwargs["max_overflow"] = settings.database_max_overflow
        engine_kwargs["pool_pre_ping"] = True

    return create_async_engine(settings.database_url, **engine_kwargs)


# Global engine instance
engine = create_engine()

# Session factory
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields a database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for database session (for use outside of FastAPI dependencies)."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize database (create tables if needed)."""
    from app.models.base import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Close database connections."""
    await engine.dispose()
