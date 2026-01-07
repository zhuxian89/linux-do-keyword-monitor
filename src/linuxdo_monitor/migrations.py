"""Database migration management"""
import logging
import sqlite3
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

# 当前数据库版本
CURRENT_VERSION = 4

# 迁移脚本
MIGRATIONS = {
    # 版本 1: 初始版本（现有结构）
    1: [],

    # 版本 2: 多论坛支持
    2: [
        # 添加 forum 字段到各表
        "ALTER TABLE users ADD COLUMN forum TEXT DEFAULT 'linux-do'",
        "ALTER TABLE subscriptions ADD COLUMN forum TEXT DEFAULT 'linux-do'",
        "ALTER TABLE user_subscriptions ADD COLUMN forum TEXT DEFAULT 'linux-do'",
        "ALTER TABLE subscribe_all ADD COLUMN forum TEXT DEFAULT 'linux-do'",
        "ALTER TABLE posts ADD COLUMN forum TEXT DEFAULT 'linux-do'",
        "ALTER TABLE notifications ADD COLUMN forum TEXT DEFAULT 'linux-do'",

        # 创建索引
        "CREATE INDEX IF NOT EXISTS idx_users_forum ON users(forum)",
        "CREATE INDEX IF NOT EXISTS idx_subscriptions_forum ON subscriptions(forum)",
        "CREATE INDEX IF NOT EXISTS idx_user_subscriptions_forum ON user_subscriptions(forum)",
        "CREATE INDEX IF NOT EXISTS idx_subscribe_all_forum ON subscribe_all(forum)",
        "CREATE INDEX IF NOT EXISTS idx_posts_forum ON posts(forum)",
        "CREATE INDEX IF NOT EXISTS idx_notifications_forum ON notifications(forum)",

        # 更新唯一约束（需要重建表，这里用索引代替）
        # subscriptions: (chat_id, keyword, forum) 唯一
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_subscriptions_unique ON subscriptions(chat_id, keyword, forum)",
        # user_subscriptions: (chat_id, author, forum) 唯一
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_user_subscriptions_unique ON user_subscriptions(chat_id, author, forum)",
    ],

    # 版本 3: 修复主键支持多论坛，移除外键约束
    3: [
        # 1. 重建 users 表
        "CREATE TABLE users_new (chat_id INTEGER NOT NULL, forum TEXT NOT NULL DEFAULT 'linux-do', created_at TEXT NOT NULL, PRIMARY KEY (chat_id, forum))",
        "INSERT OR IGNORE INTO users_new (chat_id, forum, created_at) SELECT chat_id, forum, created_at FROM users",
        "DROP TABLE users",
        "ALTER TABLE users_new RENAME TO users",
        "CREATE INDEX IF NOT EXISTS idx_users_forum ON users(forum)",

        # 2. 重建 subscribe_all 表
        "CREATE TABLE subscribe_all_new (chat_id INTEGER NOT NULL, forum TEXT NOT NULL DEFAULT 'linux-do', created_at TEXT NOT NULL, PRIMARY KEY (chat_id, forum))",
        "INSERT OR IGNORE INTO subscribe_all_new (chat_id, forum, created_at) SELECT chat_id, forum, created_at FROM subscribe_all",
        "DROP TABLE subscribe_all",
        "ALTER TABLE subscribe_all_new RENAME TO subscribe_all",
        "CREATE INDEX IF NOT EXISTS idx_subscribe_all_forum ON subscribe_all(forum)",

        # 3. 重建 subscriptions 表（移除外键）
        "CREATE TABLE subscriptions_new (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, keyword TEXT NOT NULL, forum TEXT NOT NULL DEFAULT 'linux-do', created_at TEXT NOT NULL)",
        "INSERT INTO subscriptions_new (id, chat_id, keyword, forum, created_at) SELECT id, chat_id, keyword, forum, created_at FROM subscriptions",
        "DROP TABLE subscriptions",
        "ALTER TABLE subscriptions_new RENAME TO subscriptions",
        "CREATE INDEX IF NOT EXISTS idx_subscriptions_chat_id ON subscriptions(chat_id)",
        "CREATE INDEX IF NOT EXISTS idx_subscriptions_keyword ON subscriptions(keyword)",
        "CREATE INDEX IF NOT EXISTS idx_subscriptions_forum ON subscriptions(forum)",

        # 4. 重建 user_subscriptions 表（移除外键）
        "CREATE TABLE user_subscriptions_new (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, author TEXT NOT NULL, forum TEXT NOT NULL DEFAULT 'linux-do', created_at TEXT NOT NULL)",
        "INSERT INTO user_subscriptions_new (id, chat_id, author, forum, created_at) SELECT id, chat_id, author, forum, created_at FROM user_subscriptions",
        "DROP TABLE user_subscriptions",
        "ALTER TABLE user_subscriptions_new RENAME TO user_subscriptions",
        "CREATE INDEX IF NOT EXISTS idx_user_subscriptions_chat_id ON user_subscriptions(chat_id)",
        "CREATE INDEX IF NOT EXISTS idx_user_subscriptions_author ON user_subscriptions(author)",
        "CREATE INDEX IF NOT EXISTS idx_user_subscriptions_forum ON user_subscriptions(forum)",

        # 5. 重建 posts 表
        "CREATE TABLE posts_new (id TEXT NOT NULL, forum TEXT NOT NULL DEFAULT 'linux-do', title TEXT NOT NULL, link TEXT NOT NULL, pub_date TEXT NOT NULL, author TEXT, PRIMARY KEY (id, forum))",
        "INSERT OR IGNORE INTO posts_new (id, forum, title, link, pub_date, author) SELECT id, forum, title, link, pub_date, author FROM posts",
        "DROP TABLE posts",
        "ALTER TABLE posts_new RENAME TO posts",
        "CREATE INDEX IF NOT EXISTS idx_posts_pub_date ON posts(pub_date)",
        "CREATE INDEX IF NOT EXISTS idx_posts_forum ON posts(forum)",
        "CREATE INDEX IF NOT EXISTS idx_posts_author ON posts(author)",

        # 6. 重建 notifications 表
        "CREATE TABLE notifications_new (chat_id INTEGER NOT NULL, post_id TEXT NOT NULL, keyword TEXT NOT NULL, forum TEXT NOT NULL DEFAULT 'linux-do', created_at TEXT NOT NULL, PRIMARY KEY (chat_id, post_id, keyword, forum))",
        "INSERT OR IGNORE INTO notifications_new (chat_id, post_id, keyword, forum, created_at) SELECT chat_id, post_id, keyword, forum, created_at FROM notifications",
        "DROP TABLE notifications",
        "ALTER TABLE notifications_new RENAME TO notifications",
        "CREATE INDEX IF NOT EXISTS idx_notifications_chat_post ON notifications(chat_id, post_id)",
        "CREATE INDEX IF NOT EXISTS idx_notifications_post_id ON notifications(post_id)",
        "CREATE INDEX IF NOT EXISTS idx_notifications_forum ON notifications(forum)",
    ],

    # 版本 4: blocked_users 添加 forum 字段
    4: [
        "CREATE TABLE blocked_users_new (chat_id INTEGER NOT NULL, forum TEXT NOT NULL DEFAULT 'linux-do', blocked_at TEXT NOT NULL, PRIMARY KEY (chat_id, forum))",
        "INSERT OR IGNORE INTO blocked_users_new (chat_id, forum, blocked_at) SELECT chat_id, 'linux-do', blocked_at FROM blocked_users",
        "DROP TABLE blocked_users",
        "ALTER TABLE blocked_users_new RENAME TO blocked_users",
        "CREATE INDEX IF NOT EXISTS idx_blocked_users_forum ON blocked_users(forum)",
    ],
}


