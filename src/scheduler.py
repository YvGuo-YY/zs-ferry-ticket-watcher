"""APScheduler 任务调度器，支持轮询和定时两种触发方式"""
import json
import logging
import random
from datetime import datetime, timedelta
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


def _reschedule_poll_immediately(task_id: int):
    """在随机延迟 1~10 秒（1000~10000ms）后重新调度轮询任务"""
    delay_ms = random.randint(1000, 10000)
    run_at = datetime.now() + timedelta(milliseconds=delay_ms)
    job_id = f"task_{task_id}"
    try:
        _scheduler.add_job(
            _run_task,
            trigger=DateTrigger(run_date=run_at),
            args=[task_id],
            id=job_id,
            replace_existing=True,
            max_instances=1,
        )
    except Exception as e:
        logger.error(f"Task#{task_id} 重新调度失败: {e}")


def _reschedule_next_day_0640(task_id: int):
    """班次未开放时，调度到明天 06:40 重试（避免在 7 天前高频空轮询）"""
    tomorrow = datetime.now() + timedelta(days=1)
    run_at = tomorrow.replace(hour=6, minute=40, second=0, microsecond=0)
    job_id = f"task_{task_id}"
    try:
        _scheduler.add_job(
            _run_task,
            trigger=DateTrigger(run_date=run_at),
            args=[task_id],
            id=job_id,
            replace_existing=True,
            max_instances=1,
        )
        logger.info(f"Task#{task_id} 已调度到 {run_at.strftime('%Y-%m-%d %H:%M')} 重试")
    except Exception as e:
        logger.error(f"Task#{task_id} 次日重新调度失败: {e}")


def _reschedule_today_0640(task_id: int):
    """开售日当天 06:40 前触发时，调度到今日 06:40 开始抢票"""
    run_at = datetime.now().replace(hour=6, minute=40, second=0, microsecond=0)
    job_id = f"task_{task_id}"
    try:
        _scheduler.add_job(
            _run_task,
            trigger=DateTrigger(run_date=run_at),
            args=[task_id],
            id=job_id,
            replace_existing=True,
            max_instances=1,
        )
        logger.info(f"Task#{task_id} 已调度到今日 06:40 开始抢票")
    except Exception as e:
        logger.error(f"Task#{task_id} 调度今日 06:40 失败: {e}")


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


def _get_poll_sale_start(travel_date: str) -> datetime:
    """轮询任务的最早开抢时间：按包含出行日当天在内的提前 7 天计算。"""
    now = datetime.now()
    try:
        travel_dt = datetime.strptime(travel_date, "%Y-%m-%d")
    except Exception:
        return now
    sale_day = travel_dt - timedelta(days=6)
    return sale_day.replace(hour=6, minute=40, second=0, microsecond=0)

