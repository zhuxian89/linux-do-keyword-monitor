import logging
from functools import wraps
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from ..cache import get_cache
from ..database import Database
from ..matcher.keyword import is_regex_pattern, validate_regex

logger = logging.getLogger(__name__)

# Maximum keywords per user
MAX_KEYWORDS_PER_USER = 5
# Maximum authors per user
MAX_AUTHORS_PER_USER = 5
# Maximum keyword length (callback_data limit is 64 bytes, prefix "del_kw:" is 7 bytes)
MAX_KEYWORD_LENGTH = 50

# 推荐关键词（用于快捷订阅）
RECOMMENDED_KEYWORDS = ["claude", "ai", "kiro", "gemini", "公益"]
# 推荐用户（用于快捷订阅）
RECOMMENDED_USERS = ["zhuxian123", "jason_wong1", "bytebender", "henryxiaoyang"]


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
        # 用户回来了，清除封禁标记
        self.db.unmark_user_blocked(chat_id)
        # Clear all cache on user registration for safety
        self.cache.clear_all()

        # 快捷订阅按钮
        keyboard = [
            [InlineKeyboardButton(kw, callback_data=f"quick_kw:{kw}") for kw in RECOMMENDED_KEYWORDS[:3]],
            [InlineKeyboardButton(kw, callback_data=f"quick_kw:{kw}") for kw in RECOMMENDED_KEYWORDS[3:]],
            [InlineKeyboardButton(f"@{u}", callback_data=f"quick_user:{u}") for u in RECOMMENDED_USERS[:2]],
            [InlineKeyboardButton(f"@{u}", callback_data=f"quick_user:{u}") for u in RECOMMENDED_USERS[2:]]
        ]

        await update.message.reply_text(
            "👋 欢迎使用 Linux.do 关键词监控机器人！\n\n"
            "📝 使用方法：\n"
            "/subscribe <关键词> - 订阅关键词\n"
            "/list - 查看我的关键词订阅\n"
            "/subscribe_user <用户名> - 订阅用户\n"
            "/list_users - 查看已订阅的用户\n"
            "/subscribe_all - 订阅所有新帖子\n"
            "/unsubscribe_all - 取消订阅所有\n"
            "/help - 帮助信息\n\n"
            "⚡ 快捷订阅热门关键词：\n"
            "👤 快捷订阅热门用户：",
            reply_markup=InlineKeyboardMarkup(keyboard)
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
            "/list - 查看我的关键词订阅\n\n"
            "🔤 正则表达式：\n"
            "支持正则匹配，例如：\n"
            "• \\bopenai\\b - 精确匹配 openai 单词\n"
            "• gpt-?4 - 匹配 gpt4 或 gpt-4\n"
            "• (免费|白嫖) - 匹配 免费 或 白嫖\n"
            "💡 可用 AI 工具帮你生成正则\n\n"
            "👤 用户订阅：\n"
            "/subscribe_user <用户名> - 订阅某用户的所有帖子\n"
            "/list_users - 查看已订阅的用户\n\n"
            "🌟 全部订阅：\n"
            "/subscribe_all - 订阅所有新帖子\n"
            "/unsubscribe_all - 取消订阅所有\n\n"
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

        # 检查关键词长度
        if len(keyword.encode('utf-8')) > MAX_KEYWORD_LENGTH:
            await update.message.reply_text(
                f"❌ 关键词过长，最多支持 {MAX_KEYWORD_LENGTH} 字节\n\n"
                "💡 建议使用更简短的关键词或正则表达式"
            )
            return

        # 检查是否是正则表达式，如果是则验证
        if is_regex_pattern(keyword):
            is_valid, error_msg = validate_regex(keyword)
            if not is_valid:
                await update.message.reply_text(
                    f"❌ 正则表达式无效：{error_msg}\n\n"
                    "💡 提示：可以使用 AI 工具帮你生成正则表达式"
                )
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

            # 提示用户是否使用了正则
            pattern_hint = "（正则模式）" if is_regex_pattern(keyword) else ""
            await update.message.reply_text(f"✅ 成功订阅关键词{pattern_hint}：{keyword}")
            # 自动展示订阅列表
            text, keyboard = self._build_keyword_list_message(chat_id)
            await update.message.reply_text(text, reply_markup=keyboard)
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

    def _build_keyword_list_message(self, chat_id: int) -> tuple[str, Optional[InlineKeyboardMarkup]]:
        """Build keyword list message with inline keyboard"""
        subscriptions = self.db.get_user_subscriptions(chat_id)
        is_subscribe_all = self.db.is_subscribe_all(chat_id)

        lines = []
        if is_subscribe_all:
            lines.append("🌟 已订阅所有新帖子")

        if subscriptions:
            keywords = [sub.keyword for sub in subscriptions]
            remaining = MAX_KEYWORDS_PER_USER - len(keywords)
            lines.append(f"📋 关键词订阅（{len(keywords)}/{MAX_KEYWORDS_PER_USER}）：")

            # Build inline keyboard with delete buttons
            keyboard = []
            for kw in keywords:
                display = kw if len(kw) <= 20 else kw[:17] + "..."
                keyboard.append([
                    InlineKeyboardButton(f"• {display}", callback_data="noop"),
                    InlineKeyboardButton("❌", callback_data=f"del_kw:{kw}")
                ])

            lines.append(f"📊 剩余可订阅：{remaining} 个")
            return "\n".join(lines), InlineKeyboardMarkup(keyboard)

        if not lines:
            # 空状态引导：显示推荐关键词按钮
            keyboard = [
                [InlineKeyboardButton(kw, callback_data=f"quick_kw:{kw}") for kw in RECOMMENDED_KEYWORDS[:3]],
                [InlineKeyboardButton(kw, callback_data=f"quick_kw:{kw}") for kw in RECOMMENDED_KEYWORDS[3:]]
            ]
            return (
                "📭 您还没有订阅任何关键词\n\n"
                "⚡ 点击下方按钮快速订阅："
            ), InlineKeyboardMarkup(keyboard)

        return "\n".join(lines), None

    @require_registration
    async def list_subscriptions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /list command"""
        chat_id = update.effective_chat.id
        text, keyboard = self._build_keyword_list_message(chat_id)
        await update.message.reply_text(text, reply_markup=keyboard)

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

            await update.message.reply_text(f"✅ 成功订阅用户：{author}")
            # 自动展示用户订阅列表
            text, keyboard = self._build_user_list_message(chat_id)
            await update.message.reply_text(text, reply_markup=keyboard)
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

    def _build_user_list_message(self, chat_id: int) -> tuple[str, Optional[InlineKeyboardMarkup]]:
        """Build user list message with inline keyboard"""
        authors = self.db.get_user_author_subscriptions(chat_id)

        if not authors:
            return (
                "📭 您还没有订阅任何用户\n\n"
                f"使用 /subscribe_user <用户名> 开始订阅（最多 {MAX_AUTHORS_PER_USER} 个）"
            ), None

        remaining = MAX_AUTHORS_PER_USER - len(authors)
        text = f"👤 已订阅用户（{len(authors)}/{MAX_AUTHORS_PER_USER}）：\n📊 剩余可订阅：{remaining} 个"

        keyboard = []
        for author in authors:
            display = author if len(author) <= 20 else author[:17] + "..."
            keyboard.append([
                InlineKeyboardButton(f"• {display}", callback_data="noop"),
                InlineKeyboardButton("❌", callback_data=f"del_user:{author}")
            ])

        return text, InlineKeyboardMarkup(keyboard)

    @require_registration
    async def list_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /list_users command - list subscribed authors"""
        chat_id = update.effective_chat.id
        text, keyboard = self._build_user_list_message(chat_id)
        await update.message.reply_text(text, reply_markup=keyboard)

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
            f"📤 已发送通知：{stats['notification_count']}\n"
            f"🚫 已封禁Bot：{stats['blocked_count']}"
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

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline keyboard button callbacks"""
        query = update.callback_query
        await query.answer()

        if query.data == "noop":
            return

        chat_id = query.message.chat_id

        # 删除关键词确认
        if query.data.startswith("del_kw:"):
            keyword = query.data[7:]
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ 确认删除", callback_data=f"confirm_kw:{keyword}"),
                    InlineKeyboardButton("❌ 取消", callback_data="cancel_kw")
                ]
            ])
            display = keyword if len(keyword) <= 20 else keyword[:17] + "..."
            await query.edit_message_text(f"确认删除关键词「{display}」？", reply_markup=keyboard)

        elif query.data.startswith("confirm_kw:"):
            keyword = query.data[11:]
            if self.db.remove_subscription(chat_id, keyword):
                self.cache.invalidate_keywords()
                self.cache.invalidate_subscribers(keyword)
            text, keyboard = self._build_keyword_list_message(chat_id)
            await query.edit_message_text(text, reply_markup=keyboard)

        elif query.data == "cancel_kw":
            text, keyboard = self._build_keyword_list_message(chat_id)
            await query.edit_message_text(text, reply_markup=keyboard)

        # 删除用户确认
        elif query.data.startswith("del_user:"):
            author = query.data[9:]
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ 确认删除", callback_data=f"confirm_user:{author}"),
                    InlineKeyboardButton("❌ 取消", callback_data="cancel_user")
                ]
            ])
            await query.edit_message_text(f"确认删除用户「{author}」？", reply_markup=keyboard)

        elif query.data.startswith("confirm_user:"):
            author = query.data[13:]
            if self.db.remove_user_subscription(chat_id, author):
                self.cache.invalidate_authors()
                self.cache.invalidate_author_subscribers(author.lower())
            text, keyboard = self._build_user_list_message(chat_id)
            await query.edit_message_text(text, reply_markup=keyboard)

        elif query.data == "cancel_user":
            text, keyboard = self._build_user_list_message(chat_id)
            await query.edit_message_text(text, reply_markup=keyboard)

        # 快捷订阅关键词
        elif query.data.startswith("quick_kw:"):
            keyword = query.data[9:]
            # 检查数量限制
            current_count = len(self.db.get_user_subscriptions(chat_id))
            if current_count >= MAX_KEYWORDS_PER_USER:
                await query.answer(f"已达上限 {MAX_KEYWORDS_PER_USER} 个，请先删除", show_alert=True)
                return
            if self.db.add_subscription(chat_id, keyword):
                self.cache.invalidate_keywords()
                self.cache.invalidate_subscribers(keyword)
            text, keyboard = self._build_keyword_list_message(chat_id)
            await query.edit_message_text(text, reply_markup=keyboard)

        # 快捷订阅用户
        elif query.data.startswith("quick_user:"):
            author = query.data[11:]
            # 检查数量限制
            current_count = self.db.get_user_subscription_count(chat_id)
            if current_count >= MAX_AUTHORS_PER_USER:
                await query.answer(f"已达上限 {MAX_AUTHORS_PER_USER} 个，请先删除", show_alert=True)
                return
            if self.db.add_user_subscription(chat_id, author):
                self.cache.invalidate_authors()
                self.cache.invalidate_author_subscribers(author.lower())
            text, keyboard = self._build_user_list_message(chat_id)
            await query.edit_message_text(text, reply_markup=keyboard)
