"""
db/queries.py - helper async queries
"""
from sqlalchemy import select, delete, update, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from .models import UserSession, TransferJob, FileIndex, DuplicateRecord
from typing import Optional, List


# ── Sessions ────────────────────────────────

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


# ── Jobs ────────────────────────────────

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


async def get_job_history(
    db: AsyncSession,
    user_id: int,
    limit: int = 10,
    status: Optional[str] = None,
) -> List[TransferJob]:
    """
    Return recent jobs for a user, newest first.
    Optionally filter by status (done/running/error/stopped/pending).
    """
    q = (
        select(TransferJob)
        .where(TransferJob.user_id == user_id)
        .order_by(desc(TransferJob.id))
        .limit(limit)
    )
    if status:
        q = q.where(TransferJob.status == status)
    result = await db.execute(q)
    return result.scalars().all()


async def get_job_stats(db: AsyncSession, user_id: int) -> dict:
    """Return aggregate stats across all jobs for a user."""
    result = await db.execute(
        select(
            func.count(TransferJob.id).label("total_jobs"),
            func.sum(TransferJob.moved).label("total_moved"),
            func.sum(TransferJob.skipped_duplicates).label("total_skipped"),
            func.sum(TransferJob.errors).label("total_errors"),
        ).where(TransferJob.user_id == user_id)
    )
    row = result.one()
    return {
        "total_jobs": row.total_jobs or 0,
        "total_moved": row.total_moved or 0,
        "total_skipped": row.total_skipped or 0,
        "total_errors": row.total_errors or 0,
    }


# ── File Index ────────────────────────────────

async def is_in_index(
    db: AsyncSession,
    chat_id: int,
    file_unique_id: Optional[str] = None,
    file_size: Optional[int] = None,
    topic_id: Optional[int] = None,
    scope: str = "topic",
) -> Optional[FileIndex]:
    """
    Check if a file fingerprint already exists in the DB index for a destination.

    scope='topic'  → match chat_id + topic_id (topic-level dedup)
    scope='group'  → match chat_id only (group-wide dedup)

    Checks file_unique_id first; falls back to file_size if uid not provided.
    Returns the matching FileIndex row or None.
    """
    if not file_unique_id and not file_size:
        return None

    q = select(FileIndex).where(FileIndex.chat_id == chat_id)
    if scope == "topic" and topic_id is not None:
        q = q.where(FileIndex.topic_id == topic_id)

    if file_unique_id:
        q = q.where(FileIndex.file_unique_id == file_unique_id)
    else:
        q = q.where(FileIndex.file_size == file_size)

    result = await db.execute(q.limit(1))
    return result.scalar_one_or_none()


# Keep old name as alias for backwards compatibility
async def is_duplicate(db: AsyncSession, chat_id: int, file_unique_id: str = None,
                        file_size: int = None, topic_id: int = None,
                        scope: str = "topic") -> Optional[FileIndex]:
    return await is_in_index(
        db, chat_id,
        file_unique_id=file_unique_id,
        file_size=file_size,
        topic_id=topic_id,
        scope=scope,
    )


async def add_to_index(db: AsyncSession, **kwargs) -> FileIndex:
    """
    Add a file fingerprint to the index.
    Silently ignores duplicate entries (same chat_id + file_unique_id).
    """
    # Avoid inserting exact duplicates (same chat + uid)
    if kwargs.get("file_unique_id") and kwargs.get("chat_id"):
        existing = await is_in_index(
            db,
            chat_id=kwargs["chat_id"],
            file_unique_id=kwargs["file_unique_id"],
            topic_id=kwargs.get("topic_id"),
            scope="group",  # check globally to avoid any duplicate rows
        )
        if existing:
            return existing

    obj = FileIndex(**kwargs)
    db.add(obj)
    await db.commit()
    return obj


async def save_duplicate(db: AsyncSession, **kwargs) -> DuplicateRecord:
    obj = DuplicateRecord(**kwargs)
    db.add(obj)
    await db.commit()
    return obj


async def get_duplicates_for_job(
    db: AsyncSession, job_id: int
) -> List[DuplicateRecord]:
    result = await db.execute(
        select(DuplicateRecord)
        .where(DuplicateRecord.job_id == job_id)
        .order_by(DuplicateRecord.id)
    )
    return result.scalars().all()


async def count_duplicates_for_job(db: AsyncSession, job_id: int) -> int:
    """Return the count of duplicate records for a job."""
    result = await db.execute(
        select(func.count(DuplicateRecord.id))
        .where(DuplicateRecord.job_id == job_id)
    )
    return result.scalar_one() or 0
