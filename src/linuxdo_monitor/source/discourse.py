import json
import logging
import os
import re
import time
import uuid
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlparse

import requests as std_requests
from curl_cffi import requests

from ..models import Post
from .rss import RSSSource
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
    _direct_timeout: int = 10  # 直连超时
    _direct_retries: int = 3
    _direct_retry_sleep: int = 2

    def __init__(
        self,
        base_url: str,
        cookie: str,
        timeout: int = 30,
        user_agent: Optional[str] = None,
        flaresolverr_url: Optional[str] = None,
        rss_url: Optional[str] = None,
        cf_bypass_mode: str = "flaresolverr_rss",
        drissionpage_headless: bool = True,
        drissionpage_use_xvfb: bool = True,
        drissionpage_user_data_dir: Optional[str] = None,
        forum_tag: Optional[str] = None
    ):
        # Remove trailing slash
        self.base_url = base_url.rstrip("/")
        self.forum_tag = forum_tag or self.base_url
        self.cookie = cookie
        self.timeout = timeout
        self.user_agent = user_agent or self.DEFAULT_USER_AGENT
        self.flaresolverr_url = flaresolverr_url
        self.rss_url = rss_url
        self.cf_bypass_mode = cf_bypass_mode.value if hasattr(cf_bypass_mode, "value") else cf_bypass_mode
        self.drissionpage_headless = drissionpage_headless
        self.drissionpage_use_xvfb = drissionpage_use_xvfb
        self.drissionpage_user_data_dir = drissionpage_user_data_dir
        self._flaresolverr_session_id: Optional[str] = None
        self._session_created_at: float = 0

    def get_source_name(self) -> str:
        return "Discourse API"

    def fetch(self) -> List[Post]:
        """Fetch posts from Discourse JSON API"""
        url = f"{self.base_url}/latest.json?order=created"

        if self.cf_bypass_mode == "drissionpage":
            return self._fetch_via_drissionpage(url)

        # 优先使用 FlareSolverr
        if self.flaresolverr_url:
            logger.info(f"[{self.forum_tag}][cf] FlareSolverr 模式抓取 JSON")
            return self._fetch_via_flaresolverr(url)

        logger.info(f"[{self.forum_tag}][cf] 直接请求（无 CF 代理）抓取 JSON")
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

    def _fetch_via_flaresolverr(self, url: str, max_retries: int = 3) -> List[Post]:
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
                logger.info(f"[{self.forum_tag}][cf] FlareSolverr 成功 (attempt {attempt}/{max_retries})")
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

        # FlareSolverr 失败，尝试 RSS 兜底
        logger.warning("FlareSolverr 失败，尝试 RSS 兜底...")
        try:
            rss_url = self.rss_url or f"{self.base_url}/latest.rss"
            return RSSSource(url=rss_url, timeout=self.timeout).fetch()
        except Exception as e:
            logger.error(f"RSS 兜底也失败了: {e}")
            raise last_error

    def _fetch_direct(self, url: str, *, allow_refresh: bool = True) -> List[Post]:
        """直接请求（需要有效的 cf_clearance）

        allow_refresh: 是否在 403 时尝试 DrissionPage 刷新一次
        """
        headers = {
            "User-Agent": self.user_agent,
            "Cookie": self.cookie,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": f"{self.base_url}/",
        }

        last_error = None
        for attempt in range(1, self._direct_retries + 1):
            try:
                response = requests.get(
                    url,
                    headers=headers,
                    timeout=self._direct_timeout,
                    impersonate="chrome131"
                )
                response.raise_for_status()
                data = response.json()
                return self._parse_response(data)
            except Exception as e:
                last_error = e
                is_403 = "403" in str(e) or (hasattr(e, "response") and getattr(e, "response", None) and getattr(e.response, "status_code", None) == 403)
                is_timeout = "timed out" in str(e).lower() or "timeout" in str(e).lower()

                if is_403 and allow_refresh and self.cf_bypass_mode == "drissionpage":
                    logger.warning(f"[{self.forum_tag}][cf] 检测到 403，尝试 DrissionPage 刷新后重试一次")
                    refreshed_cookie = self._refresh_cookie_via_drissionpage()
                    if refreshed_cookie:
                        return self._fetch_direct(url, allow_refresh=False)

                if is_403:
                    logger.error(f"[{self.forum_tag}][cf] Cookie 可能已过期或被 Cloudflare 拦截，请更新或刷新 Cookie")
                elif is_timeout:
                    logger.error(f"[{self.forum_tag}][cf] 请求超时（{self._direct_timeout}s），网络或对端限速：{e}")
                else:
                    logger.error(f"[{self.forum_tag}][cf] 请求失败: {e}")

                if attempt < self._direct_retries and not is_403:
                    time.sleep(self._direct_retry_sleep)
                    continue
                raise last_error

    def _fetch_via_drissionpage(self, url: str) -> List[Post]:
        """使用 DrissionPage 刷新 Cookie/CF，再请求 JSON（失败重试 3 次后再兜底）"""
        last_error = None

        # 初次尝试直连
        try:
            logger.info(f"[{self.forum_tag}][cf] DrissionPage 模式：首次直连（不刷新）")
            return self._fetch_direct(url, allow_refresh=False)
        except Exception as e:
            last_error = e
            is_403 = "403" in str(e) or (hasattr(e, "response") and getattr(e, "response", None) and getattr(e.response, "status_code", None) == 403)
            if not is_403:
                logger.warning(f"[{self.forum_tag}][cf] 直连失败但非 403（不刷新）：{e}")
                raise
            logger.warning(f"[{self.forum_tag}][cf] 直连 403，尝试 DrissionPage 刷新 Cookie: {e}")

        # DrissionPage 刷新最多 3 次
        for attempt in range(1, 4):
            logger.info(f"[{self.forum_tag}][cf] DrissionPage 刷新尝试 {attempt}/3")
            refreshed_cookie = self._refresh_cookie_via_drissionpage()
            if not refreshed_cookie:
                logger.warning(f"DrissionPage 刷新未获取到 Cookie (第 {attempt}/3 次)")
                continue

            try:
                return self._fetch_direct(url, allow_refresh=False)
            except Exception as e:
                last_error = e
                logger.warning(f"DrissionPage 刷新后仍失败 (第 {attempt}/3 次): {e}")

        logger.warning("DrissionPage 连续 3 次失败，尝试 RSS 兜底...")
        try:
            rss_url = self.rss_url or f"{self.base_url}/latest.rss"
            return RSSSource(url=rss_url, timeout=self.timeout).fetch()
        except Exception as e:
            logger.error(f"RSS 兜底也失败了: {e}")
            if last_error:
                raise last_error
            raise

    def _refresh_cookie_via_drissionpage(self) -> Optional[str]:
        """用 DrissionPage 刷新 Cookie/CF clearance（仅内存更新）"""
        try:
            from DrissionPage import ChromiumOptions, ChromiumPage
        except Exception as e:
            logger.warning(f"DrissionPage 未安装或不可用: {e}")
            return None

        display = None
        if not self.drissionpage_headless:
            need_xvfb = self.drissionpage_use_xvfb or not os.environ.get("DISPLAY")
            if need_xvfb:
                try:
                    from pyvirtualdisplay import Display
                    display = Display(visible=0, size=(1280, 800))
                    display.start()
                    logger.info(f"[{self.forum_tag}][cf] DrissionPage 启动 Xvfb 虚拟显示")
                except Exception as e:
                    logger.warning(f"Xvfb 启动失败: {e}")
                    if not os.environ.get("DISPLAY"):
                        return None

        options = ChromiumOptions()
        if self.drissionpage_headless:
            try:
                options.headless(True)
            except Exception:
                try:
                    options.set_headless(True)
                except Exception:
                    options.set_argument("--headless=new")

        try:
            options.set_argument("--no-sandbox")
            options.set_argument("--disable-dev-shm-usage")
            options.set_argument("--disable-gpu")
            options.set_argument("--disable-blink-features=AutomationControlled")
            options.set_argument("--window-size=1280,800")
        except Exception:
            pass

        try:
            options.set_argument(f"--user-agent={self.user_agent}")
        except Exception:
            pass

        if self.drissionpage_user_data_dir:
            try:
                options.set_user_data_dir(self.drissionpage_user_data_dir)
            except Exception:
                try:
                    options.set_user_data_path(self.drissionpage_user_data_dir)
                except Exception:
                    logger.warning("DrissionPage 用户数据目录设置失败，使用默认配置")

        page = ChromiumPage(options)
        try:
            cookie_dict = self._cookie_to_dict(self.cookie)
            if cookie_dict:
                self._apply_cookies_to_page(page, cookie_dict)

            page.get(self.base_url)
            time.sleep(2)
            page.get(f"{self.base_url}/latest.json?order=created")
            time.sleep(2)

            self._sync_user_agent_from_page(page)

            if not self._wait_for_cf_clearance(page, timeout=10):
                logger.warning("DrissionPage 未获取到 cf_clearance")
                return None

            refreshed = self._extract_cookies_from_page(page)
            if refreshed:
                self.cookie = refreshed
                cookie_dict = self._cookie_to_dict(refreshed)
                logger.info(
                    f"[{self.forum_tag}][cf] DrissionPage Cookie 刷新成功（_t: {'Y' if '_t' in cookie_dict else 'N'}, "
                    f"_forum_session: {'Y' if '_forum_session' in cookie_dict else 'N'}, "
                    f"cf_clearance: {'Y' if 'cf_clearance' in cookie_dict else 'N'}）"
                )
                return refreshed
            logger.warning("DrissionPage 未获取到有效 Cookie")
        except Exception as e:
            logger.warning(f"DrissionPage 刷新失败: {e}")
        finally:
            self._close_drissionpage(page)
            if display:
                display.stop()

        return None

    def _apply_cookies_to_page(self, page, cookie_dict: dict) -> None:
        """尽量把 Cookie 写入 DrissionPage"""
        domain = urlparse(self.base_url).hostname or ""
        cookie_list = []
        for k, v in cookie_dict.items():
            item = {"name": k, "value": v}
            if domain:
                item["domain"] = domain
            cookie_list.append(item)

        if hasattr(page, "set") and hasattr(page.set, "cookies"):
            try:
                page.set.cookies(cookie_list)
                return
            except Exception:
                try:
                    page.set.cookies(cookie_dict)
                    return
                except Exception:
                    pass

        if hasattr(page, "set_cookies"):
            try:
                page.set_cookies(cookie_list)
                return
            except Exception:
                try:
                    page.set_cookies(cookie_dict)
                    return
                except Exception:
                    pass

    def _extract_cookies_from_page(self, page) -> Optional[str]:
        """从 DrissionPage 中提取 Cookie 字符串"""
        cookie_dict = self._extract_cookie_dict_from_page(page)
        if cookie_dict:
            return self._cookie_dict_to_str(cookie_dict)

        cookies = None
        if hasattr(page, "cookies"):
            cookies = page.cookies() if callable(page.cookies) else page.cookies
        if isinstance(cookies, str):
            return cookies
        return None

    def _extract_cookie_dict_from_page(self, page) -> dict:
        """从 DrissionPage 中提取 Cookie dict"""
        cookies = None
        if hasattr(page, "cookies"):
            cookies = page.cookies() if callable(page.cookies) else page.cookies
        if not cookies:
            return {}

        if isinstance(cookies, dict):
            return cookies
        if isinstance(cookies, list):
            cookie_dict = {}
            for item in cookies:
                if isinstance(item, dict) and "name" in item and "value" in item:
                    cookie_dict[item["name"]] = item["value"]
            return cookie_dict
        return {}

    def _wait_for_cf_clearance(self, page, timeout: int = 10) -> bool:
        """等待 cf_clearance 出现"""
        end = time.time() + timeout
        while time.time() < end:
            cookie_dict = self._extract_cookie_dict_from_page(page)
            if cookie_dict.get("cf_clearance"):
                return True
            time.sleep(1)
        return False

    def _sync_user_agent_from_page(self, page) -> None:
        """尝试从 DrissionPage 同步 UA"""
        ua = None
        if hasattr(page, "user_agent"):
            try:
                ua = page.user_agent
            except Exception:
                pass
        if not ua and hasattr(page, "run_js"):
            try:
                ua = page.run_js("return navigator.userAgent")
            except Exception:
                pass
        if ua:
            self.user_agent = ua

    def _cookie_to_dict(self, cookie: str) -> dict:
        """将 Cookie 字符串解析为 dict"""
        cookie_dict = {}
        if not cookie:
            return cookie_dict
        normalized = cookie.replace("\r\n", ";").replace("\n", ";").replace(";;", ";")
        for item in normalized.split(";"):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                cookie_dict[k.strip()] = v
        return cookie_dict

    def _cookie_dict_to_str(self, cookie_dict: dict) -> str:
        """将 Cookie dict 还原为字符串"""
        return "; ".join(f"{k}={v}" for k, v in cookie_dict.items())

    def _close_drissionpage(self, page) -> None:
        """尽量关闭 DrissionPage"""
        try:
            page.quit()
            return
        except Exception:
            pass
        try:
            page.close()
            return
        except Exception:
            pass
        try:
            page.browser.close()
        except Exception:
            pass

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
