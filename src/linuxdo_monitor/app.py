import asyncio
import logging
import logging.handlers
from pathlib import Path
from typing import List, Optional, Set, Tuple

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .bot.bot import TelegramBot
from .cache import get_cache, AppCache
from .config import AppConfig, ConfigManager, SourceType
from .database import Database
from .matcher.keyword import KeywordMatcher
from .models import Post
from .source import BaseSource, RSSSource, DiscourseSource
from .web import test_cookie


def setup_logging(log_dir: Optional[Path] = None) -> None:
    """配置日志系统

    - 输出到 stdout（供 journald 收集）
    - 输出到文件（按天轮转，保留30天）
    """
    log_format = "%(asctime)s - %(levelname)s - %(message)s"
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # 清除已有的 handlers（避免重复添加）
    root_logger.handlers.clear()

    # Handler 1: stdout（供 systemd/journald）
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter(log_format))
    root_logger.addHandler(stream_handler)

    # Handler 2: 文件（按天轮转）
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "app.log"
        file_handler = logging.handlers.TimedRotatingFileHandler(
            filename=log_file,
            when="midnight",      # 每天午夜轮转
            interval=1,
            backupCount=30,       # 保留30天
            encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(log_format))
        file_handler.suffix = "%Y-%m-%d"  # 备份文件后缀格式
        root_logger.addHandler(file_handler)


# 默认初始化（仅 stdout，文件日志在 main 中配置）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Suppress noisy httpx logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

# Batch sending configuration
BATCH_SIZE = 25  # Number of messages to send concurrently
BATCH_INTERVAL = 1.0  # Seconds between batches (Telegram rate limit ~30/sec)


