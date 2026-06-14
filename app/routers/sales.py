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
from models import User, Organization
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


# ─── SKU → product_mode mapping ──────────────────────────────────────────────
_SKU_TO_MODE = {
    "FULL": "full",
    "TRAIN_RU": "train",
    "TRAIN_GLOBAL": "train",
}

_VALID_SKUS = {"FULL", "TRAIN_RU", "TRAIN_GLOBAL"}


@router.post("/assign-product/{user_id}")
def assign_product(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    sku: str = Form(...),
    org_name: str = Form(""),
    market: str = Form(""),
):
    """
    Назначить продукт (SKU) пользователю.
    Создаёт или обновляет Organization и явно ставит user.product_mode.
    """
    current_user = _require_sale_manager(request, db)

    if sku not in _VALID_SKUS:
        raise HTTPException(status_code=400, detail=f"Неизвестный SKU: {sku}")

    target_user = db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if target_user.role in ("admin", "sale_manager"):
        raise HTTPException(status_code=400, detail="Нельзя назначать продукт этой роли")

    # Создаём или обновляем Organization
    org = None
    if target_user.organization_id:
        org = db.get(Organization, target_user.organization_id)

    if org is None:
        name = org_name.strip() or f"Org-{target_user.email}"
        org = Organization(
            name=name,
            sku=sku,
            market=market.strip() or None,
            assigned_by=current_user.id,
            assigned_at=datetime.utcnow(),
        )
        db.add(org)
        db.flush()  # получаем org.id
    else:
        org.sku = sku
        org.market = market.strip() or org.market
        org.assigned_by = current_user.id
        org.assigned_at = datetime.utcnow()

    # Привязываем пользователя и ставим product_mode явно
    target_user.organization_id = org.id
    product_mode = _SKU_TO_MODE[sku]
    target_user.product_mode = product_mode

    # Для FULL SKU назначаем роль manager, для Train — оставляем текущую (или manager для РОПа)
    if sku == "FULL":
        target_user.is_premium = True
        target_user.premium_granted_by = current_user.id
        target_user.premium_granted_at = datetime.utcnow()
        target_user.analyses_used = 0
        if target_user.role == "user":
            target_user.role = "manager"
    else:
        # Train: роль manager нужна для управления командой, но аналитику закроет capability-guard
        if target_user.role == "user":
            target_user.role = "manager"

    db.commit()
    logger.info(
        f"Sale Manager {current_user.id} назначил SKU={sku} "
        f"(product_mode={product_mode}) пользователю {target_user.id} ({target_user.email}), "
        f"org={org.id}"
    )

    return RedirectResponse(url="/sales/", status_code=status.HTTP_302_FOUND)

    logger.info(f"Sale Manager {current_user.id} сбросил счётчик анализов для пользователя {target_user.id}")
    return RedirectResponse(url="/sales/", status_code=status.HTTP_302_FOUND)

