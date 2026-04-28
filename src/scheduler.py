"""APScheduler 任务调度器，支持轮询和定时两种触发方式"""
import json
import logging
import random
from dataclasses import dataclass
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


@dataclass(frozen=True)
class StartDecision:
    action: str
    run_at: datetime | None = None
    sale_datetime: datetime | None = None
    trigger_at: datetime | None = None


def set_ws_broadcast(fn: Callable):
    global _ws_broadcast
    _ws_broadcast = fn


def _schedule_job(task_id: int, run_at: datetime, runner: Callable[[int], None]):
    job_id = f"task_{task_id}"
    _scheduler.add_job(
        runner,
        trigger=DateTrigger(run_date=run_at),
        args=[task_id],
        id=job_id,
        replace_existing=True,
        max_instances=1,
    )


def _reschedule_poll_immediately(task_id: int):
    """在随机延迟 1~10 秒（1000~10000ms）后重新调度轮询任务"""
    delay_ms = random.randint(1000, 10000)
    run_at = datetime.now() + timedelta(milliseconds=delay_ms)
    try:
        _schedule_job(task_id, run_at, _run_task)
    except Exception as e:
        logger.error(f"Task#{task_id} 重新调度失败: {e}")


def _reschedule_next_day_0640(task_id: int):
    """班次未开放时，调度到明天 06:40 重试（避免在 7 天前高频空轮询）"""
    tomorrow = datetime.now() + timedelta(days=1)
    run_at = tomorrow.replace(hour=6, minute=40, second=0, microsecond=0)
    try:
        _schedule_job(task_id, run_at, _run_task)
        logger.info(f"Task#{task_id} 已调度到 {run_at.strftime('%Y-%m-%d %H:%M')} 重试")
    except Exception as e:
        logger.error(f"Task#{task_id} 次日重新调度失败: {e}")


def _reschedule_today_0640(task_id: int):
    """开售日当天 06:40 前触发时，调度到今日 06:40 开始抢票"""
    run_at = datetime.now().replace(hour=6, minute=40, second=0, microsecond=0)
    try:
        _schedule_job(task_id, run_at, _run_task)
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


def _parse_schedule_datetime(trigger_value: str) -> datetime:
    if not trigger_value:
        raise ValueError("schedule trigger_value is required")
    try:
        return datetime.fromisoformat(trigger_value)
    except ValueError as exc:
        raise ValueError(f"invalid schedule trigger_value: {trigger_value}") from exc


def _trip_sail_time(trip: dict) -> str:
    return trip.get("sailTime") or trip.get("sail_time", "") or ""


def _trip_in_time_range(trip: dict, sail_time_from: str, sail_time_to: str) -> bool:
    sail = _trip_sail_time(trip)
    if not sail:
        return True
    if sail_time_from and sail < sail_time_from:
        return False
    if sail_time_to and sail > sail_time_to:
        return False
    return True


def _format_trip_seat_summary(seats: list[dict], name_key: str = "className") -> str:
    if not seats:
        return "无"
    parts = []
    for seat in seats:
        name = seat.get(name_key) or seat.get("seatClassName") or seat.get("name") or "未知舱位"
        parts.append(f"{name}×{seat.get('pubCurrentCount', 0)}")
    return "、".join(parts)


