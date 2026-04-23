"""
直接调用 pc.ssky123.com REST API 的爬虫后端。
无需浏览器，速度快，适合在 Selenium Grid 不可用时使用。

认证协议（从 HAR 逆向）：
  - 请求头 token:          登录后返回的 ssky_user_xxx 字符串
  - 请求头 authentication: {unix_timestamp_seconds}{userId}  （两者直接拼接）
"""
import json
import time as _time
from datetime import datetime, timedelta
from typing import Optional

import requests
from sqlalchemy.orm import Session

from src.crawler.base import CrawlerBackend
from src.crawler.session import decrypt_password
from src.models import FerryAccount, Passenger, Vehicle

BASE_URL = "https://pc.ssky123.com/api/v2"
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://pc.ssky123.com/",
    "Origin": "https://pc.ssky123.com",
}


def _auth_header(user_id: int) -> str:
    """生成 authentication 头：{timestamp秒}{userId}"""
    ts = int(_time.time())
    return f"{ts}{user_id}"


class ApiBackend(CrawlerBackend):
    """通过 HTTP API 直接与购票系统交互，无需 Selenium"""

    # ── 内部工具 ───────────────────────────────────────────

    def _load_session(self, account: FerryAccount) -> tuple[Optional[str], int]:
        """从 local_storage_json 中读取 token 和 userId，不存在返回 (None, 0)"""
        try:
            ls = json.loads(account.local_storage_json or "{}")
            return ls.get("token"), ls.get("userId", 0)
        except Exception:
            return None, 0

    def _save_session(self, account: FerryAccount, token: str, user_id: int, db: Session):
        """把 token/userId 序列化进 local_storage_json"""
        ls = {"token": token, "userId": user_id}
        account.local_storage_json = json.dumps(ls)
        account.session_expires_at = datetime.utcnow() + timedelta(days=7)
        account.last_login_at = datetime.utcnow()
        db.commit()

    def _headers(self, token: Optional[str], user_id: int) -> dict:
        h = dict(_DEFAULT_HEADERS)
        h["token"] = token or "undefined"
        h["authentication"] = _auth_header(user_id)
        return h

    def _get(self, path: str, token: str, user_id: int, **kwargs) -> dict:
        resp = requests.get(
            BASE_URL + path,
            headers=self._headers(token, user_id),
            timeout=15,
            **kwargs,
        )
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, token: str, user_id: int, data: dict = None, params: dict = None) -> dict:
        resp = requests.post(
            BASE_URL + path,
            headers=self._headers(token, user_id),
            json=data,
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    # ── 认证 ──────────────────────────────────────────────

    def login(self, account: FerryAccount, db: Session) -> str:
        password = decrypt_password(account.password_enc)
        resp_data = self._post(
            "/user/passLogin",
            token=None,
            user_id=0,
            params={
                "phoneNum": account.phone,
                "passwd": password,
                "deviceType": "3",
            },
        )
        if resp_data.get("code") != 200:
            raise RuntimeError(f"登录失败：{resp_data.get('message')}")
        d = resp_data["data"]
        token = d["token"]
        user_id = d["userId"]
        self._save_session(account, token, user_id, db)
        return f"登录成功（userId={user_id}）"

    def verify_session(self, account: FerryAccount, db: Session) -> bool:
        token, user_id = self._load_session(account)
        if not token or not user_id:
            return False
        try:
            resp = self._get("/user/tokenCheck", token, user_id)
            return resp.get("code") == 200
        except Exception:
            return False

    def ensure_logged_in(self, account: FerryAccount, db: Session) -> str:
        """tokenCheck 验证会话有效性，无效则重新登录"""
        if self.verify_session(account, db):
            return "会话复用成功"
        return self.login(account, db)

    # ── 带自动重新认证的请求包装器 ──────────────────────────────
    # 流程：① tokenCheck 预检（ensure_logged_in）
    #       ② 发送请求
    #       ③ 若响应表示认证失败 → 强制重新登录 → 重试一次

    @staticmethod
    def _is_auth_failure(resp_json: dict) -> bool:
        """判断 API 响应是否为认证失败（token 过期/未登录）"""
        code = resp_json.get("code", 0)
        msg = str(resp_json.get("message") or "").lower()
        if code in (401, 403):
            return True
        return any(kw in msg for kw in ("未登录", "token", "请登录", "登录过期", "未授权"))

    def _authed_get(self, path: str, account: FerryAccount, db: Session, **kwargs) -> dict:
        """带自动重认证的 GET：预检 tokenCheck → 请求 → auth 失败则重登录重试一次"""
        self.ensure_logged_in(account, db)
        token, user_id = self._load_session(account)
        resp_json = self._get(path, token, user_id, **kwargs)
        if self._is_auth_failure(resp_json):
            print(f"[AUTH] GET {path} 返回认证失败，强制重登录...")
            self.login(account, db)
            token, user_id = self._load_session(account)
            resp_json = self._get(path, token, user_id, **kwargs)
        return resp_json

    def _authed_post(self, path: str, account: FerryAccount, db: Session,
                     data: dict = None, params: dict = None) -> dict:
        """带自动重认证的 POST：预检 tokenCheck → 请求 → auth 失败则重登录重试一次"""
        self.ensure_logged_in(account, db)
        token, user_id = self._load_session(account)
        resp_json = self._post(path, token, user_id, data=data, params=params)
        if self._is_auth_failure(resp_json):
            print(f"[AUTH] POST {path} 返回认证失败，强制重登录...")
            self.login(account, db)
            token, user_id = self._load_session(account)
            resp_json = self._post(path, token, user_id, data=data, params=params)
        return resp_json

    # ── 同步联系人和车辆 ────────────────────────────────────

    def sync_profile(self, account: FerryAccount, db: Session) -> dict:
        result = {
            "passengers_added": 0,
            "passengers_skipped": 0,
            "vehicles_added": 0,
            "vehicles_skipped": 0,
            "errors": [],
        }

        # 同步乘客
        try:
            resp = self._authed_get("/user/passenger/list", account, db)
            if resp.get("code") == 200:
                for p in (resp.get("data") or []):
                    _upsert_passenger(db, p, result)
        except Exception as e:
            result["errors"].append(f"同步乘客失败: {e}")

        # 同步车辆
        try:
            resp = self._authed_get("/user/vehicle/list", account, db)
            if resp.get("code") == 200:
                for v in (resp.get("data") or []):
                    _upsert_vehicle(db, v, result)
        except Exception as e:
            result["errors"].append(f"同步车辆失败: {e}")

        return result

    # ── 班次查询 ───────────────────────────────────────────

    def query_trips(
        self,
        account: FerryAccount,
        db: Session,
        start_port_no: int,
        end_port_no: int,
        date: str,
    ) -> list:
        resp = self._authed_post(
            "/line/ferry/enq",
            account,
            db,
            data={
                "startPortNo": start_port_no,
                "endPortNo": end_port_no,
                "startDate": date,
            },
        )
        if resp.get("code") != 200:
            raise RuntimeError(f"查询班次失败：{resp.get('message')}")
        return resp.get("data") or []

    # ── 反向推送：本地 → 远端 ─────────────────────────────

    def _fetch_remote_passengers(self, account: FerryAccount, db: Session) -> list:
        """获取远端常用旅客列表，失败时抛出异常"""
        resp = self._authed_get("/user/passenger/list", account, db)
        if resp.get("code") != 200:
            raise RuntimeError(f"获取远端旅客列表失败: {resp.get('message')}")
        return resp.get("data") or []

    def _fetch_remote_vehicles(self, account: FerryAccount, db: Session) -> list:
        """获取远端常用车辆列表，失败时抛出异常"""
        resp = self._authed_get("/user/vehicle/list", account, db)
        if resp.get("code") != 200:
            raise RuntimeError(f"获取远端车辆列表失败: {resp.get('message')}")
        return resp.get("data") or []

    def push_passenger(self, account: FerryAccount, db: Session, passenger) -> dict:
        """将本地旅客推送到远端购票账号的常用旅客列表（已存在则跳过）"""
        # 远端去重：获取列表失败时仍继续尝试推送（服务端会自行去重）
        try:
            for remote in self._fetch_remote_passengers(account, db):
                if remote.get("credentialNum") == passenger.id_number:
                    return {"code": 200, "message": "远端已存在，跳过"}
        except Exception as e:
            print(f"[PUSH] 获取远端旅客列表失败，跳过去重直接推送: {e}")
        cred_type_id = _ID_TYPE_TO_CRED_ID.get(passenger.id_type, 1)
        return self._authed_post("/user/passenger/save", account, db, data={
            "userId": self._load_session(account)[1],
            "passName": passenger.name,
            "passType": 1,
            "credentialTypeId": cred_type_id,
            "credentialNum": passenger.id_number,
        })

    def push_vehicle(self, account: FerryAccount, db: Session, vehicle) -> dict:
        """将本地车辆推送到远端购票账号的常用车辆列表（已存在则跳过）"""
        # 远端去重：获取列表失败时仍继续尝试推送
        try:
            for remote in self._fetch_remote_vehicles(account, db):
                if remote.get("plateNum") == vehicle.plate_number:
                    return {"code": 200, "message": "远端已存在，跳过"}
        except Exception as e:
            print(f"[PUSH] 获取远端车辆列表失败，跳过去重直接推送: {e}")
        return self._authed_post("/user/vehicle/save", account, db, data={
            "userId": self._load_session(account)[1],
            "plateNum": vehicle.plate_number,
        })

    def delete_passenger(self, account: FerryAccount, db: Session, id_number: str, id_type: str) -> dict:
        """从远端购票账号的常用旅客列表中删除指定旅客（按证件号匹配）"""
        # _fetch_remote_passengers 失败会抛出异常，由调用方（bg task）捕获并记录
        remote_list = self._fetch_remote_passengers(account, db)
        for remote in remote_list:
            if remote.get("credentialNum") == id_number:
                resp = self._authed_post("/user/passenger/delete", account, db,
                                         data={"id": remote["id"]})
                return resp
        return {"code": -1, "message": "远端不存在此旅客，无需删除"}

    def delete_vehicle(self, account: FerryAccount, db: Session, plate_number: str) -> dict:
        """从远端购票账号的常用车辆列表中删除指定车辆（按车牌匹配）"""
        remote_list = self._fetch_remote_vehicles(account, db)
        for remote in remote_list:
            if remote.get("plateNum") == plate_number:
                resp = self._authed_post("/user/vehicle/delete", account, db,
                                         data={"id": remote["id"]})
                return resp
        return {"code": -1, "message": "远端不存在此车辆，无需删除"}

    # ── 购票（API 实现占位，实际购票流程尚未从 HAR 中完整逆向） ──

    def book_ticket(
        self,
        account: FerryAccount,
        db: Session,
        trip: dict,
        passenger_ids: list,
        vehicle_id: Optional[int],
        log_fn=None,
    ) -> dict:
        def log(msg, level="INFO"):
            if log_fn:
                log_fn(level, msg)

        log("API 后端暂不支持购票，请切换为 Selenium 后端", "ERROR")
        return {
            "success": False,
            "order_no": None,
            "message": "API 后端暂不支持购票，请在系统设置中切换为 Selenium 后端",
        }


# ── 数据库写入辅助（与 sync_profile.py 的去重逻辑相同）───

# 证件类型 ID → 名称映射（来自 /user/passenger/credential/type 接口）
_CRED_TYPE_MAP = {
    1: "身份证",
    4: "其他证件",
    5: "护照",
    6: "港澳居民来往内地通行证",
    7: "台湾居民来往大陆通行证",
    11: "新版外国人永久居留身份证",
}

# 名称 → ID 反向映射（本地推送到远端时使用）
_ID_TYPE_TO_CRED_ID = {v: k for k, v in _CRED_TYPE_MAP.items()}
# 兼容旧数据中的简写名称
_ID_TYPE_TO_CRED_ID.update({
    "港澳通行证": 6,
    "台湾通行证": 7,
    "其他":       4,
})


def _upsert_passenger(db: Session, data: dict, result: dict):
    id_number = (data.get("credentialNum") or "").strip()
    if not id_number:
        return
    if db.query(Passenger).filter_by(id_number=id_number).first():
        result["passengers_skipped"] += 1
        return
    cred_type_id = data.get("credentialTypeId", 1)
    db.add(Passenger(
        name=data.get("passName", ""),
        id_type=_CRED_TYPE_MAP.get(cred_type_id, "身份证"),
        id_number=id_number,
        phone=data.get("phoneNum") or None,
        remark="自动同步(API)",
    ))
    db.commit()
    result["passengers_added"] += 1


def _upsert_vehicle(db: Session, data: dict, result: dict):
    plate = (data.get("plateNum") or "").strip()
    if not plate:
        return
    if db.query(Vehicle).filter_by(plate_number=plate).first():
        result["vehicles_skipped"] += 1
        return
    db.add(Vehicle(
        plate_number=plate,
        vehicle_type="",
        owner_name="",
        remark="自动同步(API)",
    ))
    db.commit()
    result["vehicles_added"] += 1
