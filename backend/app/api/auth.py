import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..schemas import LoginIn, RefreshIn, RegisterIn, TokensOut, UserOut
from ..security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from .deps import current_user

router = APIRouter(prefix="/auth", tags=["auth"])


def _tokens(user: User) -> TokensOut:
    return TokensOut(
        access_token=create_access_token(user.id, user.role.value),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/register", response_model=UserOut, status_code=201)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.scalar(select(User.id).where(func.lower(User.email) == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
    user = User(email=email, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=TokensOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong email or password")
    return _tokens(user)


@router.post("/refresh", response_model=TokensOut)
def refresh(body: RefreshIn, db: Session = Depends(get_db)):
    try:
        data = decode_token(body.refresh_token, "refresh")
        user = db.get(User, int(data["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        user = None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid refresh token")
    return _tokens(user)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user
