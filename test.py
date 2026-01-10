import json
import os
import re
import shutil
import time
from urllib.parse import urlparse

import requests

FLARESOLVERR_URL = "http://localhost:8191"
BASE_URL = "https://linux.do"

# 从 config.json 读取 cookie
def load_cookie():
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)
            forums = config.get("forums") or []
            if forums:
                return forums[0].get("discourse_cookie", "")
            return config.get("discourse_cookie", "")
    except Exception:
        return ""

COOKIE = load_cookie()

def extract_json_from_html(text):
    """从 HTML 中提取 JSON（FlareSolverr 可能返回 <pre>JSON</pre>）"""
    if text.startswith("{"):
        return text
    match = re.search(r'<pre[^>]*>(.*?)</pre>', text, re.DOTALL)
    if match:
        return match.group(1)
    return text

def extract_cookies(cookie_str):
    """提取需要的 cookie"""
    cookies = []
    for item in cookie_str.split("; "):
        if "=" in item:
            k, v = item.split("=", 1)
            if k in ("_t", "_forum_session"):
                cookies.append({"name": k, "value": v})
    return cookies

def cookie_str_to_dict(cookie_str):
    """将 Cookie 字符串解析为 dict"""
    cookie_dict = {}
    if not cookie_str:
        return cookie_dict
    normalized = cookie_str.replace("\r\n", ";").replace("\n", ";").replace(";;", ";")
    for item in normalized.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            cookie_dict[k.strip()] = v
    return cookie_dict

def cookie_dict_to_str(cookie_dict):
    """将 Cookie dict 还原为字符串"""
    return "; ".join(f"{k}={v}" for k, v in cookie_dict.items())

def apply_cookies_to_page(page, cookie_dict):
    """尽量把 Cookie 写入 DrissionPage"""
    domain = urlparse(BASE_URL).hostname or ""
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

def extract_cookies_from_page(page):
    """从 DrissionPage 中提取 Cookie 字符串"""
    cookies = None
    if hasattr(page, "cookies"):
        cookies = page.cookies() if callable(page.cookies) else page.cookies

    if not cookies:
        return ""

    cookie_dict = {}
    if isinstance(cookies, dict):
        cookie_dict = cookies
    elif isinstance(cookies, list):
        for item in cookies:
            if isinstance(item, dict) and "name" in item and "value" in item:
                cookie_dict[item["name"]] = item["value"]

    if cookie_dict:
        return cookie_dict_to_str(cookie_dict)
    if isinstance(cookies, str):
        return cookies
    return ""

def wait_for_cf_clearance(page, timeout=15):
    """等待 cf_clearance 出现"""
    end = time.time() + timeout
    while time.time() < end:
        cookies = None
        if hasattr(page, "cookies"):
            cookies = page.cookies() if callable(page.cookies) else page.cookies
        if isinstance(cookies, dict) and cookies.get("cf_clearance"):
            return True
        if isinstance(cookies, list):
            for item in cookies:
                if isinstance(item, dict) and item.get("name") == "cf_clearance":
                    return True
        time.sleep(1)
    return False

def close_drissionpage(page):
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

def test_flaresolverr():
    """测试 FlareSolverr 是否正常"""
    print("1. 测试 FlareSolverr 基础连接...")
    try:
        r = requests.get(f"{FLARESOLVERR_URL}/health", timeout=5)
        print(f"   健康检查: {r.status_code}")
    except Exception as e:
        print(f"   ❌ FlareSolverr 未运行: {e}")
        return

    print("\n2. 测试获取 latest.json (无需登录)...")
    payload = {
        "cmd": "request.get",
        "url": f"{BASE_URL}/latest.json",
        "maxTimeout": 60000,
        "headers": {"Accept": "application/json"}
    }
    r = requests.post(f"{FLARESOLVERR_URL}/v1", json=payload, timeout=90)
    result = r.json()
    print(f"   状态: {result.get('status')}")
    if result.get("status") == "ok":
        response = result["solution"]["response"]
        response = extract_json_from_html(response)
        if response.startswith("{"):
            data = json.loads(response)
            topics = data.get("topic_list", {}).get("topics", [])
            print(f"   ✅ 成功获取 {len(topics)} 个帖子")
        else:
            print(f"   ❌ 返回 HTML: {response[:100]}...")
    else:
        print(f"   ❌ 失败: {result.get('message')}")

    print("\n3. 测试 notifications.json (需要登录)...")
    cookies = extract_cookies(COOKIE)
    if not cookies:
        print("   ⚠️ 未配置 cookie，跳过")
        return

    payload = {
        "cmd": "request.get",
        "url": f"{BASE_URL}/notifications.json",
        "maxTimeout": 60000,
        "headers": {"Accept": "application/json"},
        "cookies": cookies
    }
    r = requests.post(f"{FLARESOLVERR_URL}/v1", json=payload, timeout=90)
    result = r.json()
    print(f"   状态: {result.get('status')}")
    if result.get("status") == "ok":
        response = result["solution"]["response"]
        response = extract_json_from_html(response)
        if response.startswith("{"):
            data = json.loads(response)
            if "errors" in data:
                print(f"   ❌ Cookie 无效: {data.get('errors')}")
            else:
                print(f"   ✅ Cookie 有效，获取到通知数据")
        else:
            print(f"   ❌ 返回 HTML: {response[:100]}...")
    else:
        print(f"   ❌ 失败: {result.get('message')}")

