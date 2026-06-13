"""
userbot/worker.py
The actual transfer engine. Called from the bot handlers.
Progress updates are sent via a callback coroutine.
"""
import asyncio
import logging
from typing import Optional, Callable, Awaitable
from telethon import TelegramClient
from telethon.tl.types import Message

from .engine import (
    _extract_file_info, build_dest_index,
    is_duplicate_fingerprint, forward_message
)

logger = logging.getLogger(__name__)

# Types
ProgressCallback = Callable[[int, int, int, int], Awaitable[None]]
# Args: (processed, total, moved, skipped)


FILE_TYPE_MAP = {
    "photo": {"photo"},
    "video": {"video"},
    "document": {"document"},
    "audio": {"audio"},
    "voice": {"voice"},
    "sticker": {"sticker"},
    "gif": {"gif"},
    "text": {None},
}


def _should_include(file_type: Optional[str], allowed_types: set[str]) -> bool:
    if "all" in allowed_types:
        return True
    if file_type is None:
        return "text" in allowed_types
    return file_type in allowed_types


async def run_transfer(
    client: TelegramClient,
    db_session,
    job_id: int,
    source_chat_id: int,
    source_topic_id: Optional[int],
    first_msg_id: int,
    last_msg_id: int,
    dest_chat_id: int,
    dest_topic_id: Optional[int],
    scan_scope: str,          # "topic" | "group" | "disabled"
    file_types: str,          # "all" or "photo,video,..."
    dry_run: bool,
    progress_cb: ProgressCallback,
) -> dict:
    """
    Main transfer coroutine.
    Returns a summary dict.
    """
    from db.queries import update_job, save_duplicate, add_to_index

    allowed_types = set(file_types.split(",")) if file_types != "all" else {"all"}
    fingerprints: set[str] = set()

    summary = {
        "moved": 0,
        "skipped": 0,
        "errors": 0,
        "duplicate_links": [],
    }

    # ── 1. Build destination fingerprint index ──────────────────────
    if scan_scope != "disabled":
        await progress_cb(0, 0, 0, 0)  # Signal "indexing"
        fingerprints = await build_dest_index(
            client, db_session,
            dest_chat_id, dest_topic_id, scan_scope
        )
        logger.info(f"Indexed {len(fingerprints)} fingerprints in destination")

    # ── 2. Collect source messages ───────────────────────────────────
    source_messages: list[Message] = []
    iter_kwargs = {
        "entity": source_chat_id,
        "min_id": first_msg_id - 1,
        "max_id": last_msg_id + 1,
        "reverse": True,
        "limit": None,
    }
    if source_topic_id:
        iter_kwargs["reply_to"] = source_topic_id

    async for msg in client.iter_messages(**iter_kwargs):
        source_messages.append(msg)

    total = len(source_messages)
    await update_job(db_session, job_id, total_messages=total)
    logger.info(f"Found {total} source messages to process")

    # ── 3. Process each message ──────────────────────────────────────
    processed = 0
    for msg in source_messages:
        processed += 1
        info = _extract_file_info(msg)

        # Filter by file type
        file_type = info["file_type"] if info else None
        if not _should_include(file_type, allowed_types):
            continue

        # Duplicate check
        if scan_scope != "disabled" and info:
            dup, reason = is_duplicate_fingerprint(info, fingerprints)
            if dup:
                summary["skipped"] += 1

                # Build source link
                username = None
                try:
                    entity = await client.get_entity(source_chat_id)
                    username = getattr(entity, "username", None)
                except Exception:
                    pass

                if username:
                    link = f"https://t.me/{username}/{msg.id}"
                elif source_topic_id:
                    cid = str(source_chat_id).replace("-100", "")
                    link = f"https://t.me/c/{cid}/{source_topic_id}/{msg.id}"
                else:
                    cid = str(source_chat_id).replace("-100", "")
                    link = f"https://t.me/c/{cid}/{msg.id}"

                summary["duplicate_links"].append((link, reason))

                await save_duplicate(
                    db_session,
                    job_id=job_id,
                    source_chat_id=source_chat_id,
                    source_message_id=msg.id,
                    duplicate_of_chat_id=dest_chat_id,
                    duplicate_of_message_id=0,
                    reason=reason,
                )
                await update_job(db_session, job_id,
                                 processed=processed,
                                 skipped_duplicates=summary["skipped"])
                await progress_cb(processed, total, summary["moved"], summary["skipped"])
                continue

        # Forward
        if not dry_run:
            ok = await forward_message(client, source_chat_id, msg.id,
                                       dest_chat_id, dest_topic_id)
            if ok:
                summary["moved"] += 1
                # Add to fingerprint set so we don't duplicate within this run
                if info:
                    if info.get("file_unique_id"):
                        fingerprints.add(f"uid:{info['file_unique_id']}")
                    if info.get("file_size"):
                        fingerprints.add(f"sz:{info['file_size']}")

                # Index the newly forwarded file
                if info:
                    await add_to_index(
                        db_session,
                        chat_id=dest_chat_id,
                        topic_id=dest_topic_id,
                        message_id=msg.id,
                        file_unique_id=info.get("file_unique_id"),
                        file_size=info.get("file_size"),
                        file_type=info.get("file_type"),
                    )
            else:
                summary["errors"] += 1
        else:
            summary["moved"] += 1  # dry run counts as "would move"

        await update_job(db_session, job_id,
                         processed=processed,
                         moved=summary["moved"],
                         last_processed_id=msg.id)
        await progress_cb(processed, total, summary["moved"], summary["skipped"])

        # Small rate-limit friendly delay
        await asyncio.sleep(0.05)

    return summary
