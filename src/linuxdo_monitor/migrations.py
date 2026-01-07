"""Database migration management"""
import logging
import sqlite3
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

# 当前数据库版本
CURRENT_VERSION = 2

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