def test_drissionpage():
    """测试 DrissionPage 刷新 Cookie 并拉取 JSON"""
    print("\n4. 测试 DrissionPage 刷新 Cookie...")
    try:
        from DrissionPage import ChromiumOptions, ChromiumPage
    except Exception as e:
        print(f"   ❌ DrissionPage 未安装或不可用: {e}")
        return

    use_xvfb = os.getenv("DP_XVFB", "0").strip().lower() in ("1", "true", "yes", "on")
    headless = os.getenv("DP_HEADLESS", "1").strip().lower() not in ("0", "false", "no")
    if use_xvfb:
        headless = False
        try:
            from pyvirtualdisplay import Display
            display = Display(visible=0, size=(1280, 800))
            display.start()
        except Exception as e:
            print(f"   ❌ Xvfb/pyvirtualdisplay 启动失败: {e}")
            return
    else:
        display = None
    options = ChromiumOptions()
    browser_path = (
        shutil.which("chromium")
        or shutil.which("chromium-browser")
        or shutil.which("google-chrome")
    )
    if browser_path:
        try:
            options.set_browser_path(browser_path)
        except Exception:
            pass

    try:
        options.set_user_data_dir("/tmp/drissionpage-test")
    except Exception:
        try:
            options.set_user_data_path("/tmp/drissionpage-test")
        except Exception:
            pass

    try:
        options.headless(headless)
    except Exception:
        try:
            options.set_headless(headless)
        except Exception:
            if headless:
                options.set_argument("--headless=new")

    try:
        options.set_argument("--no-sandbox")
        options.set_argument("--disable-dev-shm-usage")
        options.set_argument("--disable-gpu")
        options.set_argument("--disable-blink-features=AutomationControlled")
        options.set_argument("--window-size=1280,800")
    except Exception:
        pass

    ua_override = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    try:
        options.set_argument(f"--user-agent={ua_override}")
    except Exception:
        pass

    page = ChromiumPage(options)
    try:
        page.get(BASE_URL)
        cookie_dict = cookie_str_to_dict(COOKIE)
        if cookie_dict:
            apply_cookies_to_page(page, cookie_dict)
            page.get(BASE_URL)

        time.sleep(2)
        page.get(f"{BASE_URL}/latest.json?order=created")
        time.sleep(2)

        wait_for_cf_clearance(page, timeout=15)

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
            print(f"   浏览器 UA: {ua}")

        refreshed = extract_cookies_from_page(page)
        if not refreshed:
            print("   ❌ 未获取到 Cookie")
            return

        refreshed_dict = cookie_str_to_dict(refreshed)
        print(f"   ✅ 获取到 Cookie，字段数: {len(refreshed_dict)}")
        print(f"   _t: {'存在' if '_t' in refreshed_dict else '缺失'}")
        print(f"   _forum_session: {'存在' if '_forum_session' in refreshed_dict else '缺失'}")
        print(f"   cf_clearance: {'存在' if 'cf_clearance' in refreshed_dict else '缺失'}")

        print("\n5. 测试 DrissionPage Cookie 拉取 latest.json...")
        ua_for_requests = ua_override
        if ua and "HeadlessChrome" not in ua:
            ua_for_requests = ua
        headers = {
            "User-Agent": ua_for_requests,
            "Cookie": refreshed,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": f"{BASE_URL}/",
        }
        r = requests.get(f"{BASE_URL}/latest.json?order=created", headers=headers, timeout=30)
        if r.status_code == 200:
            data = r.json()
            topics = data.get("topic_list", {}).get("topics", [])
            print(f"   ✅ 成功获取 {len(topics)} 个帖子")
        else:
            print(f"   ❌ 请求失败: HTTP {r.status_code}")
    except Exception as e:
        print(f"   ❌ DrissionPage 测试失败: {e}")
    finally:
        close_drissionpage(page)
        if display:
            display.stop()

if __name__ == "__main__":
    test_flaresolverr()
    test_drissionpage()
