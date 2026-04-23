from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.database import get_db
from src.models import SystemUser
from src.auth import require_admin, hash_password
from src.schemas import UserCreate, UserUpdate, UserOut

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    _: SystemUser = Depends(require_admin),
):
    return [UserOut.model_validate(u) for u in db.query(SystemUser).all()]


@router.post("/", response_model=UserOut, status_code=201)
def create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    admin: SystemUser = Depends(require_admin),
):
    if db.query(SystemUser).filter_by(username=body.username).first():
        raise HTTPException(status_code=400, detail="用户名已存在")
    user = SystemUser(
        username=body.username,
        password_hash=hash_password(body.password),
        role=body.role,
        is_active=True,
        created_by=admin.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)


@router.put("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    body: UserUpdate,
    db: Session = Depends(get_db),
    admin: SystemUser = Depends(require_admin),
):
    user = db.query(SystemUser).get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.username == "admin" and body.is_active is False:
        raise HTTPException(status_code=400, detail="不能禁用 admin 账号")
    if body.password:
        user.password_hash = hash_password(body.password)
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)


@router.delete("/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: SystemUser = Depends(require_admin),
):
    user = db.query(SystemUser).get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.username == "admin":
        raise HTTPException(status_code=400, detail="不能删除 admin 账号")
    db.delete(user)
    db.commit()
