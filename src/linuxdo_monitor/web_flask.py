"""Flask-based Web configuration management UI"""
import json
import logging
import re
import threading
from functools import wraps
from pathlib import Path
from typing import Callable, Optional

from flask import Flask, Blueprint, render_template, request, redirect, url_for, jsonify, flash

from .cache import get_cache

logger = logging.getLogger(__name__)


def extract_json_from_html(text):
    """从 HTML 中提取 JSON（FlareSolverr 可能返回 <pre>JSON</pre>）"""
    if text.startswith("{"):
        return text
    match = re.search(r'<pre[^>]*>(.*?)</pre>', text, re.DOTALL)
    if match:
        return match.group(1)
    return text


def normalize_cookie(cookie: str) -> str:
    """标准化 cookie 格式，支持多种分隔格式"""
    return cookie.replace("\r\n", ";").replace("\n", ";").replace(";;", ";")


def extract_needed_cookies(cookie: str) -> dict:
    """从 cookie 字符串中提取需要的字段"""
    needed = {}
    normalized = normalize_cookie(cookie)
    for item in normalized.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            k = k.strip()
            if k in ("_t", "_forum_session"):
                needed[k] = v
    return needed


def test_cookie(cookie: str, base_url: str = "https://linux.do", flaresolverr_url: str = None) -> dict:
    """Test if cookie is valid by checking notifications endpoint

    Returns:
        dict with keys:
        - valid: bool - whether cookie is valid
        - error: str - error message if not valid
        - error_type: str - "service_error" (FlareSolverr/network issue) or "cookie_invalid" (cookie expired)
    """
    try:
        needed_cookies = extract_needed_cookies(cookie)
        url = f"{base_url}/notifications.json"

        if flaresolverr_url:
            import requests as std_requests
            payload = {
                "cmd": "request.get",
                "url": url,
                "maxTimeout": 30000,
                "headers": {"Accept": "application/json"},
            }
            if needed_cookies:
                payload["cookies"] = [{"name": k, "value": v} for k, v in needed_cookies.items()]

            resp = std_requests.post(f"{flaresolverr_url}/v1", json=payload, timeout=60)
            resp.raise_for_status()
            result = resp.json()

            if result.get("status") != "ok":
                return {"valid": False, "error": f"FlareSolverr: {result.get('message')}", "error_type": "service_error"}

            response_text = result["solution"]["response"]
            status_code = result["solution"]["status"]
            response_text = extract_json_from_html(response_text)

            if "<html" in response_text.lower()[:100]:
                if "Just a moment" in response_text:
                    return {"valid": False, "error": "FlareSolverr 未能绕过 Cloudflare", "error_type": "service_error"}
                return {"valid": False, "error": "返回了 HTML 而非 JSON", "error_type": "service_error"}
        else:
            from curl_cffi import requests
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Cookie": cookie,
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": f"{base_url}/",
            }
            response = requests.get(url, headers=headers, timeout=15, impersonate="chrome131")
            response_text = response.text
            status_code = response.status_code

        if status_code == 200:
            data = json.loads(response_text)
            if "errors" in data:
                error_type = data.get("error_type", "")
                if error_type == "not_logged_in":
                    return {"valid": False, "error": "Cookie 无效或已过期", "error_type": "cookie_invalid"}
                return {"valid": False, "error": data["errors"][0] if data["errors"] else "未知错误", "error_type": "cookie_invalid"}
            return {"valid": True, "message": "Cookie 有效，可以正常访问"}
        elif status_code == 403:
            return {"valid": False, "error": "被 Cloudflare 拦截，请配置 FlareSolverr", "error_type": "service_error"}
        else:
            try:
                data = json.loads(response_text)
                if data.get("error_type") == "not_logged_in":
                    return {"valid": False, "error": "Cookie 无效或已过期", "error_type": "cookie_invalid"}
                if "errors" in data:
                    return {"valid": False, "error": data["errors"][0], "error_type": "cookie_invalid"}
            except:
                pass
            return {"valid": False, "error": f"HTTP {status_code}", "error_type": "service_error"}
    except json.JSONDecodeError:
        return {"valid": False, "error": "JSON 解析失败，可能返回了 HTML 页面", "error_type": "service_error"}
    except Exception as e:
        error_str = str(e)
        return {"valid": False, "error": error_str, "error_type": "service_error"}


# Create Blueprint for Linux.do routes
linuxdo_bp = Blueprint('linuxdo', __name__, url_prefix='/linuxdo')


