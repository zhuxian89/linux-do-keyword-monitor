import hashlib
import urllib.request
from datetime import datetime
from typing import List

import feedparser

from ..models import Post
from .base import BaseSource


class RSSSource(BaseSource):
    """RSS feed data source"""

    def __init__(self, url: str, timeout: int = 30):
        self.url = url
        self.timeout = timeout

    def get_source_name(self) -> str:
        return "RSS"

    def fetch(self) -> List[Post]:
        """Fetch and parse RSS feed"""
        content = self._fetch_content()
        return self._parse_content(content)

    def _fetch_content(self) -> str:
        """Fetch RSS content via HTTP"""
        req = urllib.request.Request(
            self.url,
            headers={"User-Agent": "LinuxDoMonitor/1.0"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            return response.read().decode("utf-8")

    def _parse_content(self, content: str) -> List[Post]:
        """Parse RSS content and return list of posts"""
        feed = feedparser.parse(content)
        posts = []

        for entry in feed.entries:
            post_id = self._generate_id(entry)
            pub_date = self._parse_date(entry)

            posts.append(Post(
                id=post_id,
                title=entry.get("title", ""),
                link=entry.get("link", ""),
                pub_date=pub_date
            ))

        return posts

    def _generate_id(self, entry) -> str:
        """Generate unique ID for a post"""
        if entry.get("id"):
            return entry["id"]
        link = entry.get("link", "")
        return hashlib.md5(link.encode()).hexdigest()

    def _parse_date(self, entry) -> datetime:
        """Parse publication date from entry"""
        if entry.get("published_parsed"):
            return datetime(*entry.published_parsed[:6])
        if entry.get("updated_parsed"):
            return datetime(*entry.updated_parsed[:6])
        return datetime.now()
