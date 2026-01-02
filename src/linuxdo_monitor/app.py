import asyncio
import logging
from pathlib import Path
from typing import List

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .bot.bot import TelegramBot, MESSAGE_INTERVAL
from .config import AppConfig
from .database import Database
from .matcher.keyword import KeywordMatcher
from .models import Post
from .rss.fetcher import HttpFetcher
from .rss.parser import RSSParser

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Suppress noisy httpx logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)


class Application:
    """Main application that orchestrates all components"""

    def __init__(self, config: AppConfig, db_path: Path):
        self.config = config
        self.db = Database(db_path)
        self.bot = TelegramBot(config.bot_token, self.db)
        self.fetcher = HttpFetcher(config.rss_url)
        self.parser = RSSParser()
        self.matcher = KeywordMatcher()
        self.scheduler = AsyncIOScheduler()

    async def fetch_and_notify(self) -> None:
        """Fetch RSS feed and send notifications for matching posts"""
        try:
            logger.info("📡 开始拉取 RSS...")
            content = self.fetcher.fetch()
            posts = self.parser.parse(content)

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
            logger.error(f"❌ RSS 拉取失败: {e}")

    def run(self) -> None:
        """Start the application"""
        # Setup bot
        application = self.bot.setup()

        # Schedule RSS fetching
        self.scheduler.add_job(
            self.fetch_and_notify,
            "interval",
            seconds=self.config.fetch_interval,
            id="rss_fetch"
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
