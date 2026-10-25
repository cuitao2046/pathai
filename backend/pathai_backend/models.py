from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    openid_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AccessToken(Base):
    __tablename__ = "access_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class NavigationSession(Base):
    __tablename__ = "navigation_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    site_id: Mapped[str] = mapped_column(String(120))
    map_version: Mapped[str] = mapped_column(String(40))
    route_rules_version: Mapped[str] = mapped_column(String(40), default="1.0.0")
    status: Mapped[str] = mapped_column(String(20), default="active")
    destination_id: Mapped[str] = mapped_column(String(120))
    destination: Mapped[dict[str, Any]] = mapped_column(JSON)
    route: Mapped[dict[str, Any]] = mapped_column(JSON)
    origin: Mapped[dict[str, Any]] = mapped_column(JSON)
    last_sequence: Mapped[int] = mapped_column(Integer, default=0)
    instruction_sequence: Mapped[int] = mapped_column(Integer, default=1)
    last_instruction: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    arrival_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    arrival_last_sample_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    off_route_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)


class AnalyticsAggregate(Base):
    __tablename__ = "analytics_aggregates"
    __table_args__ = (CheckConstraint("aggregate_count >= 0", name="ck_analytics_aggregate_count_nonnegative"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    metric: Mapped[str] = mapped_column(String(80), index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    aggregate_count: Mapped[int] = mapped_column(Integer)
    anonymized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("user_id", "operation", "key", name="uq_idempotency_user_operation_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    operation: Mapped[str] = mapped_column(String(120))
    key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LocationSequence(Base):
    __tablename__ = "location_sequences"
    __table_args__ = (UniqueConstraint("user_id", "site_id", name="uq_location_user_site"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64))
    site_id: Mapped[str] = mapped_column(String(120))
    sequence: Mapped[int] = mapped_column(Integer)
