import logging
from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes

from ..cache import get_cache
from ..database import Database

logger = logging.getLogger(__name__)

# Maximum keywords per user
MAX_KEYWORDS_PER_USER = 5
# Maximum authors per user
MAX_AUTHORS_PER_USER = 5


def require_registration(func):
    """Decorator to check if user is registered before executing command"""
    @wraps(func)
    async def wrapper(self, update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        chat_id = update.effective_chat.id
        if not self.db.user_exists(chat_id):
            await update.message.reply_text(
                "👋 您还没有注册，请先发送 /start 开始使用机器人"
            )
            return
        return await func(self, update, context, *args, **kwargs)
    return wrapper


class BotHandlers:
    """Telegram bot command handlers"""

    def __init__(self, db: Database):
        self.db = db
        self.cache = get_cache()

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command - register user"""
        chat_id = update.effective_chat.id
        self.db.add_user(chat_id)
        # Clear all cache on user registration for safety
        self.cache.clear_all()

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
            "⚡ 首次使用请先发送 /start 注册\n\n"
            "本机器人监控 Linux.do 论坛的最新帖子，"
            "当帖子标题包含您订阅的关键词时，会发送通知给您。\n\n"
            "📝 关键词订阅：\n"
            "/subscribe <关键词> - 订阅关键词（不区分大小写）\n"
            "/unsubscribe <关键词> - 取消订阅\n"
            "/list - 查看我的订阅列表\n\n"
            "👤 用户订阅：\n"
            "/subscribe_user <用户名> - 订阅某用户的所有帖子\n"
            "/unsubscribe_user <用户名> - 取消订阅用户\n"
            "/list_users - 查看已订阅的用户\n\n"
            "🌟 全部订阅：\n"
            "/subscribe_all - 订阅所有新帖子\n"
            "/unsubscribe_all - 取消订阅所有\n\n"
            "📊 统计：\n"
            "/stats - 查看关键词热度统计\n\n"
            f"⚠️ 每位用户最多可订阅 {MAX_KEYWORDS_PER_USER} 个关键词和 {MAX_AUTHORS_PER_USER} 个用户\n\n"
            "💡 示例：\n"
            "/subscribe docker\n"
            "/subscribe_user neo"
        )

    @require_registration
    async def subscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /subscribe command"""
        chat_id = update.effective_chat.id

        if not context.args:
            await update.message.reply_text("❌ 请提供关键词，例如：/subscribe docker")
            return

        keyword = " ".join(context.args).strip()

        if not keyword:
            await update.message.reply_text("❌ 关键词不能为空")
            return

        # Check keyword limit
        current_subscriptions = self.db.get_user_subscriptions(chat_id)
        if len(current_subscriptions) >= MAX_KEYWORDS_PER_USER:
            await update.message.reply_text(
                f"❌ 您已达到关键词订阅上限（{MAX_KEYWORDS_PER_USER} 个）\n\n"
                "请先使用 /unsubscribe 取消一些订阅，或使用 /subscribe_all 订阅所有帖子。"
            )
            return

        subscription = self.db.add_subscription(chat_id, keyword)
        if subscription:
            # Invalidate cache
            self.cache.invalidate_keywords()
            self.cache.invalidate_subscribers(keyword)

            remaining = MAX_KEYWORDS_PER_USER - len(current_subscriptions) - 1
            await update.message.reply_text(
                f"✅ 成功订阅关键词：{keyword}\n"
                f"📊 剩余可订阅：{remaining} 个"
            )
        else:
            await update.message.reply_text(f"⚠️ 您已经订阅了关键词：{keyword}")

    @require_registration
    async def unsubscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /unsubscribe command"""
        chat_id = update.effective_chat.id

        if not context.args:
            await update.message.reply_text("❌ 请提供关键词，例如：/unsubscribe docker")
            return

        keyword = " ".join(context.args).strip()

        if not keyword:
            await update.message.reply_text("❌ 关键词不能为空")
            return

        if self.db.remove_subscription(chat_id, keyword):
            # Invalidate cache
            self.cache.invalidate_keywords()
            self.cache.invalidate_subscribers(keyword)

            await update.message.reply_text(f"✅ 已取消订阅关键词：{keyword}")
        else:
            await update.message.reply_text(f"⚠️ 您没有订阅关键词：{keyword}")

    @require_registration
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
            remaining = MAX_KEYWORDS_PER_USER - len(keywords)
            lines.append(
                f"📋 关键词订阅（{len(keywords)}/{MAX_KEYWORDS_PER_USER}）：\n{keyword_list}\n"
                f"📊 剩余可订阅：{remaining} 个"
            )

        if not lines:
            await update.message.reply_text(
                "📭 您还没有订阅任何关键词\n\n"
                f"使用 /subscribe <关键词> 开始订阅（最多 {MAX_KEYWORDS_PER_USER} 个）"
            )
            return

        await update.message.reply_text("\n\n".join(lines))

    @require_registration
    async def subscribe_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /subscribe_all command"""
        chat_id = update.effective_chat.id

        if self.db.add_subscribe_all(chat_id):
            # Invalidate cache
            self.cache.invalidate_subscribe_all()

            await update.message.reply_text(
                "✅ 成功订阅所有新帖子！\n\n"
                "您将收到 Linux.do 所有新帖子的通知。\n"
                "使用 /unsubscribe_all 可取消订阅。"
            )
        else:
            await update.message.reply_text("⚠️ 您已经订阅了所有新帖子")

    @require_registration
    async def unsubscribe_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /unsubscribe_all command"""
        chat_id = update.effective_chat.id

        if self.db.remove_subscribe_all(chat_id):
            # Invalidate cache
            self.cache.invalidate_subscribe_all()

            await update.message.reply_text("✅ 已取消订阅所有新帖子")
        else:
            await update.message.reply_text("⚠️ 您没有订阅所有新帖子")

    @require_registration
    async def subscribe_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /subscribe_user command - subscribe to a specific author"""
        chat_id = update.effective_chat.id

        if not context.args:
            await update.message.reply_text(
                "❌ 请提供用户名，例如：/subscribe_user neo\n\n"
                "💡 用户名不带 @，就是其他人可以使用 @<用户名> 来提及您\n"
                "比如 @zhuxian123 作者本人 和 @jason_wong1 就是【Wong公益站大佬】"
            )
            return

        author = " ".join(context.args).strip()

        # Remove @ prefix if provided
        if author.startswith("@"):
            author = author[1:]

        if not author:
            await update.message.reply_text(
                "❌ 用户名不能为空\n\n"
                "💡 用户名不带 @，就是其他人可以使用 @<用户名> 来提及您\n"
                "比如 @zhuxian123 作者本人 和 @jason_wong1 就是【Wong公益站大佬】"
            )
            return

        # Check author subscription limit
        current_count = self.db.get_user_subscription_count(chat_id)
        if current_count >= MAX_AUTHORS_PER_USER:
            await update.message.reply_text(
                f"❌ 您已达到用户订阅上限（{MAX_AUTHORS_PER_USER} 个）\n\n"
                "请先使用 /unsubscribe_user 取消一些订阅。"
            )
            return

        if self.db.add_user_subscription(chat_id, author):
            # Invalidate cache
            self.cache.invalidate_authors()
            self.cache.invalidate_author_subscribers(author.lower())

            remaining = MAX_AUTHORS_PER_USER - current_count - 1
            await update.message.reply_text(
                f"✅ 成功订阅用户：{author}\n"
                f"📊 剩余可订阅用户：{remaining} 个\n\n"
                f"当 {author} 发布新帖子时，您将收到通知。"
            )
        else:
            await update.message.reply_text(f"⚠️ 您已经订阅了用户：{author}")

    @require_registration
    async def unsubscribe_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /unsubscribe_user command"""
        chat_id = update.effective_chat.id

        if not context.args:
            await update.message.reply_text(
                "❌ 请提供用户名，例如：/unsubscribe_user neo\n\n"
                "💡 用户名不带 @，就是其他人可以使用 @<用户名> 来提及您\n"
                "比如 @zhuxian123 作者本人 和 @jason_wong1 就是【Wong公益站大佬】"
            )
            return

        author = " ".join(context.args).strip()

        # Remove @ prefix if provided
        if author.startswith("@"):
            author = author[1:]

        if not author:
            await update.message.reply_text("❌ 用户名不能为空")
            return

        if self.db.remove_user_subscription(chat_id, author):
            # Invalidate cache
            self.cache.invalidate_authors()
            self.cache.invalidate_author_subscribers(author.lower())

            await update.message.reply_text(f"✅ 已取消订阅用户：{author}")
        else:
            await update.message.reply_text(f"⚠️ 您没有订阅用户：{author}")

    @require_registration
    async def list_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /list_users command - list subscribed authors"""
        chat_id = update.effective_chat.id
        authors = self.db.get_user_author_subscriptions(chat_id)

        if not authors:
            await update.message.reply_text(
                "📭 您还没有订阅任何用户\n\n"
                f"使用 /subscribe_user <用户名> 开始订阅（最多 {MAX_AUTHORS_PER_USER} 个）"
            )
            return

        author_list = "\n".join(f"  • {author}" for author in authors)
        remaining = MAX_AUTHORS_PER_USER - len(authors)
        await update.message.reply_text(
            f"👤 已订阅用户（{len(authors)}/{MAX_AUTHORS_PER_USER}）：\n{author_list}\n\n"
            f"📊 剩余可订阅：{remaining} 个"
        )

    @require_registration
    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /stats command - show keyword statistics"""
        stats = self.db.get_stats()

        await update.message.reply_text(
            "📊 关键词热度统计\n\n"
            f"👥 总用户数：{stats['user_count']}\n"
            f"🔑 关键词数：{stats['keyword_count']}\n"
            f"📝 总订阅数：{stats['subscription_count']}\n"
            f"🌟 订阅全部：{stats['subscribe_all_count']}\n"
            f"📰 已处理帖子：{stats['post_count']}\n"
            f"📤 已发送通知：{stats['notification_count']}"
        )

    async def unknown_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle unknown commands"""
        await update.message.reply_text(
            "❌ 不支持的命令\n\n"
            "请输入 /help 查看支持的命令列表"
        )

    async def unknown_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle unknown text messages"""
        await update.message.reply_text(
            "❓ 无法识别的消息\n\n"
            "请输入 /help 查看支持的命令列表"
        )