def _run_task(task_id: int):
    """实际执行抢票逻辑的函数（在线程池中运行）"""
    from src.database import SessionLocal
    from src.models import Task, FerryAccount
    from src.crawler.factory import get_backend
    from src.crawler.query import find_available_trip
    from src.notify import notify_booked, notify_failed

    db = SessionLocal()
    reschedule = False

    def log(level, msg):
        logger.info(f"[Task#{task_id}] [{level}] {msg}")
        _write_log(db, task_id, level, msg)

    try:
        task = db.query(Task).get(task_id)
        if not task or task.status not in ("running", "pending", "waiting"):
            return

        # 子任务（等待主任务注入班次后直接下单）
        if getattr(task, "parent_task_id", None) and getattr(task, "linked_trip_json", None):
            trip = json.loads(task.linked_trip_json)
            pax_ids = json.loads(task.passenger_ids or "[]")
            preferred_seats = [s.strip() for s in (task.seat_class or "").split(",") if s.strip()]
            task.status = "running"
            db.commit()
            log("INFO", (
                f"关联任务启动：直接使用主任务锁定班次 "
                f"{trip.get('lineName','')} {trip.get('sailTime','')} {trip.get('shipName','')}，"
                f"共 {len(pax_ids)} 名旅客"
            ))
            account = db.query(FerryAccount).get(task.account_id)
            if not account:
                log("ERROR", "Ferry 账号不存在")
                task.status = "failed"
                db.commit()
                return
            backend = get_backend(db)
            result = backend.book_ticket(
                account, db, trip, pax_ids, None,
                preferred_seats=preferred_seats,
                log_fn=log,
            )
            if result["success"]:
                task.status = "booked"
                db.commit()
                _save_order(db, task, trip, result)
                notify_booked(result.get("order_id") or "未知",
                              f"{task.departure_name}→{task.destination_name}", task.travel_date,
                              result.get("payment_expire_at", ""))
                stop_task(task_id)
            else:
                log("ERROR", f"关联任务下单失败：{result['message']}")
                task.status = "failed"
                db.commit()
            return
        else:
            sale_start = None
            is_pre_sale = False

        task.status = "running"
        db.commit()

        account = db.query(FerryAccount).get(task.account_id)
        if not account:
            log("ERROR", "Ferry 账号不存在")
            task.status = "failed"
            db.commit()
            return

        backend = get_backend(db)

        log("INFO", "查询余票...")
        trips = backend.query_trips(
            account, db,
            task.departure_num,
            task.destination_num,
            task.travel_date,
        )

        preferred_seats = [s.strip() for s in (task.seat_class or "").split(",") if s.strip()]
        trip = find_available_trip(
            trips,
            preferred_seats=preferred_seats,
            sail_time_from=getattr(task, "sail_time_from", "") or "",
            sail_time_to=getattr(task, "sail_time_to", "") or "",
            require_vehicle=bool(task.vehicle_id),
        )
        if not trip:
            log("INFO", "未找到合适的班次，继续轮询...")
            _reschedule_poll_immediately(task_id)
            return

        pax_ids = json.loads(task.passenger_ids or "[]")
        # 打印航线及余票信息
        seat_info = "、".join(
            f"{sc['className']}×{sc.get('pubCurrentCount', 0)}"
            for sc in (trip.get("seatClasses") or [])
            if sc.get("pubCurrentCount", 0) > 0
        ) or "（舱位信息不详）"
        log("INFO", (
            f"发现有票：{trip.get('lineName', '')} "
            f"{trip.get('sailTime', '')} {trip.get('shipName', '')}，"
            f"余票：{seat_info}，开始下单，共 {len(pax_ids)} 名旅客"
        ))
        # 将驾驶员 ID 注入 trip，供 book_ticket 识别哪位乘客是驾驶员
        if task.driver_passenger_id:
            trip["_driver_passenger_id"] = task.driver_passenger_id
        result = backend.book_ticket(
            account, db, trip, pax_ids, task.vehicle_id,
            preferred_seats=preferred_seats,
            log_fn=log,
        )

        if result["success"]:
            task.status = "booked"
            db.commit()
            route = f"{task.departure_name}→{task.destination_name}"
            # 保存订单记录
            _save_order(db, task, trip, result)
            # 触发关联子任务
            _trigger_child_tasks(db, task_id, trip, log)
            notify_booked(result.get("order_id") or "未知", route, task.travel_date,
                          result.get("payment_expire_at", ""))
            stop_task(task_id)
        else:
            log("ERROR", f"下单失败：{result['message']}")
            if task.trigger_type == "schedule":
                task.status = "failed"
                db.commit()
                notify_failed(task_id, result["message"])
            else:
                reschedule = True

    except Exception as e:
        logger.exception(f"Task#{task_id} 异常：{e}")
    finally:
        db.close()
        if reschedule:
            _reschedule_poll_immediately(task_id)


