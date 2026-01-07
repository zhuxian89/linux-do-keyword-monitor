import json
from enum import Enum
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


class SourceType(str, Enum):
    """Data source type"""
    RSS = "rss"
    DISCOURSE = "discourse"


class ForumConfig(BaseModel):
    """Single forum configuration"""
    forum_id: str = Field(description="Unique forum identifier (e.g. 'linux-do', 'nodeseek')")
    name: str = Field(description="Display name for the forum")
    bot_token: str = Field(description="Telegram Bot Token for this forum")

    # Data source configuration
    source_type: SourceType = Field(
        default=SourceType.RSS,
        description="Data source type: rss or discourse"
    )

    # RSS source config
    rss_url: str = Field(
        default="",
        description="RSS feed URL"
    )

    # Discourse source config
    discourse_url: str = Field(
        default="",
        description="Discourse base URL"
    )
    discourse_cookie: Optional[str] = Field(
        default=None,
        description="Discourse cookie for authentication"
    )

    flaresolverr_url: Optional[str] = Field(
        default=None,
        description="FlareSolverr URL for bypassing Cloudflare"
    )

    cookie_check_interval: int = Field(
        default=300,
        description="Cookie check interval in seconds (0 to disable)"
    )

    fetch_interval: int = Field(
        default=60,
        description="Fetch interval in seconds"
    )

    enabled: bool = Field(
        default=True,
        description="Whether this forum is enabled"
    )


class AppConfig(BaseModel):
    """Application configuration - supports both legacy and multi-forum formats"""

    # Multi-forum configuration
    forums: List[ForumConfig] = Field(
        default_factory=list,
        description="List of forum configurations"
    )

    # Global admin configuration
    admin_chat_id: Optional[int] = Field(
        default=None,
        description="Admin chat ID for receiving alerts"
    )

    # Legacy fields (for backward compatibility)
    bot_token: Optional[str] = Field(default=None, description="Legacy: Telegram Bot Token")
    source_type: Optional[SourceType] = Field(default=None, description="Legacy: Data source type")
    rss_url: Optional[str] = Field(default=None, description="Legacy: RSS feed URL")
    discourse_url: Optional[str] = Field(default=None, description="Legacy: Discourse base URL")
    discourse_cookie: Optional[str] = Field(default=None, description="Legacy: Discourse cookie")
    flaresolverr_url: Optional[str] = Field(default=None, description="Legacy: FlareSolverr URL")
    cookie_check_interval: Optional[int] = Field(default=None, description="Legacy: Cookie check interval")
    fetch_interval: Optional[int] = Field(default=None, description="Legacy: Fetch interval")

    @model_validator(mode='after')
    def convert_legacy_config(self) -> 'AppConfig':
        """Convert legacy single-forum config to multi-forum format"""
        # If forums is empty but legacy fields are present, convert
        if not self.forums and self.bot_token:
            legacy_forum = ForumConfig(
                forum_id="linux-do",
                name="Linux.do",
                bot_token=self.bot_token,
                source_type=self.source_type or SourceType.RSS,
                rss_url=self.rss_url or "https://linux.do/latest.rss",
                discourse_url=self.discourse_url or "https://linux.do",
                discourse_cookie=self.discourse_cookie,
                flaresolverr_url=self.flaresolverr_url,
                cookie_check_interval=self.cookie_check_interval or 300,
                fetch_interval=self.fetch_interval or 60,
                enabled=True,
            )
            self.forums = [legacy_forum]
        return self

    def is_legacy_format(self) -> bool:
        """Check if config is in legacy format"""
        return self.bot_token is not None and len(self.forums) <= 1

    def get_forum(self, forum_id: str) -> Optional[ForumConfig]:
        """Get forum config by ID"""
        for forum in self.forums:
            if forum.forum_id == forum_id:
                return forum
        return None

    def get_enabled_forums(self) -> List[ForumConfig]:
        """Get all enabled forums"""
        return [f for f in self.forums if f.enabled]


class ConfigManager:
    """Manages application configuration"""

    CONFIG_FILE = "config.json"
    DB_FILE = "data.db"

    def __init__(self, config_dir: Optional[Path] = None):
        # Default to current working directory
        self.config_dir = Path(config_dir) if config_dir else Path.cwd()
        self.config_path = self.config_dir / self.CONFIG_FILE
        self.db_path = self.config_dir / self.DB_FILE

    def ensure_config_dir(self) -> None:
        """Ensure config directory exists"""
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> Optional[AppConfig]:
        """Load configuration from file"""
        if not self.config_path.exists():
            return None
        with open(self.config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return AppConfig.model_validate(data)

    def load_raw(self) -> Optional[dict]:
        """Load raw configuration as dict"""
        if not self.config_path.exists():
            return None
        with open(self.config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save(self, config: AppConfig) -> None:
        """Save configuration to file"""
        self.ensure_config_dir()
        with open(self.config_path, "w", encoding="utf-8") as f:
            # Only save non-None fields
            data = config.model_dump(exclude_none=True)
            json.dump(data, f, indent=2, ensure_ascii=False)

    def save_raw(self, data: dict) -> None:
        """Save raw configuration dict"""
        self.ensure_config_dir()
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def exists(self) -> bool:
        """Check if configuration file exists"""
        return self.config_path.exists()

    def get_db_path(self) -> Path:
        """Get database file path"""
        self.ensure_config_dir()
        return self.db_path
