from typing import Callable
from fastapi import HTTPException, Request, Depends
from sqlalchemy.orm import Session
from database import get_db
from models import User


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    user = db.get(User, uid)
    if not user:
        request.session.clear()
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    # Sale Manager не имеет доступа к обычным страницам — только к /sales/
    if user.role == "sale_manager":
        path = str(request.url.path)
        if not path.startswith("/sales") and path not in ("/logout",):
            raise HTTPException(status_code=302, headers={"Location": "/sales/"})
    return user


def require_capability(key: str) -> Callable:
    """
    Dependency-фабрика: проверяет, что аутентифицированный пользователь
    имеет capability `key`. При отсутствии — 403 с редиректом на /dashboard.

    Использование:
        router = APIRouter(dependencies=[Depends(require_capability("call_analysis"))])
    или на конкретном endpoint:
        @app.get("/...", dependencies=[Depends(require_capability("call_analysis"))])
    """
    def _check(request: Request, user: User = Depends(require_user)) -> User:
        from services.capability_service import has_capability
        if not has_capability(user, key):
            raise HTTPException(
                status_code=403,
                detail=f"Capability '{key}' required",
                headers={"Location": "/dashboard"},
            )
        return user

    _check.__name__ = f"require_{key}"
    return _check