def create_source(config: AppConfig) -> BaseSource:
    """Factory function to create data source based on config"""
    if config.source_type == SourceType.DISCOURSE:
        if not config.discourse_cookie:
            raise ValueError("Discourse source requires cookie configuration")
        return DiscourseSource(
            base_url=config.discourse_url,
            cookie=config.discourse_cookie,
            flaresolverr_url=config.flaresolverr_url
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
        self.cache = get_cache()
        self._cookie_fail_count = 0  # 连续失败计数器
        self._cookie_fail_threshold = 5  # 连续失败阈值
        self._cookie_notify_round = 0  # 第几轮通知
        self._fetch_fail_count = 0  # 拉取失败计数器
        self._fetch_fail_threshold = 5  # 拉取连续失败阈值
        self._fetch_fail_notified = False  # 是否已发送拉取失败告警

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
        self._cookie_fail_count = 0
        self._cookie_notify_round = 0
        # Invalidate cache on config change
        self.cache.clear_all()
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

    def _check_cookie_valid(self) -> dict:
        """Check if discourse cookie is valid

        Returns:
            dict with keys:
            - valid: bool
            - error_type: "cookie_invalid" | "service_error" | None
            - error: str | None
        """
        if self.config.source_type != SourceType.DISCOURSE:
            return {"valid": True, "error_type": None, "error": None}

        if not self.config.discourse_cookie:
            return {"valid": False, "error_type": "cookie_invalid", "error": "Cookie 未配置"}

        result = test_cookie(self.config.discourse_cookie, self.config.discourse_url, self.config.flaresolverr_url)
        return result

    def _fallback_to_rss(self) -> BaseSource:
        """Create RSS fallback source (deprecated, kept for compatibility)"""
        return RSSSource(url=self.config.rss_url)

    async def _check_cookie_task(self) -> None:
        """独立的 Cookie 检测任务"""
        if self.config.source_type != SourceType.DISCOURSE:
            return

        # 连续测试 3 次
        fail_count = 0
        last_result = None
        for i in range(3):
            result = self._check_cookie_valid()
            last_result = result
            if not result.get("valid", False):
                fail_count += 1
                if i < 2:  # 前两次失败后等待 2 秒再试
                    await asyncio.sleep(2)
            else:
                break

        if fail_count == 3:
            error_type = last_result.get("error_type", "unknown") if last_result else "unknown"
            error_msg = last_result.get("error", "未知错误") if last_result else "未知错误"

            # 服务错误（FlareSolverr 超时等）只记录日志，不发告警
            # 因为 fetch_and_notify 已经有告警逻辑了
            if error_type == "service_error":
                logger.warning(f"⚠️ Cookie 检测失败（服务错误）: {error_msg}")
                return

            # Cookie 真正失效才发告警
            self._cookie_fail_count += 1
            logger.warning(f"⚠️ Cookie 连续 3 次检测失败（第 {self._cookie_fail_count} 轮）: {error_msg}")
            for i in range(1, 4):
                await self._notify_admin(
                    f"⚠️ Cookie 可能已失效（第 {self._cookie_fail_count} 轮通知，第 {i}/3 遍）\n\n"
                    f"Discourse Cookie 连续 3 次验证失败。\n"
                    f"错误信息: {error_msg}\n\n"
                    f"当前仍可拉取公开数据，但部分限制内容可能无法获取。\n\n"
                    f"{'❗' * i} 请检查 Cookie 是否需要更新 {'❗' * i}\n\n"
                    f"更新方式：访问配置页面更新 Cookie"
                )
        else:
            # 检测通过
            if self._cookie_fail_count > 0:
                logger.info(f"✅ Cookie 检测恢复正常（之前失败 {self._cookie_fail_count} 轮）")
                await self._notify_admin("✅ Cookie 已恢复有效，之前的告警可以忽略了")
                self._cookie_fail_count = 0

    def _get_keywords_cached(self) -> List[str]:
        """Get keywords with caching"""
        cached = self.cache.get_keywords()
        if cached is not None:
            return cached
        keywords = self.db.get_all_keywords()
        self.cache.set_keywords(keywords)
        return keywords

    def _get_subscribe_all_users_cached(self) -> List[int]:
        """Get subscribe_all users with caching"""
        cached = self.cache.get_subscribe_all_users()
        if cached is not None:
            return cached
        users = self.db.get_all_subscribe_all_users()
        self.cache.set_subscribe_all_users(users)
        return users

    def _get_subscribers_cached(self, keyword: str) -> List[int]:
        """Get subscribers for a keyword with caching"""
        cached = self.cache.get_subscribers(keyword)
        if cached is not None:
            return cached
        subscribers = self.db.get_subscribers_by_keyword(keyword)
        self.cache.set_subscribers(keyword, subscribers)
        return subscribers

    def _get_subscribed_authors_cached(self) -> List[str]:
        """Get subscribed authors with caching"""
        cached = self.cache.get_authors()
        if cached is not None:
            return cached
        authors = self.db.get_all_subscribed_authors()
        self.cache.set_authors(authors)
        return authors

    def _get_author_subscribers_cached(self, author: str) -> List[int]:
        """Get subscribers for an author with caching"""
        cached = self.cache.get_author_subscribers(author)
        if cached is not None:
            return cached
        subscribers = self.db.get_subscribers_by_author(author)
        self.cache.set_author_subscribers(author, subscribers)
        return subscribers

    async def _send_batch(self, tasks: List[Tuple]) -> int:
        """Send a batch of notifications concurrently.

        Args:
            tasks: List of (chat_id, post, keyword_or_none) tuples

        Returns:
            Number of successfully sent notifications
        """
        if not tasks:
            return 0

        async def send_one(chat_id: int, post: Post, keyword: Optional[str]) -> bool:
            try:
                if keyword:
                    success = await self.bot.send_notification(
                        chat_id, post.title, post.link, keyword
                    )
                else:
                    success = await self.bot.send_notification_all(
                        chat_id, post.title, post.link
                    )
                if success:
                    # Record notification in DB
                    self.db.add_notification(chat_id, post.id, keyword or "__ALL__")
                return success
            except Exception as e:
                logger.error(f"发送失败 {chat_id}: {e}")
                return False

        # Execute batch concurrently
        results = await asyncio.gather(
            *[send_one(chat_id, post, keyword) for chat_id, post, keyword in tasks],
            return_exceptions=True
        )

        success_count = sum(1 for r in results if r is True)
        return success_count

    async def fetch_and_notify(self) -> None:
        """Fetch posts and send notifications"""
        try:
            # Always use the configured source (no fallback to RSS)
            logger.info(f"📡 开始拉取数据 ({self.source.get_source_name()})...")
            posts = self.source.fetch()

            # Use cached data
            keywords = self._get_keywords_cached()
            subscribe_all_users = self._get_subscribe_all_users_cached()
            subscribe_all_set: Set[int] = set(subscribe_all_users)
            subscribed_authors = self._get_subscribed_authors_cached()

            new_posts = []
            pending_tasks: List[Tuple] = []  # (chat_id, post, keyword_or_none)

            for post in posts:
                # Skip if post already processed
                if self.db.post_exists(post.id):
                    continue

                new_posts.append(post)
                self.db.add_post(post)

                # Track users already notified for this post (in this cycle)
                notified_users: Set[int] = set()

                # Collect subscribe_all notifications
                for chat_id in subscribe_all_users:
                    # Check DB for existing notification
                    if self.db.notification_exists_for_post(chat_id, post.id):
                        notified_users.add(chat_id)
                        continue
                    pending_tasks.append((chat_id, post, None))
                    notified_users.add(chat_id)

                # Collect author-based notifications
                if post.author and subscribed_authors:
                    author_lower = post.author.lower()
                    if author_lower in [a.lower() for a in subscribed_authors]:
                        subscribers = self._get_author_subscribers_cached(author_lower)
                        for chat_id in subscribers:
                            # Skip if already notified
                            if chat_id in notified_users:
                                continue
                            if chat_id in subscribe_all_set:
                                continue
                            if self.db.notification_exists_for_post(chat_id, post.id):
                                notified_users.add(chat_id)
                                continue
                            # Use special keyword format for author subscription
                            pending_tasks.append((chat_id, post, f"@{post.author}"))
                            notified_users.add(chat_id)

                # Collect keyword-based notifications
                if keywords:
                    matched_keywords = self.matcher.find_matching_keywords(post, keywords)

                    for keyword in matched_keywords:
                        subscribers = self._get_subscribers_cached(keyword)

                        for chat_id in subscribers:
                            # Skip if already notified (subscribe_all or another keyword)
                            if chat_id in notified_users:
                                continue
                            # Skip if already in subscribe_all
                            if chat_id in subscribe_all_set:
                                continue
                            # Check DB for existing notification for this post
                            if self.db.notification_exists_for_post(chat_id, post.id):
                                notified_users.add(chat_id)
                                continue

                            pending_tasks.append((chat_id, post, keyword))
                            notified_users.add(chat_id)

            # Send notifications in batches
            total_sent = 0
            for i in range(0, len(pending_tasks), BATCH_SIZE):
                batch = pending_tasks[i:i + BATCH_SIZE]
                sent = await self._send_batch(batch)
                total_sent += sent

                if sent > 0:
                    logger.info(f"  📤 批量发送 {sent}/{len(batch)} 条")

                # Rate limit between batches
                if i + BATCH_SIZE < len(pending_tasks):
                    await asyncio.sleep(BATCH_INTERVAL)

            # Summary log
            logger.info(f"✅ 拉取完成: 共 {len(posts)} 条, 新增 {len(new_posts)} 条, 推送 {total_sent} 条通知")

            # 拉取成功，重置失败计数
            if self._fetch_fail_count > 0:
                logger.info(f"✅ 数据拉取恢复正常（之前连续失败 {self._fetch_fail_count} 次）")
                if self._fetch_fail_notified:
                    await self._notify_admin("✅ 数据拉取已恢复正常，之前的告警可以忽略了")
                self._fetch_fail_count = 0
                self._fetch_fail_notified = False

        except Exception as e:
            self._fetch_fail_count += 1
            logger.error(f"❌ 数据拉取失败 (第 {self._fetch_fail_count} 次): {e}")

            # 连续失败达到阈值时发送告警
            if self._fetch_fail_count >= self._fetch_fail_threshold and not self._fetch_fail_notified:
                self._fetch_fail_notified = True
                await self._notify_admin(
                    f"⚠️ 数据拉取连续失败 {self._fetch_fail_count} 次\n\n"
                    f"错误信息: {e}\n\n"
                    f"请检查:\n"
                    f"1. FlareSolverr 服务是否正常\n"
                    f"2. 网络连接是否正常\n"
                    f"3. 目标网站是否可访问"
                )

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

        # Schedule cookie check (独立任务)
        if self.config.source_type == SourceType.DISCOURSE and self.config.cookie_check_interval > 0:
            self.scheduler.add_job(
                self._check_cookie_task,
                "interval",
                seconds=self.config.cookie_check_interval,
                id="cookie_check"
            )

        # Run initial fetch after bot starts
        async def post_init(app):
            self.scheduler.start()
            logger.info(f"⏰ 定时任务已启动, 每 {self.config.fetch_interval} 秒拉取一次")
            if self.config.source_type == SourceType.DISCOURSE and self.config.cookie_check_interval > 0:
                logger.info(f"🔐 Cookie 检测已启动, 每 {self.config.cookie_check_interval} 秒检测一次")
            # Run initial fetch
            await self.fetch_and_notify()

        application.post_init = post_init

        # Start bot (blocking)
        logger.info("🤖 Telegram Bot 启动中...")
        application.run_polling()
