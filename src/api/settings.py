from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.database import get_db
from src.models import SystemUser, Setting
from src.auth import get_current_user
from src.schemas import SettingsUpdate
from src.notify import send_bark

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(Setting).filter_by(key=key).first()
    return (row.value or default) if row else default


def _set_setting(db: Session, key: str, value: str):
    row = db.query(Setting).filter_by(key=key).first()
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


@router.get("/")
def get_settings(
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    return {
        "selenium_url": _get_setting(db, "selenium_url", "http://192.168.1.117:14444/wd/hub"),
        "bark_key": _get_setting(db, "bark_key"),
        "bark_server": _get_setting(db, "bark_server", "https://api.day.app"),
        "crawler_backend": _get_setting(db, "crawler_backend", "api"),
    }


@router.put("/")
def update_settings(
    body: SettingsUpdate,
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    for field, val in body.model_dump(exclude_none=True).items():
        _set_setting(db, field, val)
    db.commit()
    return {"message": "设置已保存"}


@router.post("/test-bark")
def test_bark(
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    from src.notify import _get_bark_config, _send_to_key
    keys, server = _get_bark_config()
    if not keys:
        return {"success": False, "message": "Bark Key 未配置"}
    payload = {
        "title": "🔔 Ferry 抢票 · 测试推送",
        "body": "Bark 推送配置正常，抢票成功后将通过此渠道通知您。",
        "sound": "birdsong",
    }
    results = {k: _send_to_key(k, server, payload) for k in keys}
    success_count = sum(1 for v in results.values() if v)
    if success_count == len(keys):
        msg = f"全部 {len(keys)} 个 Key 推送成功（服务器：{server}）"
    elif success_count > 0:
        failed = [k for k, v in results.items() if not v]
        msg = f"{success_count}/{len(keys)} 个 Key 成功，失败 Key：{', '.join(failed)}"
    else:
        msg = f"全部推送失败，服务器：{server}，请检查 Bark Key 和服务器地址是否正确"
    return {"success": success_count > 0, "message": msg}
