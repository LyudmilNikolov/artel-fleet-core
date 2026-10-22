from collections.abc import AsyncGenerator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.audit_events import register_audit_events
from app.config import settings

# Async engine for FastAPI
async_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.APP_DEBUG,
    pool_size=20,
    max_overflow=10,
)

AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Sync engine for Celery tasks
sync_engine = create_engine(
    settings.DATABASE_URL_SYNC,
    echo=False,
    pool_size=5,
    max_overflow=5,
)

SyncSessionLocal = sessionmaker(bind=sync_engine)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Every create / update / delete of an audited model is logged automatically (see app/audit_events.py).
register_audit_events()


def get_sync_db() -> Session:
    """Get sync session for Celery tasks."""
    return SyncSessionLocal()
