import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.database import get_db
from src.models import FerryAccount, Order, SystemUser
from src.auth import get_current_user

router = APIRouter(prefix="/api/orders", tags=["orders"])

PAYMENT_BASE = "https://pc.ssky123.com"
STATUS_LABELS = {
    "pending_payment": "待支付",
    "paid": "已支付",
    "cancelled": "已取消",
}


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _serialize(o: Order) -> dict:
    return {
        "id": o.id,
        "task_id": o.task_id,
        "account_id": o.account_id,
        "order_id": o.order_id,
        "departure_name": o.departure_name,
        "destination_name": o.destination_name,
        "travel_date": o.travel_date,
        "sail_time": o.sail_time,
        "ship_name": o.ship_name,
        "clxm": o.ship_type or "",
        "passengers": json.loads(o.passengers_json or "[]"),
        "remote_created_at": o.remote_created_at.isoformat() if o.remote_created_at else None,
        "payment_expire_at": o.payment_expire_at,
        "status": o.status,
        "status_label": STATUS_LABELS.get(o.status, o.status),
        "can_pay": o.status == "pending_payment",
        "can_view_detail": o.status == "paid",
        "payment_url": PAYMENT_BASE,
        "created_at": o.created_at.isoformat() if o.created_at else None,
    }


def _detail_item_view(item: dict) -> dict:
    return {
        "seatClassName": item.get("seatClassName") or "",
        "seatNumber": item.get("seatNumber") or "",
        "realFee": item.get("realFee"),
        "clxm": item.get("clxm") or "",
        "hxlxm": item.get("hxlxm") or "",
        "credentialNum": item.get("credentialNum") or "",
        "passName": item.get("passName") or "",
        "lineName": item.get("lineName") or "",
        "createTime": item.get("createTime") or "",
    }


def _status_sort_rank(status: str) -> int:
    return {"pending_payment": 0, "paid": 1, "cancelled": 2}.get(status, 9)


def _filter_orders(orders: list[Order], status_filter: str) -> list[Order]:
    if status_filter == "all":
        return orders
    if "," in status_filter:
        allowed = {part.strip() for part in status_filter.split(",") if part.strip()}
        return [o for o in orders if o.status in allowed]
    if status_filter == "cancelled":
        return [o for o in orders if o.status == "cancelled"]
    if status_filter == "paid":
        return [o for o in orders if o.status == "paid"]
    if status_filter == "pending_payment":
        return [o for o in orders if o.status == "pending_payment"]
    return [o for o in orders if o.status in ("pending_payment", "paid")]


def _sort_orders(orders: list[Order]) -> list[Order]:
    return sorted(
        orders,
        key=lambda o: (
            _status_sort_rank(o.status),
            -(int((o.travel_date or "0000-00-00").replace("-", "")) if (o.travel_date or "").replace("-", "").isdigit() else 0),
            -(int(o.remote_created_at.timestamp()) if o.remote_created_at else 0),
        ),
    )


@router.get("/")
def list_orders(
    status_filter: str = "active",
    account_id: int | None = None,
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    query = db.query(Order)
    if account_id:
        query = query.filter(Order.account_id == account_id)
    orders = query.all()
    orders = _sort_orders(_filter_orders(orders, status_filter))
    return [_serialize(o) for o in orders]


@router.get("/{order_id}/detail")
def get_order_detail(
    order_id: int,
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    o = db.query(Order).get(order_id)
    if not o:
        raise HTTPException(status_code=404, detail="订单不存在")
    items = json.loads(o.order_items_json or "[]")
    return {
        **_serialize(o),
        "order_items": [_detail_item_view(item) for item in items],
    }


@router.post("/sync")
def sync_orders(
    body: dict | None = None,
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    from src.crawler.factory import get_backend

    account_id = (body or {}).get("account_id")
    accounts_query = db.query(FerryAccount)
    if account_id:
        accounts_query = accounts_query.filter(FerryAccount.id == account_id)
    accounts = accounts_query.all()
    if not accounts:
        raise HTTPException(status_code=404, detail="未找到可同步的 Ferry 账号")

    backend = get_backend(db)
    summary = {
        "accounts": 0,
        "supported": 0,
        "fetched": 0,
        "created": 0,
        "updated": 0,
        "errors": [],
    }
    for acc in accounts:
        try:
            result = backend.sync_orders(acc, db)
            summary["accounts"] += 1
            if result.get("supported"):
                summary["supported"] += 1
            summary["fetched"] += int(result.get("fetched") or 0)
            summary["created"] += int(result.get("created") or 0)
            summary["updated"] += int(result.get("updated") or 0)
            for err in result.get("errors") or []:
                if str(err).strip():
                    summary["errors"].append(f"{acc.phone}: {err}")
        except Exception as e:
            summary["accounts"] += 1
            err = str(e).strip()
            if err:
                summary["errors"].append(f"{acc.phone}: {err}")
    return summary


@router.patch("/{order_id}/status")
def update_order_status(
    order_id: int,
    body: dict,
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    o = db.query(Order).get(order_id)
    if not o:
        raise HTTPException(status_code=404, detail="订单不存在")
    allowed = {"pending_payment", "paid", "cancelled"}
    new_status = body.get("status", "")
    if new_status not in allowed:
        raise HTTPException(status_code=400, detail=f"无效状态，可选：{allowed}")
    o.status = new_status
    db.commit()
    return _serialize(o)


@router.delete("/{order_id}")
def delete_order(
    order_id: int,
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    o = db.query(Order).get(order_id)
    if not o:
        raise HTTPException(status_code=404, detail="订单不存在")
    db.delete(o)
    db.commit()
    return {"message": "已删除"}
