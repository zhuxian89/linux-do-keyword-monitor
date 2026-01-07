import json
import logging
import re
from datetime import datetime
from typing import List, Optional

import requests as std_requests
from curl_cffi import requests

from ..models import Post
from .base import BaseSource

logger = logging.getLogger(__name__)


def extract_json_from_html(text):
    """从 HTML 中提取 JSON（FlareSolverr 可能返回 <pre>JSON</pre>）"""
    if text.startswith("{"):
        return text
    match = re.search(r'<pre[^>]*>(.*?)</pre>', text, re.DOTALL)
    if match:
        return match.group(1)
    return text


class DiscourseSource(BaseSource):
    """Discourse JSON API data source with cookie authentication"""

    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        base_url: str,
        cookie: str,
        timeout: int = 30,
        user_agent: Optional[str] = None,
        flaresolverr_url: Optional[str] = None
    ):
        # Remove trailing slash
        self.base_url = base_url.rstrip("/")
        self.cookie = cookie
        self.timeout = timeout
        self.user_agent = user_agent or self.DEFAULT_USER_AGENT
        self.flaresolverr_url = flaresolverr_url

    def get_source_name(self) -> str:
        return "Discourse API"

    def fetch(self) -> List[Post]:
        """Fetch posts from Discourse JSON API"""
        url = f"{self.base_url}/latest.json?order=created"

        # 优先使用 FlareSolverr
        if self.flaresolverr_url:
            return self._fetch_via_flaresolverr(url)
        return self._fetch_direct(url)

    def _fetch_via_flaresolverr(self, url: str) -> List[Post]:
        """通过 FlareSolverr 获取数据"""
        try:
            payload = {
                "cmd": "request.get",
                "url": url,
                "maxTimeout": self.timeout * 1000,
                "headers": {"Accept": "application/json"},
            }
            if self.cookie:
                # 支持多种分隔格式
                normalized = self.cookie.replace("\r\n", ";").replace("\n", ";").replace(";;", ";")
                cookies = []
                for item in normalized.split(";"):
                    item = item.strip()
                    if "=" in item:
                        k, v = item.split("=", 1)
                        k = k.strip()
                        if k in ("_t", "_forum_session"):
                            cookies.append({"name": k, "value": v})
                payload["cookies"] = cookies

            resp = std_requests.post(
                f"{self.flaresolverr_url}/v1",
                json=payload,
                timeout=self.timeout + 30
            )
            resp.raise_for_status()
            result = resp.json()

            if result.get("status") != "ok":
                raise Exception(f"FlareSolverr error: {result.get('message')}")

            response_text = extract_json_from_html(result["solution"]["response"])
            data = json.loads(response_text)
            return self._parse_response(data)
        except Exception as e:
            logger.error(f"FlareSolverr 请求失败: {e}")
            raise

    def _fetch_direct(self, url: str) -> List[Post]:
        """直接请求（需要有效的 cf_clearance）"""
        headers = {
            "User-Agent": self.user_agent,
            "Cookie": self.cookie,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": f"{self.base_url}/",
        }

        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=self.timeout,
                impersonate="chrome131"
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
