"""远程 WebDriver 工厂"""
from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from src.database import SessionLocal
from src.models import Setting

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _get_selenium_url() -> str:
    db = SessionLocal()
    try:
        row = db.query(Setting).filter_by(key="selenium_url").first()
        return row.value if row else "http://192.168.1.117:14444/wd/hub"
    finally:
        db.close()


def create_driver() -> webdriver.Remote:
    """创建并返回一个远程 Chrome WebDriver"""
    selenium_url = _get_selenium_url()

    options = ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-notifications")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(f"--user-agent={BROWSER_USER_AGENT}")
    options.add_argument("--lang=zh-CN")
    # 去除 Chrome 自动化控制标识，防止网站通过 navigator.webdriver 检测到 Selenium
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    # 禁用 Client Hints，避免 Sec-CH-UA-Platform/Mobile 与 UA 字符串不一致
    # 站点只会看到我们完全控制的 User-Agent 字符串
    options.add_argument("--disable-features=UserAgentClientHint")

    driver = webdriver.Remote(
        command_executor=selenium_url,
        options=options,
    )
    driver.set_page_load_timeout(60)
    driver.implicitly_wait(5)

    # 覆盖 navigator.platform 为 Win32，绕过嵊泗购票网站的 PC 端检测
    # 该网站检测 navigator.platform：不是 Win32/MacIntel 则跳转手机版
    # 通过 Selenium Grid 4 CDP 中继注入脚本，在每个页面脚本执行前生效
    try:
        driver.execute("executeCdpCommand", {
            "cmd": "Page.addScriptToEvaluateOnNewDocument",
            "params": {
                "source": "Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});"
            },
        })
    except Exception:
        pass  # Grid 不支持 CDP 中继时静默跳过，不影响其他功能

    return driver


def check_selenium_health() -> dict:
    """检查远程 Selenium 是否可用"""
    import requests
    selenium_url = _get_selenium_url()
    base = selenium_url.rstrip("/")
    # 移除 /wd/hub 后缀，查 /status
    if base.endswith("/wd/hub"):
        status_url = base[: -len("/wd/hub")] + "/status"
    else:
        status_url = base + "/status"
    try:
        resp = requests.get(status_url, timeout=5)
        data = resp.json()
        ready = data.get("value", {}).get("ready", data.get("ready", False))
        return {"ok": True, "ready": ready, "url": selenium_url}
    except Exception as e:
        return {"ok": False, "error": str(e), "url": selenium_url}
