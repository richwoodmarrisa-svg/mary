"""
main.py — Entry point for the Telegram Dedup Bot
"""
import asyncio
import logging
import os
from dotenv import load_dotenv

load_dotenv()

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from db.models import init_db
from bot.handlers.login import router as login_router
from bot.handlers.selection import router as selection_router
from bot.handlers.jobs import router as jobs_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    # Init DB
    await init_db()
    logger.info("Database initialized.")

    # Init bot
    bot = Bot(
        token=os.environ["BOT_TOKEN"],
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dp = Dispatcher()

    # Register routers
    dp.include_router(login_router)
    dp.include_router(selection_router)
    dp.include_router(jobs_router)

    logger.info("Bot starting...")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
