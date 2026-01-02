import hashlib
from datetime import datetime
from typing import List

import feedparser

from ..models import Post


class RSSParser:
    """Parse RSS feed content"""

    def parse(self, content: str) -> List[Post]:
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
        # Use entry id if available, otherwise hash the link
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
