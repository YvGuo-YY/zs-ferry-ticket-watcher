import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.database import get_db
from src.models import Task, TaskLog, SystemUser
from src.auth import get_current_user
from src.schemas import TaskCreate, TaskOut, TaskLogOut

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _serialize_task(t: Task) -> TaskOut:
    import json as _json
    pids = _json.loads(t.passenger_ids or "[]")
    seat_classes = [s.strip() for s in (t.seat_class or "").split(",") if s.strip()]
    return TaskOut(
        id=t.id,
        account_id=t.account_id,
        departure_num=t.departure_num,
        departure_name=t.departure_name,
        destination_num=t.destination_num,
        destination_name=t.destination_name,
        travel_date=t.travel_date,
        ticket_type=t.ticket_type,
        seat_classes=seat_classes,
        sail_time_from=getattr(t, "sail_time_from", "") or "",
        sail_time_to=getattr(t, "sail_time_to", "") or "",
        vehicle_id=t.vehicle_id,
        driver_passenger_id=getattr(t, "driver_passenger_id", None),
        passenger_ids=pids,
        trigger_type=t.trigger_type,
        trigger_value=t.trigger_value,
        status=t.status,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


@router.get("/", response_model=list[TaskOut])
def list_tasks(
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    return [_serialize_task(t) for t in db.query(Task).order_by(Task.created_at.desc()).all()]


@router.post("/", response_model=TaskOut, status_code=201)
def create_task(
    body: TaskCreate,
    db: Session = Depends(get_db),
    user: SystemUser = Depends(get_current_user),
):
    task = Task(
        account_id=body.account_id,
        departure_num=body.departure_num,
        departure_name=body.departure_name,
        destination_num=body.destination_num,
        destination_name=body.destination_name,
        travel_date=body.travel_date,
        ticket_type=body.ticket_type,
        seat_class=",".join(body.seat_classes),
        sail_time_from=body.sail_time_from,
        sail_time_to=body.sail_time_to,
        vehicle_id=body.vehicle_id,
        driver_passenger_id=body.driver_passenger_id,
        passenger_ids=json.dumps(body.passenger_ids),
        trigger_type=body.trigger_type,
        trigger_value=body.trigger_value or "",
        status="pending",
        created_by=user.id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return _serialize_task(task)


@router.get("/{task_id}", response_model=TaskOut)
def get_task(
    task_id: int,
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    task = db.query(Task).get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _serialize_task(task)


@router.post("/{task_id}/start")
def start_task(
    task_id: int,
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    from src.scheduler import start_task as sched_start
    task = db.query(Task).get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status == "running":
        raise HTTPException(status_code=400, detail="任务正在运行中")
    sched_start(task_id)
    task.status = "running"
    db.commit()
    return {"message": "任务已启动"}


@router.post("/{task_id}/stop")
def stop_task(
    task_id: int,
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    from src.scheduler import stop_task as sched_stop
    task = db.query(Task).get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    sched_stop(task_id)
    task.status = "stopped"
    db.commit()
    return {"message": "任务已停止"}


@router.delete("/{task_id}", status_code=204)
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    from src.scheduler import stop_task as sched_stop
    task = db.query(Task).get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    sched_stop(task_id)
    db.delete(task)
    db.commit()


@router.get("/{task_id}/logs", response_model=list[TaskLogOut])
def get_task_logs(
    task_id: int,
    limit: int = 200,
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    logs = (
        db.query(TaskLog)
        .filter_by(task_id=task_id)
        .order_by(TaskLog.created_at.desc())
        .limit(limit)
        .all()
    )
    return [TaskLogOut.model_validate(l) for l in reversed(logs)]