def build_no_trip_log_message(
    trips: list[dict],
    preferred_seats: list[str],
    sail_time_from: str,
    sail_time_to: str,
    require_vehicle: bool,
) -> str:
    scoped_trips = [t for t in trips if _trip_in_time_range(t, sail_time_from, sail_time_to)]
    scope_label = (
        f"{sail_time_from or '--:--'}~{sail_time_to or '--:--'}"
        if sail_time_from or sail_time_to else "全部时段"
    )
    if not scoped_trips:
        return f"未找到合适的班次，继续轮询... 当前筛选时段 {scope_label} 内无班次"

    preferred_label = "不限" if not preferred_seats else "/".join(preferred_seats)
    trip_parts = []
    for trip in scoped_trips:
        status = "开售" if trip.get("onSale", 1) else "未开售"
        line_name = trip.get("lineName") or ""
        ship_name = trip.get("shipName") or trip.get("ship_name") or ""
        seat_summary = _format_trip_seat_summary(trip.get("seatClasses") or [])
        part = (
            f"{_trip_sail_time(trip) or '--:--'} {ship_name or '未同步船名'}"
            f"{f' {line_name}' if line_name else ''}"
            f" [{status}] 客舱:{seat_summary}"
        )
        if require_vehicle:
            car_summary = _format_trip_seat_summary(trip.get("driverSeatClass") or [])
            part += f" 车位:{car_summary}"
        trip_parts.append(part)

    return (
        f"未找到合适的班次，继续轮询... 时段:{scope_label}，舱位偏好:{preferred_label}"
        f"{'，需同时有车位' if require_vehicle else ''}。"
        f" 当前班次：{'；'.join(trip_parts)}"
    )


def decide_start_action(trigger_type: str, trigger_value: str, travel_date: str, now: datetime) -> StartDecision:
    sale_datetime = _get_poll_sale_start(travel_date)
    sale_date = sale_datetime.replace(hour=0, minute=0, second=0, microsecond=0)
    trigger_at = None

    if trigger_type == "schedule":
        trigger_at = _parse_schedule_datetime(trigger_value)
        if now < trigger_at:
            return StartDecision(
                action="delay_until_trigger_value",
                run_at=trigger_at,
                sale_datetime=sale_datetime,
                trigger_at=trigger_at,
            )

    if now < sale_date:
        run_at = (now + timedelta(days=1)).replace(hour=6, minute=40, second=0, microsecond=0)
        return StartDecision(
            action="schedule_next_day_0640",
            run_at=run_at,
            sale_datetime=sale_datetime,
            trigger_at=trigger_at,
        )

    if now < sale_datetime:
        run_at = now.replace(hour=6, minute=40, second=0, microsecond=0)
        return StartDecision(
            action="schedule_today_0640",
            run_at=run_at,
            sale_datetime=sale_datetime,
            trigger_at=trigger_at,
        )

    return StartDecision(
        action="poll_now",
        sale_datetime=sale_datetime,
        trigger_at=trigger_at,
    )


def _schedule_task_activation(task_id: int, run_at: datetime):
    try:
        _schedule_job(task_id, run_at, _activate_scheduled_task)
        logger.info(f"Task#{task_id} 已排队到指定时间 {run_at.strftime('%Y-%m-%d %H:%M:%S')}")
    except Exception as e:
        logger.error(f"Task#{task_id} 定时排队失败: {e}")


