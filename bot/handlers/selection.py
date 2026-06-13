"""
bot/handlers/selection.py
Handles: chat browsing, topic selection, message range selection.
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery

from bot.states import get_state, Step
from bot.keyboards import (
    chat_category_menu, chat_list_keyboard, topic_list_keyboard,
    msg_range_keyboard, confirm_first_msg, scan_scope_keyboard,
    file_types_keyboard, job_confirm_keyboard, back_btn,
)
from db.models import get_session_factory
from db.queries import get_session as db_get_session
from userbot.engine import get_client, get_dialogs, get_topics

logger = logging.getLogger(__name__)
router = Router()

# Cache dialogs per user to avoid re-fetching on every button press
_dialog_cache: dict[int, dict] = {}

# Category display labels
_CATEGORY_LABELS = {
    "groups": "group",
    "channels": "channel",
    "private": "private chat",
}


async def _require_login(cb: CallbackQuery) -> str | None:
    """Return session_string or None and send error."""
    async with get_session_factory()() as db:
        sess = await db_get_session(db, cb.from_user.id)
    if not sess:
        await cb.answer("❌ Not logged in. Use /start first.", show_alert=True)
        return None
    return sess.session_string


# ── New transfer entry ─────────────────────────────────────────────

@router.callback_query(F.data == "new_job")
async def cb_new_job(cb: CallbackQuery):
    state = get_state(cb.from_user.id)
    sess_str = await _require_login(cb)
    if not sess_str:
        return

    # Show a summary of what's set so far
    lines = ["➕ <b>New Transfer</b>\n"]

    src = f"{state.source_chat_title}"
    if state.source_topic_title:
        src += f" → {state.source_topic_title}"
    lines.append(f"📤 Source: {src if state.source_chat_id else '—'}")

    dst = f"{state.dest_chat_title}"
    if state.dest_topic_title:
        dst += f" → {state.dest_topic_title}"
    lines.append(f"📥 Dest: {dst if state.dest_chat_id else '—'}")

    msg_range = "—"
    if state.source_first_msg_id and state.source_last_msg_id:
        msg_range = f"{state.source_first_msg_id} → {state.source_last_msg_id}"
    lines.append(f"📌 Range: {msg_range}")
    lines.append(f"🔍 Scope: {state.scan_scope}")
    lines.append(f"📂 Types: {', '.join(state.file_types)}")

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Set Source", callback_data="pick_source")],
        [InlineKeyboardButton(text="📥 Set Destination", callback_data="pick_dest")],
        [InlineKeyboardButton(text="📌 Set Message Range", callback_data="pick_range")],
        [InlineKeyboardButton(text="🔍 Scan Scope", callback_data="pick_scope")],
        [InlineKeyboardButton(text="📂 File Types", callback_data="pick_types")],
        [InlineKeyboardButton(text="✅ Review & Confirm", callback_data="confirm_job")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="main_menu")],
    ])

    await cb.message.edit_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")
    await cb.answer()


# ── Source / Dest category picker ─────────────────────────────────

@router.callback_query(F.data.in_({"pick_source", "pick_dest"}))
async def cb_pick_chat_mode(cb: CallbackQuery):
    mode = "source" if cb.data == "pick_source" else "dest"
    state = get_state(cb.from_user.id)
    state.step = Step.PICK_SOURCE_CHAT if mode == "source" else Step.PICK_DEST_CHAT

    sess_str = await _require_login(cb)
    if not sess_str:
        return

    await cb.message.edit_text("⏳ Loading your chats…", reply_markup=None)
    await cb.answer()

    try:
        client = await get_client(cb.from_user.id, sess_str)
        logger.info(f"Fetching dialogs for user {cb.from_user.id}")
        dialogs = await get_dialogs(client)
        _dialog_cache[cb.from_user.id] = dialogs

        total = sum(len(v) for v in dialogs.values())
        logger.info(
            f"Dialog cache populated for user {cb.from_user.id}: "
            f"{total} chats "
            f"({len(dialogs['groups'])}G / {len(dialogs['channels'])}C / "
            f"{len(dialogs['private'])}P)"
        )

        if total == 0:
            await cb.message.edit_text(
                "⚠️ No chats found in your account.\n\n"
                "Make sure you are logged in with the correct account "
                "and that you have joined some chats.",
                reply_markup=back_btn("new_job"),
            )
            return

        label = "source" if mode == "source" else "destination"
        await cb.message.edit_text(
            f"Choose a category for the <b>{label}</b>:\n\n"
            f"👥 Groups: {len(dialogs['groups'])}\n"
            f"📢 Channels: {len(dialogs['channels'])}\n"
            f"💬 Private: {len(dialogs['private'])}",
            reply_markup=chat_category_menu(mode),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.exception(f"Failed to load dialogs for user {cb.from_user.id}: {e}")
        await cb.message.edit_text(
            f"❌ Failed to load chats: <code>{e}</code>\n\n"
            "Please try again or re-login with /start.",
            reply_markup=back_btn("new_job"),
            parse_mode="HTML",
        )


# ── Category selected → show chat list ────────────────────────────

@router.callback_query(F.data.startswith("cats:"))
async def cb_chat_category(cb: CallbackQuery):
    _, category, mode = cb.data.split(":")
    dialogs = _dialog_cache.get(cb.from_user.id, {})

    # If cache is empty, prompt user to reload
    if not dialogs:
        await cb.answer(
            "⚠️ Chat list not loaded yet. Please tap Set Source/Destination again.",
            show_alert=True,
        )
        return

    chats = dialogs.get(category, [])
    label = _CATEGORY_LABELS.get(category, category)

    if not chats:
        await cb.answer(
            f"No {label}s found in your account. Try a different category.",
            show_alert=True,
        )
        return

    state = get_state(cb.from_user.id)
    state.page_offset = 0

    dest_label = "source" if mode == "source" else "destination"
    await cb.message.edit_text(
        f"Select a <b>{label}</b> as <b>{dest_label}</b>:\n"
        f"({len(chats)} found)",
        reply_markup=chat_list_keyboard(chats, category, mode, 0),
        parse_mode="HTML",
    )
    await cb.answer()


# ── Pagination ─────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("chatpage:"))
async def cb_chat_page(cb: CallbackQuery):
    _, category, mode, offset_str = cb.data.split(":")
    offset = int(offset_str)
    dialogs = _dialog_cache.get(cb.from_user.id, {})
    chats = dialogs.get(category, [])

    await cb.message.edit_reply_markup(
        reply_markup=chat_list_keyboard(chats, category, mode, offset)
    )
    await cb.answer()


# ── Chat selected ──────────────────────────────────────────────────

@router.callback_query(F.data.startswith("setchat:"))
async def cb_set_chat(cb: CallbackQuery):
    _, mode, chat_id_str, title = cb.data.split(":", 3)
    chat_id = int(chat_id_str)
    state = get_state(cb.from_user.id)

    # Validate source != destination
    if mode == "source" and state.dest_chat_id == chat_id:
        await cb.answer(
            "⚠️ Source and destination cannot be the same chat.",
            show_alert=True,
        )
        return
    if mode == "dest" and state.source_chat_id == chat_id:
        await cb.answer(
            "⚠️ Source and destination cannot be the same chat.",
            show_alert=True,
        )
        return

    # Find chat info from cache
    dialogs = _dialog_cache.get(cb.from_user.id, {})
    all_chats = (
        dialogs.get("groups", []) +
        dialogs.get("channels", []) +
        dialogs.get("private", [])
    )
    chat_info = next((c for c in all_chats if c["id"] == chat_id), None)
    has_topics = chat_info.get("has_topics", False) if chat_info else False

    if mode == "source":
        state.source_chat_id = chat_id
        state.source_chat_title = title
        state.source_topic_id = None
        state.source_topic_title = None
    else:
        state.dest_chat_id = chat_id
        state.dest_chat_title = title
        state.dest_topic_id = None
        state.dest_topic_title = None

    if has_topics:
        await cb.message.edit_text(
            f"⏳ Loading topics for <b>{title}</b>…",
            reply_markup=None,
            parse_mode="HTML",
        )
        await cb.answer()
        try:
            async with get_session_factory()() as db:
                sess = await db_get_session(db, cb.from_user.id)
            client = await get_client(cb.from_user.id, sess.session_string)
            topics = await get_topics(client, chat_id)

            if topics:
                await cb.message.edit_text(
                    f"Select a topic in <b>{title}</b>:\n"
                    f"({len(topics)} topics found)",
                    reply_markup=topic_list_keyboard(topics, mode),
                    parse_mode="HTML",
                )
                return
            else:
                # Forum group but no topics returned — treat as no-topic
                await cb.message.edit_text(
                    f"ℹ️ No topics found in <b>{title}</b>. "
                    "Using General (no topic).",
                    parse_mode="HTML",
                )
        except Exception as e:
            logger.warning(f"Failed to load topics for {chat_id}: {e}")
            await cb.message.edit_text(
                f"⚠️ Could not load topics for <b>{title}</b>: <code>{e}</code>\n"
                "Proceeding without topic selection.",
                parse_mode="HTML",
            )
    else:
        await cb.answer(
            f"✅ {title} selected as {'source' if mode == 'source' else 'destination'}."
        )

    await _back_to_new_job(cb)


# ── Topic selected ─────────────────────────────────────────────────

@router.callback_query(F.data.startswith("settopic:"))
async def cb_set_topic(cb: CallbackQuery):
    _, mode, topic_id_str, title = cb.data.split(":", 3)
    topic_id = int(topic_id_str)
    state = get_state(cb.from_user.id)

    if mode == "source":
        state.source_topic_id = topic_id if topic_id != 0 else None
        state.source_topic_title = title if topic_id != 0 else None
    else:
        state.dest_topic_id = topic_id if topic_id != 0 else None
        state.dest_topic_title = title if topic_id != 0 else None

    await cb.answer(f"✅ Topic '{title}' selected.")
    await _back_to_new_job(cb)


# ── Message range ──────────────────────────────────────────────────

@router.callback_query(F.data == "pick_range")
async def cb_pick_range(cb: CallbackQuery):
    await cb.message.edit_text(
        "📌 <b>Set Message Range</b>\n\n"
        "You need to provide the <b>first</b> and <b>last</b> message link.\n\n"
        "To copy a message link:\n"
        "• Long-press the message → Copy Link\n\n"
        "Example link:\n"
        "<code>https://t.me/c/1234567890/42</code>\n\n"
        "Tap below to enter the <b>first</b> message link:",
        reply_markup=msg_range_keyboard(),
        parse_mode="HTML",
    )
    await cb.answer()


@router.callback_query(F.data.in_({"input_first_link", "input_last_link"}))
async def cb_await_msg_link(cb: CallbackQuery):
    state = get_state(cb.from_user.id)
    state.step = Step.PICK_SOURCE_FIRST_MSG if cb.data == "input_first_link" else Step.PICK_SOURCE_LAST_MSG
    label = "first" if cb.data == "input_first_link" else "last"
    await cb.message.edit_text(
        f"Send the <b>{label}</b> message link now:",
        reply_markup=back_btn("pick_range"),
        parse_mode="HTML",
    )
    await cb.answer()


@router.message(
    lambda msg: get_state(msg.from_user.id).step in (
        Step.PICK_SOURCE_FIRST_MSG, Step.PICK_SOURCE_LAST_MSG
    )
)
async def handle_msg_link(msg: Message):
    state = get_state(msg.from_user.id)
    text = msg.text.strip()

    msg_id = _extract_msg_id(text)
    if msg_id is None:
        await msg.answer("❌ Couldn't parse message ID from that link. Please try again.")
        return

    if state.step == Step.PICK_SOURCE_FIRST_MSG:
        state.source_first_msg_id = msg_id
        state.step = Step.IDLE
        await msg.answer(
            f"✅ First message ID: <code>{msg_id}</code>",
            reply_markup=confirm_first_msg(),
            parse_mode="HTML",
        )
    else:
        state.source_last_msg_id = msg_id
        state.step = Step.IDLE
        await msg.answer(
            f"✅ Last message ID: <code>{msg_id}</code>\n"
            f"Range: <code>{state.source_first_msg_id}</code> → <code>{msg_id}</code>",
            parse_mode="HTML",
        )
        # Go back to new_job menu
        from bot.handlers.login import send_main_menu
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Back to Transfer Setup", callback_data="new_job")]
        ])
        await msg.answer("Range set! Continue from Transfer Setup:", reply_markup=kb)


def _extract_msg_id(link: str) -> int | None:
    """Extract message ID from a t.me link or plain integer."""
    try:
        return int(link.strip())
    except ValueError:
        pass

    # https://t.me/c/CHATID/TOPICID/MSGID or https://t.me/username/MSGID
    import re
    m = re.search(r"/(\d+)/?$", link)
    if m:
        return int(m.group(1))
    return None


# ── Scan scope ─────────────────────────────────────────────────────

@router.callback_query(F.data == "pick_scope")
async def cb_pick_scope(cb: CallbackQuery):
    await cb.message.edit_text(
        "🔍 <b>Duplicate Scan Scope</b>\n\n"
        "How broadly should the bot scan for duplicates in the destination?\n\n"
        "• <b>Topic Only</b> — compare against files in the destination topic\n"
        "• <b>Entire Group</b> — compare against all files in the destination group\n"
        "• <b>Disabled</b> — skip duplicate detection, forward everything",
        reply_markup=scan_scope_keyboard(),
        parse_mode="HTML",
    )
    await cb.answer()


@router.callback_query(F.data.startswith("scope:"))
async def cb_set_scope(cb: CallbackQuery):
    _, scope = cb.data.split(":")
    state = get_state(cb.from_user.id)
    state.scan_scope = scope
    labels = {"topic": "Destination Topic Only", "group": "Entire Group", "disabled": "Disabled"}
    await cb.answer(f"✅ Scope: {labels.get(scope, scope)}")
    await _back_to_new_job(cb)


# ── File types ─────────────────────────────────────────────────────

@router.callback_query(F.data == "pick_types")
async def cb_pick_types(cb: CallbackQuery):
    state = get_state(cb.from_user.id)
    await cb.message.edit_text(
        "📂 <b>Select file types to transfer:</b>",
        reply_markup=file_types_keyboard(state.file_types),
        parse_mode="HTML",
    )
    await cb.answer()


@router.callback_query(F.data.startswith("toggletype:"))
async def cb_toggle_type(cb: CallbackQuery):
    _, ftype = cb.data.split(":")
    state = get_state(cb.from_user.id)

    if ftype == "all":
        state.file_types = ["all"]
    else:
        if "all" in state.file_types:
            state.file_types = []
        if ftype in state.file_types:
            state.file_types.remove(ftype)
        else:
            state.file_types.append(ftype)

    await cb.message.edit_reply_markup(
        reply_markup=file_types_keyboard(state.file_types)
    )
    await cb.answer()


@router.callback_query(F.data == "types_done")
async def cb_types_done(cb: CallbackQuery):
    state = get_state(cb.from_user.id)
    if not state.file_types:
        state.file_types = ["all"]
    await cb.answer("✅ Types saved.")
    await _back_to_new_job(cb)


# ── Confirm job ────────────────────────────────────────────────────

@router.callback_query(F.data == "confirm_job")
async def cb_confirm_job(cb: CallbackQuery):
    state = get_state(cb.from_user.id)

    # Validate
    errors = []
    if not state.source_chat_id:
        errors.append("• Source chat not set")
    if not state.dest_chat_id:
        errors.append("• Destination chat not set")
    if not state.source_first_msg_id:
        errors.append("• First message not set")
    if not state.source_last_msg_id:
        errors.append("• Last message not set")

    if errors:
        await cb.answer("⚠️ Please complete the setup:\n" + "\n".join(errors),
                        show_alert=True)
        return

    src = state.source_chat_title
    if state.source_topic_title:
        src += f" → {state.source_topic_title}"

    dst = state.dest_chat_title
    if state.dest_topic_title:
        dst += f" → {state.dest_topic_title}"

    scope_labels = {"topic": "Topic only", "group": "Entire group", "disabled": "Off"}
    dry_label = "✅ YES (preview only)" if state.dry_run else "❌ NO (real transfer)"

    summary = (
        f"🚀 <b>Transfer Summary</b>\n\n"
        f"📤 <b>Source:</b> {src}\n"
        f"    Messages: {state.source_first_msg_id} → {state.source_last_msg_id}\n\n"
        f"📥 <b>Destination:</b> {dst}\n\n"
        f"🔍 <b>Duplicate scan:</b> {scope_labels.get(state.scan_scope)}\n"
        f"📂 <b>Types:</b> {', '.join(state.file_types)}\n"
        f"🧪 <b>Dry Run:</b> {dry_label}\n\n"
        f"Ready to start?"
    )

    await cb.message.edit_text(summary,
                               reply_markup=job_confirm_keyboard(state.dry_run),
                               parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "toggle_dry_run")
async def cb_toggle_dry_run(cb: CallbackQuery):
    state = get_state(cb.from_user.id)
    state.dry_run = not state.dry_run
    await cb.message.edit_reply_markup(
        reply_markup=job_confirm_keyboard(state.dry_run)
    )
    await cb.answer(f"Dry run {'ON' if state.dry_run else 'OFF'}")


# ── Helper ─────────────────────────────────────────────────────────

async def _back_to_new_job(cb: CallbackQuery):
    """Re-trigger new_job display."""
    cb.data = "new_job"
    from aiogram import Router as _R
    # We re-use the handler directly
    await cb_new_job(cb)
