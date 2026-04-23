"""Bark 推送通知"""
import requests

from src.database import SessionLocal
from src.models import Setting

DEFAULT_SERVER = "https://api.day.app"


def _get_bark_config() -> tuple[str, str]:
    db = SessionLocal()
    try:
        key_row = db.query(Setting).filter_by(key="bark_key").first()
        srv_row = db.query(Setting).filter_by(key="bark_server").first()
        key = key_row.value if key_row else ""
        server = srv_row.value if srv_row else DEFAULT_SERVER
        return key, server
    finally:
        db.close()


def send_bark(title: str, body: str, url: str = "", sound: str = "birdsong") -> bool:
    bark_key, bark_server = _get_bark_config()
    if not bark_key:
        return False

    endpoint = f"{bark_server.rstrip('/')}/{bark_key}"
    payload = {"title": title, "body": body, "sound": sound}
    if url:
        payload["url"] = url

    try:
        resp = requests.post(endpoint, json=payload, timeout=8)
        return resp.status_code == 200
    except Exception:
        return False


def notify_booked(order_id: str, route: str, travel_date: str):
    send_bark(
        title="🎫 抢票成功！",
        body=f"{route} {travel_date}\n订单号：{order_id}\n请在15分钟内完成支付",
        sound="success",
    )


def notify_failed(task_id: int, reason: str):
    send_bark(
        title="❌ 抢票失败",
        body=f"任务 #{task_id} 失败：{reason[:100]}",
        sound="alarm",
    )
