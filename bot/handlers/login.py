"""
bot/handlers/login.py
Handles /start, login flow (phone → code → 2FA).
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError,
    PhoneCodeExpiredError, PasswordHashInvalidError,
    FloodWaitError,
)

from bot.states import get_state, reset_state, Step
from bot.keyboards import main_menu, back_btn
from userbot.engine import create_login_client
from db.models import get_session_factory
from db.queries import get_session as db_get_session, save_session, delete_session

logger = logging.getLogger(__name__)
router = Router()


async def send_main_menu(target, user_id: int, text: str = None):
    """Send or edit main menu."""
    async with get_session_factory()() as db:
        sess = await db_get_session(db, user_id)
    logged_in = sess is not None

    text = text or (
        "👋 Welcome! You're logged in.\nChoose an option below."
        if logged_in else
        "👋 Welcome! Please log in to your Telegram account to get started."
    )
    kb = main_menu(logged_in)

    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


# ── /start ─────────────────────────────────────────────────────────

@router.message(F.text == "/start")
async def cmd_start(msg: Message):
    reset_state(msg.from_user.id)
    await send_main_menu(msg, msg.from_user.id)


# ── Login button ───────────────────────────────────────────────────

@router.callback_query(F.data == "login")
async def cb_login(cb: CallbackQuery):
    state = get_state(cb.from_user.id)
    state.step = Step.AWAIT_PHONE
    await cb.message.edit_text(
        "📱 Please send your phone number in international format:\n\n"
        "Example: <code>+33612345678</code>",
        reply_markup=back_btn("main_menu"),
        parse_mode="HTML",
    )
    await cb.answer()


# ── Phone number ───────────────────────────────────────────────────

@router.message(lambda msg: get_state(msg.from_user.id).step == Step.AWAIT_PHONE)
async def handle_phone(msg: Message):
    state = get_state(msg.from_user.id)
    phone = msg.text.strip()

    try:
        client = await create_login_client()
        state.login_client = client
        state.phone = phone

        sent = await client.send_code_request(phone)
        state.phone_code_hash = sent.phone_code_hash
        state.step = Step.AWAIT_CODE

        await msg.answer(
            "✅ Code sent!\n\n"
            "Please enter the verification code you received.\n"
            "Format: <code>12345</code> (spaces are fine too)",
            reply_markup=back_btn("login"),
            parse_mode="HTML",
        )
    except FloodWaitError as e:
        await msg.answer(f"⚠️ Too many attempts. Try again in {e.seconds} seconds.")
        state.step = Step.IDLE
    except Exception as e:
        logger.error(f"Phone send error: {e}")
        await msg.answer(f"❌ Error: {e}\n\nTry /start again.")
        state.step = Step.IDLE


# ── Verification code ──────────────────────────────────────────────

@router.message(lambda msg: get_state(msg.from_user.id).step == Step.AWAIT_CODE)
async def handle_code(msg: Message):
    state = get_state(msg.from_user.id)
    code = msg.text.strip().replace(" ", "")

    try:
        client = state.login_client
        await client.sign_in(
            phone=state.phone,
            code=code,
            phone_code_hash=state.phone_code_hash,
        )
        await _finish_login(msg, state, client)

    except SessionPasswordNeededError:
        state.step = Step.AWAIT_2FA
        await msg.answer(
            "🔐 Two-factor authentication is enabled.\n"
            "Please enter your 2FA password:",
            reply_markup=back_btn("login"),
        )

    except (PhoneCodeInvalidError, PhoneCodeExpiredError) as e:
        await msg.answer(f"❌ Invalid/expired code: {e}\n\nPlease try again.")

    except Exception as e:
        logger.error(f"Sign in error: {e}")
        await msg.answer(f"❌ Error: {e}")


# ── 2FA password ───────────────────────────────────────────────────

@router.message(lambda msg: get_state(msg.from_user.id).step == Step.AWAIT_2FA)
async def handle_2fa(msg: Message):
    state = get_state(msg.from_user.id)
    password = msg.text.strip()

    # Delete the message immediately for security
    try:
        await msg.delete()
    except Exception:
        pass

    try:
        client = state.login_client
        await client.sign_in(password=password)
        await _finish_login(msg, state, client)

    except PasswordHashInvalidError:
        await msg.answer("❌ Wrong password. Please try again:")

    except Exception as e:
        logger.error(f"2FA error: {e}")
        await msg.answer(f"❌ Error: {e}")


async def _finish_login(msg: Message, state, client):
    """Save session after successful login."""
    me = await client.get_me()
    session_string = client.session.save()

    async with get_session_factory()() as db:
        await save_session(
            db,
            telegram_id=msg.from_user.id,
            session_string=session_string,
            phone=state.phone,
            first_name=me.first_name,
        )

    state.step = Step.IDLE
    state.login_client = None

    await msg.answer(
        f"✅ Logged in as <b>{me.first_name}</b> (+{me.phone or ''})\n\n"
        "Your session is saved. You won't need to log in again.",
        parse_mode="HTML",
        reply_markup=main_menu(logged_in=True),
    )


# ── Logout ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "logout")
async def cb_logout(cb: CallbackQuery):
    user_id = cb.from_user.id
    async with get_session_factory()() as db:
        await delete_session(db, user_id)
    reset_state(user_id)
    await cb.message.edit_text(
        "👋 Logged out. Your session has been removed.",
        reply_markup=main_menu(logged_in=False),
    )
    await cb.answer()


# ── Back to main menu ──────────────────────────────────────────────

@router.callback_query(F.data == "main_menu")
async def cb_main_menu(cb: CallbackQuery):
    reset_state(cb.from_user.id)
    await send_main_menu(cb, cb.from_user.id)
    await cb.answer()


# ── Help ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "help")
async def cb_help(cb: CallbackQuery):
    await cb.message.edit_text(
        "ℹ️ <b>Telegram Dedup Bot</b>\n\n"
        "This bot lets you transfer messages between chats while "
        "automatically skipping duplicate files.\n\n"
        "<b>How to use:</b>\n"
        "1. Log in with your account\n"
        "2. Tap ➕ New Transfer\n"
        "3. Select source chat + topic\n"
        "4. Select destination chat + topic\n"
        "5. Pick message range (first & last)\n"
        "6. Choose duplicate scan scope\n"
        "7. Select file types\n"
        "8. Confirm and start!\n\n"
        "<b>Duplicate detection:</b>\n"
        "• Checks Telegram file_unique_id\n"
        "• Checks file size as fallback\n\n"
        "<b>Forwarded messages</b> keep original sender headers.",
        reply_markup=back_btn("main_menu"),
        parse_mode="HTML",
    )
    await cb.answer()
