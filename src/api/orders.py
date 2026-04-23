import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.database import get_db
from src.models import Order, SystemUser
from src.auth import get_current_user

router = APIRouter(prefix="/api/orders", tags=["orders"])

PAYMENT_BASE = "https://pc.ssky123.com"


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
        "passengers": json.loads(o.passengers_json or "[]"),
        "payment_expire_at": o.payment_expire_at,
        "status": o.status,
        "payment_url": PAYMENT_BASE,
        "created_at": o.created_at.isoformat() if o.created_at else None,
    }


@router.get("/")
def list_orders(
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    orders = db.query(Order).order_by(Order.created_at.desc()).all()
    return [_serialize(o) for o in orders]


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
