from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, scoped_session, DeclarativeBase
from config import DATABASE_URL


class Base(DeclarativeBase):
    pass


engine = create_engine(DATABASE_URL, echo=False)

# Enable WAL mode for better concurrency
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


# IMPORTANT: expire_on_commit=True (the default) so objects always
# refresh from DB after commit. This prevents stale data bugs where
# background threads see old values.
Session = scoped_session(sessionmaker(bind=engine))


def init_db():
    """Create all tables if they don't exist."""
    import models  # noqa: F401 — ensures models are registered
    Base.metadata.create_all(engine)


def get_fresh_session():
    """Get a brand new, independent session (not scoped).
    Use this for background tasks that must not share state with Flask."""
    factory = sessionmaker(bind=engine)
    return factory()
