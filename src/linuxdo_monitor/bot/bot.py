import asyncio
import logging
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from telegram.constants import ParseMode

from ..database import Database
from .handlers import BotHandlers

logger = logging.getLogger(__name__)

# Message send interval in seconds
MESSAGE_INTERVAL = 1


class TelegramBot:
    """Telegram bot wrapper"""

    def __init__(self, token: str, db: Database):
        self.token = token
        self.db = db
        self.handlers = BotHandlers(db)
        self.application: Application = None

    def setup(self) -> Application:
        """Setup bot application with handlers"""
        self.application = Application.builder().token(self.token).build()

        # Register command handlers
        self.application.add_handler(CommandHandler("start", self.handlers.start))
        self.application.add_handler(CommandHandler("help", self.handlers.help))
        self.application.add_handler(CommandHandler("subscribe", self.handlers.subscribe))
        self.application.add_handler(CommandHandler("unsubscribe", self.handlers.unsubscribe))
        self.application.add_handler(CommandHandler("list", self.handlers.list_subscriptions))
        self.application.add_handler(CommandHandler("subscribe_all", self.handlers.subscribe_all))
        self.application.add_handler(CommandHandler("unsubscribe_all", self.handlers.unsubscribe_all))
        self.application.add_handler(CommandHandler("subscribe_user", self.handlers.subscribe_user))
        self.application.add_handler(CommandHandler("unsubscribe_user", self.handlers.unsubscribe_user))
        self.application.add_handler(CommandHandler("list_users", self.handlers.list_users))
        self.application.add_handler(CommandHandler("stats", self.handlers.stats))

        # Handle unknown commands
        self.application.add_handler(MessageHandler(filters.COMMAND, self.handlers.unknown_command))

        return self.application

    async def send_notification(self, chat_id: int, title: str, link: str, keyword: str) -> bool:
        """Send notification to a user with styled message"""
        try:
            # Format message with HTML for better styling
            message = (
                f"🔔 <b>Linux.do 新帖提醒</b>\n"
                f"━━━━━━━━━━━━━━━\n\n"
                f"📌 <b>匹配关键词</b>：<code>{keyword}</code>\n\n"
                f"📝 <b>标题</b>\n"
                f"{title}\n\n"
                f"🔗 <a href=\"{link}\">点击查看原帖 →</a>"
            )
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send notification to {chat_id}: {e}")
            return False

    async def send_notification_all(self, chat_id: int, title: str, link: str) -> bool:
        """Send notification for subscribe_all users"""
        try:
            message = (
                f"📢 <b>Linux.do 新帖</b>\n"
                f"━━━━━━━━━━━━━━━\n\n"
                f"📝 <b>标题</b>\n"
                f"{title}\n\n"
                f"🔗 <a href=\"{link}\">点击查看原帖 →</a>"
            )
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send notification to {chat_id}: {e}")
            return False

    async def send_admin_alert(self, chat_id: int, message: str) -> bool:
        """Send admin alert message"""
        try:
            alert_message = (
                f"🚨 <b>系统告警</b>\n"
                f"━━━━━━━━━━━━━━━\n\n"
                f"{message}"
            )
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=alert_message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send admin alert to {chat_id}: {e}")
            return False
