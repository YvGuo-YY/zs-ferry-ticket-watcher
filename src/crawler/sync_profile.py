"""
从购票系统同步常用联系人和车辆信息。

选择器说明（基于 pc.ssky123.com Quasar SPA）：
- 如果下述 URL 或选择器在实际测试中不正确，请根据真实 HTML 更新
  CONTACT_PAGE_URL / VEHICLE_PAGE_URL 和对应的 _parse_* 函数中的 XPath/CSS。
"""
import re
import time
from typing import List, Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.remote.webdriver import WebDriver
from sqlalchemy.orm import Session

from src.models import Passenger, Vehicle

# ─── URL 候选列表（按优先级尝试）──────────────────────────
CONTACT_PAGE_URLS = [
    "https://pc.ssky123.com/online_booking_pc/#/contact"
]
VEHICLE_PAGE_URLS = [
    "https://pc.ssky123.com/online_booking_pc/#/add",
]

# ─── 各页面导航文字（在个人中心菜单中查找）───────────────
NAV_PROFILE_XPATH = (
    "//div[@class='index__titlename'] | "
    "//*[contains(@class,'user-name') or contains(@class,'username')]"
)
NAV_CONTACT_XPATH = (
    "//*[contains(text(),'常用联系人') or contains(text(),'联系人管理')]"
)
NAV_VEHICLE_XPATH = (
    "//*[contains(text(),'我的车辆') or contains(text(),'车辆管理') or contains(text(),'常用车辆')]"
)

# ─── 联系人页面元素 ────────────────────────────────────────
# 联系人列表项：尝试多种 CSS 选择器
CONTACT_ITEM_CSS_LIST = [
    "div.person-item",
    "div.contact-item",
    "div.passenger-item",
    "div.q-item.person",
    ".q-list .q-item",          # 通用 Quasar 列表
]
# 用于在整页 HTML 中用正则提取联系人信息（兜底）
# 身份证/护照/港澳通行证/台湾通行证号码特征
ID_PATTERN = re.compile(
    r"(?P<id_num>[A-Z0-9]{5,20})"  # 宽松，后处理过滤
)
CN_ID_PATTERN = re.compile(r"\b\d{17}[\dX]\b")  # 18位身份证

# ─── 车辆页面元素 ──────────────────────────────────────────
VEHICLE_ITEM_CSS_LIST = [
    "div.car-item",
    "div.vehicle-item",
    "div.q-item.car",
    ".q-list .q-item",
]
# 中国车牌号正则（含新能源双字母）
PLATE_PATTERN = re.compile(
    r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤川青藏琼宁夏]"
    r"[A-Z](?:[A-Z0-9]{5}|[DF][A-Z0-9]{5})"
)

SHORT_WAIT = 8  # 非关键等待超时（秒）
PAGE_WAIT = 15  # 页面跳转等待（秒）


# ══════════════════════════════════════════════════════════
# 公共入口
# ══════════════════════════════════════════════════════════

def sync_profile(driver: WebDriver, db: Session) -> dict:
    """
    同步购票系统中的常用联系人和车辆到本地数据库（去重）。
    返回: {"passengers_added", "passengers_skipped",
           "vehicles_added", "vehicles_skipped", "errors"}
    """
    result = {
        "passengers_added": 0,
        "passengers_skipped": 0,
        "vehicles_added": 0,
        "vehicles_skipped": 0,
        "errors": [],
    }

    # 同步联系人
    try:
        contacts = _fetch_contacts(driver)
        for c in contacts:
            _upsert_passenger(db, c, result)
    except Exception as e:
        result["errors"].append(f"同步联系人失败: {e}")

    # 同步车辆
    try:
        vehicles = _fetch_vehicles(driver)
        for v in vehicles:
            _upsert_vehicle(db, v, result)
    except Exception as e:
        result["errors"].append(f"同步车辆失败: {e}")

    return result


# ══════════════════════════════════════════════════════════
# 联系人抓取
# ══════════════════════════════════════════════════════════

