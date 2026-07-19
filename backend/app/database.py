"""
app.database
------------
Engine and session management for the platform's OWN admin/metadata
database (not the customer target databases -- see app.db.target_connection
for that). This is a single, long-lived, pooled connection since it's our
own infrastructure, so a tuned QueuePool is appropriate here (unlike
per-tenant target connections, which intentionally use NullPool).
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_settings = get_settings()

# QueuePool (SQLAlchemy's default for non-SQLite engines) is appropriate here:
# this is a single, known, always-on database we control, so holding a small
# pool of warm connections open is a performance win rather than a resource risk.
admin_engine = create_engine(
    _settings.admin_database_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,   # detects and discards stale connections after DB restarts
    pool_recycle=1800,    # recycle connections every 30 minutes to avoid firewall/idle drops
)

AdminSessionLocal = sessionmaker(bind=admin_engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_admin_db_session() -> Generator[Session, None, None]:
    """
    FastAPI dependency that yields a scoped admin-database session and
    guarantees it is closed after the request completes, even on error.
    """
    session = AdminSessionLocal()
    try:
        yield session
    finally:
        session.close()
