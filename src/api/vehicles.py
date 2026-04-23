from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from src.database import get_db, SessionLocal
from src.models import Vehicle, SystemUser
from src.auth import get_current_user
from src.schemas import VehicleCreate, VehicleUpdate, VehicleOut

router = APIRouter(prefix="/api/vehicles", tags=["vehicles"])


def _bg_push_vehicle(vehicle_id: int):
    """后台将车辆推送到所有 Ferry 账号的远端常用车辆列表"""
    from src.models import FerryAccount
    from src.crawler.factory import get_backend
    db = SessionLocal()
    try:
        vehicle = db.query(Vehicle).get(vehicle_id)
        if not vehicle:
            return
        accounts = db.query(FerryAccount).all()
        for acc in accounts:
            backend = get_backend(db)
            try:
                resp = backend.push_vehicle(acc, db, vehicle)
                code = resp.get("code", 0)
                msg = resp.get("message", "")
                if code == 200:
                    print(f"[PUSH] 车辆 {vehicle.plate_number} → {acc.phone}: 成功")
                elif code == 0:
                    pass  # 此后端不支持推送，静默跳过
                else:
                    print(f"[PUSH] 车辆 {vehicle.plate_number} → {acc.phone}: {msg}")
            except Exception as e:
                print(f"[PUSH] 车辆推送异常 ({acc.phone}): {e}")
    finally:
        db.close()


def _bg_delete_vehicle(plate_number: str):
    """后台从所有 Ferry 账号的远端常用车辆列表中删除指定车辆"""
    from src.models import FerryAccount
    from src.crawler.factory import get_backend
    db = SessionLocal()
    try:
        accounts = db.query(FerryAccount).all()
        if not accounts:
            print(f"[DEL] 车辆 {plate_number}: 无 Ferry 账号，跳过远端同步")
            return
        print(f"[DEL] 开始同步删除车辆 {plate_number}，共 {len(accounts)} 个账号")
        for acc in accounts:
            backend = get_backend(db)
            try:
                resp = backend.delete_vehicle(acc, db, plate_number)
                code = resp.get("code", 0)
                msg = resp.get("message", "")
                if code == 0:
                    pass  # 此后端不支持删除，静默跳过
                elif code == 200:
                    print(f"[DEL] 车辆 {plate_number} → {acc.phone}: 删除成功")
                elif code == -1:
                    print(f"[DEL] 车辆 {plate_number} → {acc.phone}: {msg}")
                else:
                    print(f"[DEL] 车辆 {plate_number} → {acc.phone}: 失败 code={code} {msg}")
            except Exception as e:
                print(f"[DEL] 车辆 {plate_number} → {acc.phone}: 异常 {e}")
    finally:
        db.close()


@router.post("/sync-all")
def sync_all_vehicles(
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    """从所有 Ferry 账号拉取常用车辆并合并到本地（去重）"""
    from src.models import FerryAccount
    from src.crawler.factory import get_backend
    accounts = db.query(FerryAccount).all()
    if not accounts:
        return {"vehicles_added": 0, "vehicles_skipped": 0, "errors": ["没有 Ferry 账号"]}
    total_added = 0
    total_skipped = 0
    errors = []
    backend = get_backend(db)
    for acc in accounts:
        try:
            result = backend.sync_profile(acc, db)
            total_added += result.get("vehicles_added", 0)
            total_skipped += result.get("vehicles_skipped", 0)
            errors.extend(result.get("errors", []))
        except Exception as e:
            errors.append(f"{acc.phone}: {e}")
    return {"vehicles_added": total_added, "vehicles_skipped": total_skipped, "errors": errors}


@router.get("/", response_model=list[VehicleOut])
def list_vehicles(
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    return db.query(Vehicle).order_by(Vehicle.id).all()


@router.post("/", response_model=VehicleOut, status_code=201)
def create_vehicle(
    body: VehicleCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    if db.query(Vehicle).filter_by(plate_number=body.plate_number).first():
        raise HTTPException(status_code=400, detail="该车牌已存在")
    v = Vehicle(**body.model_dump())
    db.add(v)
    db.commit()
    db.refresh(v)
    background_tasks.add_task(_bg_push_vehicle, v.id)
    return v


@router.put("/{vid}", response_model=VehicleOut)
def update_vehicle(
    vid: int,
    body: VehicleUpdate,
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    v = db.query(Vehicle).get(vid)
    if not v:
        raise HTTPException(status_code=404, detail="车辆不存在")
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(v, field, val)
    db.commit()
    db.refresh(v)
    return v


@router.delete("/{vid}", status_code=204)
def delete_vehicle(
    vid: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    v = db.query(Vehicle).get(vid)
    if not v:
        raise HTTPException(status_code=404, detail="车辆不存在")
    plate_number = v.plate_number
    db.delete(v)
    db.commit()
    background_tasks.add_task(_bg_delete_vehicle, plate_number)