def _fetch_contacts(driver: WebDriver) -> List[dict]:
    """导航到联系人管理页并解析所有联系人"""
    if not _navigate_to_page(driver, CONTACT_PAGE_URLS, NAV_CONTACT_XPATH):
        return []

    time.sleep(2)
    return _parse_contacts(driver)


def _parse_contacts(driver: WebDriver) -> List[dict]:
    """
    从当前页面解析联系人列表。
    每条记录: {"name", "id_type", "id_number", "phone"}
    """
    contacts = []

    # 策略 1：按已知 CSS 选择器逐一尝试
    items = _find_items(driver, CONTACT_ITEM_CSS_LIST)
    if items:
        for el in items:
            c = _parse_contact_element(el)
            if c:
                contacts.append(c)
        if contacts:
            return contacts

    # 策略 2：从页面完整文本用正则提取 18 位身份证号
    contacts = _parse_contacts_from_text(driver.page_source)
    return contacts


def _parse_contact_element(el) -> Optional[dict]:
    """从单个联系人元素中提取信息"""
    try:
        text = el.text.strip()
        if not text:
            return None

        # 提取 18 位身份证号
        id_match = CN_ID_PATTERN.search(text)
        if not id_match:
            return None
        id_number = id_match.group()

        # 姓名：取第一行非空行，通常在证件号前
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        name = lines[0] if lines else ""

        # 证件类型
        id_type = "身份证"
        for t in ["护照", "港澳通行证", "台湾通行证"]:
            if t in text:
                id_type = t
                break

        # 手机号（11位数字，非身份证部分）
        phone = ""
        for m in re.finditer(r"1[3-9]\d{9}", text):
            if m.group() not in id_number:
                phone = m.group()
                break

        if not name or not id_number:
            return None
        return {"name": name, "id_type": id_type, "id_number": id_number, "phone": phone}
    except Exception:
        return None


def _parse_contacts_from_text(html: str) -> List[dict]:
    """兜底方案：从页面 HTML 文本中用正则提取所有 18 位身份证号并关联姓名"""
    contacts = []
    # 找所有身份证号
    for m in CN_ID_PATTERN.finditer(html):
        id_number = m.group()
        # 向前 100 字符内找姓名（2-4 个汉字）
        prefix = html[max(0, m.start() - 150): m.start()]
        name_match = re.search(r"[\u4e00-\u9fa5]{2,4}(?=[^<]*$)", prefix)
        if not name_match:
            continue
        name = name_match.group()
        contacts.append({
            "name": name,
            "id_type": "身份证",
            "id_number": id_number,
            "phone": "",
        })
    return contacts


# ══════════════════════════════════════════════════════════
# 车辆抓取
# ══════════════════════════════════════════════════════════

def _fetch_vehicles(driver: WebDriver) -> List[dict]:
    """导航到车辆管理页并解析所有车辆"""
    if not _navigate_to_page(driver, VEHICLE_PAGE_URLS, NAV_VEHICLE_XPATH):
        return []

    time.sleep(2)
    return _parse_vehicles(driver)


def _parse_vehicles(driver: WebDriver) -> List[dict]:
    """
    从当前页面解析车辆列表。
    每条记录: {"plate_number", "vehicle_type", "owner_name"}
    """
    vehicles = []

    # 策略 1：已知 CSS 选择器
    items = _find_items(driver, VEHICLE_ITEM_CSS_LIST)
    if items:
        for el in items:
            v = _parse_vehicle_element(el)
            if v:
                vehicles.append(v)
        if vehicles:
            return vehicles

    # 策略 2：正则提取页面中的车牌号
    vehicles = _parse_vehicles_from_text(driver.page_source)
    return vehicles


