"""
bot/handlers/jobs.py
Starts, monitors, and reports on transfer jobs.
"""
import asyncio
import logging
from datetime import datetime
from aiogram import Router, F
from aiogram.types import CallbackQuery, BufferedInputFile

from bot.states import get_state, reset_state
from bot.keyboards import running_job_keyboard, main_menu
from db.models import get_session_factory
from db.queries import (
    get_session as db_get_session, create_job, update_job,
    get_job, get_duplicates_for_job, get_job_history, get_job_stats,
)
from userbot.engine import get_client
from userbot.worker import run_transfer

logger = logging.getLogger(__name__)
router = Router()

# Active job tasks  {user_id: asyncio.Task}
_active_tasks: dict[int, asyncio.Task] = {}


@router.callback_query(F.data == "start_job")
async def cb_start_job(cb: CallbackQuery):
    user_id = cb.from_user.id
    state = get_state(user_id)

    async with get_session_factory()() as db:
        sess = await db_get_session(db, user_id)
        if not sess:
            await cb.answer("❌ Not logged in.", show_alert=True)
            return

        job = await create_job(
            db,
            user_id=user_id,
            status="running",
            source_chat_id=state.source_chat_id,
            source_chat_title=state.source_chat_title,
            source_topic_id=state.source_topic_id,
            source_topic_title=state.source_topic_title,
            source_first_msg_id=state.source_first_msg_id,
            source_last_msg_id=state.source_last_msg_id,
            dest_chat_id=state.dest_chat_id,
            dest_chat_title=state.dest_chat_title,
            dest_topic_id=state.dest_topic_id,
            dest_topic_title=state.dest_topic_title,
            scan_scope=state.scan_scope,
            file_types=",".join(state.file_types),
            dry_run=state.dry_run,
        )

    state.active_job_id = job.id
    progress_msg = await cb.message.edit_text(
        f"⏳ <b>Job #{job.id} started</b>\n\n"
        f"{'🧪 DRY RUN — ' if state.dry_run else ''}"
        f"Scanning destination for duplicates...",
        reply_markup=running_job_keyboard(job.id),
        parse_mode="HTML",
    )
    await cb.answer()

    # Launch background task
    task = asyncio.create_task(
        _run_job_task(
            user_id=user_id,
            job_id=job.id,
            session_string=sess.session_string,
            state_snapshot=state,
            bot=cb.bot,
            chat_id=cb.message.chat.id,
            progress_msg_id=progress_msg.message_id,
        )
    )
    _active_tasks[user_id] = task


async def _run_job_task(user_id, job_id, session_string,
                        state_snapshot, bot, chat_id, progress_msg_id):
    """Background coroutine that runs the transfer."""
    last_edit = [0.0]

    async def progress_cb(processed, total, moved, skipped):
        import time
        now = time.time()
        # Throttle edits to once every 3 seconds
        if now - last_edit[0] < 3 and processed != total:
            return
        last_edit[0] = now

        if total == 0:
            text = (
                f"⏳ <b>Job #{job_id}</b>\n\n"
                f"🔍 Indexing destination...\n"
                f"📤 Source: {state_snapshot.source_chat_title}\n"
                f"📥 Dest: {state_snapshot.dest_chat_title}"
            )
        else:
            pct = int((processed / total) * 100) if total else 0
            bar = _progress_bar(pct)
            text = (
                f"{'🧪 DRY RUN ' if state_snapshot.dry_run else ''}⏳ <b>Job #{job_id}</b>\n\n"
                f"{bar} {pct}%\n"
                f"📨 {processed}/{total} messages\n"
                f"✅ Moved: {moved}  ⏭ Skipped: {skipped}"
            )
        try:
            await bot.edit_message_text(
                text, chat_id=chat_id, message_id=progress_msg_id,
                reply_markup=running_job_keyboard(job_id), parse_mode="HTML"
            )
        except Exception:
            pass

    try:
        client = await get_client(user_id, session_string)
        async with get_session_factory()() as db:
            summary = await run_transfer(
                client=client,
                db_session=db,
                job_id=job_id,
                source_chat_id=state_snapshot.source_chat_id,
                source_topic_id=state_snapshot.source_topic_id,
                first_msg_id=state_snapshot.source_first_msg_id,
                last_msg_id=state_snapshot.source_last_msg_id,
                dest_chat_id=state_snapshot.dest_chat_id,
                dest_topic_id=state_snapshot.dest_topic_id,
                scan_scope=state_snapshot.scan_scope,
                file_types=",".join(state_snapshot.file_types),
                dry_run=state_snapshot.dry_run,
                progress_cb=progress_cb,
            )

        async with get_session_factory()() as db:
            await update_job(db, job_id,
                             status="done",
                             finished_at=datetime.utcnow(),
                             moved=summary["moved"],
                             skipped_duplicates=summary["skipped"],
                             errors=summary["errors"])

        await _send_report(bot, chat_id, job_id, summary,
                           state_snapshot.dry_run, user_id)

    except asyncio.CancelledError:
        async with get_session_factory()() as db:
            await update_job(db, job_id, status="stopped")
        await bot.edit_message_text(
            f"⛔ Job #{job_id} was stopped.",
            chat_id=chat_id, message_id=progress_msg_id,
        )

    except Exception as e:
        logger.exception(f"Job {job_id} error: {e}")
        async with get_session_factory()() as db:
            await update_job(db, job_id, status="error")
        try:
            await bot.edit_message_text(
                f"❌ Job #{job_id} failed: {e}",
                chat_id=chat_id, message_id=progress_msg_id,
            )
        except Exception:
            pass
    finally:
        _active_tasks.pop(user_id, None)


