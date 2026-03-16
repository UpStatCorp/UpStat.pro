"""
Роутер для Sale Manager — управление подписками пользователей.
Sale Manager видит всех пользователей, их лимиты, и может давать/убирать безлимитный доступ.
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request, HTTPException, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session

from database import get_db
from models import User
from admin import get_current_user, is_sale_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sales", tags=["sales"])


def _require_sale_manager(request: Request, db: Session) -> User:
    """Проверяет, что текущий пользователь — sale_manager"""
    user = get_current_user(request, db)
    if not is_sale_manager(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для доступа к панели продаж"
        )
    return user


@router.get("/", response_class=HTMLResponse)
def sales_dashboard(request: Request, db: Session = Depends(get_db), search: str = ""):
    """Главная страница Sale Manager — список пользователей"""
    current_user = _require_sale_manager(request, db)

    # Получаем пользователей (исключая админов и sale_manager)
    query = db.query(User).filter(User.role.notin_(["admin", "sale_manager"]))

    if search:
        search_term = f"%{search}%"
        query = query.filter(
            (User.name.ilike(search_term)) | (User.email.ilike(search_term))
        )

    users = query.order_by(User.created_at.desc()).all()

    # Статистика
    total_users = len(users)
    premium_users = sum(1 for u in users if u.is_premium)
    free_users = total_users - premium_users

    return request.app.state.templates.TemplateResponse(
        "sales/users.html",
        {
            "request": request,
            "current_user": current_user,
            "users": users,
            "search": search,
            "total_users": total_users,
            "premium_users": premium_users,
            "free_users": free_users,
        }
    )


@router.post("/toggle-premium/{user_id}")
def toggle_premium(user_id: int, request: Request, db: Session = Depends(get_db)):
    """Включение/выключение безлимитного доступа для пользователя"""
    current_user = _require_sale_manager(request, db)

    target_user = db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    if target_user.role in ("admin", "sale_manager"):
        raise HTTPException(status_code=400, detail="Нельзя изменять подписку для этой роли")

    if target_user.is_premium:
        # Убираем безлимит — НЕ меняем роль
        target_user.is_premium = False
        target_user.premium_granted_by = None
        target_user.premium_granted_at = None
        logger.info(f"Sale Manager {current_user.id} убрал безлимит у пользователя {target_user.id} ({target_user.email})")
    else:
        # Даём безлимит + делаем РОПом
        target_user.is_premium = True
        target_user.role = "manager"
        target_user.premium_granted_by = current_user.id
        target_user.premium_granted_at = datetime.utcnow()
        target_user.analyses_used = 0  # Сбрасываем счётчик
        logger.info(f"Sale Manager {current_user.id} дал безлимит пользователю {target_user.id} ({target_user.email}), роль → manager")

    db.commit()

    return RedirectResponse(url="/sales/", status_code=status.HTTP_302_FOUND)


@router.post("/reset-analyses/{user_id}")
def reset_analyses(user_id: int, request: Request, db: Session = Depends(get_db)):
    """Сброс счётчика использованных анализов"""
    current_user = _require_sale_manager(request, db)

    target_user = db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    target_user.analyses_used = 0
    db.commit()

    logger.info(f"Sale Manager {current_user.id} сбросил счётчик анализов для пользователя {target_user.id}")
    return RedirectResponse(url="/sales/", status_code=status.HTTP_302_FOUND)