def _parse_vehicle_element(el) -> Optional[dict]:
    """从单个车辆元素中提取信息"""
    try:
        text = el.text.strip()
        if not text:
            return None

        plate_match = PLATE_PATTERN.search(text)
        if not plate_match:
            return None
        plate_number = plate_match.group()

        lines = [l.strip() for l in text.splitlines() if l.strip()]
        # 车型：通常含"型"或"汽车"等词
        vehicle_type = ""
        owner_name = ""
        for line in lines:
            if line == plate_number:
                continue
            if any(k in line for k in ["汽车", "客车", "货车", "车型", "型"]):
                vehicle_type = line
            elif re.match(r"[\u4e00-\u9fa5]{2,4}$", line):
                owner_name = line

        return {"plate_number": plate_number, "vehicle_type": vehicle_type, "owner_name": owner_name}
    except Exception:
        return None


def _parse_vehicles_from_text(html: str) -> List[dict]:
    """兜底方案：从 HTML 文本中用正则提取车牌号"""
    vehicles = []
    seen = set()
    for m in PLATE_PATTERN.finditer(html):
        plate = m.group()
        if plate in seen:
            continue
        seen.add(plate)
        vehicles.append({"plate_number": plate, "vehicle_type": "", "owner_name": ""})
    return vehicles


# ══════════════════════════════════════════════════════════
# 数据库写入（去重）
# ══════════════════════════════════════════════════════════

def _upsert_passenger(db: Session, data: dict, result: dict):
    """按 id_number 去重写入 Passenger"""
    id_number = data.get("id_number", "").strip()
    if not id_number:
        return
    existing = db.query(Passenger).filter_by(id_number=id_number).first()
    if existing:
        result["passengers_skipped"] += 1
        return
    p = Passenger(
        name=data.get("name", ""),
        id_type=data.get("id_type", "身份证"),
        id_number=id_number,
        phone=data.get("phone", "") or None,
        remark="自动同步",
    )
    db.add(p)
    db.commit()
    result["passengers_added"] += 1


def _upsert_vehicle(db: Session, data: dict, result: dict):
    """按 plate_number 去重写入 Vehicle"""
    plate = data.get("plate_number", "").strip()
    if not plate:
        return
    existing = db.query(Vehicle).filter_by(plate_number=plate).first()
    if existing:
        result["vehicles_skipped"] += 1
        return
    v = Vehicle(
        plate_number=plate,
        vehicle_type=data.get("vehicle_type", ""),
        owner_name=data.get("owner_name", ""),
        remark="自动同步",
    )
    db.add(v)
    db.commit()
    result["vehicles_added"] += 1


# ══════════════════════════════════════════════════════════
# 通用导航辅助
# ══════════════════════════════════════════════════════════

def _navigate_to_page(driver: WebDriver, url_candidates: List[str], nav_xpath: str) -> bool:
    """
    尝试通过直接 URL 跳转到目标页面。
    若所有 URL 均无法识别到有效内容，再尝试从当前页面点击导航。
    返回 True 表示成功到达，False 表示失败。
    """
    # 方案 A：直接 URL 导航（逐个尝试，检查页面是否有内容）
    for url in url_candidates:
        try:
            driver.get(url)
            time.sleep(2)
            # 页面标题或 body 有文字内容即认为成功
            body_text = driver.find_element(By.TAG_NAME, "body").text.strip()
            if len(body_text) > 50:  # 有实质内容
                return True
        except Exception:
            continue

    # 方案 B：从当前页面点击导航元素
    try:
        wait = WebDriverWait(driver, SHORT_WAIT)
        # 先点击用户名/个人中心入口
        profile_els = driver.find_elements(By.XPATH, NAV_PROFILE_XPATH)
        if profile_els:
            profile_els[0].click()
            time.sleep(1.5)
        # 再找目标导航项
        nav_el = wait.until(EC.element_to_be_clickable((By.XPATH, nav_xpath)))
        nav_el.click()
        time.sleep(2)
        return True
    except Exception:
        return False


def _find_items(driver: WebDriver, selectors: List[str]):
    """按优先级尝试多个 CSS 选择器，返回找到的元素列表"""
    for sel in selectors:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if els:
                return els
        except Exception:
            continue
    return []
