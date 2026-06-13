"""
userbot/engine.py
Manages Telethon user clients. One client per logged-in user.
"""
import asyncio
import logging
from typing import Optional
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import (
    Channel, Chat, User, InputPeerChannel,
    MessageMediaPhoto, MessageMediaDocument,
    ForumTopic, Message
)
from telethon.errors import FloodWaitError, ChatForwardsRestrictedError

logger = logging.getLogger(__name__)

# In-memory client pool  {telegram_id: TelegramClient}
_clients: dict[int, TelegramClient] = {}


def _get_api_credentials():
    import os
    return int(os.environ["API_ID"]), os.environ["API_HASH"]


async def get_client(telegram_id: int, session_string: str) -> TelegramClient:
    """Get or create a Telethon client for this user."""
    if telegram_id in _clients:
        client = _clients[telegram_id]
        if client.is_connected():
            return client
        # Reconnect
        await client.connect()
        return client

    api_id, api_hash = _get_api_credentials()
    client = TelegramClient(
        StringSession(session_string),
        api_id,
        api_hash,
        connection_retries=5,
        retry_delay=2,
        flood_sleep_threshold=60,
    )
    await client.connect()
    _clients[telegram_id] = client
    return client


async def create_login_client() -> TelegramClient:
    """Create a fresh client for the login flow (no session yet)."""
    api_id, api_hash = _get_api_credentials()
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    return client


async def get_dialogs(client: TelegramClient) -> dict:
    """
    Returns categorized dict:
    {
        "groups": [...],
        "channels": [...],
        "private": [...],
    }
    Each entry: {"id": int, "title": str, "username": str|None, "has_topics": bool}
    """
    categories = {"groups": [], "channels": [], "private": []}

    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        entry = {
            "id": dialog.id,
            "title": dialog.title or dialog.name or "Unknown",
            "username": getattr(entity, "username", None),
            "has_topics": getattr(entity, "forum", False),
            "access_hash": getattr(entity, "access_hash", None),
        }
        if isinstance(entity, Channel):
            if entity.megagroup or entity.gigagroup:
                categories["groups"].append(entry)
            else:
                categories["channels"].append(entry)
        elif isinstance(entity, Chat):
            categories["groups"].append(entry)
        elif isinstance(entity, User):
            categories["private"].append(entry)

    return categories


async def get_topics(client: TelegramClient, chat_id: int) -> list[dict]:
    """Get forum topics for a group."""
    from telethon.tl.functions.channels import GetForumTopicsRequest
    try:
        entity = await client.get_entity(chat_id)
        result = await client(GetForumTopicsRequest(
            channel=entity,
            q="",
            offset_date=None,
            offset_id=0,
            offset_topic=0,
            limit=100,
        ))
        topics = []
        for t in result.topics:
            if isinstance(t, ForumTopic):
                topics.append({
                    "id": t.id,
                    "title": t.title,
                    "top_message": t.top_message,
                })
        return topics
    except Exception as e:
        logger.warning(f"Could not get topics for {chat_id}: {e}")
        return []


def _extract_file_info(message: Message) -> Optional[dict]:
    """Extract file_unique_id, file_size, file_type from a message."""
    media = message.media
    if media is None:
        return None

    info = {"file_unique_id": None, "file_size": None, "file_type": "unknown"}

    if isinstance(media, MessageMediaPhoto):
        info["file_type"] = "photo"
        if media.photo:
            info["file_unique_id"] = str(media.photo.id)
    elif isinstance(media, MessageMediaDocument):
        doc = media.document
        if doc is None:
            return None
        info["file_unique_id"] = str(doc.id)
        info["file_size"] = doc.size

        # Detect sub-type from attributes
        from telethon.tl.types import (
            DocumentAttributeVideo, DocumentAttributeAudio,
            DocumentAttributeSticker, DocumentAttributeAnimated,
        )
        for attr in doc.attributes:
            if isinstance(attr, DocumentAttributeVideo):
                info["file_type"] = "video"
                break
            elif isinstance(attr, DocumentAttributeAudio):
                info["file_type"] = "voice" if attr.voice else "audio"
                break
            elif isinstance(attr, DocumentAttributeSticker):
                info["file_type"] = "sticker"
                break
            elif isinstance(attr, DocumentAttributeAnimated):
                info["file_type"] = "gif"
                break
        else:
            info["file_type"] = "document"

    return info


async def build_dest_index(client: TelegramClient, db_session,
                           dest_chat_id: int,
                           dest_topic_id: Optional[int],
                           scope: str) -> set[str]:
    """
    Pre-scan destination and return a set of "fingerprints" already there.
    Fingerprint = "uid:{file_unique_id}" or "sz:{file_size}"
    """
    from db.queries import add_to_index

    fingerprints: set[str] = set()
    iter_kwargs = {"entity": dest_chat_id, "reverse": False, "limit": None}

    if scope == "topic" and dest_topic_id:
        iter_kwargs["reply_to"] = dest_topic_id

    async for msg in client.iter_messages(**iter_kwargs):
        info = _extract_file_info(msg)
        if not info:
            continue

        if info["file_unique_id"]:
            fingerprints.add(f"uid:{info['file_unique_id']}")
        if info["file_size"]:
            fingerprints.add(f"sz:{info['file_size']}")

        # Also persist to DB index for caching
        await add_to_index(
            db_session,
            chat_id=dest_chat_id,
            topic_id=dest_topic_id if scope == "topic" else None,
            message_id=msg.id,
            file_unique_id=info["file_unique_id"],
            file_size=info["file_size"],
            file_type=info["file_type"],
        )

    return fingerprints


def is_duplicate_fingerprint(info: dict, fingerprints: set[str]) -> tuple[bool, str]:
    """Check info dict against fingerprint set. Returns (is_dup, reason)."""
    if info.get("file_unique_id"):
        key = f"uid:{info['file_unique_id']}"
        if key in fingerprints:
            return True, "file_unique_id"
    if info.get("file_size"):
        key = f"sz:{info['file_size']}"
        if key in fingerprints:
            return True, "file_size"
    return False, ""


async def forward_message(client: TelegramClient,
                          source_chat_id: int,
                          message_id: int,
                          dest_chat_id: int,
                          dest_topic_id: Optional[int] = None) -> bool:
    """
    Forward a single message preserving original sender header.
    Returns True on success, False on failure.
    """
    for attempt in range(3):
        try:
            kwargs = {
                "from_peer": source_chat_id,
                "message_ids": [message_id],
                "to_peer": dest_chat_id,
                "drop_author": False,   # Keep "Forwarded from X" header
                "with_my_score": False,
            }
            if dest_topic_id:
                kwargs["top_msg_id"] = dest_topic_id

            await client.forward_messages(**kwargs)
            return True

        except FloodWaitError as e:
            logger.warning(f"FloodWait: sleeping {e.seconds}s")
            await asyncio.sleep(e.seconds + 2)

        except ChatForwardsRestrictedError:
            logger.warning(f"Chat {source_chat_id} has forwards restricted — skipping msg {message_id}")
            return False

        except Exception as e:
            logger.error(f"Forward error (attempt {attempt+1}): {e}")
            await asyncio.sleep(3)

    return False
