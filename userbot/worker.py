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
    check_duplicate, forward_message,
    get_messages_in_range,
)

logger = logging.getLogger(__name__)

# Types
ProgressCallback = Callable[[int, int, int, int], Awaitable[None]]
# Args: (processed, total, moved, skipped)


def _should_include(file_type: Optional[str], allowed_types: set[str]) -> bool:
    if "all" in allowed_types:
        return True
    if file_type is None:
        return "text" in allowed_types
    return file_type in allowed_types


def _build_source_link(source_chat_id: int, msg_id: int,
                       topic_id: Optional[int], username: Optional[str]) -> str:
    """Build a t.me link for a source message."""
    if username:
        return f"https://t.me/{username}/{msg_id}"
    cid = str(source_chat_id).replace("-100", "")
    if topic_id:
        return f"https://t.me/c/{cid}/{topic_id}/{msg_id}"
    return f"https://t.me/c/{cid}/{msg_id}"


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
    Returns a summary dict with keys: moved, skipped, errors, duplicate_links.
    """
    from db.queries import update_job, save_duplicate, add_to_index

    allowed_types = set(file_types.split(",")) if file_types != "all" else {"all"}
    fingerprints: set[str] = set()

    summary: dict = {
        "moved": 0,
        "skipped": 0,
        "errors": 0,
        "duplicate_links": [],  # list of (link, reason)
    }

    # ── 1. Resolve source entity username once (for link building) ──
    source_username: Optional[str] = None
    try:
        src_entity = await client.get_entity(source_chat_id)
        source_username = getattr(src_entity, "username", None)
    except Exception as e:
        logger.warning(f"Could not resolve source entity {source_chat_id}: {e}")

    # ── 2. Build destination fingerprint index ──────────────────────
    if scan_scope != "disabled":
        await progress_cb(0, 0, 0, 0)  # Signal "indexing"
        fingerprints = await build_dest_index(
            client, db_session,
            dest_chat_id, dest_topic_id, scan_scope
        )
        logger.info(f"Indexed {len(fingerprints)} fingerprints in destination")

    # ── 3. Collect source messages ───────────────────────────────────
    source_messages: list[Message] = await get_messages_in_range(
        client, source_chat_id, first_msg_id, last_msg_id, source_topic_id
    )

    total = len(source_messages)
    await update_job(db_session, job_id, total_messages=total)
    logger.info(f"Found {total} source messages to process")

    # ── 4. Process each message ──────────────────────────────────────
    processed = 0
    for msg in source_messages:
        processed += 1
        info = _extract_file_info(msg)

        # Filter by file type
        file_type = info["file_type"] if info else None
        if not _should_include(file_type, allowed_types):
            # Still count as processed but don't forward
            await update_job(db_session, job_id, processed=processed)
            await progress_cb(processed, total, summary["moved"], summary["skipped"])
            continue

        # Duplicate check
        if scan_scope != "disabled" and info:
            is_dup, reason = check_duplicate(info, fingerprints)
            if is_dup:
                summary["skipped"] += 1
                link = _build_source_link(
                    source_chat_id, msg.id, source_topic_id, source_username
                )
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

        # Forward (or dry-run count)
        if not dry_run:
            ok, err_reason = await forward_message(
                client, source_chat_id, msg.id, dest_chat_id, dest_topic_id
            )
            if ok:
                summary["moved"] += 1
                # Add to in-memory fingerprint set to prevent intra-run duplicates
                if info:
                    if info.get("file_unique_id"):
                        fingerprints.add(f"uid:{info['file_unique_id']}")
                    if info.get("file_size"):
                        fingerprints.add(f"sz:{info['file_size']}")
                    # Persist to DB index
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
                logger.warning(
                    f"Failed to forward msg {msg.id} from {source_chat_id}: {err_reason}"
                )
        else:
            # Dry run — count as "would move" without actually forwarding
            summary["moved"] += 1

        await update_job(db_session, job_id,
                         processed=processed,
                         moved=summary["moved"],
                         last_processed_id=msg.id)
        await progress_cb(processed, total, summary["moved"], summary["skipped"])

        # Small rate-limit friendly delay between forwards
        await asyncio.sleep(0.05)

    return summary
