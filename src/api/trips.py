"""余票查询 API"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.auth import get_current_user
from src.database import get_db
from src.models import FerryAccount, SystemUser
from src.crawler.factory import get_backend

router = APIRouter(prefix="/api/trips", tags=["trips"])


@router.get("/sale-date")
def get_sale_date(
    account_id: int,
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    """查询指定账号当前最远可购票日期"""
    acc = db.query(FerryAccount).get(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Ferry 账号不存在")
    backend = get_backend(db)
    try:
        sale_date = backend.get_sale_date(acc, db)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"sale_date": sale_date}


class TripQueryRequest(BaseModel):
    account_id: int
    start_port_no: int
    end_port_no: int
    date: str  # YYYY-MM-DD
    require_vehicle: bool = False


@router.post("/query")
def query_trips(
    body: TripQueryRequest,
    db: Session = Depends(get_db),
    _: SystemUser = Depends(get_current_user),
):
    """查询指定日期的班次余票"""
    acc = db.query(FerryAccount).get(body.account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Ferry 账号不存在")

    backend = get_backend(db)
    try:
        trips = backend.query_trips(
            acc,
            db,
            body.start_port_no,
            body.end_port_no,
            body.date,
            require_vehicle=body.require_vehicle,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"trips": trips}
