from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class User:
    """Telegram user model"""
    chat_id: int
    created_at: datetime


@dataclass
class Subscription:
    """User subscription model"""
    id: Optional[int]
    chat_id: int
    keyword: str
    created_at: datetime


@dataclass
class Post:
    """RSS post model"""
    id: str
    title: str
    link: str
    pub_date: datetime


@dataclass
class Notification:
    """Notification record to prevent duplicates"""
    chat_id: int
    post_id: str
    keyword: str
    created_at: datetime
