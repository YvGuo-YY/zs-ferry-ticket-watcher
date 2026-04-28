import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.database import get_db
from src.models import Task, TaskLog, SystemUser
from src.auth import get_current_user
from src.schemas import TaskCreate, TaskOut, TaskLogOut, LinkedPassengerTask, TaskUpdate

router = APIRouter(prefix="/api/tasks", tags=["tasks"])
CAR_TICKET_TYPE = "小客车及随车人员"


def _validate_trigger(trigger_type: str, trigger_value: str):
    if trigger_type != "schedule":
        return
    if not trigger_value:
        raise HTTPException(status_code=400, detail="定时开售必须填写触发时间")
    try:
        datetime.fromisoformat(trigger_value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="定时开售时间格式无效") from exc


def _related_split_tasks(db: Session, source_task_id: int) -> list[Task]:
    return db.query(Task).filter(Task.split_source_task_id == source_task_id).all()

def _build_split_plan(ticket_type: str, passenger_ids: list[int], driver_passenger_id: int | None, linked_cfg):
    if not linked_cfg or not linked_cfg.enabled:
        return None
    if ticket_type != CAR_TICKET_TYPE:
        raise HTTPException(status_code=400, detail="只有人车票支持启用超员拆单")
    if len(passenger_ids) <= 3:
        raise HTTPException(status_code=400, detail="超过3名旅客后才能启用超员拆单")
    if not driver_passenger_id or driver_passenger_id not in passenger_ids:
        raise HTTPException(status_code=400, detail="启用超员拆单前请先从已选旅客中指定驾驶员")
    split_passenger_ids = [pid for pid in passenger_ids if pid != driver_passenger_id]
    if not split_passenger_ids:
        raise HTTPException(status_code=400, detail="拆单后至少需要一名旅客进入旅客票任务")
    return {
        "main_passenger_ids": [driver_passenger_id],
        "split_passenger_ids": split_passenger_ids,
        "seat_classes": linked_cfg.seat_classes,
    }


def _upsert_split_task(
    db: Session,
    source_task: Task,
    split_plan: dict | None,
    created_by: int | None,
):
    from src.scheduler import stop_task as sched_stop

    related = _related_split_tasks(db, source_task.id)
    child_tasks = [t for t in related if t.id != source_task.id]

    for task in related:
        if task.status in ("running", "booked"):
            raise HTTPException(status_code=400, detail="请先停止或处理拆单任务后再修改主任务")

    def purge(tasks_to_delete: list[Task]):
        for task in tasks_to_delete:
            sched_stop(task.id)
            db.delete(task)

    if not split_plan:
        purge(related)
        db.commit()
        return

    target = child_tasks[:1]
    purge(child_tasks[1:])

    split_task = target[0] if target else Task(created_by=created_by)
    split_task.account_id = source_task.account_id
    split_task.departure_num = source_task.departure_num
    split_task.departure_name = source_task.departure_name
    split_task.destination_num = source_task.destination_num
    split_task.destination_name = source_task.destination_name
    split_task.travel_date = source_task.travel_date
    split_task.ticket_type = "旅客"
    split_task.seat_class = ",".join(split_plan["seat_classes"])
    split_task.sail_time_from = source_task.sail_time_from
    split_task.sail_time_to = source_task.sail_time_to
    split_task.vehicle_id = None
    split_task.driver_passenger_id = None
    split_task.passenger_ids = json.dumps(split_plan["split_passenger_ids"])
    split_task.trigger_type = source_task.trigger_type
    split_task.trigger_value = source_task.trigger_value
    split_task.split_source_task_id = source_task.id
    split_task.linked_trip_json = None
    split_task.parent_task_id = source_task.id
    split_task.status = "waiting"
    if split_task.id is None:
        db.add(split_task)
    else:
        sched_stop(split_task.id)
    db.commit()


def _serialize_task(t: Task, db: Session) -> TaskOut:
    import json as _json
    pids = _json.loads(t.passenger_ids or "[]")
    seat_classes = [s.strip() for s in (t.seat_class or "").split(",") if s.strip()]
    child_ids = [c.id for c in db.query(Task).filter(Task.parent_task_id == t.id).all()]
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
        parent_task_id=getattr(t, "parent_task_id", None),
        split_source_task_id=getattr(t, "split_source_task_id", None),
        child_task_ids=child_ids,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


