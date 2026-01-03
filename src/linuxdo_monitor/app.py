import asyncio
import logging
from pathlib import Path
from typing import List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .bot.bot import TelegramBot, MESSAGE_INTERVAL
from .config import AppConfig, ConfigManager, SourceType
from .database import Database
from .matcher.keyword import KeywordMatcher
from .models import Post
from .source import BaseSource, RSSSource, DiscourseSource
from .web import test_cookie

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Suppress noisy httpx logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)


def create_source(config: AppConfig) -> BaseSource:
    """Factory function to create data source based on config"""
    if config.source_type == SourceType.DISCOURSE:
        if not config.discourse_cookie:
            raise ValueError("Discourse source requires cookie configuration")
        return DiscourseSource(
            base_url=config.discourse_url,
            cookie=config.discourse_cookie
        )
    else:
        return RSSSource(url=config.rss_url)


class Application:
    """Main application that orchestrates all components"""

    def __init__(self, config: AppConfig, db_path: Path, config_manager: Optional[ConfigManager] = None):
        self.config = config
        self.config_manager = config_manager
        self.db_path = db_path
        self.db = Database(db_path)
        self.bot = TelegramBot(config.bot_token, self.db)
        self.source = create_source(config)
        self.matcher = KeywordMatcher()
        self.scheduler = AsyncIOScheduler()
        self._cookie_invalid_notified = False  # Track if we've notified admin about invalid cookie
        self._using_fallback = False  # Track if we're using RSS fallback

    def reload_config(self):
        """Hot reload configuration"""
        if not self.config_manager:
            logger.warning("无法热更新：ConfigManager 未设置")
            return

        new_config = self.config_manager.load()
        if not new_config:
            logger.error("热更新失败：无法加载配置")
            return

        # Update source
        self.config = new_config
        self.source = create_source(new_config)
        # Reset cookie invalid state on config reload
        self._cookie_invalid_notified = False
        self._using_fallback = False
        logger.info(f"🔄 配置已热更新，数据源: {self.source.get_source_name()}")

    async def _notify_admin(self, message: str) -> None:
        """Send notification to admin"""
        if not self.config.admin_chat_id:
            logger.warning("管理员 chat_id 未配置，无法发送告警")
            return

        try:
            await self.bot.send_admin_alert(self.config.admin_chat_id, message)
            logger.info(f"📢 已发送管理员告警")
        except Exception as e:
            logger.error(f"发送管理员告警失败: {e}")

    def _check_cookie_valid(self) -> bool:
        """Check if discourse cookie is valid"""
        if self.config.source_type != SourceType.DISCOURSE:
            return True

        if not self.config.discourse_cookie:
            return False

        result = test_cookie(self.config.discourse_cookie, self.config.discourse_url)
        return result.get("valid", False)

    def _fallback_to_rss(self) -> BaseSource:
        """Create RSS fallback source"""
        return RSSSource(url=self.config.rss_url)

    async def fetch_and_notify(self) -> None:
        """Fetch posts and send notifications"""
        try:
            # Check cookie validity for Discourse source
            source_to_use = self.source
            if self.config.source_type == SourceType.DISCOURSE:
                if not self._check_cookie_valid():
                    # Cookie invalid, fallback to RSS
                    if not self._cookie_invalid_notified:
                        logger.warning("⚠️ Cookie 已失效，降级使用 RSS 源")
                        await self._notify_admin(
                            "⚠️ Cookie 已失效\n\n"
                            "Discourse Cookie 验证失败，已自动降级为 RSS 源。\n"
                            "请尽快更新 Cookie 以恢复完整功能。\n\n"
                            "更新方式：访问配置页面更新 Cookie"
                        )
                        self._cookie_invalid_notified = True
                    source_to_use = self._fallback_to_rss()
                    self._using_fallback = True
                else:
                    # Cookie is valid again
                    if self._using_fallback:
                        logger.info("✅ Cookie 已恢复有效，切换回 Discourse 源")
                        await self._notify_admin("✅ Cookie 已恢复有效，已切换回 Discourse 源")
                        self._using_fallback = False
                        self._cookie_invalid_notified = False
                    source_to_use = self.source

            logger.info(f"📡 开始拉取数据 ({source_to_use.get_source_name()})...")
            posts = source_to_use.fetch()

            keywords = self.db.get_all_keywords()
            subscribe_all_users = self.db.get_all_subscribe_all_users()

            new_posts = []
            notifications_sent = 0

            for post in posts:
                # Skip if post already processed
                if self.db.post_exists(post.id):
                    continue

                new_posts.append(post)
                # Add post to database
                self.db.add_post(post)

                # Notify subscribe_all users
                for chat_id in subscribe_all_users:
                    if self.db.notification_exists_for_all(chat_id, post.id):
                        continue

                    success = await self.bot.send_notification_all(
                        chat_id, post.title, post.link
                    )

                    if success:
                        self.db.add_notification(chat_id, post.id, "__ALL__")
                        logger.info(f"  📤 推送给 {chat_id} (全部订阅): {post.title[:30]}...")
                        notifications_sent += 1
                        await asyncio.sleep(MESSAGE_INTERVAL)

                # Find matching keywords and notify
                if keywords:
                    matched_keywords = self.matcher.find_matching_keywords(post, keywords)

                    for keyword in matched_keywords:
                        subscribers = self.db.get_subscribers_by_keyword(keyword)

                        for chat_id in subscribers:
                            # Skip if user is subscribe_all (already notified)
                            if chat_id in subscribe_all_users:
                                continue

                            if self.db.notification_exists(chat_id, post.id, keyword):
                                continue

                            success = await self.bot.send_notification(
                                chat_id, post.title, post.link, keyword
                            )

                            if success:
                                self.db.add_notification(chat_id, post.id, keyword)
                                logger.info(f"  📤 推送给 {chat_id} (关键词:{keyword}): {post.title[:30]}...")
                                notifications_sent += 1
                                await asyncio.sleep(MESSAGE_INTERVAL)

            # Summary log
            logger.info(f"✅ 拉取完成: 共 {len(posts)} 条, 新增 {len(new_posts)} 条, 推送 {notifications_sent} 条通知")

        except Exception as e:
            logger.error(f"❌ 数据拉取失败: {e}")

    def run(self) -> None:
        """Start the application"""
        # Setup bot
        application = self.bot.setup()

        # Schedule fetching
        self.scheduler.add_job(
            self.fetch_and_notify,
            "interval",
            seconds=self.config.fetch_interval,
            id="data_fetch"
        )

        # Run initial fetch after bot starts
        async def post_init(app):
            self.scheduler.start()
            logger.info(f"⏰ 定时任务已启动, 每 {self.config.fetch_interval} 秒拉取一次")
            # Run initial fetch
            await self.fetch_and_notify()

        application.post_init = post_init

        # Start bot (blocking)
        logger.info("🤖 Telegram Bot 启动中...")
        application.run_polling()
