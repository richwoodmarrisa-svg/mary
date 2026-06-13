"""
db/models.py - SQLAlchemy async models
"""
from sqlalchemy import (
    Column, Integer, BigInteger, String, Boolean,
    DateTime, Text, ForeignKey, Index
)
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from datetime import datetime
import os


class Base(DeclarativeBase):
    pass


class UserSession(Base):
    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    session_string = Column(Text, nullable=False)   # Telethon StringSession
    phone = Column(String(32))
    first_name = Column(String(128))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    jobs = relationship("TransferJob", back_populates="user")


class TransferJob(Base):
    __tablename__ = "transfer_jobs"

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("user_sessions.telegram_id"), nullable=False)
    status = Column(String(32), default="pending")  # pending/running/done/error/paused

    # Source
    source_chat_id = Column(BigInteger)
    source_chat_title = Column(String(256))
    source_topic_id = Column(Integer, nullable=True)
    source_topic_title = Column(String(256), nullable=True)
    source_first_msg_id = Column(Integer)
    source_last_msg_id = Column(Integer)

    # Destination
    dest_chat_id = Column(BigInteger)
    dest_chat_title = Column(String(256))
    dest_topic_id = Column(Integer, nullable=True)
    dest_topic_title = Column(String(256), nullable=True)

    # Options
    scan_scope = Column(String(32), default="topic")  # topic/group/disabled
    file_types = Column(String(256), default="all")   # comma-separated or "all"
    dry_run = Column(Boolean, default=False)

    # Progress
    total_messages = Column(Integer, default=0)
    processed = Column(Integer, default=0)
    moved = Column(Integer, default=0)
    skipped_duplicates = Column(Integer, default=0)
    errors = Column(Integer, default=0)
    last_processed_id = Column(Integer, nullable=True)  # for resume

    created_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)

    user = relationship("UserSession", back_populates="jobs")
    duplicates = relationship("DuplicateRecord", back_populates="job")


class FileIndex(Base):
    """Cached file index for fast duplicate detection."""
    __tablename__ = "file_index"

    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, nullable=False)
    topic_id = Column(Integer, nullable=True)
    message_id = Column(Integer, nullable=False)

    # Detection fields
    file_unique_id = Column(String(128), nullable=True, index=True)
    file_size = Column(BigInteger, nullable=True)
    file_type = Column(String(32))   # photo/video/document/audio/voice/sticker

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_file_index_chat_unique", "chat_id", "file_unique_id"),
        Index("ix_file_index_chat_size", "chat_id", "file_size"),
    )


class DuplicateRecord(Base):
    __tablename__ = "duplicates"

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("transfer_jobs.id"))
    source_chat_id = Column(BigInteger)
    source_message_id = Column(Integer)
    duplicate_of_chat_id = Column(BigInteger)
    duplicate_of_message_id = Column(Integer)
    reason = Column(String(64))   # file_unique_id / file_size

    job = relationship("TransferJob", back_populates="duplicates")


# ──────────────────────────────────────────────
# Engine + session factory
# ──────────────────────────────────────────────
_engine = None
_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        url = os.environ["DATABASE_URL"]
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        _engine = create_async_engine(url, echo=False, pool_pre_ping=True)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), expire_on_commit=False
        )
    return _session_factory


async def init_db():
    """Create all tables."""
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
