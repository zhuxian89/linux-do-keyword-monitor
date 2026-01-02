import logging
from telegram import Update
from telegram.ext import ContextTypes

from ..database import Database

logger = logging.getLogger(__name__)


class BotHandlers:
    """Telegram bot command handlers"""

    def __init__(self, db: Database):
        self.db = db

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command - register user"""
        chat_id = update.effective_chat.id
        self.db.add_user(chat_id)

        await update.message.reply_text(
            "👋 欢迎使用 Linux.do 关键词监控机器人！\n\n"
            "📝 使用方法：\n"
            "/subscribe <关键词> - 订阅关键词\n"
            "/unsubscribe <关键词> - 取消订阅\n"
            "/subscribe_all - 订阅所有新帖子\n"
            "/unsubscribe_all - 取消订阅所有\n"
            "/list - 查看我的订阅\n"
            "/help - 帮助信息\n\n"
            "当 Linux.do 有新帖子标题包含您订阅的关键词时，我会第一时间通知您！"
        )

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command"""
        await update.message.reply_text(
            "📖 帮助信息\n\n"
            "本机器人监控 Linux.do 论坛的最新帖子，"
            "当帖子标题包含您订阅的关键词时，会发送通知给您。\n\n"
            "📝 命令列表：\n"
            "/subscribe <关键词> - 订阅关键词（不区分大小写）\n"
            "/unsubscribe <关键词> - 取消订阅\n"
            "/subscribe_all - 订阅所有新帖子\n"
            "/unsubscribe_all - 取消订阅所有\n"
            "/list - 查看我的订阅列表\n"
            "/help - 显示此帮助信息\n\n"
            "💡 示例：\n"
            "/subscribe docker\n"
            "/subscribe 求助"
        )

    async def subscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /subscribe command"""
        chat_id = update.effective_chat.id

        if not context.args:
            await update.message.reply_text("❌ 请提供关键词，例如：/subscribe docker")
            return

        keyword = " ".join(context.args)

        # Ensure user exists
        self.db.add_user(chat_id)

        subscription = self.db.add_subscription(chat_id, keyword)
        if subscription:
            await update.message.reply_text(f"✅ 成功订阅关键词：{keyword}")
        else:
            await update.message.reply_text(f"⚠️ 您已经订阅了关键词：{keyword}")

    async def unsubscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /unsubscribe command"""
        chat_id = update.effective_chat.id

        if not context.args:
            await update.message.reply_text("❌ 请提供关键词，例如：/unsubscribe docker")
            return

        keyword = " ".join(context.args)

        if self.db.remove_subscription(chat_id, keyword):
            await update.message.reply_text(f"✅ 已取消订阅关键词：{keyword}")
        else:
            await update.message.reply_text(f"⚠️ 您没有订阅关键词：{keyword}")

    async def list_subscriptions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /list command"""
        chat_id = update.effective_chat.id
        subscriptions = self.db.get_user_subscriptions(chat_id)
        is_subscribe_all = self.db.is_subscribe_all(chat_id)

        lines = []
        if is_subscribe_all:
            lines.append("🌟 已订阅所有新帖子")

        if subscriptions:
            keywords = [sub.keyword for sub in subscriptions]
            keyword_list = "\n".join(f"  • {kw}" for kw in keywords)
            lines.append(f"📋 关键词订阅（共 {len(keywords)} 个）：\n{keyword_list}")

        if not lines:
            await update.message.reply_text("📭 您还没有订阅任何关键词\n\n使用 /subscribe <关键词> 开始订阅")
            return

        await update.message.reply_text("\n\n".join(lines))

    async def subscribe_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /subscribe_all command"""
        chat_id = update.effective_chat.id
        self.db.add_user(chat_id)

        if self.db.add_subscribe_all(chat_id):
            await update.message.reply_text(
                "✅ 成功订阅所有新帖子！\n\n"
                "您将收到 Linux.do 所有新帖子的通知。\n"
                "使用 /unsubscribe_all 可取消订阅。"
            )
        else:
            await update.message.reply_text("⚠️ 您已经订阅了所有新帖子")

    async def unsubscribe_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /unsubscribe_all command"""
        chat_id = update.effective_chat.id

        if self.db.remove_subscribe_all(chat_id):
            await update.message.reply_text("✅ 已取消订阅所有新帖子")
        else:
            await update.message.reply_text("⚠️ 您没有订阅所有新帖子")
