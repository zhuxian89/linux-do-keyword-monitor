import requests
import json

FLARESOLVERR_URL = "http://localhost:8191"
BASE_URL = "https://linux.do"

# 从 config.json 读取 cookie
def load_cookie():
    try:
        with open("config.json", "r") as f:
            config = json.load(f)
            return config.get("discourse_cookie", "")
    except:
        return ""

COOKIE = load_cookie()

def extract_cookies(cookie_str):
    """提取需要的 cookie"""
    cookies = []
    for item in cookie_str.split("; "):
        if "=" in item:
            k, v = item.split("=", 1)
            if k in ("_t", "_forum_session"):
                cookies.append({"name": k, "value": v})
    return cookies

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

if __name__ == "__main__":
    test_flaresolverr()
