"""
抢票后端抽象接口。

两套实现：
  - SeleniumBackend：通过 Selenium 远程 Chrome 操作浏览器（对付复杂交互/验证码）
  - ApiBackend：直接调用 pc.ssky123.com REST API（速度快、无需浏览器）

通过系统配置 crawler_backend = "selenium" | "api" 切换。
"""
from abc import ABC, abstractmethod
from typing import Optional

from sqlalchemy.orm import Session

from src.models import FerryAccount


class CrawlerBackend(ABC):
    """所有爬虫后端必须实现此接口"""

    # ── 认证 ──────────────────────────────────────────────

    @abstractmethod
    def login(self, account: FerryAccount, db: Session) -> str:
        """
        登录账号，持久化会话（cookie/token）到数据库。
        返回描述性消息字符串（"登录成功"/"会话复用"等）。
        """

    @abstractmethod
    def verify_session(self, account: FerryAccount, db: Session) -> bool:
        """
        检查账号当前会话是否有效。
        返回 True 表示有效，无需重新登录。
        """

    # ── 同步用户信息 ───────────────────────────────────────

    @abstractmethod
    def sync_profile(self, account: FerryAccount, db: Session) -> dict:
        """
        从购票系统拉取常用联系人和车辆，写入本地数据库（去重）。
        返回:
          {
            "passengers_added": int,
            "passengers_skipped": int,
            "vehicles_added": int,
            "vehicles_skipped": int,
            "errors": list[str],
          }
        """

    # ── 班次查询 ───────────────────────────────────────────

    @abstractmethod
    def query_trips(
        self,
        account: FerryAccount,
        db: Session,
        start_port_no: int,
        end_port_no: int,
        date: str,
    ) -> list:
        """
        查询指定日期的可售班次。
        date 格式: "YYYY-MM-DD"
        返回班次列表（原始数据结构，供 booking 层使用）。
        """

    # ── 购票 ──────────────────────────────────────────────

    @abstractmethod
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
        """
        执行购票流程。
        trip: query_trips 返回的单条班次数据
        passenger_ids: 本地 passengers 表的 id 列表
        vehicle_id: 本地 vehicles 表的 id（小客车票时传入）
        log_fn: 可选，callable(level, message) 用于记录步骤日志
        返回: {"success": bool, "order_no": str, "message": str}
        """

    # ── 反向推送（可选，默认 no-op）──────────────────────────
    # SeleniumBackend 不实现，ApiBackend 覆盖。

    def push_passenger(self, account: FerryAccount, db: Session, passenger) -> dict:
        """
        将本地旅客推送到远端购票账号的常用旅客列表。
        默认不操作，ApiBackend 覆盖实现。
        返回: {"code": int, "message": str}
        """
        return {"code": 0, "message": "此后端不支持推送"}

    def push_vehicle(self, account: FerryAccount, db: Session, vehicle) -> dict:
        """
        将本地车辆推送到远端购票账号的常用车辆列表。
        默认不操作，ApiBackend 覆盖实现。
        返回: {"code": int, "message": str}
        """
        return {"code": 0, "message": "此后端不支持推送"}

    def delete_passenger(self, account: FerryAccount, db: Session, id_number: str, id_type: str) -> dict:
        """
        从远端购票账号的常用旅客列表中删除指定旅客（按证件号匹配）。
        默认不操作，ApiBackend 覆盖实现。
        返回: {"code": int, "message": str}
        """
        return {"code": 0, "message": "此后端不支持删除"}

    def delete_vehicle(self, account: FerryAccount, db: Session, plate_number: str) -> dict:
        """
        从远端购票账号的常用车辆列表中删除指定车辆（按车牌匹配）。
        默认不操作，ApiBackend 覆盖实现。
        返回: {"code": int, "message": str}
        """
        return {"code": 0, "message": "此后端不支持删除"}

    def get_sale_date(self, account: FerryAccount, db: Session) -> Optional[str]:
        """
        查询当前最远可购票日期。
        返回: "YYYY-MM-DD" 字符串，或 None（后端不支持时）。
        """
        return None