async def _send_report(bot, chat_id, job_id, summary, dry_run, user_id):
    """Send final report with optional duplicate file."""
    mode = "🧪 DRY RUN " if dry_run else ""
    report_text = (
        f"{mode}✅ <b>Job #{job_id} Complete!</b>\n\n"
        f"{'📨 Would move' if dry_run else '✅ Moved'}: <b>{summary['moved']}</b>\n"
        f"⏭ Skipped (duplicates): <b>{summary['skipped']}</b>\n"
        f"❌ Errors: <b>{summary['errors']}</b>"
    )

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu")]
    ])

    await bot.send_message(chat_id, report_text, parse_mode="HTML", reply_markup=kb)

    # Send duplicate links as file if any
    if summary["duplicate_links"]:
        lines = [f"Job #{job_id} — Duplicate files skipped\n",
                 f"Total: {len(summary['duplicate_links'])}\n\n"]
        for link, reason in summary["duplicate_links"]:
            lines.append(f"{link}  [{reason}]\n")

        content = "".join(lines).encode("utf-8")
        await bot.send_document(
            chat_id,
            BufferedInputFile(content, filename=f"duplicates_job{job_id}.txt"),
            caption=f"📄 {len(summary['duplicate_links'])} duplicate links from job #{job_id}",
        )


@router.callback_query(F.data.startswith("stopjob:"))
async def cb_stop_job(cb: CallbackQuery):
    _, job_id_str = cb.data.split(":")
    user_id = cb.from_user.id

    task = _active_tasks.get(user_id)
    if task and not task.done():
        task.cancel()
        await cb.answer("⛔ Stopping job...")
    else:
        await cb.answer("No active job to stop.", show_alert=True)


@router.callback_query(F.data == "my_jobs")
async def cb_my_jobs(cb: CallbackQuery):
    """Show last 10 jobs with aggregate stats."""
    async with get_session_factory()() as db:
        jobs = await get_job_history(db, cb.from_user.id, limit=10)
        stats = await get_job_stats(db, cb.from_user.id)

    if not jobs:
        await cb.message.edit_text(
            "📂 No transfer jobs yet.\n\nStart a new transfer with ➕ New Transfer.",
            reply_markup=main_menu(logged_in=True),
        )
        await cb.answer()
        return

    status_icons = {
        "done": "✅", "running": "⏳", "error": "❌",
        "stopped": "⛔", "pending": "⏸",
    }

    lines = [
        "📂 <b>Recent Jobs</b>\n",
        f"📊 All-time: {stats['total_jobs']} jobs · "
        f"{stats['total_moved']} moved · "
        f"{stats['total_skipped']} skipped\n",
    ]

    for j in jobs:
        icon = status_icons.get(j.status, "❓")
        src = j.source_chat_title or "?"
        if j.source_topic_title:
            src += f" → {j.source_topic_title}"
        dst = j.dest_chat_title or "?"
        if j.dest_topic_title:
            dst += f" → {j.dest_topic_title}"
        dry = " 🧪" if j.dry_run else ""
        lines.append(
            f"{icon} <b>#{j.id}</b>{dry} {src} → {dst}\n"
            f"   ✅ {j.moved}  ⏭ {j.skipped_duplicates}  ❌ {j.errors}\n"
        )

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Back", callback_data="main_menu")]
    ])
    await cb.message.edit_text(
        "\n".join(lines), reply_markup=kb, parse_mode="HTML"
    )
    await cb.answer()


def _progress_bar(pct: int, width: int = 10) -> str:
    filled = int(width * pct / 100)
    return "█" * filled + "░" * (width - filled)
