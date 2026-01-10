import json
import logging
import re
import time
import uuid
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
        "Chrome/142.0.0.0 Safari/537.36"
    )

    _session_max_age: int = 1800  # session 最长存活 30 分钟
    _flaresolverr_max_timeout_ms: int = 30000  # 30 秒
    _flaresolverr_request_timeout: int = 40  # 留出一点余量
    _flaresolverr_retry_sleep: int = 2

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
        self._flaresolverr_session_id: Optional[str] = None
        self._session_created_at: float = 0

    def get_source_name(self) -> str:
        return "Discourse API"

    def fetch(self) -> List[Post]:
        """Fetch posts from Discourse JSON API"""
        url = f"{self.base_url}/latest.json?order=created"

        # 优先使用 FlareSolverr
        if self.flaresolverr_url:
            return self._fetch_via_flaresolverr(url)
        return self._fetch_direct(url)

    def _get_or_create_session(self) -> Optional[str]:
        """获取或创建 FlareSolverr session"""
        now = time.time()

        # 检查现有 session 是否过期
        if (self._flaresolverr_session_id and
            now - self._session_created_at < self._session_max_age):
            return self._flaresolverr_session_id

        # 创建新 session
        session_id = f"linuxdo_{uuid.uuid4().hex[:8]}"
        try:
            resp = std_requests.post(
                f"{self.flaresolverr_url}/v1",
                json={"cmd": "sessions.create", "session": session_id},
                timeout=30
            )
            if resp.status_code == 200:
                result = resp.json()
                if result.get("status") == "ok":
                    self._flaresolverr_session_id = session_id
                    self._session_created_at = now
                    logger.info(f"FlareSolverr session 创建成功: {session_id}")
                    return session_id
        except Exception as e:
            logger.warning(f"创建 FlareSolverr session 失败: {e}")

        return None

    def _destroy_session(self):
        """销毁当前 session"""
        if not self._flaresolverr_session_id:
            return

        try:
            std_requests.post(
                f"{self.flaresolverr_url}/v1",
                json={"cmd": "sessions.destroy", "session": self._flaresolverr_session_id},
                timeout=10
            )
            logger.info(f"FlareSolverr session 已销毁: {self._flaresolverr_session_id}")
        except Exception:
            pass

        self._flaresolverr_session_id = None
        self._session_created_at = 0

    def _fetch_via_flaresolverr(self, url: str, max_retries: int = 2) -> List[Post]:
        """通过 FlareSolverr 获取数据，使用 session 模式"""
        # 获取或创建 session
        session_id = self._get_or_create_session()

        payload = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": self._flaresolverr_max_timeout_ms,
            "userAgent": self.user_agent,
        }

        # 使用 session
        if session_id:
            payload["session"] = session_id

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

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                resp = std_requests.post(
                    f"{self.flaresolverr_url}/v1",
                    json=payload,
                    timeout=self._flaresolverr_request_timeout
                )
                resp.raise_for_status()
                result = resp.json()

                if result.get("status") != "ok":
                    error_msg = result.get('message', 'Unknown error')
                    # 如果是 session 相关错误，销毁并重试
                    if "session" in error_msg.lower():
                        self._destroy_session()
                    raise Exception(f"FlareSolverr error: {error_msg}")

                response_text = extract_json_from_html(result["solution"]["response"])
                data = json.loads(response_text)
                return self._parse_response(data)
            except Exception as e:
                last_error = e
                logger.warning(f"FlareSolverr 请求失败 (尝试 {attempt}/{max_retries}): {e}")

                # 第一次失败后销毁 session，下次重试会创建新的
                if attempt == 1 and session_id:
                    self._destroy_session()
                    session_id = self._get_or_create_session()
                    if session_id:
                        payload["session"] = session_id

                if attempt < max_retries:
                    time.sleep(self._flaresolverr_retry_sleep)

        # FlareSolverr 失败，尝试 curl_cffi 直接请求
        logger.warning(f"FlareSolverr 失败，尝试 curl_cffi 直接请求...")
        try:
            return self._fetch_direct(url)
        except Exception as e:
            logger.error(f"curl_cffi 也失败了: {e}")
            # 返回 FlareSolverr 的错误
            raise last_error

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
