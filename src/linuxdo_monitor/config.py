import json
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class AppConfig(BaseModel):
    """Application configuration"""
    bot_token: str = Field(description="Telegram Bot Token")
    rss_url: str = Field(
        default="https://linux.do/latest.rss",
        description="RSS feed URL"
    )
    fetch_interval: int = Field(
        default=60,
        description="RSS fetch interval in seconds"
    )


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

    def save(self, config: AppConfig) -> None:
        """Save configuration to file"""
        self.ensure_config_dir()
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(config.model_dump(), f, indent=2, ensure_ascii=False)

    def exists(self) -> bool:
        """Check if configuration file exists"""
        return self.config_path.exists()

    def get_db_path(self) -> Path:
        """Get database file path"""
        self.ensure_config_dir()
        return self.db_path