def _activate_scheduled_task(task_id: int):
    from src.database import SessionLocal
    from src.models import Task

    db = SessionLocal()
    try:
        task = db.query(Task).get(task_id)
        if not task or task.status not in ("pending", "running", "waiting"):
            return
        _write_log(db, task_id, "INFO", "已到定时时间，进入开售规则判断")
    finally:
        db.close()

    start_task(task_id, bypass_schedule=True)


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
        now = datetime.now()
        if now > now.replace(hour=23, minute=0, second=0, microsecond=0):
            log("INFO", "当前时间已过 23:00，调度到次日 06:40 开始抢票")
            _reschedule_next_day_0640(task_id)
            return

        # 拆单子任务（已拿到主任务班次后直接下单）
        if getattr(task, "linked_trip_json", None) and getattr(task, "parent_task_id", None):
            trip = json.loads(task.linked_trip_json)
            pax_ids = json.loads(task.passenger_ids or "[]")
            preferred_seats = [s.strip() for s in (task.seat_class or "").split(",") if s.strip()]
            task.status = "running"
            db.commit()
            log("INFO", (
                f"关联任务启动：直接使用主任务锁定班次 "
                f"{trip.get('lineName', '')} {trip.get('sailTime', '')} {trip.get('shipName', '')}，"
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

        task.status = "running"
        db.commit()

        account = db.query(FerryAccount).get(task.account_id)
        if not account:
            log("ERROR", "Ferry 账号不存在")
            task.status = "failed"
            db.commit()
            return

        backend = get_backend(db)

        query_path = "/line/ferry/enq" if bool(task.vehicle_id) else "/line/ship/enq"
        query_mode = "人车票接口" if bool(task.vehicle_id) else "旅客票接口"
        log("INFO", f"开始查询余票（{query_mode} {query_path}）...")
        trips = backend.query_trips(
            account, db,
            task.departure_num,
            task.destination_num,
            task.travel_date,
            require_vehicle=bool(task.vehicle_id),
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
            log(
                "INFO",
                build_no_trip_log_message(
                    trips,
                    preferred_seats,
                    getattr(task, "sail_time_from", "") or "",
                    getattr(task, "sail_time_to", "") or "",
                    bool(task.vehicle_id),
                ),
            )
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
            _trigger_split_tasks(db, task_id, trip, log)
            notify_booked(result.get("order_id") or "未知", route, task.travel_date,
                          result.get("payment_expire_at", ""))
            stop_task(task_id)
        else:
            if result.get("order_id"):
                log("WARN", f"下单失败（订单号 {result['order_id']}）：{result.get('message', '未知错误')}")
            else:
                log("WARN", f"下单失败：{result.get('message', '未知错误')}")
            reschedule = True

    except Exception as e:
        logger.exception(f"Task#{task_id} 异常：{e}")
        try:
            log("ERROR", f"执行异常：{e}")
        except Exception:
            pass
        notify_failed(task_id, f"Task#{task_id} 执行异常: {e}")
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


def _trigger_split_tasks(db, parent_task_id: int, trip: dict, log_fn):
    """主任务下单成功后，将班次注入所有 waiting 关联子任务并立刻触发。"""
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


def start_task(task_id: int, bypass_schedule: bool = False):
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

        if getattr(task, "linked_trip_json", None) and getattr(task, "parent_task_id", None):
            task.status = "running"
            db.commit()
            _write_log(db, task_id, "INFO", "关联任务已获取主任务班次，立即开始下单")
            _reschedule_poll_immediately(task_id)
            return

        now = datetime.now()
        decision = decide_start_action(
            task.trigger_type,
            task.trigger_value,
            task.travel_date,
            now,
        ) if not bypass_schedule else decide_start_action("poll", "", task.travel_date, now)

        if task.trigger_type == "schedule" and not bypass_schedule:
            if decision.action == "delay_until_trigger_value":
                task.status = "waiting"
                db.commit()
                _write_log(
                    db,
                    task_id,
                    "INFO",
                    f"已排队到指定时间 {decision.run_at.strftime('%Y-%m-%d %H:%M:%S')}，到点后进入开售规则判断",
                )
                _schedule_task_activation(task_id, decision.run_at)
                return
            _write_log(db, task_id, "INFO", "定时时间已过，按当前开售规则继续处理")

        if decision.action == "schedule_next_day_0640":
            task.status = "waiting"
            db.commit()
            _write_log(db, task_id, "INFO", (
                f"开售日之前（开售日：{decision.sale_datetime.strftime('%Y-%m-%d')}），"
                f"下次检查时间：{decision.run_at.strftime('%Y-%m-%d %H:%M:%S')}"
            ))
            _reschedule_next_day_0640(task_id)
            return

        if decision.action == "schedule_today_0640":
            task.status = "waiting"
            db.commit()
            _write_log(db, task_id, "INFO", (
                f"开售日当天 06:40 前（开售日：{decision.sale_datetime.strftime('%Y-%m-%d')}），"
                f"将于 {decision.run_at.strftime('%Y-%m-%d %H:%M:%S')} 开始抢票"
            ))
            _reschedule_today_0640(task_id)
            return

        task.status = "running"
        db.commit()
        _write_log(db, task_id, "INFO", (
            f"已到开售日当天 06:40 或之后（开售日：{decision.sale_datetime.strftime('%Y-%m-%d')}），"
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
