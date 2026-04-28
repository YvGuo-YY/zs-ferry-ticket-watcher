"""
基于 Selenium 远程 Chrome 的爬虫后端。
将现有 login.py / sync_profile.py / booking.py 包装成 CrawlerBackend 接口。
"""
from typing import Optional

from sqlalchemy.orm import Session

from src.crawler.base import CrawlerBackend
from src.crawler.driver import create_driver
from src.crawler.login import ensure_logged_in as _selenium_ensure_logged_in
from src.crawler.sync_profile import sync_profile as _selenium_sync_profile
from src.crawler.booking import book_ticket as _selenium_book_ticket
from src.models import FerryAccount, Passenger, Vehicle


class SeleniumBackend(CrawlerBackend):
    """通过 Selenium Remote WebDriver 操作浏览器与购票系统交互"""

    # ── 认证 ──────────────────────────────────────────────

    def login(self, account: FerryAccount, db: Session) -> str:
        driver, msg = _selenium_ensure_logged_in(account, db)
        try:
            driver.quit()
        except Exception:
            pass
        return msg

    def verify_session(self, account: FerryAccount, db: Session) -> bool:
        from src.crawler.session import is_session_valid, verify_session_online, restore_session
        if not is_session_valid(account):
            return False
        driver = create_driver()
        try:
            restore_session(driver, account)
            return verify_session_online(driver)
        except Exception:
            return False
        finally:
            try:
                driver.quit()
            except Exception:
                pass

    def ensure_logged_in(self, account: FerryAccount, db: Session) -> tuple:
        """返回 (driver, message)，调用方负责 driver.quit()"""
        return _selenium_ensure_logged_in(account, db)

    # ── 同步联系人和车辆 ────────────────────────────────────

    def sync_profile(self, account: FerryAccount, db: Session) -> dict:
        driver, _ = _selenium_ensure_logged_in(account, db)
        try:
            return _selenium_sync_profile(driver, db)
        finally:
            try:
                driver.quit()
            except Exception:
                pass

    # ── 班次查询 ───────────────────────────────────────────

    def query_trips(
        self,
        account: FerryAccount,
        db: Session,
        start_port_no: int,
        end_port_no: int,
        date: str,
        require_vehicle: bool = False,
    ) -> list:
        from src.crawler.query import query_trips as _query
        driver, _ = _selenium_ensure_logged_in(account, db)
        try:
            return _query(driver, start_port_no, end_port_no, date, require_vehicle=require_vehicle)
        finally:
            try:
                driver.quit()
            except Exception:
                pass

    # ── 购票 ──────────────────────────────────────────────

    def book_ticket(
        self,
        account: FerryAccount,
        db: Session,
        trip: dict,
        passenger_ids: list,
        vehicle_id: Optional[int],
        preferred_seats: list = None,
        log_fn=None,
    ) -> dict:
        # 从数据库读取乘客和车辆详情
        passengers = []
        for pid in passenger_ids:
            p = db.query(Passenger).get(pid)
            if p:
                passengers.append({
                    "name": p.name,
                    "id_type": p.id_type,
                    "id_number": p.id_number,
                    "phone": p.phone or "",
                })

        vehicle = None
        if vehicle_id:
            v = db.query(Vehicle).get(vehicle_id)
            if v:
                vehicle = {
                    "plate_number": v.plate_number,
                    "vehicle_type": v.vehicle_type,
                    "owner_name": v.owner_name,
                }

        driver, _ = _selenium_ensure_logged_in(account, db)
        try:
            return _selenium_book_ticket(driver, trip, passengers, vehicle, log_fn)
        finally:
            try:
                driver.quit()
            except Exception:
                pass
