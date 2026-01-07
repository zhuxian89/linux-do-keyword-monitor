import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, List, Optional, Tuple

from .models import Post, Subscription, User

# 默认论坛 ID（向后兼容）
DEFAULT_FORUM = "linux-do"


class Database:
    """SQLite database repository with multi-forum support"""

    def __init__(self, db_path: Path):
        self.db_path = db_path

    @contextmanager
    def _get_conn(self) -> Generator[sqlite3.Connection, None, None]:
        """Get database connection context manager"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize database tables - call this manually via db-init command"""
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    chat_id INTEGER NOT NULL,
                    forum TEXT NOT NULL DEFAULT 'linux-do',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (chat_id, forum)
                );

                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    keyword TEXT NOT NULL,
                    forum TEXT NOT NULL DEFAULT 'linux-do',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS posts (
                    id TEXT NOT NULL,
                    forum TEXT NOT NULL DEFAULT 'linux-do',
                    title TEXT NOT NULL,
                    link TEXT NOT NULL,
                    pub_date TEXT NOT NULL,
                    author TEXT,
                    PRIMARY KEY (id, forum)
                );

                CREATE TABLE IF NOT EXISTS notifications (
                    chat_id INTEGER NOT NULL,
                    post_id TEXT NOT NULL,
                    keyword TEXT NOT NULL,
                    forum TEXT NOT NULL DEFAULT 'linux-do',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (chat_id, post_id, keyword, forum)
                );

                CREATE TABLE IF NOT EXISTS subscribe_all (
                    chat_id INTEGER NOT NULL,
                    forum TEXT NOT NULL DEFAULT 'linux-do',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (chat_id, forum)
                );

                CREATE TABLE IF NOT EXISTS user_subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    author TEXT NOT NULL,
                    forum TEXT NOT NULL DEFAULT 'linux-do',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS blocked_users (
                    chat_id INTEGER PRIMARY KEY,
                    blocked_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );

                -- Indexes
                CREATE INDEX IF NOT EXISTS idx_users_forum ON users(forum);
                CREATE INDEX IF NOT EXISTS idx_subscriptions_chat_id ON subscriptions(chat_id);
                CREATE INDEX IF NOT EXISTS idx_subscriptions_keyword ON subscriptions(keyword);
                CREATE INDEX IF NOT EXISTS idx_subscriptions_forum ON subscriptions(forum);
                CREATE INDEX IF NOT EXISTS idx_notifications_chat_post ON notifications(chat_id, post_id);
                CREATE INDEX IF NOT EXISTS idx_notifications_post_id ON notifications(post_id);
                CREATE INDEX IF NOT EXISTS idx_notifications_forum ON notifications(forum);
                CREATE INDEX IF NOT EXISTS idx_posts_pub_date ON posts(pub_date);
                CREATE INDEX IF NOT EXISTS idx_posts_forum ON posts(forum);
                CREATE INDEX IF NOT EXISTS idx_posts_author ON posts(author);
                CREATE INDEX IF NOT EXISTS idx_user_subscriptions_chat_id ON user_subscriptions(chat_id);
                CREATE INDEX IF NOT EXISTS idx_user_subscriptions_author ON user_subscriptions(author);
                CREATE INDEX IF NOT EXISTS idx_user_subscriptions_forum ON user_subscriptions(forum);
                CREATE INDEX IF NOT EXISTS idx_subscribe_all_forum ON subscribe_all(forum);
            """)

    # User operations
    def add_user(self, chat_id: int, forum: str = DEFAULT_FORUM) -> User:
        """Add a new user"""
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (chat_id, forum, created_at) VALUES (?, ?, ?)",
                (chat_id, forum, now)
            )
        return User(chat_id=chat_id, created_at=datetime.fromisoformat(now))

    def get_user(self, chat_id: int, forum: str = DEFAULT_FORUM) -> Optional[User]:
        """Get user by chat_id"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE chat_id = ? AND forum = ?", (chat_id, forum)
            ).fetchone()
        if row:
            return User(
                chat_id=row["chat_id"],
                created_at=datetime.fromisoformat(row["created_at"])
            )
        return None

    def user_exists(self, chat_id: int, forum: str = DEFAULT_FORUM) -> bool:
        """Check if user exists"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM users WHERE chat_id = ? AND forum = ?", (chat_id, forum)
            ).fetchone()
        return row is not None

    # Subscription operations
    def add_subscription(self, chat_id: int, keyword: str, forum: str = DEFAULT_FORUM) -> Optional[Subscription]:
        """Add a subscription for a user"""
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            try:
                cursor = conn.execute(
                    "INSERT INTO subscriptions (chat_id, keyword, forum, created_at) VALUES (?, ?, ?, ?)",
                    (chat_id, keyword.lower(), forum, now)
                )
                return Subscription(
                    id=cursor.lastrowid,
                    chat_id=chat_id,
                    keyword=keyword.lower(),
                    created_at=datetime.fromisoformat(now)
                )
            except sqlite3.IntegrityError:
                return None

    def remove_subscription(self, chat_id: int, keyword: str, forum: str = DEFAULT_FORUM) -> bool:
        """Remove a subscription"""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM subscriptions WHERE chat_id = ? AND keyword = ? AND forum = ?",
                (chat_id, keyword.lower(), forum)
            )
        return cursor.rowcount > 0

    def get_user_subscriptions(self, chat_id: int, forum: str = DEFAULT_FORUM) -> List[Subscription]:
        """Get all subscriptions for a user"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM subscriptions WHERE chat_id = ? AND forum = ?", (chat_id, forum)
            ).fetchall()
        return [
            Subscription(
                id=row["id"],
                chat_id=row["chat_id"],
                keyword=row["keyword"],
                created_at=datetime.fromisoformat(row["created_at"])
            )
            for row in rows
        ]

    def get_all_keywords(self, forum: str = DEFAULT_FORUM) -> List[str]:
        """Get all unique keywords"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT keyword FROM subscriptions WHERE forum = ?", (forum,)
            ).fetchall()
        return [row["keyword"] for row in rows]

    def get_subscribers_by_keyword(self, keyword: str, forum: str = DEFAULT_FORUM) -> List[int]:
        """Get all chat_ids subscribed to a keyword"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT chat_id FROM subscriptions WHERE keyword = ? AND forum = ?",
                (keyword.lower(), forum)
            ).fetchall()
        return [row["chat_id"] for row in rows]

    # Post operations
    def add_post(self, post: Post, forum: str = DEFAULT_FORUM) -> bool:
        """Add a post, returns True if new"""
        with self._get_conn() as conn:
            try:
                author = getattr(post, 'author', None)
                conn.execute(
                    "INSERT INTO posts (id, forum, title, link, pub_date, author) VALUES (?, ?, ?, ?, ?, ?)",
                    (post.id, forum, post.title, post.link, post.pub_date.isoformat(), author)
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def post_exists(self, post_id: str, forum: str = DEFAULT_FORUM) -> bool:
        """Check if post exists"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM posts WHERE id = ? AND forum = ?", (post_id, forum)
            ).fetchone()
        return row is not None

    # Notification operations
    def add_notification(self, chat_id: int, post_id: str, keyword: str, forum: str = DEFAULT_FORUM) -> bool:
        """Add notification record, returns True if new"""
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO notifications (chat_id, post_id, keyword, forum, created_at) VALUES (?, ?, ?, ?, ?)",
                    (chat_id, post_id, keyword, forum, now)
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def notification_exists(self, chat_id: int, post_id: str, keyword: str, forum: str = DEFAULT_FORUM) -> bool:
        """Check if notification was already sent for specific keyword"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM notifications WHERE chat_id = ? AND post_id = ? AND keyword = ? AND forum = ?",
                (chat_id, post_id, keyword, forum)
            ).fetchone()
        return row is not None

    def notification_exists_for_post(self, chat_id: int, post_id: str, forum: str = DEFAULT_FORUM) -> bool:
        """Check if any notification was already sent for this post to this user."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM notifications WHERE chat_id = ? AND post_id = ? AND forum = ?",
                (chat_id, post_id, forum)
            ).fetchone()
        return row is not None

    # Subscribe all operations
    def add_subscribe_all(self, chat_id: int, forum: str = DEFAULT_FORUM) -> bool:
        """Add user to subscribe all list"""
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO subscribe_all (chat_id, forum, created_at) VALUES (?, ?, ?)",
                    (chat_id, forum, now)
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def remove_subscribe_all(self, chat_id: int, forum: str = DEFAULT_FORUM) -> bool:
        """Remove user from subscribe all list"""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM subscribe_all WHERE chat_id = ? AND forum = ?", (chat_id, forum)
            )
        return cursor.rowcount > 0

    def is_subscribe_all(self, chat_id: int, forum: str = DEFAULT_FORUM) -> bool:
        """Check if user is subscribed to all"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM subscribe_all WHERE chat_id = ? AND forum = ?", (chat_id, forum)
            ).fetchone()
        return row is not None

    def get_all_subscribe_all_users(self, forum: str = DEFAULT_FORUM) -> List[int]:
        """Get all users subscribed to all posts"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT chat_id FROM subscribe_all WHERE forum = ?", (forum,)
            ).fetchall()
        return [row["chat_id"] for row in rows]

    def notification_exists_for_all(self, chat_id: int, post_id: str, forum: str = DEFAULT_FORUM) -> bool:
        """Check if notification was already sent for subscribe_all user"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM notifications WHERE chat_id = ? AND post_id = ? AND keyword = '__ALL__' AND forum = ?",
                (chat_id, post_id, forum)
            ).fetchone()
        return row is not None

    # User subscription operations (subscribe to specific authors)
    def add_user_subscription(self, chat_id: int, author: str, forum: str = DEFAULT_FORUM) -> bool:
        """Add a user subscription (subscribe to an author)"""
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO user_subscriptions (chat_id, author, forum, created_at) VALUES (?, ?, ?, ?)",
                    (chat_id, author.lower(), forum, now)
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def remove_user_subscription(self, chat_id: int, author: str, forum: str = DEFAULT_FORUM) -> bool:
        """Remove a user subscription"""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM user_subscriptions WHERE chat_id = ? AND author = ? AND forum = ?",
                (chat_id, author.lower(), forum)
            )
        return cursor.rowcount > 0

    def get_user_author_subscriptions(self, chat_id: int, forum: str = DEFAULT_FORUM) -> List[str]:
        """Get all authors a user is subscribed to"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT author FROM user_subscriptions WHERE chat_id = ? AND forum = ?", (chat_id, forum)
            ).fetchall()
        return [row["author"] for row in rows]

    def get_all_subscribed_authors(self, forum: str = DEFAULT_FORUM) -> List[str]:
        """Get all unique subscribed authors"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT author FROM user_subscriptions WHERE forum = ?", (forum,)
            ).fetchall()
        return [row["author"] for row in rows]

    def get_subscribers_by_author(self, author: str, forum: str = DEFAULT_FORUM) -> List[int]:
        """Get all chat_ids subscribed to an author"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT chat_id FROM user_subscriptions WHERE author = ? AND forum = ?",
                (author.lower(), forum)
            ).fetchall()
        return [row["chat_id"] for row in rows]

    def get_user_subscription_count(self, chat_id: int, forum: str = DEFAULT_FORUM) -> int:
        """Get the number of authors a user is subscribed to"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM user_subscriptions WHERE chat_id = ? AND forum = ?", (chat_id, forum)
            ).fetchone()
        return row[0]

    # Statistics operations
    def get_all_users(self, forum: str = DEFAULT_FORUM, page: int = 1, page_size: int = 20) -> Tuple[List[dict], int]:
        """Get users with pagination."""
        offset = (page - 1) * page_size

        with self._get_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM users WHERE forum = ?", (forum,)
            ).fetchone()[0]

            rows = conn.execute("""
                SELECT
                    u.chat_id,
                    u.created_at,
                    (SELECT COUNT(*) FROM subscriptions s WHERE s.chat_id = u.chat_id AND s.forum = ?) as keyword_count,
                    (SELECT GROUP_CONCAT(s.keyword, ', ') FROM subscriptions s WHERE s.chat_id = u.chat_id AND s.forum = ?) as keywords,
                    (SELECT 1 FROM subscribe_all sa WHERE sa.chat_id = u.chat_id AND sa.forum = ?) as is_subscribe_all,
                    (SELECT COUNT(*) FROM notifications n WHERE n.chat_id = u.chat_id AND n.forum = ?) as notification_count
                FROM users u
                WHERE u.forum = ?
                ORDER BY u.created_at DESC
                LIMIT ? OFFSET ?
            """, (forum, forum, forum, forum, forum, page_size, offset)).fetchall()

        users = [
            {
                "chat_id": row["chat_id"],
                "created_at": row["created_at"],
                "keyword_count": row["keyword_count"] or 0,
                "keywords": row["keywords"] or "",
                "is_subscribe_all": bool(row["is_subscribe_all"]),
                "notification_count": row["notification_count"] or 0,
            }
            for row in rows
        ]
        return users, total

    def get_stats(self, forum: str = DEFAULT_FORUM) -> dict:
        """Get overall statistics for a forum"""
        with self._get_conn() as conn:
            user_count = conn.execute(
                "SELECT COUNT(*) FROM users WHERE forum = ?", (forum,)
            ).fetchone()[0]
            subscription_count = conn.execute(
                "SELECT COUNT(*) FROM subscriptions WHERE forum = ?", (forum,)
            ).fetchone()[0]
            subscribe_all_count = conn.execute(
                "SELECT COUNT(*) FROM subscribe_all WHERE forum = ?", (forum,)
            ).fetchone()[0]
            post_count = conn.execute(
                "SELECT COUNT(*) FROM posts WHERE forum = ?", (forum,)
            ).fetchone()[0]
            notification_count = conn.execute(
                "SELECT COUNT(*) FROM notifications WHERE forum = ?", (forum,)
            ).fetchone()[0]
            keyword_count = conn.execute(
                "SELECT COUNT(DISTINCT keyword) FROM subscriptions WHERE forum = ?", (forum,)
            ).fetchone()[0]
            blocked_count = conn.execute("SELECT COUNT(*) FROM blocked_users").fetchone()[0]
        return {
            "user_count": user_count,
            "subscription_count": subscription_count,
            "subscribe_all_count": subscribe_all_count,
            "post_count": post_count,
            "notification_count": notification_count,
            "keyword_count": keyword_count,
            "blocked_count": blocked_count,
        }

    # Blocked users operations (global, not per-forum)
    def mark_user_blocked(self, chat_id: int) -> bool:
        """Mark a user as having blocked the bot"""
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO blocked_users (chat_id, blocked_at) VALUES (?, ?)",
                    (chat_id, now)
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def unmark_user_blocked(self, chat_id: int) -> bool:
        """Remove user from blocked list"""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM blocked_users WHERE chat_id = ?", (chat_id,)
            )
        return cursor.rowcount > 0

    def is_user_blocked(self, chat_id: int) -> bool:
        """Check if user has blocked the bot"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM blocked_users WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        return row is not None

    def get_blocked_user_count(self) -> int:
        """Get count of users who blocked the bot"""
        with self._get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM blocked_users").fetchone()
        return row[0]
