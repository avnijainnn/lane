from datetime import datetime, timezone
import os
from sqlalchemy import create_engine, ForeignKey, UniqueConstraint, String, DateTime, Integer, Boolean, Text, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def now():
    return datetime.now(timezone.utc)

URL = os.getenv('DATABASE_URL', 'sqlite:///./lane_dev.db')
engine = create_engine(URL, pool_pre_ping=True, connect_args={'check_same_thread': False} if URL.startswith('sqlite') else {})
SessionLocal = sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

class Client(Base):
    __tablename__ = 'clients'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    retell_agent_id: Mapped[str] = mapped_column(String(160), unique=True)
    highlevel_location_id: Mapped[str] = mapped_column(String(160))
    timezone: Mapped[str] = mapped_column(String(80), default='America/Chicago')
    calendar_id: Mapped[str] = mapped_column(String(160))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)

class Manifest(Base):
    __tablename__ = 'manifests'
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey('clients.id'), index=True)
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[dict] = mapped_column(JSON)
    sha256: Mapped[str] = mapped_column(String(64))
    approved_agent_version: Mapped[str] = mapped_column(String(40))
    approver: Mapped[str] = mapped_column(String(160))
    approver_role: Mapped[str] = mapped_column(String(20))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    __table_args__ = (UniqueConstraint('client_id','version'),)

class Call(Base):
    __tablename__ = 'calls'
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey('clients.id'), index=True)
    retell_call_id: Mapped[str] = mapped_column(String(160), unique=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    agent_version: Mapped[str] = mapped_column(String(40))
    transcript: Mapped[str] = mapped_column(Text)
    tool_trace: Mapped[list] = mapped_column(JSON, default=list)
    manifest_id: Mapped[int | None] = mapped_column(ForeignKey('manifests.id'), nullable=True)
    manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    coverage: Mapped[str] = mapped_column(String(40), default='unknown')
    processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

class Finding(Base):
    __tablename__ = 'findings'
    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey('calls.id'), index=True)
    type: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text)
    promised: Mapped[dict] = mapped_column(JSON, default=dict)
    observed: Mapped[dict] = mapped_column(JSON, default=dict)
    manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_version: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('call_id','type'),)

class Observation(Base):
    __tablename__ = 'observations'
    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey('findings.id'), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    source: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict] = mapped_column(JSON)

class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey('findings.id'), index=True)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    stage: Mapped[str] = mapped_column(String(20))
    done: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (UniqueConstraint('finding_id','stage'),)

class DemoAppointment(Base):
    __tablename__ = 'demo_appointments'
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey('clients.id'))
    record: Mapped[dict] = mapped_column(JSON)


def init_db():
    Base.metadata.create_all(engine)
