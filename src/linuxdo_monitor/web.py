import json
import logging
import threading
from functools import partial
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from .cache import get_cache

logger = logging.getLogger(__name__)


def test_cookie(cookie: str, base_url: str = "https://linux.do") -> dict:
    """Test if cookie is valid by checking notifications endpoint"""
    try:
        from curl_cffi import requests

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Cookie": cookie,
            "Accept": "application/json",
            "Referer": f"{base_url}/",
        }

        # Use /notifications.json - requires login, returns clear error if not logged in
        response = requests.get(
            f"{base_url}/notifications.json",
            headers=headers,
            timeout=10,
            impersonate="chrome120"
        )

        if response.status_code == 200:
            data = response.json()
            # If we get here without error, cookie is valid
            if "errors" in data:
                error_type = data.get("error_type", "")
                if error_type == "not_logged_in":
                    return {"valid": False, "error": "Cookie 无效或已过期"}
                return {"valid": False, "error": data["errors"][0] if data["errors"] else "未知错误"}
            # Success - cookie is valid
            return {
                "valid": True,
                "message": "Cookie 有效，可以正常访问",
            }
        elif response.status_code == 403:
            return {"valid": False, "error": "被 Cloudflare 拦截，请更新 Cookie"}
        else:
            # Try to parse error message
            try:
                data = response.json()
                if data.get("error_type") == "not_logged_in":
                    return {"valid": False, "error": "Cookie 无效或已过期"}
                if "errors" in data:
                    return {"valid": False, "error": data["errors"][0]}
            except:
                pass
            return {"valid": False, "error": f"HTTP {response.status_code}"}
    except Exception as e:
        return {"valid": False, "error": str(e)}


class ConfigWebHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler for config management"""

    def __init__(self, config_path: Path, password: str, on_config_update: Callable, db_path: Optional[Path], *args, **kwargs):
        self.config_path = config_path
        self.password = password
        self.on_config_update = on_config_update
        self.db_path = db_path
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        logger.debug(f"Web: {args[0]}")

    def _send_response(self, code: int, content: str, content_type: str = "text/html; charset=utf-8"):
        content_bytes = content.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content_bytes)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(content_bytes)

    def _check_auth(self) -> bool:
        """Check password from query string"""
        query = urlparse(self.path).query
        params = parse_qs(query)
        pwd = params.get("pwd", [""])[0]
        return pwd == self.password

    def _load_config(self) -> dict:
        with open(self.config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_config(self, config: dict):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    def do_GET(self):
        path = urlparse(self.path).path

        if not self._check_auth():
            self._send_response(401, "Unauthorized. Add ?pwd=yourpassword to URL")
            return

        # Test cookie endpoint (GET tests saved cookie)
        if path == "/test-cookie":
            config = self._load_config()
            cookie = config.get("discourse_cookie", "")
            base_url = config.get("discourse_url", "https://linux.do")

            if not cookie:
                result = {"valid": False, "error": "Cookie 未配置"}
            else:
                result = test_cookie(cookie, base_url)

            self._send_response(200, json.dumps(result, ensure_ascii=False), "application/json")
            return

        # Clear cache endpoint
        if path == "/api/cache/clear":
            try:
                cache = get_cache()
                cache.clear_all()
                result = {"success": True, "message": "缓存已清除"}
            except Exception as e:
                result = {"success": False, "error": str(e)}
            self._send_response(200, json.dumps(result, ensure_ascii=False), "application/json")
            return

        # Users page
        if path == "/users":
            self._serve_users_page()
            return

        # Main page
        config = self._load_config()
        cookie_display = config.get("discourse_cookie", "")[:50] + "..." if config.get("discourse_cookie") else "未设置"

        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Linux.do Monitor 配置</title>
    <style>
        body {{ font-family: -apple-system, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }}
        h1 {{ color: #333; }}
        .field {{ margin: 20px 0; }}
        label {{ display: block; margin-bottom: 5px; font-weight: bold; }}
        input, select, textarea {{ width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; }}
        textarea {{ height: 150px; font-family: monospace; font-size: 12px; }}
        button {{ background: #007bff; color: white; padding: 12px 24px; border: none; border-radius: 4px; cursor: pointer; margin-top: 10px; margin-right: 10px; }}
        button:hover {{ background: #0056b3; }}
        .btn-test {{ background: #28a745; }}
        .btn-test:hover {{ background: #1e7e34; }}
        .info {{ background: #f8f9fa; padding: 10px; border-radius: 4px; margin: 10px 0; }}
        .success {{ background: #d4edda; color: #155724; padding: 10px; border-radius: 4px; }}
        .error {{ background: #f8d7da; color: #721c24; padding: 10px; border-radius: 4px; }}
        .warning {{ background: #fff3cd; color: #856404; padding: 10px; border-radius: 4px; }}
        #test-result {{ margin-top: 10px; display: none; }}
    </style>
</head>
<body>
    <h1>Linux.do Monitor 配置</h1>

    <div class="info">
        <strong>当前状态:</strong><br>
        数据源: {config.get('source_type', 'rss')}<br>
        Cookie: {cookie_display}<br><br>
        <a href="/users?pwd={self.password}">📊 查看用户统计</a>
        &nbsp;|&nbsp;
        <a href="#" onclick="clearCache(); return false;">🔄 刷新缓存</a>
        <div id="cache-result" style="margin-top: 10px; display: none;"></div>
    </div>

    <form method="POST" action="?pwd={self.password}">
        <div class="field">
            <label>数据源类型</label>
            <select name="source_type">
                <option value="rss" {"selected" if config.get("source_type") == "rss" else ""}>RSS (公开内容)</option>
                <option value="discourse" {"selected" if config.get("source_type") == "discourse" else ""}>Discourse API (需要Cookie)</option>
            </select>
        </div>

        <div class="field">
            <label>Discourse Cookie</label>
            <textarea name="discourse_cookie" id="cookie-input" placeholder="粘贴完整的 Cookie 值...">{config.get('discourse_cookie', '')}</textarea>
            <small>从浏览器开发者工具复制完整 Cookie</small>
            <br>
            <button type="button" class="btn-test" onclick="testCookie()">测试 Cookie 有效性</button>
            <div id="test-result"></div>
        </div>

        <div class="field">
            <label>拉取间隔 (秒)</label>
            <input type="number" name="fetch_interval" value="{config.get('fetch_interval', 60)}">
        </div>

        <div class="field">
            <label>管理员 Chat ID</label>
            <input type="number" name="admin_chat_id" value="{config.get('admin_chat_id', '') or ''}" placeholder="可选，用于接收系统告警">
            <small>Cookie 失效时会发送告警到此 ID。可通过 @userinfobot 获取你的 Chat ID</small>
        </div>

        <button type="submit">保存并应用</button>
    </form>

    <script>
        async function testCookie() {{
            const resultDiv = document.getElementById('test-result');
            const cookieInput = document.getElementById('cookie-input');
            const cookie = cookieInput.value.trim();

            resultDiv.style.display = 'block';

            if (!cookie) {{
                resultDiv.className = 'error';
                resultDiv.innerHTML = '❌ 请先输入 Cookie';
                return;
            }}

            resultDiv.className = 'warning';
            resultDiv.innerHTML = '正在测试...';

            try {{
                const response = await fetch('/test-cookie?pwd={self.password}', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
                    body: 'cookie=' + encodeURIComponent(cookie)
                }});

                if (!response.ok) {{
                    resultDiv.className = 'error';
                    resultDiv.innerHTML = '❌ HTTP错误: ' + response.status;
                    return;
                }}

                const text = await response.text();
                let data;
                try {{
                    data = JSON.parse(text);
                }} catch (parseErr) {{
                    resultDiv.className = 'error';
                    resultDiv.innerHTML = '❌ 解析响应失败: ' + text.substring(0, 100);
                    return;
                }}

                if (data.valid) {{
                    resultDiv.className = 'success';
                    resultDiv.innerHTML = '✅ ' + (data.message || 'Cookie 有效！');
                }} else {{
                    resultDiv.className = 'error';
                    resultDiv.innerHTML = '❌ ' + (data.error || '未知错误');
                }}
            }} catch (e) {{
                resultDiv.className = 'error';
                resultDiv.innerHTML = '❌ 测试失败: ' + e.message;
            }}
        }}

        async function clearCache() {{
            const resultDiv = document.getElementById('cache-result');
            resultDiv.style.display = 'block';
            resultDiv.className = 'warning';
            resultDiv.innerHTML = '正在清除缓存...';

            try {{
                const response = await fetch('/api/cache/clear?pwd={self.password}');
                const data = await response.json();

                if (data.success) {{
                    resultDiv.className = 'success';
                    resultDiv.innerHTML = '✅ ' + data.message;
                }} else {{
                    resultDiv.className = 'error';
                    resultDiv.innerHTML = '❌ ' + data.error;
                }}
            }} catch (e) {{
                resultDiv.className = 'error';
                resultDiv.innerHTML = '❌ 清除失败: ' + e.message;
            }}

            // Auto hide after 3 seconds
            setTimeout(() => {{
                resultDiv.style.display = 'none';
            }}, 3000);
        }}
    </script>
</body>
</html>"""
        self._send_response(200, html)

    def _serve_users_page(self):
        """Serve users management page with pagination"""
        # Load database
        if not self.db_path or not self.db_path.exists():
            self._send_response(500, "数据库未配置或不存在")
            return

        # Get page parameter
        query = urlparse(self.path).query
        params = parse_qs(query)
        page = int(params.get("page", ["1"])[0])
        page_size = 20

        from .database import Database
        db = Database(self.db_path)
        stats = db.get_stats()
        users, total = db.get_all_users(page=page, page_size=page_size)

        # Calculate pagination
        total_pages = (total + page_size - 1) // page_size
        has_prev = page > 1
        has_next = page < total_pages

        # Build users table rows
        user_rows = ""
        for user in users:
            keywords_display = user["keywords"][:50] + "..." if len(user["keywords"]) > 50 else user["keywords"]
            subscribe_all_badge = '<span style="color: #28a745;">✓ 全部</span>' if user["is_subscribe_all"] else ""
            user_rows += f"""
            <tr>
                <td><code>{user["chat_id"]}</code></td>
                <td>{user["created_at"][:10]}</td>
                <td>{user["keyword_count"]} {subscribe_all_badge}</td>
                <td title="{user["keywords"]}">{keywords_display or "-"}</td>
                <td>{user["notification_count"]}</td>
            </tr>"""

        # Build pagination links
        pagination_html = ""
        if total_pages > 1:
            pagination_html = '<div class="pagination">'
            if has_prev:
                pagination_html += f'<a href="/users?pwd={self.password}&page={page-1}">← 上一页</a>'
            pagination_html += f'<span class="page-info">第 {page} / {total_pages} 页 (共 {total} 条)</span>'
            if has_next:
                pagination_html += f'<a href="/users?pwd={self.password}&page={page+1}">下一页 →</a>'
            pagination_html += '</div>'

        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>用户统计 - Linux.do Monitor</title>
    <style>
        body {{ font-family: -apple-system, sans-serif; max-width: 900px; margin: 50px auto; padding: 20px; }}
        h1 {{ color: #333; }}
        .stats {{ display: flex; gap: 20px; flex-wrap: wrap; margin: 20px 0; }}
        .stat-card {{ background: #f8f9fa; padding: 20px; border-radius: 8px; min-width: 120px; text-align: center; }}
        .stat-card .number {{ font-size: 32px; font-weight: bold; color: #007bff; }}
        .stat-card .label {{ color: #666; margin-top: 5px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background: #f8f9fa; font-weight: bold; }}
        tr:hover {{ background: #f5f5f5; }}
        code {{ background: #e9ecef; padding: 2px 6px; border-radius: 3px; }}
        a {{ color: #007bff; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .back {{ margin-bottom: 20px; }}
        .pagination {{ margin-top: 20px; display: flex; justify-content: center; align-items: center; gap: 20px; }}
        .pagination a {{ padding: 8px 16px; background: #007bff; color: white; border-radius: 4px; }}
        .pagination a:hover {{ background: #0056b3; text-decoration: none; }}
        .page-info {{ color: #666; }}
    </style>
</head>
<body>
    <div class="back">
        <a href="/?pwd={self.password}">← 返回配置页面</a>
    </div>

    <h1>📊 用户统计</h1>

    <div class="stats">
        <div class="stat-card">
            <div class="number">{stats["user_count"]}</div>
            <div class="label">总用户数</div>
        </div>
        <div class="stat-card">
            <div class="number">{stats["subscribe_all_count"]}</div>
            <div class="label">订阅全部</div>
        </div>
        <div class="stat-card">
            <div class="number">{stats["keyword_count"]}</div>
            <div class="label">关键词数</div>
        </div>
        <div class="stat-card">
            <div class="number">{stats["subscription_count"]}</div>
            <div class="label">总订阅数</div>
        </div>
        <div class="stat-card">
            <div class="number">{stats["post_count"]}</div>
            <div class="label">已处理帖子</div>
        </div>
        <div class="stat-card">
            <div class="number">{stats["notification_count"]}</div>
            <div class="label">已发送通知</div>
        </div>
    </div>

    <h2>用户列表</h2>
    <table>
        <thead>
            <tr>
                <th>Chat ID</th>
                <th>注册时间</th>
                <th>订阅数</th>
                <th>关键词</th>
                <th>收到通知</th>
            </tr>
        </thead>
        <tbody>
            {user_rows if user_rows else "<tr><td colspan='5' style='text-align:center;color:#999;'>暂无用户</td></tr>"}
        </tbody>
    </table>
    {pagination_html}
</body>
</html>"""
        self._send_response(200, html)

    def do_POST(self):
        if not self._check_auth():
            self._send_response(401, "Unauthorized")
            return

        path = urlparse(self.path).path
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length).decode("utf-8")
        params = parse_qs(post_data)

        # Test cookie endpoint (POST tests provided cookie from input)
        if path == "/test-cookie":
            cookie = params.get("cookie", [""])[0]
            config = self._load_config()
            base_url = config.get("discourse_url", "https://linux.do")

            if not cookie:
                result = {"valid": False, "error": "请输入 Cookie"}
            else:
                result = test_cookie(cookie, base_url)

            self._send_response(200, json.dumps(result, ensure_ascii=False), "application/json")
            return

        config = self._load_config()

        # Update config
        if "source_type" in params:
            config["source_type"] = params["source_type"][0]
        if "discourse_cookie" in params:
            config["discourse_cookie"] = params["discourse_cookie"][0]
        if "fetch_interval" in params:
            try:
                config["fetch_interval"] = int(params["fetch_interval"][0])
            except ValueError:
                pass
        if "admin_chat_id" in params:
            admin_id = params["admin_chat_id"][0].strip()
            if admin_id:
                try:
                    config["admin_chat_id"] = int(admin_id)
                except ValueError:
                    pass
            else:
                config["admin_chat_id"] = None

        self._save_config(config)

        # Trigger hot reload
        if self.on_config_update:
            try:
                self.on_config_update()
                message = "配置已保存并热更新成功！"
                msg_class = "success"
            except Exception as e:
                message = f"配置已保存，但热更新失败: {e}"
                msg_class = "error"
        else:
            message = "配置已保存！重启服务后生效。"
            msg_class = "success"

        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>保存成功</title>
    <style>
        body {{ font-family: -apple-system, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; text-align: center; }}
        .{msg_class} {{ background: {"#d4edda" if msg_class == "success" else "#f8d7da"}; color: {"#155724" if msg_class == "success" else "#721c24"}; padding: 20px; border-radius: 4px; margin: 20px 0; }}
        a {{ color: #007bff; }}
    </style>
</head>
<body>
    <div class="{msg_class}">{message}</div>
    <a href="?pwd={self.password}">返回配置页面</a>
</body>
</html>"""
        self._send_response(200, html)


class ConfigWebServer:
    """Lightweight web server for config management"""

    def __init__(self, config_path: Path, port: int = 8080, password: str = "admin", db_path: Optional[Path] = None):
        self.config_path = config_path
        self.port = port
        self.password = password
        self.db_path = db_path
        self.server: Optional[HTTPServer] = None
        self.on_config_update: Optional[Callable] = None

    def set_update_callback(self, callback: Callable):
        """Set callback for config updates"""
        self.on_config_update = callback

    def start(self):
        """Start web server in background thread"""
        handler = partial(
            ConfigWebHandler,
            self.config_path,
            self.password,
            self.on_config_update,
            self.db_path
        )
        self.server = HTTPServer(("0.0.0.0", self.port), handler)

        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        logger.info(f"🌐 配置管理页面: http://localhost:{self.port}?pwd={self.password}")

    def stop(self):
        """Stop web server"""
        if self.server:
            self.server.shutdown()
