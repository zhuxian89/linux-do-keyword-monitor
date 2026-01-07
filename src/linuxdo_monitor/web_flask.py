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
            except (json.JSONDecodeError, TypeError, ValueError):
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
            config = web_server._load_config()
            forums = config.get('forums', [])
            # Legacy format support
            if not forums and config.get('bot_token'):
                forums = [{'forum_id': 'linux-do', 'name': 'Linux.do', 'enabled': True}]
            return render_template('index.html', forums=forums)

        @app.route('/forum/add', methods=['GET', 'POST'])
        @require_auth
        def add_forum():
            if request.method == 'POST':
                config = web_server._load_config()
                forums = config.get('forums', [])

                # Get form data
                forum_id = request.form.get('forum_id', '').strip().lower()
                name = request.form.get('name', '').strip()
                bot_token = request.form.get('bot_token', '').strip()
                source_type = request.form.get('source_type', 'rss')
                rss_url = request.form.get('rss_url', '').strip()
                discourse_url = request.form.get('discourse_url', '').strip()
                fetch_interval = int(request.form.get('fetch_interval', 60))

                # Validate
                if not forum_id or not name or not bot_token:
                    flash('论坛ID、名称和Bot Token为必填项', 'danger')
                    return redirect(url_for('add_forum', pwd=request.args.get('pwd', '')))

                # Check duplicate
                for f in forums:
                    if f.get('forum_id') == forum_id:
                        flash(f'论坛ID "{forum_id}" 已存在', 'danger')
                        return redirect(url_for('add_forum', pwd=request.args.get('pwd', '')))

                # Create new forum config
                new_forum = {
                    'forum_id': forum_id,
                    'name': name,
                    'bot_token': bot_token,
                    'source_type': source_type,
                    'rss_url': rss_url or f'https://{forum_id}.com/latest.rss',
                    'discourse_url': discourse_url or f'https://{forum_id}.com',
                    'discourse_cookie': None,
                    'flaresolverr_url': None,
                    'fetch_interval': fetch_interval,
                    'cookie_check_interval': 0,
                    'enabled': True
                }

                forums.append(new_forum)
                config['forums'] = forums
                web_server._save_config(config)

                flash(f'论坛 "{name}" 添加成功！重启服务后生效。', 'success')
                return redirect(url_for('index', pwd=request.args.get('pwd', '')))

            return render_template('add_forum.html')

        @app.route('/forum/delete/<forum_id>', methods=['POST'])
        @require_auth
        def delete_forum(forum_id):
            config = web_server._load_config()
            forums = config.get('forums', [])

            # Find and remove forum
            new_forums = [f for f in forums if f.get('forum_id') != forum_id]

            if len(new_forums) == len(forums):
                flash(f'论坛 "{forum_id}" 不存在', 'danger')
            else:
                config['forums'] = new_forums
                web_server._save_config(config)
                flash(f'论坛 "{forum_id}" 已删除！重启服务后生效。', 'success')

            return redirect(url_for('index', pwd=request.args.get('pwd', '')))

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

        def get_forum_config(config: dict, forum_id: str = None) -> tuple:
            """Get forum config from config dict.

            Returns:
                (forum_config, forum_index, is_legacy)
            """
            forums = config.get('forums', [])
            if forums:
                # Multi-forum format
                if forum_id:
                    for i, f in enumerate(forums):
                        if f.get('forum_id') == forum_id:
                            return f, i, False
                # Default to first forum
                return forums[0] if forums else None, 0, False
            else:
                # Legacy format - return config itself
                return config, -1, True

        @linuxdo_bp.route('/')
        @linuxdo_bp.route('/config')
        @require_auth
        def config_page():
            config = web_server._load_config()
            forum_id = request.args.get('forum_id')
            forum_config, forum_index, is_legacy = get_forum_config(config, forum_id)

            # Get list of all forums for navigation
            forums = config.get('forums', [])
            if not forums and config.get('bot_token'):
                # Legacy format
                forums = [{'forum_id': 'linux-do', 'name': 'Linux.do'}]

            return render_template('linuxdo/config.html',
                                 config=config,
                                 forum_config=forum_config,
                                 forum_id=forum_config.get('forum_id', 'linux-do') if forum_config else 'linux-do',
                                 forums=forums,
                                 is_legacy=is_legacy)

        @linuxdo_bp.route('/config/save', methods=['POST'])
        @require_auth
        def save_config():
            config = web_server._load_config()
            forum_id = request.args.get('forum_id') or request.form.get('forum_id', 'linux-do')

            forums = config.get('forums', [])
            is_legacy = not forums and config.get('bot_token')

            if is_legacy:
                # Legacy format - update config directly
                target = config
            else:
                # Multi-forum format - find or create forum
                target = None
                for f in forums:
                    if f.get('forum_id') == forum_id:
                        target = f
                        break
                if not target:
                    # Create new forum config
                    target = {'forum_id': forum_id, 'name': forum_id, 'enabled': True}
                    forums.append(target)
                    config['forums'] = forums

            # Update forum config from form
            # Name
            if request.form.get('name', '').strip():
                target['name'] = request.form['name'].strip()

            # Enabled status (checkbox)
            target['enabled'] = 'enabled' in request.form

            if request.form.get('bot_token', '').strip():
                target['bot_token'] = request.form['bot_token'].strip()

            target['source_type'] = request.form.get('source_type', 'rss')

            if request.form.get('rss_url', '').strip():
                target['rss_url'] = request.form['rss_url'].strip()

            if request.form.get('discourse_url', '').strip():
                target['discourse_url'] = request.form['discourse_url'].strip()

            # Process cookie
            raw_cookie = request.form.get('discourse_cookie', '')
            if raw_cookie:
                needed = extract_needed_cookies(raw_cookie)
                if needed:
                    target['discourse_cookie'] = "; ".join(f"{k}={v}" for k, v in needed.items())
                else:
                    target['discourse_cookie'] = raw_cookie
            else:
                target['discourse_cookie'] = ""

            try:
                target['fetch_interval'] = int(request.form.get('fetch_interval', 60))
            except ValueError:
                pass

            flaresolverr_url = request.form.get('flaresolverr_url', '').strip()
            target['flaresolverr_url'] = flaresolverr_url if flaresolverr_url else None

            try:
                target['cookie_check_interval'] = int(request.form.get('cookie_check_interval', 300))
            except ValueError:
                pass

            # Update global admin_chat_id
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

            return redirect(url_for('linuxdo.config_page', pwd=request.args.get('pwd', ''), forum_id=forum_id))

        @linuxdo_bp.route('/test-cookie', methods=['GET', 'POST'])
        @require_auth
        def test_cookie_route():
            config = web_server._load_config()
            forum_id = request.args.get('forum_id')
            forum_config, _, is_legacy = get_forum_config(config, forum_id)

            if forum_config:
                base_url = forum_config.get('discourse_url', 'https://linux.do')
                flaresolverr_url = forum_config.get('flaresolverr_url')
                default_cookie = forum_config.get('discourse_cookie', '')
            else:
                base_url = 'https://linux.do'
                flaresolverr_url = None
                default_cookie = ''

            if request.method == 'POST':
                cookie = request.form.get('cookie', '')
            else:
                cookie = default_cookie

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

            forum_id = request.args.get('forum_id', 'linux-do')
            page = int(request.args.get('page', 1))
            page_size = 20

            stats = db.get_stats(forum=forum_id)
            users, total = db.get_all_users(forum=forum_id, page=page, page_size=page_size)
            total_pages = (total + page_size - 1) // page_size

            # Get list of forums for navigation
            config = web_server._load_config()
            forums = config.get('forums', [])
            if not forums and config.get('bot_token'):
                forums = [{'forum_id': 'linux-do', 'name': 'Linux.do'}]

            return render_template('linuxdo/users.html',
                                 stats=stats,
                                 users=users,
                                 page=page,
                                 total=total,
                                 total_pages=total_pages,
                                 forum_id=forum_id,
                                 forums=forums)

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
