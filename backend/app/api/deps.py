from __future__ import annotations

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Role, User
from ..security import decode_token

_bearer = HTTPBearer(auto_error=False)


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not logged in")
    try:
        data = decode_token(creds.credentials, "access")
        user = db.get(User, int(data["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        user = None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role not in (Role.ADMIN, Role.OPERATOR):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
    return user


def current_kiosk(
    x_kiosk_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Kiosk agents authenticate with the API key shown when the kiosk was created."""
    from ..services.kiosks import authenticate_kiosk

    kiosk = authenticate_kiosk(db, x_kiosk_key) if x_kiosk_key else None
    if kiosk is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid kiosk key")
    return kiosk
