import json
import logging
from datetime import datetime
from typing import List, Optional

from curl_cffi import requests

from ..models import Post
from .base import BaseSource

logger = logging.getLogger(__name__)


class DiscourseSource(BaseSource):
    """Discourse JSON API data source with cookie authentication"""

    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        base_url: str,
        cookie: str,
        timeout: int = 30,
        user_agent: Optional[str] = None
    ):
        # Remove trailing slash
        self.base_url = base_url.rstrip("/")
        self.cookie = cookie
        self.timeout = timeout
        self.user_agent = user_agent or self.DEFAULT_USER_AGENT

    def get_source_name(self) -> str:
        return "Discourse API"

    def fetch(self) -> List[Post]:
        """Fetch posts from Discourse JSON API"""
        url = f"{self.base_url}/latest.json?order=created"

        headers = {
            "User-Agent": self.user_agent,
            "Cookie": self.cookie,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": f"{self.base_url}/",
        }

        try:
            # Use curl_cffi with Chrome impersonation to bypass Cloudflare
            response = requests.get(
                url,
                headers=headers,
                timeout=self.timeout,
                impersonate="chrome120"
            )
            response.raise_for_status()
            data = response.json()
            return self._parse_response(data)
        except Exception as e:
            if "403" in str(e):
                logger.error("Cookie 可能已过期或被 Cloudflare 拦截，请更新 Cookie")
            else:
                logger.error(f"请求失败: {e}")
            raise

    def _parse_response(self, data: dict) -> List[Post]:
        """Parse Discourse JSON response"""
        posts = []
        topics = data.get("topic_list", {}).get("topics", [])

        # Build user id to username mapping
        users = data.get("users", [])
        user_map = {user.get("id"): user.get("username") for user in users}

        for topic in topics:
            post_id = str(topic.get("id", ""))
            title = topic.get("title", "")
            slug = topic.get("slug", "")

            # Build link
            link = f"{self.base_url}/t/{slug}/{post_id}"

            # Parse date
            created_at = topic.get("created_at", "")
            pub_date = self._parse_date(created_at)

            # Parse author from posters (first poster is the author)
            author = None
            posters = topic.get("posters", [])
            if posters:
                # First poster with description containing "原始发帖人" or "Original Poster" is the author
                for poster in posters:
                    desc = poster.get("description", "")
                    if "原始发帖人" in desc or "Original Poster" in desc:
                        user_id = poster.get("user_id")
                        author = user_map.get(user_id)
                        break
                # Fallback to first poster
                if not author and posters:
                    user_id = posters[0].get("user_id")
                    author = user_map.get(user_id)

            posts.append(Post(
                id=post_id,
                title=title,
                link=link,
                pub_date=pub_date,
                author=author
            ))

        return posts

    def _parse_date(self, date_str: str) -> datetime:
        """Parse ISO format date string"""
        if not date_str:
            return datetime.now()
        try:
            # Handle ISO format: 2024-01-02T12:34:56.789Z
            date_str = date_str.replace("Z", "+00:00")
            return datetime.fromisoformat(date_str.replace("+00:00", ""))
        except ValueError:
            return datetime.now()
