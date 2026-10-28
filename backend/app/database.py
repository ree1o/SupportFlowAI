from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import Request
from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Settings


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Database:
    def __init__(self, settings: Settings):
        timeout = settings.database_timeout_seconds
        url = make_url(settings.database_url.get_secret_value())
        options = url.query.get("options", "")
        if not isinstance(options, str):
            raise ValueError("Database options must be a string")
        self.engine = create_engine(
            settings.database_url.get_secret_value(),
            pool_pre_ping=True,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=timeout,
            hide_parameters=True,
            connect_args={
                "connect_timeout": timeout,
                "options": f"{options} -c statement_timeout={timeout * 1000}".strip(),
            },
        )
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        # Services explicitly commit / use session.begin(). Dependency cleanup never commits.
        with self.session_factory() as session:
            try:
                yield session
            except Exception:
                session.rollback()
                raise

    def dispose(self) -> None:
        self.engine.dispose()


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.database.session() as session:
        yield session
