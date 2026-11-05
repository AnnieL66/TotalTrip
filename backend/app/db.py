"""Persistence. SQLite by default for local dev; set DATABASE_URL to PostgreSQL in production."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Engine,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Trip(Base):
    __tablename__ = "trips"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    brief_version: Mapped[int] = mapped_column(Integer, default=0)


class BriefVersion(Base):
    """Every brief change is kept, so later features (locks, undo) can build on the history."""

    __tablename__ = "brief_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON)
    changed_by: Mapped[str] = mapped_column(String(16))  # "user" | "assistant"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PlanSearch(Base):
    __tablename__ = "plan_searches"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"), index=True)
    brief_version: Mapped[int] = mapped_column(Integer)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Event(Base):
    """Product analytics and cost tracking: plans viewed, clicks to book, LLM token usage, ..."""

    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    props: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PriceReport(Base):
    __tablename__ = "price_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"), index=True)
    search_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    plan_id: Mapped[str | None] = mapped_column(String(8), nullable=True)
    item_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    shown_price: Mapped[float | None] = mapped_column(nullable=True)
    observed_price: Mapped[float | None] = mapped_column(nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SpendReport(Base):
    """What the traveler actually spent, compared later with our estimate."""

    __tablename__ = "spend_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"), index=True)
    plan_id: Mapped[str | None] = mapped_column(String(8), nullable=True)
    search_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # plan ids only mean something within one search
    estimated_total: Mapped[float | None] = mapped_column(nullable=True)
    actual: Mapped[dict] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def add_missing_columns(bind: Engine) -> None:
    """create_all won't change existing tables. Stands in for migrations until Alembic is set up."""
    columns = {c["name"] for c in inspect(bind).get_columns("spend_reports")}
    if "search_id" not in columns:
        with bind.begin() as conn:
            conn.execute(text("ALTER TABLE spend_reports ADD COLUMN search_id VARCHAR(32)"))


def init_db() -> None:
    Base.metadata.create_all(engine)
    add_missing_columns(engine)


def get_session():
    with SessionLocal() as session:
        yield session


def log_event(session: Session, name: str, trip_id: str | None = None, **props) -> None:
    session.add(Event(trip_id=trip_id, name=name, props=props))
