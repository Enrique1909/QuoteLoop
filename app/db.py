import os

from sqlmodel import Session, SQLModel, create_engine


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:///./clientroom.db")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


DATABASE_URL = _database_url()
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)


def init_db() -> None:
    from app import models  # noqa: F401  (register tables)
    SQLModel.metadata.create_all(engine)


def get_session():
    return Session(engine)
