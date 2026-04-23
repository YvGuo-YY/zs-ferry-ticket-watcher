from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from src.database import get_db, SessionLocal
from src.models import Passenger, SystemUser
from src.auth import get_current_user
from src.schemas import PassengerCreate, PassengerUpdate, PassengerOut

router = APIRouter(prefix="/api/passengers", tags=["passengers"])


def _bg_push_passenger(passenger_id: int):
    """后台将旅客推送到所有 Ferry 账号的远端常用旅客列表"""
    from src.models import FerryAccount
    from src.crawler.factory import get_backend
    db = SessionLocal()
    try:
        passenger = db.query(Passenger).get(passenger_id)
        if not passenger:
            return
        accounts = db.query(FerryAccount).all()
        for acc in accounts:
            backend = get_backend(db)
            try:
                resp = backend.push_passenger(acc, db, passenger)
                code = resp.get("code", 0)
                msg = resp.get("message", "")
                if code == 200:
                    print(f"[PUSH] 旅客 {passenger.name} → {acc.phone}: 成功")
                elif code == 0:
                    pass  # 此后端不支持推送，静默跳过
                else:
                    print(f"[PUSH] 旅客 {passenger.name} → {acc.phone}: {msg}")
            except Exception as e:
                print(f"[PUSH] 旅客推送异常 ({acc.phone}): {e}")
    finally:
        db.close()


def _bg_delete_passenger(id_number: str, id_type: str, name: str):
    """后台从所有 Ferry 账号的远端常用旅客列表中删除指定旅客"""
    from src.models import FerryAccount
    from src.crawler.factory import get_backend
    db = SessionLocal()
    try:
        accounts = db.query(FerryAccount).all()
        if not accounts:
            print(f"[DEL] 旅客 {name}: 无 Ferry 账号，跳过远端同步")
            return
        print(f"[DEL] 开始同步删除旅客 {name}（{id_number}），共 {len(accounts)} 个账号")
        for acc in accounts:
            backend = get_backend(db)
            try:
                resp = backend.delete_passenger(acc, db, id_number, id_type)
                code = resp.get("code", 0)
                msg = resp.get("message", "")
                if code == 0:
                    pass  # 此后端不支持删除，静默跳过
                elif code == 200:
                    print(f"[DEL] 旅客 {name} → {acc.phone}: 删除成功")
                elif code == -1:
                    print(f"[DEL] 旅客 {name} → {acc.phone}: {msg}")
                else:
                    print(f"[DEL] 旅客 {name} → {acc.phone}: 失败 code={code} {msg}")
            except Exception as e:
                print(f"[DEL] 旅客 {name} → {acc.phone}: 异常 {e}")
    finally:
        db.close()


@router.get("/", response_model=list[PassengerOut])
def list_passengers(
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    return [PassengerOut.model_validate(p) for p in db.query(Passenger).all()]


@router.post("/", response_model=PassengerOut, status_code=201)
def create_passenger(
    body: PassengerCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    p = Passenger(**body.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    background_tasks.add_task(_bg_push_passenger, p.id)
    return PassengerOut.model_validate(p)


@router.put("/{pid}", response_model=PassengerOut)
def update_passenger(
    pid: int,
    body: PassengerUpdate,
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    p = db.query(Passenger).get(pid)
    if not p:
        raise HTTPException(status_code=404, detail="旅客不存在")
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(p, field, val)
    db.commit()
    db.refresh(p)
    return PassengerOut.model_validate(p)


@router.delete("/{pid}", status_code=204)
def delete_passenger(
    pid: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    p = db.query(Passenger).get(pid)
    if not p:
        raise HTTPException(status_code=404, detail="旅客不存在")
    # 先快照，本地删除后后台再同步到远端
    id_number, id_type, name = p.id_number, p.id_type, p.name
    db.delete(p)
    db.commit()
    background_tasks.add_task(_bg_delete_passenger, id_number, id_type, name)
