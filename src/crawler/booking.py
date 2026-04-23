"""下单流程：点击预订 → 勾选常用联系人 → 填联系电话 → 提交订单"""
import time
from typing import Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.remote.webdriver import WebDriver

WAIT_TIMEOUT = 20


def book_ticket(
    driver: WebDriver,
    trip: dict,
    passengers: list,
    vehicle: Optional[dict] = None,
    log_fn=None,
) -> dict:
    """
    点击班次预订按钮，勾选旅客，填联系电话，提交订单。
    passengers: [{"name":..., "id_type":..., "id_number":..., "phone":...}, ...]
    vehicle:    {"plate_number":..., "vehicle_type":..., "owner_name":...} 或 None（旅客票不填）
    返回: {"success": bool, "order_id": str|None, "screenshot_b64": str|None, "message": str}
    """
    def log(msg, level="INFO"):
        if log_fn:
            log_fn(level, msg)

    wait = WebDriverWait(driver, WAIT_TIMEOUT)

    # ── 点击预订 span ─────────────────────────────────────────
    # 结果页每行最后一列：<span style="...background: rgb(0,140,221)...">预订</span>
    try:
        log("点击目标班次的预订按钮")
        book_el = trip.get("book_element")
        if book_el is None:
            # 兜底：从 element 行里找 span
            row_el = trip.get("element")
            if row_el:
                book_el = row_el.find_element(By.XPATH, ".//span[text()='预订']")
        book_el.click()
        time.sleep(1.5)
    except Exception as e:
        log(f"点击预订失败：{e}", "ERROR")
        screenshot = driver.get_screenshot_as_base64()
        return {"success": False, "order_id": None, "screenshot_b64": screenshot, "message": str(e)}

    # ── 等待订单提交页加载 ────────────────────────────────────
    # 订单页有"确定下单"按钮：<button><div>确定下单</div></button>
    try:
        wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "//button[.//div[text()='确定下单']]")
            )
        )
    except Exception as e:
        log(f"等待订单页超时：{e}", "ERROR")
        screenshot = driver.get_screenshot_as_base64()
        return {"success": False, "order_id": None, "screenshot_b64": screenshot, "message": str(e)}

    # ── 勾选常用联系人 ────────────────────────────────────────
    # 订单页"二、填写乘客信息"区域：
    # <div class="q-option ... selectperson q-checkbox ...">
    #   <div class="q-option-inner ..."> (checkbox 图标)
    #   <span>乘客姓名</span>
    # </div>
    log(f"勾选 {len(passengers)} 名常用联系人")
    try:
        _select_passengers(driver, wait, passengers, log)
    except Exception as e:
        log(f"勾选乘客失败：{e}", "WARN")

    # ── 填写车牌号（小客车票）────────────────────────────────
    if vehicle and vehicle.get("plate_number"):
        try:
            log(f"填写车牌号：{vehicle['plate_number']}")
            _fill_vehicle(driver, wait, vehicle, log)
        except Exception as e:
            log(f"填写车牌失败：{e}", "WARN")

    # ── 填写联系电话 ──────────────────────────────────────────
    # 从旅客列表取第一位有手机号的乘客作为联系人
    contact_phone = next(
        (p["phone"] for p in passengers if p.get("phone")), ""
    )
    if contact_phone:
        try:
            log(f"填写联系电话：{contact_phone}")
            phone_input = wait.until(
                EC.presence_of_element_located(
                    (By.XPATH, "//input[@placeholder='请输入电话号码']")
                )
            )
            phone_input.clear()
            phone_input.send_keys(contact_phone)
            time.sleep(0.3)
        except Exception as e:
            log(f"填写联系电话失败：{e}", "WARN")

    # ── 点击确定下单 ──────────────────────────────────────────
    try:
        log("点击确定下单")
        submit_btn = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[.//div[text()='确定下单']]")
            )
        )
        submit_btn.click()
        time.sleep(1.5)
    except Exception as e:
        log(f"提交订单失败：{e}", "ERROR")
        screenshot = driver.get_screenshot_as_base64()
        return {"success": False, "order_id": None, "screenshot_b64": screenshot, "message": str(e)}

    # ── 处理可能的弹窗 ────────────────────────────────────────
    _handle_post_submit_dialogs(driver, wait, log)

    # ── 截图 + 获取订单号 ────────────────────────────────────
    screenshot = driver.get_screenshot_as_base64()
    order_id = _extract_order_id(driver)
    msg = f"下单成功！订单号：{order_id or '未获取到'}，请在15分钟内完成支付。"
    log(msg)

    return {
        "success": True,
        "order_id": order_id,
        "screenshot_b64": screenshot,
        "message": msg,
    }


