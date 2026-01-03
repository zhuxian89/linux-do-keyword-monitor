import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, List, Optional

from .models import Notification, Post, Subscription, User


class Database:
    """SQLite database repository"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

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
        """Initialize database tables"""
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    chat_id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    keyword TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (chat_id) REFERENCES users(chat_id),
                    UNIQUE(chat_id, keyword)
                );

                CREATE TABLE IF NOT EXISTS posts (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    link TEXT NOT NULL,
                    pub_date TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS notifications (
                    chat_id INTEGER NOT NULL,
                    post_id TEXT NOT NULL,
                    keyword TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (chat_id, post_id, keyword)
                );

                CREATE INDEX IF NOT EXISTS idx_subscriptions_chat_id
                    ON subscriptions(chat_id);
                CREATE INDEX IF NOT EXISTS idx_subscriptions_keyword
                    ON subscriptions(keyword);

                CREATE TABLE IF NOT EXISTS subscribe_all (
                    chat_id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL
                );
            """)

    # User operations
    def add_user(self, chat_id: int) -> User:
        """Add a new user"""
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (chat_id, created_at) VALUES (?, ?)",
                (chat_id, now)
            )
        return User(chat_id=chat_id, created_at=datetime.fromisoformat(now))

    def get_user(self, chat_id: int) -> Optional[User]:
        """Get user by chat_id"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        if row:
            return User(
                chat_id=row["chat_id"],
                created_at=datetime.fromisoformat(row["created_at"])
            )
        return None

    # Subscription operations
    def add_subscription(self, chat_id: int, keyword: str) -> Optional[Subscription]:
        """Add a subscription for a user"""
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            try:
                cursor = conn.execute(
                    "INSERT INTO subscriptions (chat_id, keyword, created_at) VALUES (?, ?, ?)",
                    (chat_id, keyword.lower(), now)
                )
                return Subscription(
                    id=cursor.lastrowid,
                    chat_id=chat_id,
                    keyword=keyword.lower(),
                    created_at=datetime.fromisoformat(now)
                )
            except sqlite3.IntegrityError:
                return None

    def remove_subscription(self, chat_id: int, keyword: str) -> bool:
        """Remove a subscription"""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM subscriptions WHERE chat_id = ? AND keyword = ?",
                (chat_id, keyword.lower())
            )
        return cursor.rowcount > 0

    def get_user_subscriptions(self, chat_id: int) -> List[Subscription]:
        """Get all subscriptions for a user"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM subscriptions WHERE chat_id = ?", (chat_id,)
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

    def get_all_keywords(self) -> List[str]:
        """Get all unique keywords"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT keyword FROM subscriptions"
            ).fetchall()
        return [row["keyword"] for row in rows]

    def get_subscribers_by_keyword(self, keyword: str) -> List[int]:
        """Get all chat_ids subscribed to a keyword"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT chat_id FROM subscriptions WHERE keyword = ?",
                (keyword.lower(),)
            ).fetchall()
        return [row["chat_id"] for row in rows]

    # Post operations
    def add_post(self, post: Post) -> bool:
        """Add a post, returns True if new"""
        with self._get_conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO posts (id, title, link, pub_date) VALUES (?, ?, ?, ?)",
                    (post.id, post.title, post.link, post.pub_date.isoformat())
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def post_exists(self, post_id: str) -> bool:
        """Check if post exists"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM posts WHERE id = ?", (post_id,)
            ).fetchone()
        return row is not None

    # Notification operations
    def add_notification(self, chat_id: int, post_id: str, keyword: str) -> bool:
        """Add notification record, returns True if new"""
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO notifications (chat_id, post_id, keyword, created_at) VALUES (?, ?, ?, ?)",
                    (chat_id, post_id, keyword, now)
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def notification_exists(self, chat_id: int, post_id: str, keyword: str) -> bool:
        """Check if notification was already sent"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM notifications WHERE chat_id = ? AND post_id = ? AND keyword = ?",
                (chat_id, post_id, keyword)
            ).fetchone()
        return row is not None

    # Subscribe all operations
    def add_subscribe_all(self, chat_id: int) -> bool:
        """Add user to subscribe all list"""
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO subscribe_all (chat_id, created_at) VALUES (?, ?)",
                    (chat_id, now)
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def remove_subscribe_all(self, chat_id: int) -> bool:
        """Remove user from subscribe all list"""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM subscribe_all WHERE chat_id = ?", (chat_id,)
            )
        return cursor.rowcount > 0

    def is_subscribe_all(self, chat_id: int) -> bool:
        """Check if user is subscribed to all"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM subscribe_all WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        return row is not None

    def get_all_subscribe_all_users(self) -> List[int]:
        """Get all users subscribed to all posts"""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT chat_id FROM subscribe_all").fetchall()
        return [row["chat_id"] for row in rows]

    def notification_exists_for_all(self, chat_id: int, post_id: str) -> bool:
        """Check if notification was already sent for subscribe_all user"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM notifications WHERE chat_id = ? AND post_id = ? AND keyword = '__ALL__'",
                (chat_id, post_id)
            ).fetchone()
        return row is not None

    # Statistics operations
    def get_all_users(self) -> List[dict]:
        """Get all users with their subscription info"""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT
                    u.chat_id,
                    u.created_at,
                    (SELECT COUNT(*) FROM subscriptions s WHERE s.chat_id = u.chat_id) as keyword_count,
                    (SELECT GROUP_CONCAT(s.keyword, ', ') FROM subscriptions s WHERE s.chat_id = u.chat_id) as keywords,
                    (SELECT 1 FROM subscribe_all sa WHERE sa.chat_id = u.chat_id) as is_subscribe_all,
                    (SELECT COUNT(*) FROM notifications n WHERE n.chat_id = u.chat_id) as notification_count
                FROM users u
                ORDER BY u.created_at DESC
            """).fetchall()
        return [
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

    def get_stats(self) -> dict:
        """Get overall statistics"""
        with self._get_conn() as conn:
            user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            subscription_count = conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0]
            subscribe_all_count = conn.execute("SELECT COUNT(*) FROM subscribe_all").fetchone()[0]
            post_count = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
            notification_count = conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
            keyword_count = conn.execute("SELECT COUNT(DISTINCT keyword) FROM subscriptions").fetchone()[0]
        return {
            "user_count": user_count,
            "subscription_count": subscription_count,
            "subscribe_all_count": subscribe_all_count,
            "post_count": post_count,
            "notification_count": notification_count,
            "keyword_count": keyword_count,
        }
