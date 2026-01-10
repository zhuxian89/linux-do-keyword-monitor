import asyncio
import logging
import logging.handlers
from pathlib import Path
from typing import List, Optional, Set, Tuple

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .bot.bot import TelegramBot
from .cache import AppCache
from .config import AppConfig, ConfigManager, SourceType, ForumConfig
from .database import Database, DEFAULT_FORUM
from .matcher.keyword import KeywordMatcher
from .models import Post
from .source import BaseSource, RSSSource, DiscourseSource
from .web_flask import test_cookie


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


def create_source(config) -> BaseSource:
    """Factory function to create data source based on config

    Args:
        config: Either AppConfig (legacy) or ForumConfig (new)
    """
    # Handle both AppConfig and ForumConfig
    if isinstance(config, ForumConfig):
        forum_config = config
    else:
        # Legacy AppConfig - get first forum or use legacy fields
        if config.forums:
            forum_config = config.forums[0]
        else:
            # Create a temporary ForumConfig from legacy fields
            forum_config = ForumConfig(
                forum_id=DEFAULT_FORUM,
                name="Linux.do",
                bot_token=config.bot_token,
                source_type=config.source_type or SourceType.RSS,
                rss_url=config.rss_url or "https://linux.do/latest.rss",
                discourse_url=config.discourse_url or "https://linux.do",
                discourse_cookie=config.discourse_cookie,
                flaresolverr_url=config.flaresolverr_url,
            )

    if forum_config.source_type == SourceType.DISCOURSE:
        if not forum_config.discourse_cookie:
            raise ValueError("Discourse source requires cookie configuration")
        return DiscourseSource(
            base_url=forum_config.discourse_url,
            cookie=forum_config.discourse_cookie,
            flaresolverr_url=forum_config.flaresolverr_url
        )
    else:
        return RSSSource(url=forum_config.rss_url)


