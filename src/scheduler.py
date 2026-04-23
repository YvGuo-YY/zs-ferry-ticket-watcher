"""APScheduler 任务调度器，支持轮询和定时两种触发方式"""
import json
import logging
from datetime import datetime
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger

logger = logging.getLogger(__name__)

_scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
_scheduler.start()

# WebSocket 日志广播函数（由 main.py 注入）
_ws_broadcast: Callable = None


def set_ws_broadcast(fn: Callable):
    global _ws_broadcast
    _ws_broadcast = fn


def _write_log(db, task_id: int, level: str, message: str):
    from src.models import TaskLog
    log = TaskLog(task_id=task_id, level=level, message=message)
    db.add(log)
    db.commit()
    if _ws_broadcast:
        try:
            _ws_broadcast(task_id, level, message)
        except Exception:
            pass


def _run_task(task_id: int):
    """实际执行抢票逻辑的函数（在线程池中运行）"""
    from src.database import SessionLocal
    from src.models import Task, Passenger, FerryAccount, Vehicle
    from src.crawler.login import ensure_logged_in
    from src.crawler.query import query_tickets, find_available_trip
    from src.crawler.booking import book_ticket
    from src.notify import notify_booked, notify_failed

    db = SessionLocal()
    driver = None

    def log(level, msg):
        logger.info(f"[Task#{task_id}] [{level}] {msg}")
        _write_log(db, task_id, level, msg)

    try:
        task = db.query(Task).get(task_id)
        if not task or task.status not in ("running", "pending"):
            return

        task.status = "running"
        db.commit()
        log("INFO", f"任务启动：{task.departure_name} → {task.destination_name}，日期：{task.travel_date}")

        # 获取旅客列表
        pax_ids = json.loads(task.passenger_ids or "[]")
        passengers = db.query(Passenger).filter(Passenger.id.in_(pax_ids)).all()
        pax_list = [
            {"name": p.name, "id_type": p.id_type, "id_number": p.id_number, "phone": p.phone or ""}
            for p in passengers
        ]

        # 获取车辆信息（小客车票时使用）
        vehicle_info = None
        if task.vehicle_id:
            v = db.query(Vehicle).get(task.vehicle_id)
            if v:
                vehicle_info = {
                    "plate_number": v.plate_number,
                    "vehicle_type": v.vehicle_type,
                    "owner_name": v.owner_name,
                }
                log("INFO", f"关联车辆：{v.plate_number}")

        # 获取 Ferry 账号
        account = db.query(FerryAccount).get(task.account_id)
        if not account:
            log("ERROR", "Ferry 账号不存在")
            task.status = "failed"
            db.commit()
            return

        # 登录（复用或重新登录）
        log("INFO", "检查登录状态...")
        driver, login_msg = ensure_logged_in(account, db)
        log("INFO", login_msg)

        # 查询余票
        log("INFO", "开始查询余票...")
        trips = query_tickets(
            driver,
            task.departure_num,
            task.destination_num,
            task.travel_date,
            task.ticket_type,
            departure_name=task.departure_name,
            destination_name=task.destination_name,
            log_fn=log,
        )

        trip = find_available_trip(trips, preferred_seat=task.seat_class or "")
        if not trip:
            log("WARN", "暂无余票")
            # 轮询模式：不更新 status，等下一次触发
            if task.trigger_type == "schedule":
                task.status = "failed"
                db.commit()
                notify_failed(task_id, "查询不到余票")
            return

        # 下单
        log("INFO", f"发现有票，开始下单，共 {len(pax_list)} 名旅客")
        result = book_ticket(driver, trip, pax_list, vehicle=vehicle_info, log_fn=log)

        if result["success"]:
            task.status = "booked"
            db.commit()
            route = f"{task.departure_name}→{task.destination_name}"
            notify_booked(result["order_id"] or "未知", route, task.travel_date)
            # 下单成功后停止轮询
            stop_task(task_id)
        else:
            log("ERROR", f"下单失败：{result['message']}")
            if task.trigger_type == "schedule":
                task.status = "failed"
                db.commit()
                notify_failed(task_id, result["message"])

    except Exception as e:
        logger.exception(f"Task#{task_id} 异常：{e}")
        try:
            log("ERROR", f"任务异常：{str(e)[:500]}")
            task = db.query(Task).get(task_id)
            if task and task.trigger_type == "schedule":
                task.status = "failed"
                db.commit()
        except Exception:
            pass
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        db.close()


def start_task(task_id: int):
    """注册并立即触发任务"""
    from src.database import SessionLocal
    from src.models import Task
    db = SessionLocal()
    try:
        task = db.query(Task).get(task_id)
        if not task:
            return

        job_id = f"task_{task_id}"
        # 先移除旧任务（若有）
        if _scheduler.get_job(job_id):
            _scheduler.remove_job(job_id)

        if task.trigger_type == "poll":
            interval_secs = max(5, int(task.trigger_value or "10"))
            _scheduler.add_job(
                _run_task,
                trigger=IntervalTrigger(seconds=interval_secs),
                args=[task_id],
                id=job_id,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
        else:
            # schedule 模式：指定时间触发一次
            run_at = datetime.fromisoformat(task.trigger_value)
            _scheduler.add_job(
                _run_task,
                trigger=DateTrigger(run_date=run_at, timezone="Asia/Shanghai"),
                args=[task_id],
                id=job_id,
                replace_existing=True,
                max_instances=1,
            )
    finally:
        db.close()


def stop_task(task_id: int):
    """停止任务的调度"""
    job_id = f"task_{task_id}"
    if _scheduler.get_job(job_id):
        _scheduler.remove_job(job_id)


def get_running_jobs() -> list[str]:
    return [job.id for job in _scheduler.get_jobs()]
