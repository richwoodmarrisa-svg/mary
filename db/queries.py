"""
db/queries.py - helper async queries
"""
from sqlalchemy import select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession
from .models import UserSession, TransferJob, FileIndex, DuplicateRecord
from typing import Optional


# ── Sessions ──────────────────────────────────

async def get_session(db: AsyncSession, telegram_id: int) -> Optional[UserSession]:
    result = await db.execute(
        select(UserSession).where(UserSession.telegram_id == telegram_id)
    )
    return result.scalar_one_or_none()


async def save_session(db: AsyncSession, telegram_id: int, session_string: str,
                       phone: str = None, first_name: str = None) -> UserSession:
    existing = await get_session(db, telegram_id)
    if existing:
        existing.session_string = session_string
        if phone:
            existing.phone = phone
        if first_name:
            existing.first_name = first_name
        await db.commit()
        return existing
    obj = UserSession(
        telegram_id=telegram_id,
        session_string=session_string,
        phone=phone,
        first_name=first_name,
    )
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


async def delete_session(db: AsyncSession, telegram_id: int):
    await db.execute(delete(UserSession).where(UserSession.telegram_id == telegram_id))
    await db.commit()


# ── Jobs ──────────────────────────────────────

async def create_job(db: AsyncSession, **kwargs) -> TransferJob:
    job = TransferJob(**kwargs)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def get_job(db: AsyncSession, job_id: int) -> Optional[TransferJob]:
    result = await db.execute(select(TransferJob).where(TransferJob.id == job_id))
    return result.scalar_one_or_none()


async def update_job(db: AsyncSession, job_id: int, **kwargs):
    await db.execute(
        update(TransferJob).where(TransferJob.id == job_id).values(**kwargs)
    )
    await db.commit()


# ── File Index ────────────────────────────────

async def is_duplicate(db: AsyncSession, chat_id: int, file_unique_id: str = None,
                        file_size: int = None, topic_id: int = None,
                        scope: str = "topic") -> Optional[FileIndex]:
    """
    Check if a file already exists in destination.
    scope: 'topic' = match topic_id too, 'group' = entire chat_id.
    """
    if not file_unique_id and not file_size:
        return None

    q = select(FileIndex).where(FileIndex.chat_id == chat_id)
    if scope == "topic" and topic_id is not None:
        q = q.where(FileIndex.topic_id == topic_id)

    if file_unique_id:
        q = q.where(FileIndex.file_unique_id == file_unique_id)
    elif file_size:
        q = q.where(FileIndex.file_size == file_size)

    result = await db.execute(q.limit(1))
    return result.scalar_one_or_none()


async def add_to_index(db: AsyncSession, **kwargs) -> FileIndex:
    obj = FileIndex(**kwargs)
    db.add(obj)
    await db.commit()
    return obj


async def save_duplicate(db: AsyncSession, **kwargs) -> DuplicateRecord:
    obj = DuplicateRecord(**kwargs)
    db.add(obj)
    await db.commit()
    return obj


async def get_duplicates_for_job(db: AsyncSession, job_id: int):
    result = await db.execute(
        select(DuplicateRecord).where(DuplicateRecord.job_id == job_id)
    )
    return result.scalars().all()