def _save_order(db, task, trip, result):
    """将下单成功的结果写入 orders 表"""
    try:
        from src.models import Order, Passenger
        import json as _json
        pax_ids_list = _json.loads(task.passenger_ids or "[]")
        pax_names = [
            p.name for p in db.query(Passenger).filter(Passenger.id.in_(pax_ids_list)).all()
        ]
        db.add(Order(
            task_id=task.id,
            account_id=task.account_id,
            order_id=result.get("order_id") or "",
            departure_name=task.departure_name,
            destination_name=task.destination_name,
            travel_date=task.travel_date,
            sail_time=result.get("sail_time") or trip.get("sailTime", ""),
            ship_name=result.get("ship_name") or trip.get("shipName", ""),
            passengers_json=_json.dumps(pax_names, ensure_ascii=False),
            payment_expire_at=result.get("payment_expire_at") or "",
            status="pending_payment",
        ))
        db.commit()
    except Exception as e:
        logger.warning(f"保存订单记录失败: {e}")


def _trigger_child_tasks(db, parent_task_id: int, trip: dict, log_fn):
    """主任务下单成功后，将班次注入所有 waiting 子任务并立刻触发"""
    from src.models import Task
    children = db.query(Task).filter(
        Task.parent_task_id == parent_task_id,
        Task.status == "waiting",
    ).all()
    for child in children:
        try:
            child.linked_trip_json = json.dumps(trip, ensure_ascii=False)
            child.status = "pending"
            db.commit()
            start_task(child.id)
            log_fn("INFO", f"已触发关联旅客单 Task#{child.id}（共 {len(json.loads(child.passenger_ids or '[]'))} 人）")
        except Exception as e:
            logger.warning(f"触发子任务 Task#{child.id} 失败: {e}")


def start_task(task_id: int):
    """注册并立即触发任务"""
    from src.database import SessionLocal
    from src.models import Task
    db = SessionLocal()
    try:
        task = db.query(Task).get(task_id)
        if not task:
            return

        # 先移除旧任务（若有）
        _remove_existing_job(task_id)

        now = datetime.now()
        if now > datetime.now().replace(hour=23):
            log("INFO", "当前时间已过 23:00，调度到次日 06:40 开始抢票")
            _reschedule_next_day_0640(task_id)
            return
        # 刷票时间限制：
        # - 开售日为包含出行日当天在内的提前 7 天
        # - 例如 5 月 3 日的票，在 4 月 27 日 06:50 开始查票
        sale_datetime = _get_poll_sale_start(task.travel_date)  # 开售日 06:40
        sale_date = sale_datetime.replace(hour=0, minute=0, second=0, microsecond=0)

        if now < sale_date:
            # 情况3：now 在开售日之前 → 每日 06:40 检查一次
            log("INFO", (
                f"开售日之前（开售日：{sale_datetime.strftime('%Y-%m-%d')}），"
                f"将于每天 06:40 检查是否开放抢票"
            ))
            _reschedule_next_day_0640(task_id)
        elif now < sale_datetime:
            # 情况1：now 在开售日当天且在 06:40 之前 → 调度到今日 06:40，无需查询
            log("INFO", (
                f"开售日当天 06:40 前（开售日：{sale_datetime.strftime('%Y-%m-%d')}）,"
                f"将于今日 06:40 开始抢票"
            ))
            _reschedule_today_0640(task_id)
            return
        else:
            # 情况2：now 在开售日当天且在 06:40 或之后 → 立即开始高频轮询
            log("INFO", (
                f"已到开售日当天 06:40 或之后（开售日：{sale_datetime.strftime('%Y-%m-%d')}）,"
                f"立即开始抢票"
            ))
            _reschedule_poll_immediately(task_id)
    finally:
        db.close()

def _remove_existing_job(task_id: int):
    """移除已存在的调度任务（如果有）"""
    job_id = f"task_{task_id}"
    if _scheduler.get_job(job_id):
        _scheduler.remove_job(job_id)

def stop_task(task_id: int):
    """停止任务的调度"""
    _remove_existing_job(task_id)

def get_running_jobs() -> list[str]:
    return [job.id for job in _scheduler.get_jobs()]