def _fill_vehicle(driver: WebDriver, wait: WebDriverWait, vehicle: dict, log):
    """
    在订单页填写车辆信息（小客车及随车人员票）。

    策略：先尝试从"常用车辆"列表中勾选匹配车牌；若找不到，
    则直接定位车牌输入框手动输入。
    """
    plate = vehicle.get("plate_number", "")

    # 方式 1：勾选已有常用车辆（类似常用联系人的复选框）
    car_checkboxes = driver.find_elements(
        By.XPATH,
        "//div[contains(@class,'selectperson') and contains(@class,'q-checkbox')]"
        " | //div[contains(@class,'selectcar') and contains(@class,'q-checkbox')]"
        " | //div[contains(@class,'caritem')]"
    )
    for cb in car_checkboxes:
        try:
            if plate in cb.text:
                inner = cb.find_elements(By.XPATH, ".//div[contains(@class,'q-option-inner')]")
                if inner:
                    is_checked = "active" in (inner[0].get_attribute("class") or "")
                    if not is_checked:
                        cb.click()
                        import time as _t; _t.sleep(0.3)
                else:
                    cb.click()
                    import time as _t; _t.sleep(0.3)
                log(f"已勾选常用车辆：{plate}")
                return
        except Exception:
            pass

    # 方式 2：直接输入车牌号
    plate_inputs = driver.find_elements(
        By.XPATH,
        "//input[@placeholder='请输入车牌号' or @placeholder='输入车牌号' or @placeholder='车牌号码']"
    )
    if plate_inputs:
        inp = plate_inputs[0]
        inp.clear()
        inp.send_keys(plate)
        import time as _t; _t.sleep(0.3)
        log(f"已输入车牌号：{plate}")
        return

    log(f"未找到车牌输入区域，请确认页面结构", "WARN")


def _select_passengers(driver: WebDriver, wait: WebDriverWait, passengers: list, log):
    """
    在订单页的"常用联系人"区域勾选对应乘客。

    页面结构：
    <div class="q-option cursor-pointer ... selectperson q-checkbox q-focusable">
      <div class="q-option-inner ...">  ← checkbox 图标区域
      <span>张三</span>               ← 乘客姓名
    </div>
    """
    pax_names = {p["name"] for p in passengers}
    # 找所有 selectperson 复选框
    checkboxes = driver.find_elements(
        By.XPATH, "//div[contains(@class,'selectperson') and contains(@class,'q-checkbox')]"
    )
    for cb in checkboxes:
        try:
            name_span = cb.find_element(By.XPATH, ".//span[last()]")
            name = name_span.text.strip()
            if name in pax_names:
                # 仅当未选中时才点击
                inner = cb.find_element(By.XPATH, ".//div[contains(@class,'q-option-inner')]")
                is_checked = "active" in (inner.get_attribute("class") or "")
                if not is_checked:
                    cb.click()
                    time.sleep(0.2)
                    log(f"已勾选乘客：{name}")
        except Exception:
            pass


def _handle_post_submit_dialogs(driver: WebDriver, wait: WebDriverWait, log):
    """
    处理提交后可能出现的弹窗。

    已知弹窗：
    1. 候补订单提示：含"去添加"/"不添加"按钮 → 点"不添加"
    2. 风险提示：含"我已阅读并理解上述风险"按钮 → 点击确认
    3. 验证码弹窗：含 input[placeholder='输入验证结果'] → 无法自动处理，截图提示
    4. 人脸核验二维码：含"请用手机扫码进行人脸核验" → 无法自动处理
    """
    time.sleep(1)
    for _ in range(5):
        try:
            # 候补订单弹窗
            skip_btn = driver.find_elements(
                By.XPATH, "//div[contains(@class,'modal-content')]//div[contains(@class,'q-btn-inner') and text()='不添加']"
            )
            if skip_btn:
                skip_btn[0].click()
                log("已关闭候补订单弹窗（不添加）")
                time.sleep(0.5)
                continue

            # 风险告知弹窗
            risk_btn = driver.find_elements(
                By.XPATH, "//div[contains(@class,'q-btn-inner') and contains(text(),'我已阅读并理解上述风险')]"
            )
            if risk_btn:
                risk_btn[0].click()
                log("已确认风险告知弹窗")
                time.sleep(0.5)
                continue

            # 验证码弹窗（无法自动处理，仅记录）
            captcha = driver.find_elements(
                By.XPATH, "//input[@placeholder='输入验证结果']"
            )
            if captcha:
                log("检测到验证码弹窗，无法自动处理", "WARN")
                break

        except Exception:
            pass
        break


def _extract_order_id(driver: WebDriver) -> Optional[str]:
    """尝试从当前页面提取订单号"""
    import re
    try:
        els = driver.find_elements(
            By.XPATH, "//*[contains(text(),'订单号') or contains(text(),'单号')]"
        )
        for el in els:
            match = re.search(r"[A-Z0-9]{8,}", el.text)
            if match:
                return match.group(0)
        # 从 URL 中提取
        url = driver.current_url
        match = re.search(r"order[_/=]([A-Za-z0-9]+)", url)
        if match:
            return match.group(1)
    except Exception:
        pass
    return None
