"""登录 ssky123.com（手机号 + 密码），支持会话复用"""
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from sqlalchemy.orm import Session

from src.models import FerryAccount
from src.crawler.driver import create_driver
from src.crawler.session import (
    decrypt_password,
    restore_session,
    save_session,
    is_session_valid,
    verify_session_online,
)

SITE_URL = "https://pc.ssky123.com/online_booking_pc/#/index"
LOGIN_URL = "https://pc.ssky123.com/online_booking_pc/#/login"
WAIT_TIMEOUT = 20


def ensure_logged_in(account: FerryAccount, db: Session) -> tuple:
    """
    确保账号处于登录状态。
    返回 (driver, message)，调用方负责 driver.quit()。
    如果本函数内部发生不可恢复的异常，会先 quit driver 再向上抛出。
    """
    driver = create_driver()
    try:
        # 1. 尝试恢复已有会话
        if is_session_valid(account):
            try:
                restore_session(driver, account)
                if verify_session_online(driver):
                    return driver, "会话复用成功，无需重新登录"
            except Exception:
                pass  # 恢复失败，走完整登录

        # 2. 完整登录流程
        _do_login(driver, account, db)
        return driver, "登录成功，会话已保存"
    except Exception:
        try:
            driver.quit()
        except Exception:
            pass
        raise


def _do_login(driver, account: FerryAccount, db: Session):
    """执行完整的登录操作"""
    password = decrypt_password(account.password_enc)

    driver.get(LOGIN_URL)
    wait = WebDriverWait(driver, WAIT_TIMEOUT)

    # 等待页面加载完成，找到账号输入框
    # 实际页面：<input type="text" placeholder="输入您的账号/手机号">
    phone_input = wait.until(
        EC.presence_of_element_located(
            (By.XPATH, "//input[@placeholder='输入您的账号/手机号']")
        )
    )
    phone_input.clear()
    phone_input.send_keys(account.phone)
    time.sleep(0.3)

    # 密码输入框：<input type="password" placeholder="输入您的密码">
    pwd_input = wait.until(
        EC.presence_of_element_located(
            (By.XPATH, "//input[@placeholder='输入您的密码']")
        )
    )
    pwd_input.clear()
    pwd_input.send_keys(password)
    time.sleep(0.3)

    # 点击登录按钮：<button><div class="q-btn-inner ..."><div>登录</div></div></button>
    login_btn = wait.until(
        EC.element_to_be_clickable(
            (By.XPATH, "//button[.//div[text()='登录']]")
        )
    )
    login_btn.click()

    # 等待登录成功：标志是 header 中出现手机号显示区域
    # <div class="index__titlename"><p>13145218799</p></div>
    wait.until(
        EC.presence_of_element_located(
            (By.XPATH, "//div[@class='index__titlename']")
        )
    )
    time.sleep(1)  # 等待 localStorage 写入

    # 3. 保存会话到数据库
    save_session(driver, account, db)
