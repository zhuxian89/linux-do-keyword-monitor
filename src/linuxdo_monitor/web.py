import json
import logging
import threading
from functools import partial
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

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

    def __init__(self, config_path: Path, password: str, on_config_update: Callable, *args, **kwargs):
        self.config_path = config_path
        self.password = password
        self.on_config_update = on_config_update
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        logger.debug(f"Web: {args[0]}")

    def _send_response(self, code: int, content: str, content_type: str = "text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(content.encode("utf-8"))

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
        Cookie: {cookie_display}
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
                const data = await response.json();

                if (data.valid) {{
                    resultDiv.className = 'success';
                    resultDiv.innerHTML = '✅ ' + (data.message || 'Cookie 有效！');
                }} else {{
                    resultDiv.className = 'error';
                    resultDiv.innerHTML = '❌ ' + data.error;
                }}
            }} catch (e) {{
                resultDiv.className = 'error';
                resultDiv.innerHTML = '❌ 测试失败: ' + e.message;
            }}
        }}
    </script>
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

    def __init__(self, config_path: Path, port: int = 8080, password: str = "admin"):
        self.config_path = config_path
        self.port = port
        self.password = password
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
            self.on_config_update
        )
        self.server = HTTPServer(("0.0.0.0", self.port), handler)

        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        logger.info(f"🌐 配置管理页面: http://localhost:{self.port}?pwd={self.password}")

    def stop(self):
        """Stop web server"""
        if self.server:
            self.server.shutdown()
