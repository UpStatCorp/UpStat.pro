from fastapi import APIRouter, Depends, Request, status, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from database import get_db
from models import User, Conversation, ZoomMeeting
from admin import admin_required, get_current_user, is_admin

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    """Главная страница админки"""
    current_user = get_current_user(request, db)
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для доступа к админ-панели"
        )
    
    # Получаем статистику
    total_users = db.query(User).count()
    total_conversations = db.query(Conversation).count()
    total_meetings = db.query(ZoomMeeting).count()
    
    # Получаем последних пользователей
    recent_users = db.query(User).order_by(User.created_at.desc()).limit(5).all()
    
    stats = {
        "total_users": total_users,
        "total_conversations": total_conversations,
        "total_meetings": total_meetings,
        "new_users_today": 0,  # TODO: реализовать подсчет новых пользователей за день
        "new_conversations_today": 0,  # TODO: реализовать подсчет новых диалогов за день
        "new_meetings_today": 0,  # TODO: реализовать подсчет новых встреч за день
        "active_sessions": 1  # TODO: реализовать подсчет активных сессий
    }
    
    return request.app.state.templates.TemplateResponse(
        "admin/dashboard.html",
        {"request": request, "current_user": current_user, "stats": stats, "recent_users": recent_users}
    )


@router.get("/users", response_class=HTMLResponse)
def admin_users(request: Request, db: Session = Depends(get_db)):
    """Управление пользователями"""
    current_user = get_current_user(request, db)
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для доступа к управлению пользователями"
        )
    
    users = db.query(User).order_by(User.created_at.desc()).all()
    
    return request.app.state.templates.TemplateResponse(
        "admin/users.html",
        {"request": request, "current_user": current_user, "users": users}
    )


ALLOWED_ROLES = {"user", "admin", "manager", "sale_manager"}


@router.post("/users/{user_id}/set-role")
def set_user_role(
    user_id: int,
    request: Request,
    role: str = Form(...),
    db: Session = Depends(get_db)
):
    """Установка роли пользователя"""
    current_user = get_current_user(request, db)
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для изменения ролей пользователей"
        )
    
    if role not in ALLOWED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Недопустимая роль: {role}"
        )
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Пользователь не найден"
        )
    
    # Нельзя изменить роль самого себя
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя изменить собственную роль"
        )
    
    user.role = role
    db.commit()
    
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)


@router.post("/users/{user_id}/delete")
def delete_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Удаление пользователя"""
    current_user = get_current_user(request, db)
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для удаления пользователей"
        )
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Пользователь не найден"
        )
    
    # Нельзя удалить самого себя
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя удалить самого себя"
        )
    
    db.delete(user)
    db.commit()
    
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)
