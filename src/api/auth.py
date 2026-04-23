from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.database import get_db
from src.models import SystemUser
from src.auth import verify_password, create_access_token, get_current_user, hash_password
from src.schemas import LoginRequest, TokenOut, UserOut, UserUpdate

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(SystemUser).filter_by(username=body.username, is_active=True).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    token = create_access_token({"sub": user.username})
    return TokenOut(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(current_user: SystemUser = Depends(get_current_user)):
    return UserOut.model_validate(current_user)


@router.put("/me/password")
def change_own_password(
    body: UserUpdate,
    current_user: SystemUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not body.password:
        raise HTTPException(status_code=400, detail="密码不能为空")
    current_user.password_hash = hash_password(body.password)
    db.commit()
    return {"message": "密码修改成功"}