class Application:
    """Main application that orchestrates all components for a single forum"""

    def __init__(
        self,
        forum_config: ForumConfig,
        db: Database,
        admin_chat_id: Optional[int] = None,
        config_manager: Optional[ConfigManager] = None
    ):
        self.forum_config = forum_config
        self.forum_id = forum_config.forum_id
        self.forum_name = forum_config.name
        self.admin_chat_id = admin_chat_id
        self.config_manager = config_manager
        self.db = db
        self.cache = AppCache(forum_id=self.forum_id)  # Shared cache
        self.bot = TelegramBot(
            forum_config.bot_token,
            self.db,
            forum_id=self.forum_id,
            forum_name=self.forum_name,
            cache=self.cache  # Pass shared cache to bot
        )
        self.source = create_source(forum_config)
        self.matcher = KeywordMatcher()
        self.scheduler = AsyncIOScheduler()
        self._cookie_fail_count = 0  # 连续失败计数器
        self._cookie_fail_threshold = 5  # 连续失败阈值
        self._cookie_notify_round = 0  # 第几轮通知
        self._fetch_fail_count = 0  # 拉取失败计数器
        self._fetch_fail_threshold = 5  # 拉取连续失败阈值
        self._fetch_fail_notified = False  # 是否已发送拉取失败告警
        self.application = None  # Telegram Application instance

    def reload_config(self):
        """Hot reload configuration"""
        if not self.config_manager:
            logger.warning(f"[{self.forum_id}] 无法热更新：ConfigManager 未设置")
            return

        new_app_config = self.config_manager.load()
        if not new_app_config:
            logger.error(f"[{self.forum_id}] 热更新失败：无法加载配置")
            return

        # Find this forum's config in the new config
        new_forum_config = new_app_config.get_forum(self.forum_id)
        if not new_forum_config:
            logger.error(f"[{self.forum_id}] 热更新失败：找不到论坛配置")
            return

        old_forum_config = self.forum_config

        # Update source
        self.forum_config = new_forum_config
        self.admin_chat_id = new_app_config.admin_chat_id
        self.source = create_source(new_forum_config)
        # Reset cookie invalid state on config reload
        self._cookie_fail_count = 0
        self._cookie_notify_round = 0
        # Reset fetch fail state on config reload
        self._fetch_fail_count = 0
        self._fetch_fail_notified = False
        # Invalidate cache on config change
        self.cache.clear_all()

        # Job IDs are unique per forum
        data_fetch_job_id = f"data_fetch_{self.forum_id}"
        cookie_check_job_id = f"cookie_check_{self.forum_id}"

        # 更新 scheduler 定时任务间隔
        if self.scheduler.running:
            # 更新数据拉取间隔
            if old_forum_config.fetch_interval != new_forum_config.fetch_interval:
                # reschedule_job 不支持 misfire_grace_time，需要先删除再添加
                self.scheduler.remove_job(data_fetch_job_id)
                self.scheduler.add_job(
                    self.fetch_and_notify,
                    "interval",
                    seconds=new_forum_config.fetch_interval,
                    id=data_fetch_job_id,
                    misfire_grace_time=None,
                    coalesce=True
                )
                logger.info(f"[{self.forum_id}] ⏰ 数据拉取间隔已更新: {old_forum_config.fetch_interval}s → {new_forum_config.fetch_interval}s")

            # 更新 Cookie 检测间隔
            if old_forum_config.cookie_check_interval != new_forum_config.cookie_check_interval:
                if new_forum_config.cookie_check_interval > 0:
                    # 先删除旧任务（如果存在）
                    job = self.scheduler.get_job(cookie_check_job_id)
                    if job:
                        self.scheduler.remove_job(cookie_check_job_id)
                    # 添加新任务
                    self.scheduler.add_job(
                        self._check_cookie_task,
                        "interval",
                        seconds=new_forum_config.cookie_check_interval,
                        id=cookie_check_job_id,
                        misfire_grace_time=None,
                        coalesce=True
                    )
                    logger.info(f"[{self.forum_id}] 🔐 Cookie 检测间隔已更新: {old_forum_config.cookie_check_interval}s → {new_forum_config.cookie_check_interval}s")
                else:
                    # 禁用 Cookie 检测
                    job = self.scheduler.get_job(cookie_check_job_id)
                    if job:
                        self.scheduler.remove_job(cookie_check_job_id)
                        logger.info(f"[{self.forum_id}] 🔐 Cookie 检测已禁用")

        logger.info(f"[{self.forum_id}] 🔄 配置已热更新，数据源: {self.source.get_source_name()}")

    async def _notify_admin(self, message: str) -> None:
        """Send notification to admin"""
        if not self.admin_chat_id:
            logger.warning(f"[{self.forum_id}] 管理员 chat_id 未配置，无法发送告警")
            return

        try:
            await self.bot.send_admin_alert(self.admin_chat_id, message)
            logger.info(f"[{self.forum_id}] 📢 已发送管理员告警")
        except Exception as e:
            logger.error(f"[{self.forum_id}] 发送管理员告警失败: {e}")

    def _check_cookie_valid(self) -> dict:
        """Check if discourse cookie is valid

        Returns:
            dict with keys:
            - valid: bool
            - error_type: "cookie_invalid" | "service_error" | None
            - error: str | None
        """
        if self.forum_config.source_type != SourceType.DISCOURSE:
            return {"valid": True, "error_type": None, "error": None}

        if not self.forum_config.discourse_cookie:
            return {"valid": False, "error_type": "cookie_invalid", "error": "Cookie 未配置"}

        # 打印当前使用的 cookie（只显示前50字符）
        cookie_preview = self.forum_config.discourse_cookie[:50] + "..." if len(self.forum_config.discourse_cookie) > 50 else self.forum_config.discourse_cookie
        logger.info(f"[{self.forum_id}] 🔍 检测 Cookie: {cookie_preview}")

        result = test_cookie(self.forum_config.discourse_cookie, self.forum_config.discourse_url, self.forum_config.flaresolverr_url)
        return result

    def _fallback_to_rss(self) -> BaseSource:
        """Create RSS fallback source (deprecated, kept for compatibility)"""
        return RSSSource(url=self.forum_config.rss_url)

    async def _check_cookie_task(self) -> None:
        """独立的 Cookie 检测任务"""
        if self.forum_config.source_type != SourceType.DISCOURSE:
            return

        # 连续测试 3 次
        fail_count = 0
        last_result = None
        loop = asyncio.get_event_loop()
        for i in range(3):
            # 在线程池中执行同步的 cookie 检测，避免阻塞事件循环
            result = await loop.run_in_executor(None, self._check_cookie_valid)
            last_result = result
            if not result.get("valid", False):
                fail_count += 1
                error_msg = result.get("error", "未知错误")
                logger.warning(f"[{self.forum_id}] ⚠️ Cookie 检测失败 (第 {fail_count}/3 次): {error_msg}")
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
                logger.warning(f"[{self.forum_id}] ⚠️ Cookie 检测失败（服务错误）: {error_msg}")
                return

            # Cookie 真正失效才发告警
            self._cookie_fail_count += 1
            logger.warning(f"[{self.forum_id}] ⚠️ Cookie 连续 3 次检测失败（第 {self._cookie_fail_count} 轮）: {error_msg}")
            for i in range(1, 4):
                await self._notify_admin(
                    f"⚠️ [{self.forum_name}] Cookie 可能已失效（第 {self._cookie_fail_count} 轮通知，第 {i}/3 遍）\n\n"
                    f"Discourse Cookie 连续 3 次验证失败。\n"
                    f"错误信息: {error_msg}\n\n"
                    f"当前仍可拉取公开数据，但部分限制内容可能无法获取。\n\n"
                    f"{'❗' * i} 请检查 Cookie 是否需要更新 {'❗' * i}\n\n"
                    f"更新方式：访问配置页面更新 Cookie"
                )
        else:
            # 检测通过
            logger.info(f"[{self.forum_id}] ✅ Cookie 检测通过，状态有效")
            if self._cookie_fail_count > 0:
                logger.info(f"[{self.forum_id}] ✅ Cookie 检测恢复正常（之前失败 {self._cookie_fail_count} 轮）")
                await self._notify_admin(f"✅ [{self.forum_name}] Cookie 已恢复有效，之前的告警可以忽略了")
                self._cookie_fail_count = 0

    def _get_keywords_cached(self) -> List[str]:
        """Get keywords with caching (or direct DB if cache disabled)"""
        if not self.forum_config.cache_enabled:
            return self.db.get_all_keywords(forum=self.forum_id)
        cached = self.cache.get_keywords()
        if cached is not None:
            return cached
        keywords = self.db.get_all_keywords(forum=self.forum_id)
        self.cache.set_keywords(keywords)
        return keywords

    def _get_subscribe_all_users_cached(self) -> List[int]:
        """Get subscribe_all users with caching (or direct DB if cache disabled)"""
        if not self.forum_config.cache_enabled:
            return self.db.get_all_subscribe_all_users(forum=self.forum_id)
        cached = self.cache.get_subscribe_all_users()
        if cached is not None:
            return cached
        users = self.db.get_all_subscribe_all_users(forum=self.forum_id)
        self.cache.set_subscribe_all_users(users)
        return users

    def _get_subscribers_cached(self, keyword: str) -> List[int]:
        """Get subscribers for a keyword with caching (or direct DB if cache disabled)"""
        if not self.forum_config.cache_enabled:
            return self.db.get_subscribers_by_keyword(keyword, forum=self.forum_id)
        cached = self.cache.get_subscribers(keyword)
        if cached is not None:
            return cached
        subscribers = self.db.get_subscribers_by_keyword(keyword, forum=self.forum_id)
        self.cache.set_subscribers(keyword, subscribers)
        return subscribers

    def _get_subscribed_authors_cached(self) -> List[str]:
        """Get subscribed authors with caching (or direct DB if cache disabled)"""
        if not self.forum_config.cache_enabled:
            return self.db.get_all_subscribed_authors(forum=self.forum_id)
        cached = self.cache.get_authors()
        if cached is not None:
            return cached
        authors = self.db.get_all_subscribed_authors(forum=self.forum_id)
        self.cache.set_authors(authors)
        return authors

    def _get_author_subscribers_cached(self, author: str) -> List[int]:
        """Get subscribers for an author with caching (or direct DB if cache disabled)"""
        if not self.forum_config.cache_enabled:
            return self.db.get_subscribers_by_author(author, forum=self.forum_id)
        cached = self.cache.get_author_subscribers(author)
        if cached is not None:
            return cached
        subscribers = self.db.get_subscribers_by_author(author, forum=self.forum_id)
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
                    self.db.add_notification(chat_id, post.id, keyword or "__ALL__", forum=self.forum_id)
                return success
            except Exception as e:
                logger.error(f"[{self.forum_id}] 发送失败 {chat_id}: {e}")
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
            logger.info(f"[{self.forum_id}] 📡 开始拉取数据 ({self.source.get_source_name()})...")
            # 在线程池中执行同步的 fetch，避免阻塞事件循环
            loop = asyncio.get_event_loop()
            posts = await loop.run_in_executor(None, self.source.fetch)

            # Use cached data
            keywords = self._get_keywords_cached()
            subscribe_all_users = self._get_subscribe_all_users_cached()
            subscribe_all_set: Set[int] = set(subscribe_all_users)
            subscribed_authors = self._get_subscribed_authors_cached()

            new_posts = []
            pending_tasks: List[Tuple] = []  # (chat_id, post, keyword_or_none)

            for post in posts:
                # Skip if post already processed
                if self.db.post_exists(post.id, forum=self.forum_id):
                    continue

                new_posts.append(post)
                self.db.add_post(post, forum=self.forum_id)

                # Track users already notified for this post (in this cycle)
                notified_users: Set[int] = set()

                # Collect subscribe_all notifications
                for chat_id in subscribe_all_users:
                    # Check DB for existing notification
                    if self.db.notification_exists_for_post(chat_id, post.id, forum=self.forum_id):
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
                            if self.db.notification_exists_for_post(chat_id, post.id, forum=self.forum_id):
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
                            if self.db.notification_exists_for_post(chat_id, post.id, forum=self.forum_id):
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
                    logger.info(f"[{self.forum_id}]   📤 批量发送 {sent}/{len(batch)} 条")

                # Rate limit between batches
                if i + BATCH_SIZE < len(pending_tasks):
                    await asyncio.sleep(BATCH_INTERVAL)

            # Summary log
            logger.info(f"[{self.forum_id}] ✅ 拉取完成: 共 {len(posts)} 条, 新增 {len(new_posts)} 条, 推送 {total_sent} 条通知")

            # 拉取成功，重置失败计数
            if self._fetch_fail_count > 0:
                logger.info(f"[{self.forum_id}] ✅ 数据拉取恢复正常（之前连续失败 {self._fetch_fail_count} 次）")
                if self._fetch_fail_notified:
                    await self._notify_admin(f"✅ [{self.forum_name}] 数据拉取已恢复正常，之前的告警可以忽略了")
                self._fetch_fail_count = 0
                self._fetch_fail_notified = False

        except Exception as e:
            self._fetch_fail_count += 1
            logger.error(f"[{self.forum_id}] ❌ 数据拉取失败 (第 {self._fetch_fail_count} 次): {e}")

            # 连续失败达到阈值时发送告警
            if self._fetch_fail_count >= self._fetch_fail_threshold and not self._fetch_fail_notified:
                self._fetch_fail_notified = True
                await self._notify_admin(
                    f"⚠️ [{self.forum_name}] 数据拉取连续失败 {self._fetch_fail_count} 次\n\n"
                    f"错误信息: {e}\n\n"
                    f"请检查:\n"
                    f"1. FlareSolverr 服务是否正常\n"
                    f"2. 网络连接是否正常\n"
                    f"3. 目标网站是否可访问"
                )

    def run(self) -> None:
        """Start the application (blocking, for single forum mode)"""
        # Setup bot
        application = self.bot.setup()

        # Job IDs are unique per forum
        data_fetch_job_id = f"data_fetch_{self.forum_id}"
        cookie_check_job_id = f"cookie_check_{self.forum_id}"

        # Schedule fetching
        # misfire_grace_time: 允许延迟执行的时间（秒），None 表示无限
        # coalesce: 如果错过多次，只执行一次
        self.scheduler.add_job(
            self.fetch_and_notify,
            "interval",
            seconds=self.forum_config.fetch_interval,
            id=data_fetch_job_id,
            misfire_grace_time=None,
            coalesce=True
        )

        # Schedule cookie check (独立任务)
        if self.forum_config.source_type == SourceType.DISCOURSE and self.forum_config.cookie_check_interval > 0:
            self.scheduler.add_job(
                self._check_cookie_task,
                "interval",
                seconds=self.forum_config.cookie_check_interval,
                id=cookie_check_job_id,
                misfire_grace_time=None,
                coalesce=True
            )

        # Run initial fetch after bot starts
        async def post_init(app):
            self.scheduler.start()
            logger.info(f"[{self.forum_id}] ⏰ 定时任务已启动, 每 {self.forum_config.fetch_interval} 秒拉取一次")
            if self.forum_config.source_type == SourceType.DISCOURSE and self.forum_config.cookie_check_interval > 0:
                logger.info(f"[{self.forum_id}] 🔐 Cookie 检测已启动, 每 {self.forum_config.cookie_check_interval} 秒检测一次")
            # Run initial fetch
            await self.fetch_and_notify()

        application.post_init = post_init

        # Start bot (blocking)
        logger.info(f"[{self.forum_id}] 🤖 Telegram Bot 启动中...")
        application.run_polling()

    async def start_async(self) -> None:
        """Start the application asynchronously (for multi-forum mode)"""
        # Setup bot
        self.application = self.bot.setup()

        # Job IDs are unique per forum
        data_fetch_job_id = f"data_fetch_{self.forum_id}"
        cookie_check_job_id = f"cookie_check_{self.forum_id}"

        # Schedule fetching
        self.scheduler.add_job(
            self.fetch_and_notify,
            "interval",
            seconds=self.forum_config.fetch_interval,
            id=data_fetch_job_id,
            misfire_grace_time=None,
            coalesce=True
        )

        # Schedule cookie check
        if self.forum_config.source_type == SourceType.DISCOURSE and self.forum_config.cookie_check_interval > 0:
            self.scheduler.add_job(
                self._check_cookie_task,
                "interval",
                seconds=self.forum_config.cookie_check_interval,
                id=cookie_check_job_id,
                misfire_grace_time=None,
                coalesce=True
            )

        # Initialize and start bot
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()

        # Start scheduler
        self.scheduler.start()
        logger.info(f"[{self.forum_id}] 🤖 Telegram Bot 已启动")
        logger.info(f"[{self.forum_id}] ⏰ 定时任务已启动, 每 {self.forum_config.fetch_interval} 秒拉取一次")

        if self.forum_config.source_type == SourceType.DISCOURSE and self.forum_config.cookie_check_interval > 0:
            logger.info(f"[{self.forum_id}] 🔐 Cookie 检测已启动, 每 {self.forum_config.cookie_check_interval} 秒检测一次")

        # Run initial fetch
        await self.fetch_and_notify()

    async def stop_async(self) -> None:
        """Stop the application asynchronously"""
        if hasattr(self, 'application') and self.application:
            try:
                await self.application.updater.stop()
                await self.application.stop()
                await self.application.shutdown()
            except Exception as e:
                logger.error(f"[{self.forum_id}] 停止 Bot 时出错: {e}")
        if self.scheduler.running:
            self.scheduler.shutdown()
        logger.info(f"[{self.forum_id}] 🛑 已停止")

    def is_running(self) -> bool:
        """Check if the application is running"""
        if not hasattr(self, 'application') or not self.application:
            return False
        if not self.application.updater:
            return False
        return self.application.updater.running

    def _reset_state(self) -> None:
        """Reset application state for restart"""
        # Reset failure counters
        self._cookie_fail_count = 0
        self._cookie_notify_round = 0
        self._fetch_fail_count = 0
        self._fetch_fail_notified = False

        # Clear cache
        self.cache.clear_all()

        # Recreate cache with forum isolation
        self.cache = AppCache(forum_id=self.forum_id)

        # Recreate scheduler (old one is shutdown)
        self.scheduler = AsyncIOScheduler()

        # Recreate bot and source
        self.bot = TelegramBot(
            self.forum_config.bot_token,
            self.db,
            forum_id=self.forum_id,
            forum_name=self.forum_name,
            cache=self.cache  # Pass shared cache
        )
        self.source = create_source(self.forum_config)

        # Clear application reference
        self.application = None


