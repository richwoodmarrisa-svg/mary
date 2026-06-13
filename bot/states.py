"""
bot/states.py
In-memory wizard state for each user (aiogram FSM-style but manual).
"""
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum, auto


class Step(Enum):
    IDLE = auto()

    # Login flow
    AWAIT_PHONE = auto()
    AWAIT_CODE = auto()
    AWAIT_2FA = auto()

    # Selection flow
    PICK_SOURCE_CHAT = auto()
    PICK_SOURCE_TOPIC = auto()
    PICK_SOURCE_FIRST_MSG = auto()
    PICK_SOURCE_LAST_MSG = auto()

    PICK_DEST_CHAT = auto()
    PICK_DEST_TOPIC = auto()

    PICK_SCAN_SCOPE = auto()
    PICK_FILE_TYPES = auto()
    CONFIRM_JOB = auto()


@dataclass
class UserState:
    step: Step = Step.IDLE

    # Login temp data
    phone: Optional[str] = None
    phone_code_hash: Optional[str] = None
    login_client = None   # temporary Telethon client during login

    # Job wizard data
    source_chat_id: Optional[int] = None
    source_chat_title: Optional[str] = None
    source_topic_id: Optional[int] = None
    source_topic_title: Optional[str] = None
    source_first_msg_id: Optional[int] = None
    source_last_msg_id: Optional[int] = None

    dest_chat_id: Optional[int] = None
    dest_chat_title: Optional[str] = None
    dest_topic_id: Optional[int] = None
    dest_topic_title: Optional[str] = None

    scan_scope: str = "topic"
    file_types: list = field(default_factory=lambda: ["all"])
    dry_run: bool = False

    # Pagination for chat lists
    page_offset: int = 0

    # Current running job id
    active_job_id: Optional[int] = None


# Global store  {telegram_id: UserState}
_states: dict[int, UserState] = {}


def get_state(user_id: int) -> UserState:
    if user_id not in _states:
        _states[user_id] = UserState()
    return _states[user_id]


def reset_state(user_id: int):
    _states[user_id] = UserState()