def get_schema_version(db_path: Path) -> int:
    """获取当前数据库版本"""
    conn = sqlite3.connect(db_path)
    try:
        # 检查 schema_version 表是否存在
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        )
        if not cursor.fetchone():
            # 表不存在，检查是否有 forum 字段来判断版本
            cursor = conn.execute("PRAGMA table_info(users)")
            columns = [row[1] for row in cursor.fetchall()]
            if "forum" in columns:
                # 已经有 forum 字段，说明是版本 2
                return 2
            else:
                # 没有 forum 字段，是版本 1
                return 1

        # 从表中读取版本
        cursor = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
        row = cursor.fetchone()
        return row[0] if row else 1
    finally:
        conn.close()


def set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    """设置数据库版本"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
    """)
    from datetime import datetime
    conn.execute(
        "INSERT OR REPLACE INTO schema_version (version, applied_at) VALUES (?, ?)",
        (version, datetime.now().isoformat())
    )


def migrate(db_path: Path, target_version: int = None) -> Tuple[int, int]:
    """执行数据库迁移

    Args:
        db_path: 数据库文件路径
        target_version: 目标版本，默认为最新版本

    Returns:
        (旧版本, 新版本)
    """
    if target_version is None:
        target_version = CURRENT_VERSION

    current = get_schema_version(db_path)

    if current >= target_version:
        logger.info(f"数据库已是最新版本 (v{current})")
        return current, current

    conn = sqlite3.connect(db_path)
    try:
        for version in range(current + 1, target_version + 1):
            if version not in MIGRATIONS:
                continue

            logger.info(f"执行迁移 v{version - 1} → v{version}...")

            for sql in MIGRATIONS[version]:
                try:
                    conn.execute(sql)
                    logger.debug(f"  执行: {sql[:50]}...")
                except sqlite3.OperationalError as e:
                    # 忽略 "duplicate column" 等错误
                    if "duplicate column" in str(e).lower():
                        logger.debug(f"  跳过（已存在）: {sql[:50]}...")
                    else:
                        raise

            set_schema_version(conn, version)
            conn.commit()
            logger.info(f"  ✅ 迁移到 v{version} 完成")

        return current, target_version
    except Exception as e:
        conn.rollback()
        logger.error(f"迁移失败: {e}")
        raise
    finally:
        conn.close()


def check_migration_needed(db_path: Path) -> Tuple[bool, int, int]:
    """检查是否需要迁移

    Returns:
        (是否需要迁移, 当前版本, 最新版本)
    """
    if not db_path.exists():
        return False, 0, CURRENT_VERSION

    current = get_schema_version(db_path)
    return current < CURRENT_VERSION, current, CURRENT_VERSION
