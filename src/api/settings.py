from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.database import get_db
from src.models import SystemUser, Setting
from src.auth import get_current_user
from src.schemas import SettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(Setting).filter_by(key=key).first()
    return row.value if row else default


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