class MultiForumApplication:
    """Manages multiple forum applications running in parallel with fault isolation"""

    def __init__(
        self,
        config: AppConfig,
        db: Database,
        config_manager: Optional[ConfigManager] = None
    ):
        self.config = config
        self.db = db
        self.config_manager = config_manager
        self.apps: List[Application] = []
        self._running = False
        self._tasks: List[asyncio.Task] = []

    def _create_apps(self) -> None:
        """Create Application instances for each enabled forum"""
        self.apps = []
        for forum_config in self.config.get_enabled_forums():
            app = Application(
                forum_config=forum_config,
                db=self.db,
                admin_chat_id=self.config.admin_chat_id,
                config_manager=self.config_manager
            )
            self.apps.append(app)

    def reload_config(self) -> None:
        """Hot reload configuration for all apps"""
        for app in self.apps:
            try:
                app.reload_config()
            except Exception as e:
                logger.error(f"[{app.forum_id}] 热更新失败: {e}")

    def run(self) -> None:
        """Start all forum applications"""
        self._create_apps()

        if not self.apps:
            logger.error("没有启用的论坛配置")
            return

        if len(self.apps) == 1:
            # Single forum - use blocking mode
            logger.info(f"🚀 启动单论坛模式: {self.apps[0].forum_name}")
            self.apps[0].run()
        else:
            # Multiple forums - use async mode
            logger.info(f"🚀 启动多论坛模式: {len(self.apps)} 个论坛")
            asyncio.run(self._run_multi_async())

    async def _run_single_app(self, app: Application) -> None:
        """Run a single app with automatic restart on failure"""
        restart_delay = 5  # seconds
        max_restart_delay = 300  # 5 minutes max

        while self._running:
            try:
                logger.info(f"[{app.forum_id}] 🚀 启动中...")
                await app.start_async()

                # Keep running until stopped or error
                while self._running and app.is_running():
                    await asyncio.sleep(1)

                if not self._running:
                    break

                logger.warning(f"[{app.forum_id}] ⚠️ Bot 意外停止，将在 {restart_delay} 秒后重启")

            except Exception as e:
                logger.error(f"[{app.forum_id}] ❌ 运行出错: {e}")
                logger.warning(f"[{app.forum_id}] 将在 {restart_delay} 秒后重启")

            # Stop and cleanup
            try:
                await app.stop_async()
            except Exception as e:
                logger.error(f"[{app.forum_id}] 停止时出错: {e}")

            if not self._running:
                break

            # Wait before restart
            await asyncio.sleep(restart_delay)

            # Exponential backoff (cap at max_restart_delay)
            restart_delay = min(restart_delay * 2, max_restart_delay)

            # Reset app state for restart
            app._reset_state()

        logger.info(f"[{app.forum_id}] 🛑 已停止")

    async def _run_multi_async(self) -> None:
        """Run multiple forums asynchronously with fault isolation"""
        self._running = True

        # Start each app in its own task (isolated)
        self._tasks = []
        for app in self.apps:
            task = asyncio.create_task(
                self._run_single_app(app),
                name=f"forum_{app.forum_id}"
            )
            self._tasks.append(task)

        logger.info(f"✅ 已启动 {len(self._tasks)} 个论坛任务")

        # Wait for shutdown signal
        try:
            while self._running:
                await asyncio.sleep(1)

                # Check if all tasks died
                alive_tasks = [t for t in self._tasks if not t.done()]
                if not alive_tasks:
                    logger.error("❌ 所有论坛任务都已停止")
                    break

        except asyncio.CancelledError:
            logger.info("收到取消信号")
        finally:
            self._running = False

            # Cancel all tasks
            for task in self._tasks:
                if not task.done():
                    task.cancel()

            # Wait for all tasks to complete
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)

            logger.info("🛑 所有论坛已停止")
