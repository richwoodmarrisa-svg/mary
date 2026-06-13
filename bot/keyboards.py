"""
bot/keyboards.py
Aiogram inline keyboard helpers.
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def _kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


# ── Main menu ──────────────────────────────────────────────────────

def main_menu(logged_in: bool = False) -> InlineKeyboardMarkup:
    if not logged_in:
        return _kb(
            [_btn("🔑 Login Account", "login")],
            [_btn("ℹ️ Help", "help")],
        )
    return _kb(
        [_btn("➕ New Transfer", "new_job")],
        [_btn("📂 My Jobs", "my_jobs")],
        [_btn("🚪 Logout", "logout")],
    )


def back_btn(target: str = "main_menu") -> InlineKeyboardMarkup:
    return _kb([_btn("⬅️ Back", target)])


# ── Chat list ──────────────────────────────────────────────────────

def chat_category_menu(mode: str = "source") -> InlineKeyboardMarkup:
    """Choose category of chat to browse."""
    back_target = "main_menu" if mode == "source" else "new_job"
    return _kb(
        [_btn("👥 Groups", f"cats:groups:{mode}"),
         _btn("📢 Channels", f"cats:channels:{mode}")],
        [_btn("💬 Private", f"cats:private:{mode}")],
        [_btn("⬅️ Back", back_target)],
    )


def chat_list_keyboard(chats: list[dict], category: str,
                       mode: str, offset: int = 0) -> InlineKeyboardMarkup:
    """Paginated chat list. mode = 'source' or 'dest'."""
    PAGE = 8
    page = chats[offset: offset + PAGE]
    rows = []
    for c in page:
        icon = "📋" if c.get("has_topics") else "💬"
        rows.append([_btn(f"{icon} {c['title'][:40]}",
                          f"setchat:{mode}:{c['id']}:{c['title'][:30]}")]):

    nav = []
    if offset > 0:
        nav.append(_btn("◀️ Prev", f"chatpage:{category}:{mode}:{offset - PAGE}"))
    if offset + PAGE < len(chats):
        nav.append(_btn("▶️ Next", f"chatpage:{category}:{mode}:{offset + PAGE}"))
    if nav:
        rows.append(nav)
    
    back_target = f"cats:{category}:{mode}"
    rows.append([_btn("⬅️ Back", back_target)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def topic_list_keyboard(topics: list[dict], mode: str) -> InlineKeyboardMarkup:
    rows = []
    for t in topics:
        rows.append([_btn(f"📌 {t['title'][:40]}",
                          f"settopic:{mode}:{t['id']}:{t['title'][:30]}")]):
    rows.append([_btn("⛔ No Topic (General)", f"settopic:{mode}:0:General")])
    rows.append([_btn("⬅️ Back", "new_job")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Message range ──────────────────────────────────────────────────

def msg_range_keyboard() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("📎 Paste Message Link", "input_first_link")],
        [_btn("⬅️ Back", "new_job")],
    )


def confirm_first_msg() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("✅ Now set LAST message", "input_last_link")],
        [_btn("🔄 Re-enter first", "input_first_link")],
    )


# ── Scan scope ─────────────────────────────────────────────────────

def scan_scope_keyboard() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🎯 Destination Topic Only", "scope:topic")],
        [_btn("🗂 Entire Destination Group", "scope:group")],
        [_btn("⏭ Disabled (forward all)", "scope:disabled")],
        [_btn("⬅️ Back", "new_job")],
    )


# ── File types ─────────────────────────────────────────────────────

def file_types_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    all_types = [
        ("📷 Photos", "photo"),
        ("🎬 Videos", "video"),
        ("📄 Documents", "document"),
        ("🎵 Audio", "audio"),
        ("🎤 Voice", "voice"),
        ("🎭 Stickers", "sticker"),
        ("💬 Text Messages", "text"),
    ]
    rows = []
    for label, key in all_types:
        if "all" in selected:
            check = "☑️"
        else:
            check = "✅" if key in selected else "⬜"
        rows.append([_btn(f"{check} {label}", f"toggletype:{key}")])

    rows.append([_btn(
        "☑️ Everything (All)" if "all" not in selected else "✅ Everything (All)",
        "toggletype:all"
    )])
    rows.append([_btn("➡️ Continue", "types_done")])
    rows.append([_btn("⬅️ Back", "new_job")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Job confirmation ───────────────────────────────────────────────

def job_confirm_keyboard(dry_run: bool) -> InlineKeyboardMarkup:
    dry_label = "✅ Dry Run ON" if dry_run else "⬜ Dry Run OFF"
    return _kb(
        [_btn("🚀 Start Transfer", "start_job")],
        [_btn(dry_label, "toggle_dry_run")],
        [_btn("⬅️ Cancel", "main_menu")],
    )


# ── Running job ────────────────────────────────────────────────────

def running_job_keyboard(job_id: int) -> InlineKeyboardMarkup:
    return _kb([_btn("⛔ Stop Job", f"stopjob:{job_id}")])