@router.get("/", response_model=list[TaskOut])
def list_tasks(
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    return [_serialize_task(t, db) for t in db.query(Task).order_by(Task.created_at.desc()).all()]


@router.post("/", response_model=TaskOut, status_code=201)
def create_task(
    body: TaskCreate,
    db: Session = Depends(get_db),
    user: SystemUser = Depends(get_current_user),
):
    _validate_trigger(body.trigger_type, body.trigger_value or "")
    split_plan = _build_split_plan(
        body.ticket_type,
        body.passenger_ids,
        body.driver_passenger_id,
        body.linked_passenger_task,
    )
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
        passenger_ids=json.dumps(split_plan["main_passenger_ids"] if split_plan else body.passenger_ids),
        trigger_type=body.trigger_type,
        trigger_value=body.trigger_value or "",
        status="pending",
        created_by=user.id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    _upsert_split_task(db, task, split_plan, user.id)

    return _serialize_task(task, db)


@router.put("/{task_id}", response_model=TaskOut)
def update_task(
    task_id: int,
    body: TaskUpdate,
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    task = db.query(Task).get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status == "running":
        raise HTTPException(status_code=400, detail="任务运行中，请先停止再编辑")
    related_split_tasks = _related_split_tasks(db, task.id)
    next_trigger_type = body.trigger_type if body.trigger_type is not None else task.trigger_type
    next_trigger_value = (
        body.trigger_value if body.trigger_value is not None
        else ("" if body.trigger_type == "poll" else task.trigger_value)
    )
    next_ticket_type = body.ticket_type if body.ticket_type is not None else task.ticket_type
    next_passenger_ids = body.passenger_ids if body.passenger_ids is not None else json.loads(task.passenger_ids or "[]")
    next_driver_passenger_id = (
        body.driver_passenger_id if body.driver_passenger_id is not None
        else task.driver_passenger_id
    )
    _validate_trigger(next_trigger_type, next_trigger_value)
    split_plan = None
    if not task.parent_task_id and (next_ticket_type == CAR_TICKET_TYPE or related_split_tasks or body.linked_passenger_task is not None):
        split_plan = _build_split_plan(
            next_ticket_type,
            next_passenger_ids,
            next_driver_passenger_id,
            body.linked_passenger_task,
        )
    if body.account_id is not None:
        task.account_id = body.account_id
    if body.travel_date is not None:
        task.travel_date = body.travel_date
    if body.ticket_type is not None:
        task.ticket_type = body.ticket_type
    if body.seat_classes is not None:
        task.seat_class = ",".join(body.seat_classes)
    if body.sail_time_from is not None:
        task.sail_time_from = body.sail_time_from
    if body.sail_time_to is not None:
        task.sail_time_to = body.sail_time_to
    if body.vehicle_id is not None:
        task.vehicle_id = body.vehicle_id
    if body.driver_passenger_id is not None:
        task.driver_passenger_id = body.driver_passenger_id
    if body.passenger_ids is not None:
        task.passenger_ids = json.dumps(body.passenger_ids)
    if body.trigger_type is not None:
        task.trigger_type = body.trigger_type
    if body.trigger_value is not None:
        task.trigger_value = body.trigger_value
    elif body.trigger_type == "poll":
        task.trigger_value = ""
    # 当票种改为旅客时清除车辆关联
    if body.ticket_type == "旅客":
        task.vehicle_id = None
        task.driver_passenger_id = None
    if split_plan:
        task.passenger_ids = json.dumps(split_plan["main_passenger_ids"])
    db.commit()
    db.refresh(task)
    _upsert_split_task(db, task, split_plan, task.created_by)
    db.refresh(task)
    return _serialize_task(task, db)


@router.get("/{task_id}", response_model=TaskOut)
def get_task(
    task_id: int,
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    task = db.query(Task).get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _serialize_task(task, db)


@router.post("/{task_id}/start")
def start_task(
    task_id: int,
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    from src.scheduler import start_task as sched_start, stop_task as sched_stop
    task = db.query(Task).get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status in ("running", "waiting"):
        raise HTTPException(status_code=400, detail="任务已在运行或等待触发中")
    _validate_trigger(task.trigger_type, task.trigger_value or "")
    if not task.parent_task_id:
        legacy_split_tasks = db.query(Task).filter(
            Task.split_source_task_id == task.id,
            Task.id != task.id,
        ).all()
        for split_task in legacy_split_tasks:
            sched_stop(split_task.id)
            split_task.parent_task_id = task.id
            split_task.status = "waiting"
            split_task.linked_trip_json = None
        if legacy_split_tasks:
            db.commit()
    sched_start(task_id)
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
    # 删除关联的拆单任务
    for child in db.query(Task).filter(
        (Task.parent_task_id == task_id) | (Task.split_source_task_id == task_id)
    ).all():
        if child.id == task_id:
            continue
        sched_stop(child.id)
        db.delete(child)
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