class ConfigWebServer:
    """Flask-based web server for config management"""

    def __init__(self, config_path: Path, port: int = 8080, password: str = "admin", db_path: Optional[Path] = None):
        self.config_path = config_path
        self.port = port
        self.password = password
        self.db_path = db_path
        self.on_config_update: Optional[Callable] = None

        # Create Flask app
        self.app = Flask(__name__,
                        template_folder=Path(__file__).parent / 'templates',
                        static_folder=Path(__file__).parent / 'static')
        self.app.secret_key = password  # For flash messages

        # Store reference to self in app config
        self.app.config['web_server'] = self

        # Setup routes
        self._setup_routes()

    def _load_config(self) -> dict:
        with open(self.config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_config(self, config: dict):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    def set_update_callback(self, callback: Callable):
        """Set callback for config updates"""
        self.on_config_update = callback

    def _setup_routes(self):
        """Setup all Flask routes"""
        app = self.app
        web_server = self

        def require_auth(f):
            """Decorator to require password authentication"""
            @wraps(f)
            def decorated_function(*args, **kwargs):
                pwd = request.args.get('pwd', '')
                if pwd != web_server.password:
                    return "Unauthorized. Add ?pwd=yourpassword to URL", 401
                return f(*args, **kwargs)
            return decorated_function

        @app.context_processor
        def inject_password():
            """Inject password into all templates"""
            return {'password': request.args.get('pwd', '')}

        @app.route('/')
        @require_auth
        def index():
            return render_template('index.html')

        # Register Linux.do blueprint
        self._setup_linuxdo_routes()
        app.register_blueprint(linuxdo_bp)

    def _setup_linuxdo_routes(self):
        """Setup Linux.do specific routes"""
        web_server = self

        def require_auth(f):
            @wraps(f)
            def decorated_function(*args, **kwargs):
                pwd = request.args.get('pwd', '')
                if pwd != web_server.password:
                    return "Unauthorized", 401
                return f(*args, **kwargs)
            return decorated_function

        @linuxdo_bp.route('/')
        @linuxdo_bp.route('/config')
        @require_auth
        def config_page():
            config = web_server._load_config()
            return render_template('linuxdo/config.html', config=config)

        @linuxdo_bp.route('/config/save', methods=['POST'])
        @require_auth
        def save_config():
            config = web_server._load_config()

            # Update config from form
            if request.form.get('bot_token', '').strip():
                config['bot_token'] = request.form['bot_token'].strip()

            config['source_type'] = request.form.get('source_type', 'rss')

            if request.form.get('rss_url', '').strip():
                config['rss_url'] = request.form['rss_url'].strip()

            if request.form.get('discourse_url', '').strip():
                config['discourse_url'] = request.form['discourse_url'].strip()

            # Process cookie
            raw_cookie = request.form.get('discourse_cookie', '')
            if raw_cookie:
                needed = extract_needed_cookies(raw_cookie)
                if needed:
                    config['discourse_cookie'] = "; ".join(f"{k}={v}" for k, v in needed.items())
                else:
                    config['discourse_cookie'] = raw_cookie
            else:
                config['discourse_cookie'] = ""

            try:
                config['fetch_interval'] = int(request.form.get('fetch_interval', 60))
            except ValueError:
                pass

            flaresolverr_url = request.form.get('flaresolverr_url', '').strip()
            config['flaresolverr_url'] = flaresolverr_url if flaresolverr_url else None

            try:
                config['cookie_check_interval'] = int(request.form.get('cookie_check_interval', 300))
            except ValueError:
                pass

            admin_id = request.form.get('admin_chat_id', '').strip()
            if admin_id:
                try:
                    config['admin_chat_id'] = int(admin_id)
                except ValueError:
                    pass
            else:
                config['admin_chat_id'] = None

            # Save config
            web_server._save_config(config)

            # Trigger hot reload
            if web_server.on_config_update:
                try:
                    web_server.on_config_update()
                    flash('配置已保存并热更新成功！', 'success')
                except Exception as e:
                    flash(f'配置已保存，但热更新失败: {e}', 'warning')
            else:
                flash('配置已保存！重启服务后生效。', 'success')

            return redirect(url_for('linuxdo.config_page', pwd=request.args.get('pwd', '')))

        @linuxdo_bp.route('/test-cookie', methods=['GET', 'POST'])
        @require_auth
        def test_cookie_route():
            config = web_server._load_config()
            base_url = config.get('discourse_url', 'https://linux.do')
            flaresolverr_url = config.get('flaresolverr_url')

            if request.method == 'POST':
                cookie = request.form.get('cookie', '')
            else:
                cookie = config.get('discourse_cookie', '')

            if not cookie:
                return jsonify({"valid": False, "error": "Cookie 未配置"})

            result = test_cookie(cookie, base_url, flaresolverr_url)
            return jsonify(result)

        @linuxdo_bp.route('/cache/clear')
        @require_auth
        def clear_cache():
            try:
                cache = get_cache()
                cache.clear_all()
                return jsonify({"success": True, "message": "缓存已清除"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)})

        @linuxdo_bp.route('/users')
        @require_auth
        def users_page():
            if not web_server.db_path or not web_server.db_path.exists():
                flash('数据库未配置或不存在', 'danger')
                return redirect(url_for('linuxdo.config_page', pwd=request.args.get('pwd', '')))

            from .database import Database
            db = Database(web_server.db_path)

            page = int(request.args.get('page', 1))
            page_size = 20

            stats = db.get_stats()
            users, total = db.get_all_users(page=page, page_size=page_size)
            total_pages = (total + page_size - 1) // page_size

            return render_template('linuxdo/users.html',
                                 stats=stats,
                                 users=users,
                                 page=page,
                                 total=total,
                                 total_pages=total_pages)

    def start(self):
        """Start web server in background thread"""
        def run():
            # Disable Flask's default logging
            import logging as log
            log.getLogger('werkzeug').setLevel(log.WARNING)
            self.app.run(host='0.0.0.0', port=self.port, threaded=True, use_reloader=False)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        logger.info(f"🌐 配置管理页面: http://localhost:{self.port}?pwd={self.password}")

    def stop(self):
        """Stop web server (Flask doesn't have a clean shutdown in dev mode)"""
        pass
